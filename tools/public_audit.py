#!/usr/bin/env python3
"""Targeted public-export checks; no network access and no publication side effects."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_utils import binding_error  # noqa: E402
from deployment_evidence import read_record as read_deployment_record, validate_report as validate_deployment_report  # noqa: E402
from run_mutation_checks import validate_report as validate_mutation_report  # noqa: E402
from agent_guidance import CONTEXT, SKILLS, inspect as inspect_guidance  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'.git', '.venv', 'build', 'dist', 'node_modules', '__pycache__', '.pytest_cache', '.hypothesis', '.lab'}
# Outer handoff metadata is generated after the source archive and bundle.
# Including it when rebuilding an unpacked release would embed stale hashes
# and commit IDs (and introduce self-referential checksum dependencies).
HANDOFF_FILES = {'RELEASE_MANIFEST.json', 'RELEASE_SHA256SUMS', 'START_HERE.md'}
FORBIDDEN_NAMES = {'IMPLEMENTATION_PACKET.md', 'BASELINE_REV_1_2.md', 'DELIVERY_REPORT.md'}
# The README hero illustrations (owner-added 2026-10-01) are the only binary files the public export
# may carry: exact paths, a PNG signature and a size ceiling in bytes. Any other binary is a finding.
PUBLIC_BINARY_FILES = {'docs/assets/readme-hero.png': 2 * 1024 * 1024,
                       'docs/assets/readme-hero-dark.png': 2 * 1024 * 1024}
REQUIRED = {'LICENSE', 'NOTICE', 'THIRD_PARTY.md', 'README.md', 'SECURITY.md', 'CONTRIBUTING.md',
            'GOVERNANCE.md', 'CODE_OF_CONDUCT.md', 'CHANGELOG.md', 'docs/DESIGN.md',
            'config/production.example.json', '.github/workflows/ci.yml'}
REQUIRED |= set(CONTEXT) | {f'.agents/skills/{name}/SKILL.md' for name in SKILLS}


def sql_manifest_findings(root: Path) -> list[dict]:
    """Require exact coverage and byte hashes for shipped SQL and its runners."""
    name = 'docs/sql_manifest.json'
    findings = []
    try:
        manifest = json.loads((root / name).read_text())
        if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), list):
            raise ValueError('Invalid SQL manifest shape')
    except (OSError, ValueError):
        return [{'path': name, 'rule': 'sql-manifest-invalid'}]
    expected = {path.relative_to(root).as_posix(): path for path in public_files(root)
                if path.relative_to(root).parts[0] == 'sql' and path.suffix in {'.sql', '.py'}}
    seen = set()
    for item in manifest['files']:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str):
            findings.append({'path': name, 'rule': 'sql-manifest-entry-invalid'})
            continue
        path = item['path']
        # Exact membership excludes absolute, traversal, noncanonical, missing
        # and unrelated paths before any path supplied by the manifest is read.
        if path not in expected:
            findings.append({'path': name, 'rule': 'sql-manifest-path-invalid'})
            continue
        if path in seen:
            findings.append({'path': path, 'rule': 'sql-manifest-duplicate-path'})
        seen.add(path)
        digest = item.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            findings.append({'path': path, 'rule': 'sql-manifest-digest-invalid'})
        elif expected[path].is_symlink():
            findings.append({'path': path, 'rule': 'sql-manifest-symlink'})
        elif hashlib.sha256(expected[path].read_bytes()).hexdigest() != digest:
            findings.append({'path': path, 'rule': 'sql-manifest-hash-mismatch'})
    findings.extend({'path': path, 'rule': 'sql-manifest-file-missing'} for path in sorted(expected.keys() - seen))
    return findings


def evidence_binding_findings(root: Path) -> list[dict]:
    """Executed-run records in the export must be final and bound to this exact tree."""
    findings = []
    for name, label, optional in (('evidence/reference-run.json', 'Reference', False),
                                  ('evidence/native-run.json', 'Native', True),
                                  ('evidence/real-zabbix-run.json', 'Real-Zabbix', True),
                                  ('evidence/acceptance-results.json', 'Acceptance', False),
                                  ('evidence/quality-run.json', 'Quality', False),
                                  ('evidence/mutation-run.json', 'Mutation', False),
                                  ('evidence/mutation-pr-run.json', 'Mutation PR', True),
                                  ('evidence/mutation-nightly-run.json', 'Mutation nightly', True),
                                  ('evidence/deployment-e2e-run.json', 'Deployment E2E', True),
                                  ('evidence/property-nightly-run.json', 'Generated nightly', True)):
        path = root / name
        if not path.is_file():
            if not optional:
                findings.append({'path': name, 'rule': 'evidence-record-missing'})
            continue
        try:
            record = read_deployment_record(path.read_bytes()) if label == 'Deployment E2E' else json.loads(path.read_text())
        except (OSError, ValueError):
            findings.append({'path': name, 'rule': 'evidence-record-invalid'})
            continue
        if not isinstance(record, dict):
            findings.append({'path': name, 'rule': 'evidence-record-invalid'})
            continue
        if label == 'Deployment E2E':
            try:
                validate_deployment_report(record, root)
            except ValueError:
                findings.append({'path': name, 'rule': 'deployment-evidence-invalid'})
            continue
        if label == 'Reference' and record.get('all_executed_phases_succeeded') is not True:
            findings.append({'path': name, 'rule': 'evidence-record-not-final'})
        if optional and not label.startswith('Mutation') and record.get('status') == 'NOT_RUN':
            continue
        if (optional or label in {'Quality', 'Mutation'}) and record.get('status') != 'PASS':
            findings.append({'path': name, 'rule': 'evidence-record-not-final'})
        if binding_error(record, root, label):
            findings.append({'path': name, 'rule': 'evidence-not-bound-to-tree'})
        if label.startswith('Mutation'):
            try:
                expected_profile = {'Mutation PR': 'pr', 'Mutation nightly': 'nightly'}.get(label)
                validate_mutation_report(record, root, expected_profile=expected_profile)
            except ValueError:
                findings.append({'path': name, 'rule': 'mutation-gate-invalid'})
    return findings


def export_mode_findings(files: list[Path], root: Path) -> list[dict]:
    """An exported file must stay readable to every consumer of the archives.

    An atomic writer that keeps mkstemp's 0600 temporary mode silently ships an
    owner-only record: readable to whoever produced it, unreadable to a CI
    collector, a packaging step or anyone verifying the extracted handoff.
    """
    return [{'path': str(path.relative_to(root)), 'rule': 'public-file-not-world-readable'}
            for path in files if path.stat().st_mode & 0o044 != 0o044]


def public_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob('*') if p.is_file() and not any(
        part in EXCLUDED or part.endswith('.egg-info') for part in p.relative_to(root).parts)
        and p.relative_to(root).as_posix() not in HANDOFF_FILES
        and p.suffix not in {'.pyc', '.pyo', '.log'} and p.name != '.DS_Store')


def inspect_file(name: str, data: bytes) -> list[str]:
    findings = []
    path = Path(name)
    if path.name in FORBIDDEN_NAMES or path.suffix in {'.bundle', '.key', '.pem'} or path.name == '.env':
        findings.append('private-or-secret-file-type')
    try:
        content = data.decode('utf-8')
    except UnicodeDecodeError:
        limit = PUBLIC_BINARY_FILES.get(name)
        if limit is not None and data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) <= limit:
            return findings
        return findings + ['unexpected-binary-file']
    patterns = {
        'private-key-material': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        'github-token': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b',
        'aws-access-key': r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    }
    for label, expression in patterns.items():
        if re.search(expression, content):
            findings.append(label)
    if name.startswith('evidence/'):
        if re.search(r'/(?:workspace|root)/[^\s]+', content):
            findings.append('private-path-in-evidence')
        if re.search(r'\[(?:zabalack|ackline):v1 action=[0-9a-f-]{36} kid=[^ ]+ mac=[0-9a-f]{64}\]', content, re.I):
            findings.append('signing-proof-in-evidence')
    return findings


def audit(root: Path) -> dict:
    findings = sql_manifest_findings(root) + evidence_binding_findings(root)
    try:
        inspect_guidance(root)
    except (OSError, UnicodeError, ValueError):
        findings.append({'path': '.agents/skills', 'rule': 'agent-guidance-invalid'})
    files = public_files(root)
    findings.extend(export_mode_findings(files, root))
    names = {str(path.relative_to(root)) for path in files}
    for missing in sorted(REQUIRED - names):
        findings.append({'path': missing, 'rule': 'required-file-missing'})
    for path in files:
        name = str(path.relative_to(root))
        if path.is_symlink():
            findings.append({'path': name, 'rule': 'symlink-in-public-source'})
        findings.extend({'path': name, 'rule': rule} for rule in inspect_file(name, path.read_bytes()))
    production = json.loads((root / 'config/production.example.json').read_text())
    if production.get('enabled') is not False or production.get('external_writes_enabled') is not False:
        findings.append({'path': 'config/production.example.json', 'rule': 'enabled-production-example'})
    if production.get('cohorts') or production.get('endpoints'):
        findings.append({'path': 'config/production.example.json', 'rule': 'active-production-targets'})
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    if project['license'] != 'Apache-2.0' or project['version'] != '0.1.0b1':
        findings.append({'path': 'pyproject.toml', 'rule': 'alpha-metadata-mismatch'})
    # Validate vendored Alerta schema against the inspected immutable manifest.
    manifest = json.loads((root / 'docs/reference_manifest.json').read_text())
    source = next(item for item in manifest['sources'] if item['component'] == 'alerta')
    expected = next(item['sha256'] for item in source['files'] if item['path'] == 'alerta/sql/schema.sql')
    schema = root / 'lab/upstream/alerta-9.1.0-schema.sql'
    if hashlib.sha256(schema.read_bytes()).hexdigest() != expected:
        findings.append({'path': str(schema.relative_to(root)), 'rule': 'vendored-source-hash-mismatch'})
    return {'status': 'PASS' if not findings else 'FAIL', 'file_count': len(files), 'findings': findings,
        'scope': 'Targeted file, metadata, inert-default and common-secret checks; not an exhaustive secret or legal audit',
        'publication_authorized': False, 'deployment_gates': 'MANUAL_GATE_PENDING'}


def publication_inputs(root: Path) -> list[str]:
    data = json.loads((root / 'config/publication.pending.json').read_text())
    missing = [key for key in ('repository_url', 'package_owner', 'primary_maintainer',
        'backup_maintainer', 'private_conduct_contact') if not data.get(key)]
    missing += [key for key in ('private_security_reporting_verified', 'copyright_and_third_party_review_approved',
        'public_content_review_approved', 'pypi_namespace_confirmed', 'publication_authorized') if data.get(key) is not True]
    if data.get('repository_url') and not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', data['repository_url']):
        missing.append('valid_github_repository_url')
    if not missing:
        project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
        if project.get('urls', {}).get('Repository') != data['repository_url']:
            missing.append('pyproject_repository_url')
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication', action='store_true', help='Also require assigned owners and explicit publication authorization')
    parser.add_argument('--write-report', action='store_true')
    args = parser.parse_args()
    report = audit(ROOT)
    if args.publication:
        report['publication_inputs_required'] = publication_inputs(ROOT)
    if args.write_report:
        (ROOT / 'evidence/public-audit.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if report['status'] != 'PASS':
        return 1
    return 2 if report.get('publication_inputs_required') else 0


if __name__ == '__main__':
    raise SystemExit(main())
