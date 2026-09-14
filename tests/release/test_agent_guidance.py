"""Guidance integrity, archive inclusion and separation from execution evidence."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from agent_guidance import CONTEXT, SKILLS, inspect
from build_release import source_zip
from evidence_utils import code_digests
from public_audit import evidence_binding_findings
from tests.release.mutation_fixtures import passing_mutation_record

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('script', ['collect_ci_evidence.py', 'security_scan.py'])
def test_ci_helpers_start_without_site_packages(script):
    result = subprocess.run(
        [sys.executable, '-E', '-S', str(ROOT / 'tools' / script), '--help'],
        cwd=ROOT, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout


def test_ci_preparation_runs_without_site_packages(tmp_path):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (evidence / 'reference-run.json').write_text('{"historical": true}')
    program = (
        'from pathlib import Path; import sys; '
        'from collect_ci_evidence import prepare; '
        'prepare(Path(sys.argv[1]), "quality", '
        '{"repository": "example/test", "run_id": "1", '
        '"run_attempt": "1", "sha": "0" * 40})'
    )
    result = subprocess.run(
        [sys.executable, '-E', '-S', '-c', program, str(tmp_path)],
        cwd=ROOT / 'tools', capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert not (evidence / 'reference-run.json').exists()
    record = json.loads((tmp_path / 'build/ci-preparation/quality.json').read_text())
    assert record['kind'] == 'quality'
    assert record['code_digests'] == code_digests(tmp_path)


@pytest.fixture
def guidance(tmp_path):
    for name in (*CONTEXT, *(f'.agents/skills/{s}/SKILL.md' for s in SKILLS)):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    return tmp_path


def test_guidance_hash_detects_prose_without_rebinding_code(guidance):
    before = inspect(guidance)
    code = code_digests(guidance)
    path = guidance / 'docs/agent-workflows.md'
    path.write_text(path.read_text() + '\nAdditional guidance.\n')
    assert inspect(guidance)['sha256'] != before['sha256']
    assert code_digests(guidance) == code


def test_source_zip_contains_exact_validated_guidance(guidance, tmp_path):
    manifest = inspect(guidance)
    output = tmp_path.parent / (tmp_path.name + '-source.zip')
    source_zip(guidance, output, '0.0.0a1')
    try:
        with zipfile.ZipFile(output) as archive:
            for row in manifest['files']:
                data = archive.read('zabalack-0.0.0a1/' + row['path'])
                assert hashlib.sha256(data).hexdigest() == row['sha256']
    finally:
        output.unlink()


@pytest.mark.parametrize('change', ['missing', 'symlink', 'name', 'reference', 'description'])
def test_guidance_validation_rejects_incomplete_or_misdirected_skill(guidance, change):
    path = guidance / '.agents/skills/zabalack-release-review/SKILL.md'
    if change == 'missing':
        path.unlink()
    elif change == 'symlink':
        path.unlink()
        path.symlink_to(guidance / 'AGENTS.md')
    else:
        text = path.read_text()
        text = text.replace('name: zabalack-release-review', 'name: unrelated') if change == 'name' else text
        text = text.replace('../../../docs/RELEASING.md', '../../../../outside.md') if change == 'reference' else text
        text = text.replace('description: Review', 'description: [Review') if change == 'description' else text
        path.write_text(text)
    with pytest.raises(ValueError):
        inspect(guidance)


@pytest.mark.parametrize('profile', ['pr', 'nightly'])
def test_profile_specific_evidence_is_validated_for_its_profile(tmp_path, profile):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    record = passing_mutation_record(tmp_path, profile=profile)
    path = evidence / f'mutation-{profile}-run.json'
    path.write_text(json.dumps(record))
    assert not [f for f in evidence_binding_findings(tmp_path) if f['path'] == f'evidence/{path.name}']
    record['profile'] = 'nightly' if profile == 'pr' else 'pr'
    path.write_text(json.dumps(record))
    assert any(f['path'] == f'evidence/{path.name}' and f['rule'] == 'mutation-gate-invalid'
               for f in evidence_binding_findings(tmp_path))


def test_export_audit_rejects_an_owner_only_exported_file(tmp_path):
    """Exported files must stay readable to every consumer of the archives."""
    from public_audit import export_mode_findings, public_files
    root = tmp_path / 'tree'
    (root / 'evidence').mkdir(parents=True)
    (root / 'evidence/reference-run.json').write_text('{}')
    (root / 'evidence/reference-run.json').chmod(0o644)
    (root / 'evidence/mutation-run.json').write_text('{}')
    (root / 'evidence/mutation-run.json').chmod(0o600)
    assert export_mode_findings(public_files(root), root) == [
        {'path': 'evidence/mutation-run.json', 'rule': 'public-file-not-world-readable'}]
    (root / 'evidence/mutation-run.json').chmod(0o644)
    assert export_mode_findings(public_files(root), root) == []
