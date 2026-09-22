"""Fresh-run ownership and failure handling; mocked, no service evidence."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import evidence_utils
import run_quality
import run_sql_checks


def tree(tmp_path):
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'tools/check.py').write_text('VALUE = 1\n')
    for name in run_quality.INFRASTRUCTURE:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text('VALUE = 1\n')
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'requirements-quality.lock').write_text(
        '\n'.join(name + '==1.2.3 --hash=sha256:abc' for name in ('ruff', 'mypy', 'hypothesis')))
    return tmp_path


@pytest.mark.parametrize('flaw', ['none', 'failure', 'timeout', 'interrupt', 'edited-tree', 'version-mismatch'])
def test_quality_runner_invalidates_prior_success_and_pins_tools(tmp_path, monkeypatch, flaw):
    root = tree(tmp_path)
    output = root / 'evidence/quality-run.json'
    output.write_text('{"status":"PASS","old":true}')
    monkeypatch.setattr(run_quality, 'ROOT', root)
    monkeypatch.setattr(run_quality.importlib.metadata, 'version', lambda name: '9.0' if flaw == 'version-mismatch' else '1.2.3')

    def child(command, **kwargs):
        assert json.loads(output.read_text())['status'] == 'RUNNING'
        assert kwargs['env']['TZ'] == 'UTC'
        if flaw == 'timeout':
            raise subprocess.TimeoutExpired(command, 1)
        if flaw == 'interrupt':
            raise KeyboardInterrupt
        if flaw == 'edited-tree':
            (root / 'tools/check.py').write_text('VALUE = 2\n')
        return subprocess.CompletedProcess(command, 1 if flaw == 'failure' else 0)

    monkeypatch.setattr(run_quality.subprocess, 'run', child)
    assert run_quality.main() == (0 if flaw == 'none' else 1)
    report = json.loads(output.read_text())
    assert report['status'] == ('PASS' if flaw == 'none' else 'FAIL')
    assert 'old' not in report
    if flaw == 'version-mismatch':
        assert report['checks'] == []


def test_infrastructure_lint_is_an_explicit_existing_file_list_run_as_its_own_check(tmp_path, monkeypatch):
    """R29: a bounded ruff pass over the Docker-free lab/runner code, not a repository-wide lint."""
    real = Path(run_quality.__file__).resolve().parents[1]
    assert all((real / name).is_file() for name in run_quality.INFRASTRUCTURE)
    assert not any(any(ch in name for ch in '*?[') or name.endswith('/') for name in run_quality.INFRASTRUCTURE)
    assert run_quality.CHECKS['ruff-infrastructure'] == ['-m', 'ruff', 'check', *run_quality.INFRASTRUCTURE]
    assert set(run_quality.CHECKS) == {'ruff', 'ruff-infrastructure', 'mypy', 'pip-check'}
    root = tree(tmp_path)
    (root / run_quality.INFRASTRUCTURE[0]).unlink()
    monkeypatch.setattr(run_quality, 'ROOT', root)
    monkeypatch.setattr(run_quality.importlib.metadata, 'version', lambda name: '1.2.3')
    monkeypatch.setattr(run_quality.subprocess, 'run', lambda *args, **kwargs: pytest.fail('nothing runs with a listed file missing'))
    assert run_quality.main() == 1
    report = json.loads((root / 'evidence/quality-run.json').read_text())
    assert report['status'] == 'FAIL' and report['reason'] == 'ValueError' and report['checks'] == []


@pytest.mark.parametrize('flaw', ['none', 'failure', 'missing', 'malformed', 'timeout', 'edited-tree'])
def test_sql_runner_owns_fresh_private_output_and_binding(tmp_path, monkeypatch, flaw):
    root = tree(tmp_path)
    output = root / 'evidence/runtime_wasm_sql.json'
    output.write_text('{"status":"PASS","old":true}')
    raw = root / 'build/evidence-raw/sql-groups.json'
    raw.parent.mkdir(parents=True)
    raw.write_text('{"results":[{"status":"PASS"}]}')
    monkeypatch.setattr(run_sql_checks, 'ROOT', root)
    monkeypatch.setattr(sys, 'argv', ['run_sql_checks.py', '--isolated-lab'])

    def child(command, **kwargs):
        assert not raw.exists()
        assert json.loads(output.read_text())['status'] == 'RUNNING'
        assert kwargs['env']['ACK_SQL_REPORT_PATH'] == str(raw)
        if flaw == 'timeout':
            raise subprocess.TimeoutExpired(command, 1)
        if flaw == 'edited-tree':
            (root / 'tools/check.py').write_text('VALUE = 2\n')
        if flaw != 'missing':
            packet = {'results': [{'status': 'FAIL' if flaw == 'failure' else 'PASS'}],
                      'code_digests': {'source': 'untrusted'}, 'diagnostic': str(root / 'private')}
            raw.write_text('<broken' if flaw == 'malformed' else json.dumps(packet))
        return subprocess.CompletedProcess(command, 1 if flaw == 'failure' else 0)

    before = evidence_utils.code_digests(root)
    monkeypatch.setattr(run_sql_checks.subprocess, 'run', child)
    assert run_sql_checks.main() == (0 if flaw == 'none' else 1)
    report = json.loads(output.read_text())
    assert report['status'] == ('PASS' if flaw == 'none' else 'FAIL')
    assert report['code_digests'] == before and 'old' not in report
    assert str(root) not in output.read_text()


@pytest.mark.parametrize('path', ['requirements-quality.lock', 'ruff.toml', 'mypy.ini', '.github/workflows/ci.yml'])
def test_quality_and_ci_inputs_are_part_of_harness_binding(tmp_path, path):
    root = tree(tmp_path)
    before = evidence_utils.code_digests(root)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('changed input\n')
    after = evidence_utils.code_digests(root)
    assert after['source'] == before['source'] and after['harness'] != before['harness']


def test_advisory_queries_include_quality_pins_without_enabling_installation(tmp_path, monkeypatch):
    import security_scan
    root = tree(tmp_path)
    (root / 'requirements.lock').write_text('base==1.0\nshared==2.0\n')
    (root / 'requirements-quality.lock').write_text('quality==3.0 --hash=sha256:abc\nshared==2.0 --hash=sha256:def\n')
    raw = root / 'build/evidence-raw'
    raw.mkdir(parents=True)
    monkeypatch.setattr(security_scan, 'ROOT', root)

    def scanner(command, **kwargs):
        assert '--disable-pip' in command and '--no-deps' in command
        query = Path(command[command.index('-r') + 1]).read_text()
        assert query == 'base==1.0\nquality==3.0\nshared==2.0\n'
        Path(command[command.index('-o') + 1]).write_text(json.dumps({'dependencies': [
            {'name': name, 'vulns': []} for name in ('base', 'quality', 'shared')]}))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(security_scan.subprocess, 'run', scanner)
    assert security_scan.run_audit('synthetic-scanner', raw)['status'] == 'PASS'
