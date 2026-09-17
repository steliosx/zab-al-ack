#!/usr/bin/env python3
"""Collect fresh, source-bound reports from one GitHub Actions run and attempt.

The manifest is a transport-integrity check, not a signed attestation. Workflow
dependencies and the Actions artifact boundary establish which jobs produced it.
No network, publication, or operational-system access occurs in this tool.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
from uuid import uuid4
import xml.etree.ElementTree as ET

from evidence_utils import binding_error, cases, code_digests
from native_ci import PHASES as NATIVE_PHASES
from public_audit import inspect_file
from run_mutation_checks import validate_report as validate_mutation_report

ROOT = Path(__file__).resolve().parents[1]
KINDS = ('reference', 'native', 'quality', 'mutation')
REFERENCE_PHASES = {
    'unit-mocked': 'unit_mocked',
    'postgres-wasm': 'postgres_wasm',
    'worker-sql-wasm': 'postgres_wasm',
    'alerta-persistence-wasm': 'postgres_wasm',
    'performance-wasm': 'postgres_wasm',
    'optional-services': 'optional_native_real_zabbix',
}
CONTEXT_KEYS = {'repository', 'run_id', 'run_attempt', 'sha'}
MAX_FILE_BYTES = 20 * 1024 * 1024


class EvidenceError(ValueError):
    """Fixed, public-safe description of an invalid collection."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def _object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        require(key not in value, 'Duplicate JSON field')
        value[key] = item
    return value


