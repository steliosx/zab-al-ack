#!/usr/bin/env python3
"""Execute fresh, explicitly labelled reference phases; never publish raw logs."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from evidence_utils import redact_junit, cases, code_digests

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wasm', action='store_true')
    args = parser.parse_args()
    phases = [('unit-mocked', 'unit_mocked', [sys.executable, '-m', 'pytest',
        'tests/unit', 'tests/rpc', 'tests/security', 'tests/release', 'tests/property', 'tests/native_support',
        'tests/alerta/test_http_mocked_storage.py', 'tests/alerta/test_reader_confidentiality.py', '-q'])]
    if args.wasm:
        for name, targets in [
            ('postgres-wasm', ['tests/postgres/test_postgres.py', 'tests/postgres/test_late_response.py', 'tests/postgres/test_stateful_ledger.py', 'tests/postgres/test_settled_expiry.py', 'tests/postgres/test_intent_retirement.py']),
            ('worker-sql-wasm', ['tests/postgres/test_worker_path.py']),
            ('alerta-persistence-wasm', ['tests/alerta/test_postgres_http.py', 'tests/alerta/test_health_postgres.py', 'tests/alerta/test_docs_walkthrough.py', 'tests/alerta/test_postgres_search_confidentiality.py']),
            ('performance-wasm', ['tests/performance/test_reference_batches.py'])]:
            phases.append((name, 'postgres_wasm', [sys.executable, 'lab/pglite_exec.py',
                '--isolated-lab', '--', sys.executable, '-m', 'pytest', *targets, '-q']))
    phases.append(('optional-services', 'optional_native_real_zabbix', [sys.executable,
        '-m', 'pytest', 'tests/real_zabbix', 'tests/deployment_e2e', 'tests/postgres/test_native_login.py', 'tests/postgres/test_native_delivery_faults.py', 'tests/alerta/test_native_staging_interval.py', 'tests/postgres/test_native_generation_race.py', '-q']))
    evidence = ROOT / 'evidence'
    raw_dir = ROOT / 'build/evidence-raw'
    evidence.mkdir(exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # No inherited native/live fixture: use the deliberately selected native or
    # real-Zabbix command for those services, never this reference runner.
    for key in list(env):
        if key.startswith('ACK_TEST_') or key == 'ACK_DEPLOYMENT_E2E':
            del env[key]
    # Evidence timestamps are rendered in UTC; a local zone offset would identify the lab host.
    env['TZ'] = 'UTC'
    env['HYPOTHESIS_STORAGE_DIRECTORY'] = str(ROOT / 'build/private/hypothesis')
    env['ACK_HYPOTHESIS_PROFILE'] = os.environ.get('ACK_HYPOTHESIS_PROFILE', 'pr')
    # Bind the record to the tree before anything runs; a harness edit during
    # the run leaves the record pointing at the pre-run tree and mapping refuses it.
    digests = code_digests(ROOT)
    (evidence / 'reference-run.json').write_text(json.dumps({'status': 'RUNNING',
        'phases': [], 'code_digests': digests, 'all_executed_phases_succeeded': False}, indent=2) + '\n')
    results = []
    observed = {}
    priority = {'passed': 0, 'skipped': 1, 'failed': 2}
    for name, layer, command in phases:
        raw_xml = raw_dir / (name + '.xml')
        public_xml = evidence / (name + '.xml')
        # A timed-out/crashed child may produce no JUnit. Never reuse a previous
        # invocation's passing report or advertise it as fresh execution.
        raw_xml.unlink(missing_ok=True)
        public_xml.unlink(missing_ok=True)
        command.append('--junitxml=' + str(raw_xml.relative_to(ROOT)))
        started = datetime.now(timezone.utc).isoformat()
        with (raw_dir / (name + '.log')).open('w') as log:
            try:
                run = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
                code = run.returncode
            except subprocess.TimeoutExpired:
                code = 124
        phase_cases = []
        if raw_xml.exists():
            redact_junit(raw_xml, public_xml, ROOT)
            phase_cases = cases(public_xml)
            for row in phase_cases:
                key = (row['nodeid'], layer)
                if priority[row['status']] >= priority[observed.get(key, 'passed')]:
                    observed[key] = row['status']
        if code == 0 and not phase_cases:
            code = 3  # A successful process with no executed report is not a pass.
        results.append({'phase': name, 'layer': layer, 'command': ['python' if x == sys.executable else x for x in command],
            'exit_code': code, 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(),
            'junit': str(public_xml.relative_to(ROOT)) if public_xml.exists() else None})
        print(json.dumps({'phase': name, 'exit_code': code, 'layer': layer}), flush=True)
    counts = {status: sum(value == status for value in observed.values()) for status in priority}
    by_layer = {layer: {status: sum(key[1] == layer and value == status for key, value in observed.items())
                 for status in priority} for layer in sorted({key[1] for key in observed})}
    report = {'phases': results, 'counts': counts, 'by_layer': by_layer, 'code_digests': digests,
        'native_postgres': 'NOT_RUN', 'pgbouncer': 'NOT_RUN', 'real_zabbix': 'NOT_RUN',
        'deployment_gates': 'MANUAL_GATE_PENDING', 'hypothesis_profile': env['ACK_HYPOTHESIS_PROFILE'],
        'tree_unchanged': digests == code_digests(ROOT),
        'all_executed_phases_succeeded': all(x['exit_code'] == 0 for x in results) and digests == code_digests(ROOT)}
    report['status'] = 'PASS' if report['all_executed_phases_succeeded'] else 'FAIL'
    (evidence / 'reference-run.json').write_text(json.dumps(report, indent=2) + '\n')
    (evidence / 'test-summary.json').write_text(json.dumps({k: report[k] for k in
        ('counts', 'by_layer', 'native_postgres', 'pgbouncer', 'real_zabbix', 'deployment_gates')}, indent=2) + '\n')
    return 0 if report['all_executed_phases_succeeded'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
