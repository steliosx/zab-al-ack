"""A stale or incomplete SQL identity manifest must stop release construction."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import public_audit
import build_release


def write_manifest(root):
    entries = [{'path': path.relative_to(root).as_posix(),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in sorted((root / 'sql').rglob('*'))
               if path.is_file() and path.suffix in {'.sql', '.py'}]
    manifest = {'schema_version': 17, 'files': entries}
    (root / 'docs').mkdir(exist_ok=True)
    (root / 'docs/sql_manifest.json').write_text(json.dumps(manifest))
    return manifest


@pytest.fixture
def sql_tree(tmp_path):
    (tmp_path / 'sql/migrations').mkdir(parents=True)
    (tmp_path / 'sql/migrations/001_test.sql').write_text('SELECT 1;\n')
    (tmp_path / 'sql/migrate.py').write_text('# synthetic migration runner\n')
    (tmp_path / 'sql/README.md').write_text('Public documentation is not executable SQL.')
    write_manifest(tmp_path)
    return tmp_path


def test_sql_manifest_covers_current_sql_and_runner_without_fixed_schema_version(sql_tree):
    assert public_audit.sql_manifest_findings(sql_tree) == []


@pytest.mark.parametrize('problem,rule', [
    ('changed-bytes', 'sql-manifest-hash-mismatch'),
    ('missing-entry', 'sql-manifest-file-missing'),
    ('new-sql', 'sql-manifest-file-missing'),
    ('deleted-file', 'sql-manifest-path-invalid'),
    ('duplicate', 'sql-manifest-duplicate-path'),
    ('traversal', 'sql-manifest-path-invalid'),
    ('absolute', 'sql-manifest-path-invalid'),
    ('digest', 'sql-manifest-digest-invalid'),
    ('bad-entry', 'sql-manifest-entry-invalid'),
    ('bad-shape', 'sql-manifest-invalid'),
    ('bad-json', 'sql-manifest-invalid'),
    ('missing-manifest', 'sql-manifest-invalid'),
])
def test_sql_manifest_rejects_drift_and_invalid_inventory(sql_tree, problem, rule):
    path = sql_tree / 'docs/sql_manifest.json'
    manifest = json.loads(path.read_text())
    if problem == 'changed-bytes':
        (sql_tree / 'sql/migrate.py').write_text('# renamed implementation\n')
    elif problem == 'missing-entry':
        manifest['files'].pop()
    elif problem == 'new-sql':
        (sql_tree / 'sql/migrations/002_new.sql').write_text('SELECT 2;\n')
    elif problem == 'deleted-file':
        (sql_tree / manifest['files'][0]['path']).unlink()
    elif problem == 'duplicate':
        manifest['files'].append(manifest['files'][0].copy())
    elif problem in {'traversal', 'absolute'}:
        manifest['files'][0]['path'] = '../outside.sql' if problem == 'traversal' else '/outside.sql'
    elif problem == 'digest':
        manifest['files'][0]['sha256'] = 'invalid'
    elif problem == 'bad-entry':
        manifest['files'][0] = 'invalid'
    elif problem == 'bad-shape':
        manifest['files'] = 'invalid'
    path.write_text(json.dumps(manifest))
    if problem == 'bad-json':
        path.write_text('{')
    elif problem == 'missing-manifest':
        path.unlink()
    assert rule in {item['rule'] for item in public_audit.sql_manifest_findings(sql_tree)}


def minimal_audit_tree(tmp_path):
    # Minimal real audit inputs, copied without source execution or dependencies.
    for relative in public_audit.REQUIRED | {'pyproject.toml', 'docs/reference_manifest.json',
                                          'lab/upstream/alerta-9.1.0-schema.sql'}:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    (tmp_path / 'sql').mkdir()
    script = tmp_path / 'sql/migrate.py'
    script.write_text('# before rename\n')
    write_manifest(tmp_path)
    from evidence_utils import code_digests
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'evidence/reference-run.json').write_text(json.dumps({'phases': [], 'all_executed_phases_succeeded': True, 'code_digests': code_digests(tmp_path)}))
    (tmp_path / 'evidence/acceptance-results.json').write_text(json.dumps({'code_digests': code_digests(tmp_path)}))
    (tmp_path / 'evidence/quality-run.json').write_text(json.dumps({'status': 'PASS', 'code_digests': code_digests(tmp_path)}))
    from mutation_fixtures import passing_mutation_record
    (tmp_path / 'evidence/mutation-run.json').write_text(json.dumps(passing_mutation_record(tmp_path)))
    assert public_audit.audit(tmp_path)['status'] == 'PASS'
    return script


def test_release_builder_refuses_sql_manifest_drift_before_building(tmp_path, monkeypatch):
    script = minimal_audit_tree(tmp_path)
    # SQL changes invalidate both the SQL identity and the run/matrix bindings.
    script.write_text('# after rename\n')
    assert public_audit.audit(tmp_path)['findings'] == [
        {'path': 'sql/migrate.py', 'rule': 'sql-manifest-hash-mismatch'},
        {'path': 'evidence/reference-run.json', 'rule': 'evidence-not-bound-to-tree'},
        {'path': 'evidence/acceptance-results.json', 'rule': 'evidence-not-bound-to-tree'},
        {'path': 'evidence/quality-run.json', 'rule': 'evidence-not-bound-to-tree'},
        {'path': 'evidence/mutation-run.json', 'rule': 'evidence-not-bound-to-tree'},
        {'path': 'evidence/mutation-run.json', 'rule': 'mutation-gate-invalid'}]
    monkeypatch.setattr(build_release, 'ROOT', tmp_path)
    monkeypatch.setattr(build_release.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('Stale SQL manifest reached the package builder'))
    assert build_release.main() == 1
    assert not (tmp_path / 'dist').exists()


def test_release_builder_refuses_foreign_acceptance_report_before_building(tmp_path, monkeypatch):
    minimal_audit_tree(tmp_path)
    (tmp_path / 'evidence/acceptance-results.json').write_text(json.dumps({
        'code_digests': {'source': '0' * 64, 'harness': '0' * 64}}))
    assert public_audit.audit(tmp_path)['findings'] == [
        {'path': 'evidence/acceptance-results.json', 'rule': 'evidence-not-bound-to-tree'}]
    monkeypatch.setattr(build_release, 'ROOT', tmp_path)
    monkeypatch.setattr(build_release.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('Foreign acceptance evidence reached the package builder'))
    assert build_release.main() == 1
    assert not (tmp_path / 'dist').exists()
