"""lab/local_runtime.py state classification and verified stop (R26).

pg_ctl and the loopback port probe are mocked; no cluster exists or starts.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('review_local_runtime_state', ROOT / 'lab/local_runtime.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = tmp_path / '.lab'
    for key, value in [('REPO', tmp_path), ('STATE', state), ('PGDATA', state / 'pgdata'), ('MARKER', state / 'ISOLATED_ACK_LAB')]:
        monkeypatch.setattr(module, key, value)
    monkeypatch.setattr(module, 'binaries', lambda: tmp_path / 'bin')
    return module


def layout(module, kind):
    if kind == 'none':
        return
    module.STATE.mkdir()
    if kind == 'unmarked':
        (module.STATE / 'stranger.txt').write_text('not ours')
        return
    module.MARKER.write_text('Disposable lab\n')
    if kind == 'uninitialised':
        return
    module.PGDATA.mkdir()
    (module.PGDATA / 'PG_VERSION').write_text('18\n')
    if kind == 'stale-pid':
        (module.PGDATA / 'postmaster.pid').write_text('4242\n/synthetic/pgdata\n')


def tree(root):
    return sorted((str(path.relative_to(root)), path.read_bytes() if path.is_file() else None)
                  for path in root.rglob('*'))


def mock(module, monkeypatch, *, status, listening):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, status, '', '')
    monkeypatch.setattr(module, 'run', run)
    monkeypatch.setattr(module, 'port_listening', lambda port=module.PORT: listening)
    return commands


@pytest.mark.parametrize('kind,status,listening,expected', [
    ('none', 3, False, 'absent'),
    ('none', 3, True, 'ambiguous'),
    ('unmarked', 3, False, 'unmarked'),
    ('unmarked', 0, True, 'unmarked'),
    ('uninitialised', 3, False, 'stopped'),
    ('uninitialised', 3, True, 'ambiguous'),
    ('cluster', 0, True, 'running'),
    ('cluster', 3, False, 'stopped'),
    ('cluster', 3, True, 'ambiguous'),
    ('cluster', 4, False, 'ambiguous'),
    ('cluster', 1, False, 'ambiguous'),
    ('stale-pid', 3, False, 'stopped'),
    ('stale-pid', 3, True, 'ambiguous'),
], ids=['absent', 'port-without-lab', 'unmarked', 'unmarked-even-if-running', 'marked-uninitialised',
        'uninitialised-port-busy', 'running', 'stopped', 'stopped-port-busy', 'inaccessible', 'unreadable-pid',
        'stale-pid', 'stale-pid-port-busy'])
def test_inspect_classifies_every_lab_layout_without_touching_it(runtime, tmp_path, monkeypatch, kind, status, listening, expected):
    layout(runtime, kind)
    before = tree(tmp_path)
    commands = mock(runtime, monkeypatch, status=status, listening=listening)
    observed = runtime.inspect(tmp_path / 'bin')
    assert observed['state'] == expected
    assert observed['port'] == 55432 and observed['port_listening'] is listening
    assert tree(tmp_path) == before
    # pg_ctl is consulted only about an initialised, marked cluster, and only for status.
    assert bool(commands) == (kind in ('cluster', 'stale-pid'))
    assert all(command[-1] == 'status' and command[-3] == '-D' for command in commands)
    if kind == 'stale-pid':
        assert observed['postmaster_pid'] == 4242
        assert observed.get('stale_pid_file') is (True if expected == 'stopped' else None)
    if kind == 'cluster':
        assert observed['pg_ctl_status'] == status and observed['postmaster_pid'] is None


def test_state_command_is_read_only_and_needs_no_isolated_lab_flag(runtime, tmp_path, monkeypatch, capsys):
    layout(runtime, 'cluster')
    commands = mock(runtime, monkeypatch, status=0, listening=True)
    monkeypatch.setattr(sys, 'argv', ['local_runtime.py', 'state'])
    assert runtime.main() == 0
    observed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert observed['state'] == 'running' and observed['detail']
    assert [command[-1] for command in commands] == ['status']


@pytest.mark.parametrize('after_stop,expected', [(3, 0), (0, 5)], ids=['stopped', 'still-running'])
def test_stop_exit_code_reflects_the_observed_state_not_only_pg_ctl(runtime, tmp_path, monkeypatch, capsys, after_stop, expected):
    layout(runtime, 'cluster')
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        code = 0 if command[-1] == 'stop' else after_stop
        return subprocess.CompletedProcess(command, code, '', '')
    monkeypatch.setattr(runtime, 'run', run)
    monkeypatch.setattr(runtime, 'port_listening', lambda port=runtime.PORT: after_stop == 0)
    monkeypatch.setattr(sys, 'argv', ['local_runtime.py', 'stop', '--isolated-lab'])
    assert runtime.main() == expected
    assert [command[-1] for command in commands] == ['stop', 'status']
    observed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert observed['state'] == ('stopped' if expected == 0 else 'running')


def test_stop_still_refuses_an_unmarked_directory(runtime, monkeypatch):
    layout(runtime, 'unmarked')
    commands = mock(runtime, monkeypatch, status=0, listening=True)
    monkeypatch.setattr(sys, 'argv', ['local_runtime.py', 'stop', '--isolated-lab'])
    with pytest.raises(SystemExit, match='unmarked'):
        runtime.main()
    assert commands == []


def test_failed_initdb_keeps_its_output_in_the_lab_log_for_preservation(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.os, 'geteuid', lambda: 1001)

    def run(command, **kwargs):
        if Path(command[0]).name == 'initdb':
            raise subprocess.CalledProcessError(1, command, 'creating directory\n', 'initdb: error: synthetic permission denied\n')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(runtime, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['local_runtime.py', 'start', '--isolated-lab'])
    with pytest.raises(SystemExit, match='initdb failed'):
        runtime.main()
    assert (runtime.STATE / 'initdb.log').read_text() == 'creating directory\ninitdb: error: synthetic permission denied\n'
    assert runtime.MARKER.exists() and not (runtime.PGDATA / 'PG_VERSION').exists()
