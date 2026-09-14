"""Retained attempts and process boundaries, using only temporary local files."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import run_review as runner
from review_runs import atomic_json, new_run, private_directory, workspace_lock


def read_run(root, name):
    return json.loads((root / 'build/review-runs' / name / 'run.json').read_text())


def test_private_leaf_preserves_shared_ancestor_permissions(tmp_path):
    shared = tmp_path / 'build'
    shared.mkdir(mode=0o755)
    mode = shared.stat().st_mode
    leaf = private_directory(shared / 'private/result')
    assert leaf.stat().st_mode & 0o777 == 0o700
    assert shared.stat().st_mode == mode
    assert (shared / 'private').stat().st_mode & 0o111  # traversable for local service users


def test_existing_public_leaf_is_refused_without_chmod(tmp_path):
    leaf = tmp_path / 'shared'
    leaf.mkdir(mode=0o755)
    before = leaf.stat().st_mode
    with pytest.raises(ValueError, match='not_private'):
        private_directory(leaf)
    assert leaf.stat().st_mode == before


@pytest.mark.parametrize('location', ['leaf', 'ancestor'])
def test_paths_refuse_symlinks_before_writing(tmp_path, location):
    target = tmp_path / 'outside'
    target.mkdir()
    link = tmp_path / 'alias'
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        private_directory(link if location == 'leaf' else link / 'new')
    assert not list(target.iterdir())


def test_atomic_write_does_not_follow_existing_link(tmp_path):
    target = tmp_path / 'original'
    target.write_text('keep')
    link = tmp_path / 'report.json'
    link.symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        atomic_json(link, {'status': 'PASS'})
    assert target.read_text() == 'keep'


@pytest.mark.parametrize('run_id', ['../escape', '/absolute', '.', '', 'a/b', 'x' * 81])
def test_invalid_run_identity_creates_nothing(tmp_path, run_id):
    with pytest.raises(ValueError):
        new_run(tmp_path, run_id)
    assert list(tmp_path.iterdir()) == []


def test_retry_uses_new_directory_and_preserves_old_bytes(tmp_path):
    first = new_run(tmp_path, 'attempt')
    path = first / 'run.json'
    atomic_json(path, {'status': 'RUNNING'})
    old = path.read_bytes()
    with pytest.raises(FileExistsError):
        new_run(tmp_path, 'attempt')
    assert new_run(tmp_path) != first
    assert path.read_bytes() == old


def test_checkout_lock_excludes_other_process_and_releases_on_exit(tmp_path):
    code = ('from pathlib import Path\nfrom review_runs import workspace_lock\n'
            'with workspace_lock(Path(__import__("sys").argv[1])): print("acquired")\n')
    env = dict(os.environ, PYTHONPATH=str(Path(runner.__file__).parent))
    with workspace_lock(tmp_path):
        result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and 'review_workspace_busy' in result.stderr
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and result.stdout.strip() == 'acquired'


@pytest.mark.parametrize('code,phase,expected', [(0, 'quality', 'PASS'),
    (2, 'native', 'NOT_RUN'), (2, 'quality', 'FAIL'), (1, 'quality', 'FAIL')])
def test_phase_result_and_unexecuted_remainder_are_distinct(tmp_path, monkeypatch, code, phase, expected):
    calls = []
    def execute(command, root, output, timeout):
        calls.append(command)
        return {'exit_code': code, 'interrupted': False, 'cleanup': 'CHILD_MANAGED'}
    monkeypatch.setattr(runner, 'execute', execute)
    result = runner.run(tmp_path, [phase, 'sql'], run_id='one')
    record = read_run(tmp_path, 'one')
    assert record['phases'][0]['status'] == expected
    assert record['phases'][1]['status'] == ('PASS' if code == 0 else 'NOT_RUN')
    assert len(calls) == (2 if code == 0 else 1)
    assert result == (0 if code == 0 else 1)


def test_only_changed_evidence_belongs_to_new_phase(tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (evidence / 'old.json').write_text('{"historical": true}')
    def execute(command, root, output, timeout):
        (evidence / 'new.json').write_text('{"fresh": true}')
        return {'exit_code': 0, 'interrupted': False}
    monkeypatch.setattr(runner, 'execute', execute)
    assert runner.run(tmp_path, ['quality'], run_id='new') == 0
    snapshot = tmp_path / 'build/review-runs/new/quality/evidence'
    assert {p.name for p in snapshot.iterdir()} == {'new.json'}
    assert (evidence / 'old.json').read_text() == '{"historical": true}'


def test_child_cannot_pass_after_harness_edit(tmp_path, monkeypatch):
    (tmp_path / 'tests').mkdir()
    def execute(command, root, output, timeout):
        (root / 'tests/changed.py').write_text('# concurrent edit\n')
        return {'exit_code': 0, 'interrupted': False}
    monkeypatch.setattr(runner, 'execute', execute)
    assert runner.run(tmp_path, ['quality'], run_id='changed') == 1
    record = read_run(tmp_path, 'changed')
    assert record['tree_unchanged'] is False and record['phases'][0]['status'] == 'FAIL'


def test_real_timeout_is_incomplete_and_cleanup_not_assumed(tmp_path):
    result = runner.execute([sys.executable, '-c', 'import time; time.sleep(30)'],
                            tmp_path, tmp_path, 1)
    assert result['exit_code'] is None and result['interrupted'] is True
    assert result['cleanup'] == 'NOT_CONFIRMED_AFTER_INTERRUPTION'


def test_plan_is_side_effect_free_without_lab_permission(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['run_review.py', '--plan', '--phase', 'quality'])
    assert runner.main() == 0
    assert list(tmp_path.iterdir()) == []
    assert json.loads(capsys.readouterr().out)['phases'][0]['name'] == 'quality'


def test_execution_requires_explicit_isolated_lab(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['run_review.py'])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2 and list(tmp_path.iterdir()) == []
