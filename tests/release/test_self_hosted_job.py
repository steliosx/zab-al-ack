"""Synthetic filesystem/metadata tests; no runner enrollment or Actions claims."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
from types import SimpleNamespace

import pytest

pytest.importorskip('fcntl')
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import self_hosted_job as hooks


@pytest.fixture
def setup(tmp_path, monkeypatch):
    paths = hooks.Paths(tmp_path / 'runner', tmp_path / 'retained')
    paths.workspace.mkdir(parents=True)
    paths.temporary.mkdir(parents=True)
    monkeypatch.setattr(hooks.shutil, 'disk_usage', lambda path: SimpleNamespace(free=20 * 1024**3))
    context = {'fixture': 'synthetic-unit-test', 'sha': 'a' * 40}
    return paths, context


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def begin(paths, context):
    return hooks.run_hook('start', paths, context, head_reader=lambda path: None)


def records(paths, attempt):
    return [json.loads(path.read_text()) for path in (paths.evidence / attempt).glob('*.json')
            if path.name != 'start.json']


def test_completion_retains_private_failures_untracked_output_and_actual_head(setup):
    paths, context = setup
    state = begin(paths, context)
    expected = {'build/private/failed.log': 'failed example', 'evidence/native-run.json': '{"status":"FAIL"}',
                'dist/candidate.whl': 'candidate', 'untracked.log': 'interrupted output'}
    for name, data in expected.items():
        write(paths.workspace, name, data)
    for name in ('.git/config', 'lab/node_modules/dependency.js', '.venv/pyvenv.cfg', '.cache/old'):
        write(paths.workspace, name, 'excluded dependency or credential')
    hooks.run_hook('complete', paths, context, head_reader=lambda path: 'b' * 40)
    report, = records(paths, state['attempt'])
    archive = paths.evidence / state['attempt'] / report['archive']
    assert report['status'] == 'JOB_COMPLETED'
    assert report['actual_checkout_sha'] == 'b' * 40
    assert report['context']['sha'] == 'a' * 40  # Difference is retained, never rewritten.
    assert report['job_result'] == 'UNAVAILABLE_FROM_HOOK_USE_ACTIONS_RESULT'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == report['archive_sha256']
    with tarfile.open(archive) as retained:
        assert set(retained.getnames()) == set(expected)
        for name, content in expected.items():
            assert retained.extractfile(name).read().decode() == content
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert stat.S_IMODE(archive.parent.stat().st_mode) == 0o700
    assert (paths.workspace / 'untracked.log').exists()


def test_next_start_preserves_interrupted_attempt_before_workspace_and_venv_reset(setup):
    paths, context = setup
    first = begin(paths, context)
    write(paths.workspace, 'build/private/partial.log', 'keep interrupted attempt')
    write(paths.temporary, 'venv/pyvenv.cfg', 'old environment')
    write(paths.temporary, 'gitleaks/archive.tar.gz', 'old tool')
    second = begin(paths, {'fixture': 'second-synthetic-job'})
    report, = records(paths, first['attempt'])
    assert report['status'] == 'INTERRUPTED'
    assert report['context'] == context
    assert report['files'][0]['path'] == 'build/private/partial.log'
    assert not list(paths.workspace.iterdir())
    assert not (paths.temporary / 'venv').exists()
    assert not (paths.temporary / 'gitleaks').exists()
    assert second['attempt'] != first['attempt']
    assert (paths.evidence / first['attempt'] / 'start.json').exists()


def test_completed_attempt_is_preserved_and_post_hook_changes_are_archived(setup):
    paths, context = setup
    first = begin(paths, context)
    write(paths.workspace, 'result.log', 'first')
    hooks.run_hook('complete', paths, context, head_reader=lambda path: 'a' * 40)
    original = records(paths, first['attempt'])[0]
    original_path = paths.evidence / first['attempt'] / original['archive']
    original_bytes = original_path.read_bytes()
    write(paths.workspace, 'post-hook.log', 'preserve before reset')
    begin(paths, {'fixture': 'next-job'})
    assert original_path.read_bytes() == original_bytes
    assert {record['status'] for record in records(paths, first['attempt'])} == {'JOB_COMPLETED', 'PRE_RESET_SNAPSHOT'}


def test_archive_failure_leaves_workspace_start_state_and_temp_untouched(setup, monkeypatch):
    paths, context = setup
    first = begin(paths, context)
    log = write(paths.workspace, 'build/private/failed.log', 'must survive')
    venv = write(paths.temporary, 'venv/pyvenv.cfg', 'must survive')
    def fail(*args, **kwargs):
        raise OSError('synthetic storage failure')
    monkeypatch.setattr(hooks, 'archive_workspace', fail)
    with pytest.raises(OSError):
        begin(paths, {'fixture': 'next-job'})
    assert log.read_text() == 'must survive'
    assert venv.exists()
    assert hooks.read_json(paths.evidence / 'current-job.json') == first


@pytest.mark.parametrize('entry', ['file-link', 'directory-link', 'fifo', 'hardlink'])
def test_unsafe_entries_cannot_export_external_bytes_or_trigger_reset(setup, tmp_path, entry):
    paths, context = setup
    first = begin(paths, context)
    outside = write(tmp_path, 'outside/secret', 'outside must remain private')
    target = paths.workspace / 'unsafe'
    if entry == 'file-link':
        target.symlink_to(outside)
    elif entry == 'directory-link':
        target.symlink_to(outside.parent, target_is_directory=True)
    elif entry == 'fifo':
        os.mkfifo(target)
    else:
        os.link(outside, target)
    with pytest.raises(ValueError, match='symlink|unsafe_workspace'):
        begin(paths, {'fixture': 'next-job'})
    assert outside.read_text() == 'outside must remain private'
    assert target.lstat()
    assert hooks.read_json(paths.evidence / 'current-job.json') == first


def test_workspace_symlink_and_retention_inside_runner_are_refused(setup, tmp_path):
    paths, context = setup
    paths.workspace.rmdir()
    external = tmp_path / 'external'
    external.mkdir()
    paths.workspace.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink_path'):
        begin(paths, context)
    with pytest.raises(ValueError, match='retention_inside_runner'):
        hooks.validate_paths(hooks.Paths(tmp_path / 'other', tmp_path / 'other/retained'))
    assert external.is_dir()


def test_low_space_refuses_before_reset_and_preserves_records(setup, monkeypatch):
    paths, context = setup
    first = begin(paths, context)
    log = write(paths.workspace, 'failure.log', 'retained on failure')
    monkeypatch.setattr(hooks.shutil, 'disk_usage', lambda path: SimpleNamespace(free=hooks.MIN_FREE_BYTES - 1))
    with pytest.raises(ValueError, match='insufficient_retention_space'):
        begin(paths, {'fixture': 'next-job'})
    assert log.exists()
    assert hooks.read_json(paths.evidence / 'current-job.json') == first


def test_archive_capacity_reserves_space_for_data_and_five_gib_floor(setup, monkeypatch):
    paths, context = setup
    begin(paths, context)
    log = write(paths.workspace, 'failure.log', 'large enough to require room')
    monkeypatch.setattr(hooks.shutil, 'disk_usage', lambda path: SimpleNamespace(free=hooks.MIN_FREE_BYTES + 1))
    with pytest.raises(ValueError, match='insufficient_retention_space'):
        begin(paths, {'fixture': 'next-job'})
    assert log.exists()


def test_foreign_completion_or_parallel_hook_cannot_replace_current_attempt(setup):
    paths, context = setup
    first = begin(paths, context)
    with pytest.raises(ValueError, match='completion_context_mismatch'):
        hooks.run_hook('complete', paths, {'fixture': 'foreign-job'})
    with hooks.hook_lock(paths):
        with pytest.raises(BlockingIOError):
            begin(paths, {'fixture': 'parallel-job'})
    assert hooks.read_json(paths.evidence / 'current-job.json') == first


def test_missing_workspace_still_records_interruption(setup):
    paths, context = setup
    first = begin(paths, context)
    paths.workspace.rmdir()
    begin(paths, {'fixture': 'next-job'})
    report, = records(paths, first['attempt'])
    assert report['status'] == 'INTERRUPTED' and report['files'] == []


def test_cli_rejects_non_actions_context_without_creating_fake_values(setup):
    paths, _ = setup
    with pytest.raises(ValueError, match='actions_context_required'):
        hooks.actions_context(paths, {})


def test_archive_bounds_fail_without_deleting_large_attempt(setup, monkeypatch):
    paths, context = setup
    begin(paths, context)
    log = write(paths.workspace, 'failure.log', '12345')
    monkeypatch.setattr(hooks, 'MAX_ARCHIVE_BYTES', 4)
    with pytest.raises(ValueError, match='retention_bound_exceeded'):
        begin(paths, {'fixture': 'next-job'})
    assert log.read_text() == '12345'
