"""The trusted job hook never archives or resets live or ambiguously owned lab data (R26).

Synthetic runner roots and short-lived child processes; no runner enrollment.
"""
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

pytest.importorskip('fcntl')
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import self_hosted_job as hooks

SLEEP = 'import time; time.sleep(120)'
HOLD = 'import sys, time; handle = open(sys.argv[1]); print("held", flush=True); time.sleep(120)'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    paths = hooks.Paths(tmp_path / 'runner', tmp_path / 'retained')
    paths.workspace.mkdir(parents=True)
    paths.temporary.mkdir(parents=True)
    monkeypatch.setattr(hooks.shutil, 'disk_usage', lambda path: SimpleNamespace(free=20 * 1024**3))
    return paths, {'fixture': 'synthetic-unit-test', 'sha': 'a' * 40}


@pytest.fixture
def child():
    processes = []

    def spawn(code, *args, **kwargs):
        process = subprocess.Popen([sys.executable, '-c', code, *args], **kwargs)
        processes.append(process)
        return process
    yield spawn
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait()


def begin(paths, context):
    return hooks.run_hook('start', paths, context, head_reader=lambda path: None)


def untouched(paths):
    return ((paths.workspace / 'keep.txt').read_text() == 'keep' and not (paths.evidence / 'current-job.json').exists()
            and not list(paths.evidence.rglob('*.tar')) and not list(paths.evidence.rglob('*.tar.partial')))


@pytest.mark.parametrize('pid_file', hooks.LAB_PID_FILES,
                         ids=['postgres', 'pgbouncer', 'zabbix-postgres', 'zabbix-server', 'zabbix-php', 'deployment-postgres'])
def test_live_lab_pid_refuses_start_without_archive_or_reset_then_stale_pid_is_retained(setup, child, pid_file):
    paths, context = setup
    (paths.workspace / 'keep.txt').write_text('keep')
    path = paths.workspace / pid_file
    path.parent.mkdir(parents=True)
    process = child(SLEEP, cwd=paths.evidence.parent)
    path.write_text(f'{process.pid}\n/synthetic/data\n')
    with pytest.raises(ValueError, match='live_lab_service'):
        begin(paths, context)
    assert untouched(paths) and path.exists()
    process.kill()
    process.wait()
    # A PID file whose process is gone is a crashed lab, not a live one: the
    # start retains it with everything else and only then resets.
    begin(paths, context)
    assert not path.exists() and not (paths.workspace / 'keep.txt').exists()
    archive, = paths.evidence.glob('orphan-*/*.tar')
    with tarfile.open(archive) as retained:
        assert {pid_file, 'keep.txt'} <= set(retained.getnames())


@pytest.mark.parametrize('content', ['not-a-pid\n', '', '\n/synthetic/data\n'], ids=['text', 'empty', 'blank-first-line'])
def test_unreadable_lab_pid_file_is_ambiguous_and_refuses(setup, content):
    paths, context = setup
    (paths.workspace / 'keep.txt').write_text('keep')
    path = paths.workspace / hooks.LAB_PID_FILES[0]
    path.parent.mkdir(parents=True)
    path.write_text(content)
    with pytest.raises(ValueError, match='unreadable_lab_pid_file'):
        begin(paths, context)
    assert untouched(paths)


def test_symlinked_lab_pid_file_is_refused_without_following_it(setup, tmp_path):
    paths, context = setup
    (paths.workspace / 'keep.txt').write_text('keep')
    outside = tmp_path / 'outside.pid'
    outside.write_text(f'{os.getpid()}\n')
    path = paths.workspace / hooks.LAB_PID_FILES[1]
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)
    with pytest.raises(ValueError, match='symlink_path'):
        begin(paths, context)
    assert untouched(paths)


@pytest.mark.parametrize('hold', ['cwd', 'open-file'])
def test_a_process_working_inside_the_workspace_refuses_start_until_it_is_gone(setup, child, hold):
    paths, context = setup
    (paths.workspace / 'keep.txt').write_text('keep')
    (paths.workspace / 'build/private').mkdir(parents=True)
    log = paths.workspace / 'build/private/service.log'
    log.write_text('synthetic service output\n')
    if hold == 'cwd':
        process = child(SLEEP, cwd=paths.workspace / 'build/private')
    else:
        process = child(HOLD, str(log), cwd=paths.evidence.parent, stdout=subprocess.PIPE, text=True)
        assert process.stdout.readline().strip() == 'held'  # the file is open before the scan runs
    with pytest.raises(ValueError, match='workspace_in_use'):
        begin(paths, context)
    assert untouched(paths) and log.exists()
    process.kill()
    process.wait()
    begin(paths, context)
    assert not log.exists()
    archive, = paths.evidence.glob('orphan-*/*.tar')
    with tarfile.open(archive) as retained:
        assert 'build/private/service.log' in retained.getnames()


def test_completion_also_refuses_while_a_job_process_still_owns_workspace_data(setup, child):
    paths, context = setup
    started = begin(paths, context)
    (paths.workspace / 'evidence').mkdir()
    (paths.workspace / 'evidence/native-run.json').write_text('{"status": "RUNNING"}')
    process = child(SLEEP, cwd=paths.workspace / 'evidence')
    with pytest.raises(ValueError, match='workspace_in_use'):
        hooks.run_hook('complete', paths, context, head_reader=lambda path: None)
    state = hooks.read_json(paths.evidence / 'current-job.json')
    assert state['phase'] == 'started' and state['attempt'] == started['attempt']
    assert not list((paths.evidence / started['attempt']).glob('*.tar'))
    process.kill()
    process.wait()
    completed = hooks.run_hook('complete', paths, context, head_reader=lambda path: None)
    assert completed['phase'] == 'completed'


def test_process_scan_is_injectable_and_excludes_the_hook_and_its_runner_ancestors(setup):
    paths, context = setup
    (paths.workspace / 'keep.txt').write_text('keep')
    with pytest.raises(ValueError, match='workspace_in_use'):
        hooks.run_hook('start', paths, context, head_reader=lambda path: None, processes=lambda root: [4242])
    assert untouched(paths)
    lineage = hooks.own_lineage()
    assert os.getpid() in lineage and os.getppid() in lineage
    assert not hooks.processes_within(paths.workspace)
    # This test process itself works in the checkout, not the synthetic
    # workspace, and is excluded from its own scan even for its own cwd.
    assert os.getpid() not in hooks.processes_within(Path.cwd())
