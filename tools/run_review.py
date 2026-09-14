#!/usr/bin/env python3
"""Run selected local review phases serially, retaining each attempt privately."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from evidence_utils import code_digests
from review_runs import atomic_json, new_run, now, workspace_lock

ROOT = Path(__file__).resolve().parents[1]
PHASES = {
    'quality': ('tools/run_quality.py',),
    'sql': ('tools/run_sql_checks.py', '--isolated-lab'),
    'reference': ('tools/run_reference_suite.py', '--wasm'),
    'mutation-pr': ('tools/run_mutation_checks.py', '--profile', 'pr'),
    'mutation-nightly': ('tools/run_mutation_checks.py', '--profile', 'nightly'),
    'property-nightly': ('tools/run_property_nightly.py', '--isolated-lab'),
    'quickstart': ('tools/quickstart.py', '--isolated-lab'),
    'native': ('tools/native_ci.py', '--isolated-lab'),
    'native-local': ('tools/native_local.py', '--isolated-lab'),
    'real-zabbix': ('tools/record_real_zabbix.py', '--run'),
    'deployment-plan': ('tools/run_deployment_e2e.py', '--isolated-lab', '--plan'),
}
DEFAULT = ('quality', 'sql', 'reference', 'mutation-pr')
OPTIONAL = {'native', 'native-local', 'real-zabbix'}


def evidence_files(root: Path) -> dict[str, bytes]:
    directory = root / 'evidence'
    if directory.is_symlink():
        raise ValueError('review_evidence_symlink')
    rows = {}
    for path in sorted(directory.glob('*')):
        if path.suffix not in {'.json', '.xml'}:
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('review_evidence_invalid')
        rows[path.name] = path.read_bytes()
    return rows


def snapshot(root: Path, output: Path, before: dict[str, bytes]) -> list[dict]:
    rows = []
    for name, data in evidence_files(root).items():
        if before.get(name) == data:
            continue
        target = output / 'evidence' / name
        target.parent.mkdir(mode=0o700, exist_ok=True)
        target.write_bytes(data)
        rows.append({'path': 'evidence/' + name, 'sha256': hashlib.sha256(data).hexdigest()})
    return rows


def execute(command: list[str], root: Path, output: Path, timeout: int) -> dict:
    process = None
    start = time.monotonic()
    result = {'exit_code': None, 'interrupted': False, 'cleanup': 'CHILD_MANAGED'}
    with (output / 'output.log').open('x') as log:
        try:
            process = subprocess.Popen(command, cwd=root, env=dict(os.environ, TZ='UTC'),
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            result['exit_code'] = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            result.update(interrupted=True, cleanup='NOT_CONFIRMED_AFTER_INTERRUPTION')
        except OSError:
            result.update(reason='child_start_failed', cleanup='NOT_STARTED')
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    result['duration_ms'] = round((time.monotonic() - start) * 1000)
    return result


def run(root: Path, phases: list[str], *, run_id: str | None = None, timeout: int = 3600) -> int:
    if not phases or len(phases) != len(set(phases)) or any(p not in PHASES for p in phases):
        raise ValueError('review_phases_invalid')
    with workspace_lock(root):
        output = new_run(root, run_id)
        initial = code_digests(root)
        report = {'schema_version': 1, 'run_id': output.name, 'status': 'RUNNING',
                  'started_at_utc': now(), 'code_digests': initial, 'phases': [],
                  'scope': 'Local phase execution; per-phase evidence defines its coverage; G1-G8 pending'}
        atomic_json(output / 'run.json', report)
        failed = False
        try:
            for name in phases:
                row = {'name': name, 'status': 'NOT_RUN', 'command': ['python', *PHASES[name]]}
                report['phases'].append(row)
                if failed:
                    continue
                phase_dir = output / name
                phase_dir.mkdir(mode=0o700)
                before = evidence_files(root)
                row.update(status='RUNNING', started_at_utc=now(), code_digests=code_digests(root))
                atomic_json(output / 'run.json', report)
                row.update(execute([sys.executable, *PHASES[name]], root, phase_dir, timeout))
                row.update(finished_at_utc=now(), tree_unchanged=initial == code_digests(root))
                row['evidence'] = snapshot(root, phase_dir, before)
                if row['exit_code'] == 0 and not row['interrupted'] and row['tree_unchanged']:
                    row['status'] = 'PASS'
                elif row['exit_code'] == 2 and name in OPTIONAL and row['tree_unchanged']:
                    row['status'] = 'NOT_RUN'
                else:
                    row['status'] = 'INTERRUPTED' if row['interrupted'] else 'FAIL'
                failed = row['status'] != 'PASS'
                atomic_json(output / 'run.json', report)
                print(json.dumps({'run_id': output.name, 'phase': name, 'status': row['status']}), flush=True)
        except (OSError, ValueError, KeyboardInterrupt) as error:
            failed = True
            report['reason'] = type(error).__name__
            for row in report['phases']:
                if row['status'] == 'RUNNING':
                    row.update(status='INTERRUPTED', cleanup='NOT_CONFIRMED_AFTER_INTERRUPTION')
        finally:
            done = {row['name'] for row in report['phases']}
            report['phases'].extend({'name': name, 'status': 'NOT_RUN'} for name in phases if name not in done)
            report.update(status='INCOMPLETE' if failed else 'PASS', finished_at_utc=now(),
                          tree_unchanged=initial == code_digests(root))
            if not report['tree_unchanged']:
                report['status'] = 'FAIL'
            atomic_json(output / 'run.json', report)
        return 0 if report['status'] == 'PASS' else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', action='append', choices=PHASES)
    parser.add_argument('--run-id', help='Single-use label; default UTC timestamp plus random suffix')
    parser.add_argument('--timeout', type=int, default=3600, help='Per phase bound in seconds (1-7200)')
    parser.add_argument('--isolated-lab', action='store_true')
    parser.add_argument('--plan', action='store_true', help='Print commands without running or creating files')
    args = parser.parse_args()
    phases = args.phase or list(DEFAULT)
    if len(phases) != len(set(phases)) or not 1 <= args.timeout <= 7200:
        parser.error('phases must be unique and timeout must be 1-7200 seconds')
    if args.plan:
        print(json.dumps({'phases': [{'name': p, 'command': ['python', *PHASES[p]]} for p in phases]}, indent=2))
        return 0
    if not args.isolated_lab:
        parser.error('--isolated-lab is required for execution')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        return run(ROOT, phases, run_id=args.run_id, timeout=args.timeout)
    except (OSError, ValueError):
        print(json.dumps({'status': 'REFUSED', 'reason': 'Review path, lock or run identity unavailable'}))
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
