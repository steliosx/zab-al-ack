#!/usr/bin/env python3
"""Explicit public advisory lookups and redacted scans of the public source/history."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from public_audit import public_files

ROOT = Path(__file__).resolve().parents[1]


def run_audit(executable: str, raw_dir: Path) -> dict:
    output = ROOT / 'evidence/pip-audit.json'
    output.unlink(missing_ok=True)
    locks = [ROOT / 'requirements.lock']
    if (ROOT / 'requirements-quality.lock').is_file():
        locks.append(ROOT / 'requirements-quality.lock')
    # Advisory queries consume names and versions, never install requirements.
    # Mixing hashed and unhashed input files activates pip-audit's all-hashed
    # parser mode; create one exact-pin query list instead. Hashes remain
    # mandatory for the separate quality-tool installation.
    pins = {}
    for lock in locks:
        for line in lock.read_text().splitlines():
            if not line.strip() or line.startswith(('#', ' ', '-')):
                continue
            requirement = line.split()[0]
            name, version = requirement.split('==', 1)
            name = name.lower().replace('_', '-')
            if name in pins and pins[name] != version:
                raise ValueError('Conflicting advisory dependency pins')
            pins[name] = version
    query = raw_dir / 'advisory-requirements.txt'
    query.write_text(''.join(name + '==' + version + '\n' for name, version in sorted(pins.items())))
    command = [executable, '-r', str(query), '--no-deps', '--disable-pip', '--strict',
               '--progress-spinner', 'off', '--timeout', '30', '--desc', 'off', '-f', 'json', '-o', str(output)]
    try:
        with (raw_dir / 'pip-audit.log').open('w') as log:
            code = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=180).returncode
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {'status': 'NOT_RUN', 'reason': 'pip-audit unavailable or lookup did not complete'}
    if not output.exists():
        return {'status': 'ERROR', 'exit_code': code, 'reason': 'No complete advisory result'}
    dependencies = json.loads(output.read_text()).get('dependencies', [])
    skipped = [item['name'] for item in dependencies if item.get('skip_reason')]
    count = sum(len(item.get('vulns', [])) for item in dependencies)
    expected = len(pins)
    return {'status': 'PASS' if code == 0 and not count and not skipped and len(dependencies) == expected else 'FAIL',
            'exit_code': code, 'package_count': len(dependencies), 'advisory_count': count,
            'skipped_packages': skipped, 'scope': 'Known Python advisories for exact lock; no source-code audit'}


def scan_secrets(executable: str, raw_dir: Path) -> dict:
    if not shutil.which(executable):
        return {'status': 'NOT_RUN', 'reason': 'Gitleaks unavailable; targeted export checks are separate'}
    results = []
    # The scanned copy of the public selection is kept for retained evidence, not removed.
    selected = Path(tempfile.mkdtemp(prefix='public-source-', dir=ROOT / 'build'))
    for source in public_files(ROOT):
        if source.is_symlink():
            raise ValueError('Symlink in public selection')
        destination = selected / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    modes = [('dir', selected)]
    if (ROOT / '.git').is_dir():
        modes.append(('git', ROOT))
    for mode, target in modes:
        output = raw_dir / ('gitleaks-' + mode + '.json')
        output.unlink(missing_ok=True)
        command = [executable, mode, str(target), '--redact', '--no-banner',
                   '--report-format', 'json', '--report-path', str(output)]
        if mode == 'git':
            command += ['--log-opts=--all']
        try:
            with (raw_dir / ('gitleaks-' + mode + '.log')).open('w') as log:
                code = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=180).returncode
        except subprocess.TimeoutExpired:
            results.append({'mode': mode, 'status': 'NOT_RUN', 'reason': 'Scanner did not complete'})
            continue
        has_report = output.exists()
        findings = json.loads(output.read_text()) if has_report else None
        # Pinned Go JsonReporter encodes a nil []Finding as JSON null.
        # A missing file is distinct from that valid completed empty report.
        empty = has_report and (findings is None or findings == [])
        results.append({'mode': mode, 'status': 'PASS' if code == 0 and empty else 'FAIL',
                        'exit_code': code, 'finding_count': len(findings or []) if has_report else None})
    return {'status': 'PASS' if results and all(r['status'] == 'PASS' for r in results) else 'FAIL',
            'phases': results, 'history_scanned': any(r['mode'] == 'git' for r in results),
            'scope': 'Public source selection and, when present, this repository history; raw findings remain local'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-advisory-lookups', action='store_true', required=True)
    parser.add_argument('--pip-audit', default='pip-audit')
    parser.add_argument('--gitleaks', default='gitleaks')
    args = parser.parse_args()
    raw = ROOT / 'build/evidence-raw'
    raw.mkdir(parents=True, exist_ok=True)
    report = {'pip_audit': run_audit(args.pip_audit, raw), 'gitleaks': scan_secrets(args.gitleaks, raw),
              'independent_external_security_review': 'PENDING', 'deployment_gates': 'MANUAL_GATE_PENDING'}
    (ROOT / 'evidence/security-scans.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    values = [report[name]['status'] for name in ('pip_audit', 'gitleaks')]
    return 0 if values == ['PASS', 'PASS'] else (2 if all(v in {'PASS', 'NOT_RUN'} for v in values) else 1)


if __name__ == '__main__':
    raise SystemExit(main())
