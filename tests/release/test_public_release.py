"""Release safety boundaries: public selection, truthful evidence and inert CI."""
import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from public_audit import inspect_file, public_files, publication_inputs
from evidence_utils import redact_junit, cases
from build_release import normalise_sdist, source_zip
from native_ci import ensure_local_docker


def test_public_selection_excludes_private_history_runtime_and_raw_logs(tmp_path):
    for name in ['README.md', '.github/workflows/ci.yml', '.git/config', '.hypothesis/constants/cache', 'build/evidence-raw/failed.xml',
                 'lab/node_modules/a.js', 'dist/old.whl', '.venv/key', 'src/pkg/__pycache__/x.pyc',
                 '.lab/pgdata/base/1/1247', '.lab/ISOLATED_ACK_LAB']:
        file = tmp_path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('synthetic')
    assert {p.relative_to(tmp_path).as_posix() for p in public_files(tmp_path)} == {'README.md', '.github/workflows/ci.yml'}


@pytest.mark.parametrize('name,content,rule', [
    ('copy.bundle', b'git-history', 'private-or-secret-file-type'),
    ('test.txt', ('-----BEGIN ' + 'PRIVATE KEY-----').encode(), 'private-key-material'),
    ('test.txt', ('ghp_' + 'a' * 36).encode(), 'github-token'),
    ('test.txt', ('AKIA' + 'A' * 16).encode(), 'aws-access-key'),
    ('evidence/run.xml', ('/' + 'workspace/private/run.py').encode(), 'private-path-in-evidence'),
], ids=['history-bundle', 'private-key-header', 'github-token', 'aws-key', 'private-path'])
def test_export_audit_rejects_sensitive_artifacts(name, content, rule):
    assert rule in inspect_file(name, content)


def test_evidence_redaction_retains_failed_and_skipped_outcomes(tmp_path):
    raw, output = tmp_path / 'raw.xml', tmp_path / 'public.xml'
    raw.write_text('<testsuite hostname="private-host"><testcase classname="tests.a" name="bad">'
        '<failure message="' + str(tmp_path.parent) + '/private/path">failed</failure></testcase>'
        '<testcase classname="tests.b" name="missing"><skipped message="NOT_RUN" /></testcase></testsuite>')
    redact_junit(raw, output, tmp_path)
    assert 'private-host' not in output.read_text()
    assert str(tmp_path.parent) not in output.read_text()
    assert [row['status'] for row in cases(output)] == ['failed', 'skipped']


def test_publication_requires_real_assignment_and_authorization(tmp_path):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/publication.pending.json').write_text('{}')
    missing = publication_inputs(tmp_path)
    assert 'publication_authorized' in missing
    assert 'private_security_reporting_verified' in missing


def test_live_publish_workflow_absent_and_ci_permissions_are_narrow():
    workflows = [p for p in (ROOT / '.github/workflows').glob('*.yml') if p.name != 'publish.yml']
    assert workflows
    for path in workflows:
        value = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        # The manual deployment job reads another run's candidate artifact: read-only actions scope, nothing else.
        narrow = {'contents': 'read', 'actions': 'read'} if path.name == 'deployment-e2e.yml' else {'contents': 'read'}
        assert value['permissions'] == narrow
        assert 'pull_request_target' not in value['on']
        for name, job in value['jobs'].items():
            expected = {'contents': 'read', 'security-events': 'write'} if (path.name, name) == ('codeql.yml', 'analyze') else narrow
            assert job.get('permissions', value['permissions']) == expected
            if expected.get('security-events') == 'write':
                assert job['if'] == "${{ vars.ZABALACK_CODEQL_ENABLED == 'true' }}"
            for step in job['steps']:
                assert 'gh-action-pypi-publish' not in step.get('uses', '')
                if 'uses' in step:
                    assert len(step['uses'].split('@')[1]) == 40
    template = ROOT / 'release/publish.yml.example'
    assert template.is_file() and 'public_audit.py --publication' in template.read_text()
    if (ROOT / '.github/workflows/publish.yml').exists():
        assert publication_inputs(ROOT) == []


def test_native_runner_refuses_remote_docker_without_contact(monkeypatch):
    monkeypatch.setattr('native_ci.shutil.which', lambda _: '/synthetic/docker')
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setenv('DOCKER_HOST', 'tcp://remote.invalid:2376')
    monkeypatch.setattr('native_ci.subprocess.check_output', lambda *a, **k: pytest.fail('Remote daemon must not be contacted'))
    with pytest.raises(RuntimeError, match='local Unix-socket'):
        ensure_local_docker()


def test_native_runner_rejects_context_override_of_local_socket(monkeypatch):
    monkeypatch.setattr('native_ci.shutil.which', lambda _: '/synthetic/docker')
    monkeypatch.setenv('DOCKER_HOST', 'unix:///synthetic/docker.sock')
    monkeypatch.setenv('DOCKER_CONTEXT', 'remote-context')
    monkeypatch.setattr('native_ci.subprocess.check_output', lambda *a, **k: pytest.fail('Must not contact daemon'))
    with pytest.raises(RuntimeError, match='Ambiguous'):
        ensure_local_docker()


@pytest.mark.parametrize('document,exit_code,expected,count', [
    ('null', 0, 'PASS', 0), ('[]', 0, 'PASS', 0),
    (None, 0, 'FAIL', None), ('[{}]', 1, 'FAIL', 1),
], ids=['go-nil-slice', 'empty-array', 'missing-report', 'findings'])
def test_scanner_empty_report_contract_and_missing_evidence(tmp_path, monkeypatch, document, exit_code, expected, count):
    import security_scan
    repository = tmp_path / 'source'
    raw = repository / 'build/evidence-raw'
    raw.mkdir(parents=True)
    (repository / 'README.md').write_text('synthetic source')
    executable = tmp_path / 'synthetic-scanner'
    program = '#!/usr/bin/env python3\nimport pathlib,sys\n'
    if document is not None:
        program += 'pathlib.Path(sys.argv[sys.argv.index("--report-path")+1]).write_text(' + repr(document) + ')\n'
    program += 'sys.exit(' + str(exit_code) + ')\n'
    executable.write_text(program)
    executable.chmod(0o700)
    monkeypatch.setattr(security_scan, 'ROOT', repository)
    result = security_scan.scan_secrets(str(executable), raw)
    assert result['status'] == expected
    assert result['phases'][0]['finding_count'] == count


def test_source_archive_excludes_sensitive_directories_and_is_repeatable(tmp_path):
    source = tmp_path / 'source'
    (source / '.git').mkdir(parents=True)
    (source / 'README.md').write_text('synthetic public documentation')
    (source / '.git/config').write_text('private original history')
    first, second = tmp_path / 'a.zip', tmp_path / 'b.zip'
    source_zip(source, first, '0.1.0a6')
    source_zip(source, second, '0.1.0a6')
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ['zabalack-0.1.0a6/README.md']


def test_sdist_rejects_paths_outside_archive_root(tmp_path):
    raw = tmp_path / 'raw.tar.gz'
    with tarfile.open(raw, 'w:gz') as archive:
        info = tarfile.TarInfo('../outside')
        info.size = 4
        archive.addfile(info, io.BytesIO(b'test'))
    with pytest.raises(ValueError, match='Unsafe'):
        normalise_sdist(raw, tmp_path / 'normalised.tar.gz')
