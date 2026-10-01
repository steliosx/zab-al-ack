#!/usr/bin/env python3
"""Run the pinned lint/type gates and record fresh source-bound results."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

from evidence_utils import code_digests

ROOT = Path(__file__).resolve().parents[1]
# The Docker-free lab and runner infrastructure (R26–R29): an explicit, bounded
# list, not a repository-wide lint of tools/ and lab/. Every file must exist.
INFRASTRUCTURE = (
    'lab/deployment_native_lab.py', 'lab/local_runtime.py', 'lab/real_zabbix_prepare.py', 'lab/zabbix_native_lab.py',
    'lab/zabbix_reference.py', 'tools/cancellation.py', 'tools/collect_package_identities.py', 'tools/deployment_evidence.py', 'tools/evidence_bundle.py',
    'tools/lab_retention.py', 'tools/native_local.py', 'tools/record_real_zabbix.py', 'tools/run_deployment_e2e_local.py',
    'tools/runner_account_check.py', 'tools/runner_admission.py', 'tools/self_hosted_job.py', 'tools/supplied_wheel.py',
)
CHECKS = {
    'ruff': ['-m', 'ruff', 'check', 'src'],
    'ruff-infrastructure': ['-m', 'ruff', 'check', *INFRASTRUCTURE],
    'mypy': ['-m', 'mypy', '--config-file', 'mypy.ini'],
    'pip-check': ['-m', 'pip', 'check'],
}


def main() -> int:
    output = ROOT / 'evidence/quality-run.json'
    output.parent.mkdir(exist_ok=True)
    raw = ROOT / 'build/quality'
    raw.mkdir(parents=True, exist_ok=True)
    report = {'status': 'RUNNING', 'checks': [], 'code_digests': code_digests(ROOT),
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Static checks and installed dependency consistency; no service or deployment evidence'}
    output.write_text(json.dumps(report, indent=2) + '\n')
    try:
        pins = dict(line.split('==', 1) for line in (ROOT / 'requirements-quality.lock').read_text().splitlines()
                    if line and not line.startswith(('#', ' ', '-')) and '==' in line)
        report['tools'] = {name: importlib.metadata.version(name) for name in ('ruff', 'mypy', 'hypothesis')}
        if any(report['tools'][name] != pins[name].split()[0] for name in report['tools']):
            raise ValueError('quality_tool_version_mismatch')
        if not all((ROOT / name).is_file() for name in INFRASTRUCTURE):
            raise ValueError('infrastructure_check_file_missing')
        env = dict(os.environ, TZ='UTC', MYPY_CACHE_DIR=str(raw / 'mypy-cache'),
                   RUFF_CACHE_DIR=str(raw / 'ruff-cache'), PIP_DISABLE_PIP_VERSION_CHECK='1')
        for name, arguments in CHECKS.items():
            with (raw / (name + '.log')).open('w') as log:
                code = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=env,
                                      stdout=log, stderr=subprocess.STDOUT, timeout=180).returncode
            check = {'name': name, 'exit_code': code, 'status': 'PASS' if code == 0 else 'FAIL'}
            report['checks'].append(check)
            print(json.dumps(check), flush=True)
        report['tree_unchanged'] = report['code_digests'] == code_digests(ROOT)
        report['status'] = 'PASS' if report['tree_unchanged'] and all(
            check['status'] == 'PASS' for check in report['checks']) else 'FAIL'
    except (OSError, ValueError, KeyError, importlib.metadata.PackageNotFoundError,
            subprocess.SubprocessError, KeyboardInterrupt) as error:
        report['status'] = 'FAIL'
        report['reason'] = type(error).__name__
    finally:
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