def read_bytes(path: Path) -> bytes:
    require(path.is_file() and not path.is_symlink() and not any(parent.is_symlink() for parent in path.parents),
            'Evidence file missing or unsafe')
    require(path.stat().st_size <= MAX_FILE_BYTES, 'Evidence file exceeds size bound')
    return path.read_bytes()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(read_bytes(path), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvidenceError('Malformed JSON evidence') from error
    require(isinstance(value, dict), 'JSON evidence must be an object')
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def validate_context(context: object) -> dict:
    # Four-field historical/non-PR contexts remain valid. Exact context equality
    # still prevents them from satisfying a current PR run with PR metadata.
    require(isinstance(context, dict) and set(context) in (CONTEXT_KEYS, CONTEXT_KEYS | {'pull_request'}),
            'Invalid CI context fields')
    require(all(isinstance(context[key], str) for key in CONTEXT_KEYS), 'Invalid CI context types')
    require(bool(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', context['repository'])), 'Invalid repository context')
    require(all(bool(re.fullmatch(r'[1-9][0-9]*', context[key])) for key in ('run_id', 'run_attempt')), 'Invalid run context')
    require(bool(re.fullmatch(r'[0-9a-f]{40}', context['sha'])), 'Invalid commit context')
    if 'pull_request' in context:
        pull = context['pull_request']
        require(isinstance(pull, dict) and set(pull) == {'number', 'head_sha', 'base_sha'},
                'Invalid pull request context fields')
        require(type(pull['number']) is int and pull['number'] > 0, 'Invalid pull request number')
        require(all(isinstance(pull[key], str) and bool(re.fullmatch(r'[0-9a-f]{40}', pull[key]))
                    for key in ('head_sha', 'base_sha')), 'Invalid pull request commit context')
    return context


def pull_request_context(repository: str) -> dict:
    """Select only PR identities from the Actions webhook, never its raw body."""
    path = Path(os.environ.get('GITHUB_EVENT_PATH', ''))
    require(path.is_absolute(), 'Missing or invalid Actions event path')
    event = read_json(path)
    pull = event.get('pull_request')
    event_repository = event.get('repository')
    require(isinstance(pull, dict) and isinstance(event_repository, dict), 'Missing pull request event context')
    require(event_repository.get('full_name') == repository, 'Foreign pull request event repository')
    head, base = pull.get('head'), pull.get('base')
    require(isinstance(head, dict) and isinstance(base, dict) and isinstance(base.get('repo'), dict),
            'Missing pull request commit context')
    require(base['repo'].get('full_name') == repository, 'Foreign pull request base repository')
    number = event.get('number')
    require(type(number) is int and number > 0 and type(pull.get('number')) is int
            and pull['number'] == number, 'Invalid pull request event number')
    return {'number': number, 'head_sha': head.get('sha'), 'base_sha': base.get('sha')}


def environment_context(root: Path) -> dict:
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'This command requires GitHub Actions context')
    context = validate_context({key: os.environ.get('GITHUB_' + key.upper(), '') for key in CONTEXT_KEYS})
    event_name = os.environ.get('GITHUB_EVENT_NAME', '')
    require(bool(re.fullmatch(r'[a-z][a-z0-9_]*', event_name)), 'Missing or invalid Actions event name')
    if event_name in ('pull_request', 'pull_request_target'):
        context['pull_request'] = pull_request_context(context['repository'])
        validate_context(context)
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=10).strip()
    require(actual == context['sha'], 'Checkout commit does not match CI context')
    return context


def clear_evidence(root: Path) -> None:
    """Remove generated reports, including committed prior-run artifacts."""
    directory = root / 'evidence'
    require(not directory.is_symlink(), 'Unsafe evidence directory')
    readme = directory / 'README.md'
    contents = read_bytes(readme) if readme.exists() else None
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir()
    if contents is not None:
        readme.write_bytes(contents)


def artifact_name(kind: str, context: dict) -> str:
    return f'zabalack-evidence-{context["run_id"]}-{context["run_attempt"]}-{kind}'


def prepare(root: Path, kind: str, context: dict) -> None:
    require(kind in KINDS, 'Unknown CI evidence kind')
    validate_context(context)
    clear_evidence(root)
    output = root / 'build/ci-artifacts' / kind
    if output.exists():
        require(not output.is_symlink(), 'Unsafe artifact directory')
        shutil.rmtree(output)
    write_json(root / 'build/ci-preparation' / f'{kind}.json', {
        'schema_version': 1, 'kind': kind, 'context': context,
        'preparation_id': uuid4().hex, 'code_digests': code_digests(root),
    })


def safe_name(name: object) -> str:
    require(isinstance(name, str), 'Invalid evidence path')
    path = PurePosixPath(name)
    require(len(path.parts) == 2 and path.parts[0] == 'evidence'
            and path.as_posix() == name
            and bool(re.fullmatch(r'[a-z0-9][a-z0-9_-]*\.(?:json|xml)', path.name)),
            'Unsafe or noncanonical evidence path')
    return name


def bound_record(root: Path, directory: Path, filename: str) -> dict:
    record = read_json(directory / 'evidence' / filename)
    require(binding_error(record, root, filename) is None, 'Evidence is stale or from a foreign tree')
    require(record.get('tree_unchanged') is True, 'Runner did not verify an unchanged source and harness tree')
    return record


def counts(rows: list[dict]) -> dict:
    return {status: sum(row['status'] == status for row in rows) for status in ('passed', 'failed', 'skipped')}


def count_record(value: object, expected: dict) -> bool:
    return (isinstance(value, dict) and value == expected
            and all(type(item) is int for item in value.values()))


def junit_rows(directory: Path, name: str) -> list[dict]:
    data = read_bytes(directory / safe_name(name))
    require(b'<!DOCTYPE' not in data.upper() and b'<!ENTITY' not in data.upper(), 'Unsupported XML declaration')
    try:
        rows = cases(directory / name)
    except ET.ParseError as error:
        raise EvidenceError('Malformed JUnit evidence') from error
    require(bool(rows) and all(row['nodeid'] != '::' for row in rows), 'JUnit contains no identified test cases')
    require(len({row['nodeid'] for row in rows}) == len(rows), 'Duplicate JUnit test case')
    require(not any(row['status'] == 'failed' for row in rows), 'JUnit includes failed tests')
    return rows


def validate_reports(root: Path, directory: Path, kind: str) -> set[str]:
    """Return the exact report/XML allowlist after validating successful gates."""
    record = bound_record(root, directory, f'{kind}-run.json')
    selected = {f'evidence/{kind}-run.json'}
    if kind == 'mutation':
        try:
            validate_mutation_report(record, root, expected_profile=os.environ.get('ACK_HYPOTHESIS_PROFILE'))
        except ValueError as error:
            raise EvidenceError('Required mutation gate did not pass: ' + str(error)) from error
        return selected
    if kind == 'quality':
        checks = record.get('checks')
        require(record.get('status') == 'PASS' and isinstance(checks, list) and bool(checks), 'Quality gate did not pass')
        require(all(isinstance(check, dict) and isinstance(check.get('name'), str)
                    and check.get('status') == 'PASS' and type(check.get('exit_code')) is int
                    and check['exit_code'] == 0 for check in checks), 'Invalid quality check results')
        names = [check['name'] for check in checks]
        require(len(set(names)) == len(names) and {'ruff', 'mypy', 'pip-check'} <= set(names), 'Required quality checks missing')
        return selected
    phases = record.get('phases')
    expected = REFERENCE_PHASES if kind == 'reference' else NATIVE_PHASES
    require(isinstance(phases, list) and all(isinstance(phase, dict) and isinstance(phase.get('phase'), str)
                                            for phase in phases), 'Invalid phase schema')
    require(len(phases) == len(expected) and {phase.get('phase') for phase in phases} == set(expected), 'Required phase set missing or duplicated')
    if kind == 'native':
        require(record.get('status') == 'PASS', 'Required native gate did not pass')
    else:
        require(record.get('all_executed_phases_succeeded') is True, 'Required reference gate did not pass')
    observed = {}
    for phase in phases:
        name = phase['phase']
        filename = 'evidence/' + ('native-' if kind == 'native' else '') + name + '.xml'
        require(phase.get('junit') == filename, 'Phase JUnit path differs from the expected report')
        require(type(phase.get('exit_code')) is int and phase['exit_code'] == 0, 'Phase did not exit successfully')
        rows = junit_rows(directory, filename)
        if kind == 'native':
            require(phase.get('status') == 'PASS' and type(phase.get('shutdown_exit_code')) is int
                    and phase['shutdown_exit_code'] == 0 and not phase.get('shutdown_reason'), 'Native cleanup did not succeed')
            require(all(row['status'] == 'passed' for row in rows), 'Native gate includes skipped tests')
            require(count_record(phase.get('counts'), counts(rows)), 'Native counts disagree with JUnit')
        else:
            require(phase.get('layer') == expected[name], 'Incorrect reference evidence layer')
            require(name == 'optional-services' or any(row['status'] == 'passed' for row in rows), 'Required reference phase has no passing cases')
            for row in rows:
                key = (row['nodeid'], phase['layer'])
                previous = observed.get(key, 'passed')
                observed[key] = 'skipped' if 'skipped' in (previous, row['status']) else 'passed'
        selected.add(filename)
    if kind == 'reference':
        aggregate = counts([{'status': status} for status in observed.values()])
        require(count_record(record.get('counts'), aggregate), 'Reference counts disagree with JUnit')
        by_layer = {layer: counts([{'status': value} for (nodeid, found), value in observed.items() if found == layer])
                    for layer in {key[1] for key in observed}}
        require(isinstance(record.get('by_layer'), dict) and set(record['by_layer']) == set(by_layer)
                and all(count_record(record['by_layer'][layer], expected) for layer, expected in by_layer.items()),
                'Reference layer counts disagree with JUnit')
        sql = bound_record(root, directory, 'runtime_wasm_sql.json')
        results = sql.get('results')
        require(sql.get('status') == 'PASS' and type(sql.get('exit_code')) is int and sql['exit_code'] == 0
                and isinstance(results, list) and bool(results) and all(isinstance(item, dict)
                    and item.get('status') == 'PASS' for item in results), 'Supplemental SQL checks did not pass')
        selected.add('evidence/runtime_wasm_sql.json')
    return selected


def pack(root: Path, kind: str, context: dict) -> Path:
    require(kind in KINDS, 'Unknown CI evidence kind')
    validate_context(context)
    preparation = read_json(root / 'build/ci-preparation' / f'{kind}.json')
    validate_context(preparation.get('context'))
    require(preparation.get('context') == context and preparation.get('kind') == kind, 'CI preparation context mismatch')
    require(binding_error(preparation, root, 'Preparation') is None, 'Tree changed after CI preparation')
    names = validate_reports(root, root, kind)
    output = root / 'build/ci-artifacts' / kind
    require(not output.exists(), 'Artifact already packed; prepare and rerun the job')
    files = []
    for name in sorted(names):
        data = read_bytes(root / name)
        require(not inspect_file(name, data), 'Evidence export contains sensitive content')
        files.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    write_json(output / 'manifest.json', {'schema_version': 1, 'kind': kind, 'context': context,
        'code_digests': code_digests(root), 'preparation_id': preparation['preparation_id'], 'files': files})
    return output


def validate_artifact(root: Path, directory: Path, kind: str, context: dict) -> dict[str, bytes]:
    require(directory.is_dir() and not directory.is_symlink(), 'Required CI artifact missing or unsafe')
    manifest = read_json(directory / 'manifest.json')
    require(set(manifest) == {'schema_version', 'kind', 'context', 'code_digests', 'preparation_id', 'files'}
            and type(manifest.get('schema_version')) is int and manifest['schema_version'] == 1, 'Invalid artifact manifest schema')
    require(manifest['kind'] == kind and validate_context(manifest['context']) == context, 'Foreign CI run, attempt, repository, commit or kind')
    require(binding_error(manifest, root, 'Artifact') is None, 'Artifact is stale or from a foreign tree')
    require(isinstance(manifest['preparation_id'], str)
            and bool(re.fullmatch(r'[0-9a-f]{32}', manifest['preparation_id'])), 'Invalid preparation identity')
    require(isinstance(manifest['files'], list) and bool(manifest['files']), 'Empty artifact manifest')
    contents = {}
    for item in manifest['files']:
        require(isinstance(item, dict) and set(item) == {'path', 'sha256', 'size'}, 'Invalid artifact file schema')
        name = safe_name(item['path'])
        require(name not in contents, 'Duplicate artifact path')
        require(isinstance(item['sha256'], str) and bool(re.fullmatch(r'[0-9a-f]{64}', item['sha256']))
                and type(item['size']) is int and 0 <= item['size'] <= MAX_FILE_BYTES, 'Invalid artifact size or hash')
        data = read_bytes(directory / name)
        require(len(data) == item['size'] and hashlib.sha256(data).hexdigest() == item['sha256'], 'Artifact content hash mismatch')
        require(not inspect_file(name, data), 'Evidence export contains sensitive content')
        contents[name] = data
    actual = set()
    for path in directory.rglob('*'):
        require(not path.is_symlink(), 'Symlink inside artifact')
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    require(actual == set(contents) | {'manifest.json'}, 'Unmanifested or missing artifact file')
    require(set(contents) == validate_reports(root, directory, kind), 'Unexpected or missing report in artifact')
    return contents


def collect(root: Path, source: Path, context: dict) -> None:
    validate_context(context)
    # Invalidate exported prior-run evidence before inspecting untrusted inputs.
    clear_evidence(root)
    require(source.is_dir() and not source.is_symlink(), 'Artifact input directory missing or unsafe')
    expected = {artifact_name(kind, context) for kind in KINDS}
    require({path.name for path in source.iterdir()} == expected, 'Required artifact set missing or contains foreign artifacts')
    contents = {}
    for kind in KINDS:
        selected = validate_artifact(root, source / artifact_name(kind, context), kind, context)
        require(not (set(contents) & set(selected)), 'Artifact report ownership collision')
        contents.update(selected)
    for name, data in contents.items():
        (root / name).write_bytes(data)
    reference = read_json(root / 'evidence/reference-run.json')
    write_json(root / 'evidence/test-summary.json', {key: reference[key] for key in
        ('counts', 'by_layer', 'native_postgres', 'pgbouncer', 'real_zabbix', 'deployment_gates')})
    write_json(root / 'evidence/real-zabbix-run.json', {
        'status': 'NOT_RUN', 'reason': 'Real Zabbix is not a service in this CI workflow',
        'junit': None, 'counts': {'passed': 0, 'failed': 0, 'skipped': 0},
        'code_digests': code_digests(root), 'deployment_gates': 'MANUAL_GATE_PENDING',
    })
    write_json(root / 'evidence/ci-collection.json', {
        'schema_version': 1, 'status': 'PASS', 'context': context,
        'code_digests': code_digests(root), 'kinds': list(KINDS),
        'files': [{'path': name, 'sha256': hashlib.sha256(data).hexdigest()} for name, data in sorted(contents.items())],
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'deployment_gates': 'MANUAL_GATE_PENDING', 'signed_attestation': False,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'pack'):
        subcommands.add_parser(name).add_argument('--kind', choices=KINDS, required=True)
    subcommands.add_parser('collect').add_argument('--input', type=Path, required=True)
    args = parser.parse_args()
    try:
        context = environment_context(ROOT)
        if args.command == 'prepare':
            prepare(ROOT, args.kind, context)
        elif args.command == 'pack':
            pack(ROOT, args.kind, context)
        else:
            collect(ROOT, args.input, context)
    except (EvidenceError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'FAIL', 'reason': str(error) if isinstance(error, EvidenceError) else type(error).__name__}))
        return 1
    print(json.dumps({'status': 'PASS', 'operation': args.command}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
