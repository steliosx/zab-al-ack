#!/usr/bin/env python3
"""Fresh native PostgreSQL/PgBouncer phases on a local disposable Docker daemon."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from uuid import uuid4
from evidence_utils import redact_junit, cases, code_digests

ROOT = Path(__file__).resolve().parents[1]
PHASES = {
    'postgres': ['tests/postgres/test_postgres.py', 'tests/postgres/test_native_login.py',
                 'tests/postgres/test_late_response.py', 'tests/postgres/test_stateful_ledger.py', 'tests/postgres/test_settled_expiry.py'],
    'delivery_faults': ['tests/postgres/test_native_delivery_faults.py'],
    'worker': ['tests/postgres/test_worker_path.py'],
    'alerta': ['tests/alerta/test_postgres_http.py', 'tests/alerta/test_health_postgres.py', 'tests/alerta/test_docs_walkthrough.py'],
    'performance': ['tests/performance/test_reference_batches.py'],
}


def ensure_local_docker() -> None:
    if not shutil.which('docker'):
        raise RuntimeError('Docker Compose unavailable; native service tests NOT_RUN')
    override = os.environ.get('DOCKER_HOST')
    if override and os.environ.get('DOCKER_CONTEXT'):
        raise RuntimeError('Ambiguous Docker host/context combination refused')
    if override and not override.startswith('unix://'):
        raise RuntimeError('Only a local Unix-socket Docker daemon is accepted')
    if not override:
        data = json.loads(subprocess.check_output(['docker', 'context', 'inspect'], text=True, timeout=10))
        if not data[0]['Endpoints']['docker']['Host'].startswith('unix://'):
            raise RuntimeError('Remote Docker context refused')
    subprocess.run(['docker', 'compose', 'version'], check=True, timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-lab', action='store_true', required=True)
    parser.add_argument('--phase', choices=['all', *PHASES], default='all')
    parser.add_argument('--plan', action='store_true', help='Print bounded phases without starting Docker')
    args = parser.parse_args()
    selected = PHASES if args.phase == 'all' else {args.phase: PHASES[args.phase]}
    if args.plan:
        print(json.dumps({'phases': selected, 'bind': '127.0.0.1', 'network': 'internal',
            'fresh_cluster_per_phase': True, 'shutdown': 'stop; preserve volumes', 'external_zabbix_writes': False}, indent=2))
        return 0
    evidence = ROOT / 'evidence'
    evidence.mkdir(exist_ok=True)
    raw = ROOT / 'build/evidence-raw'
    raw.mkdir(parents=True, exist_ok=True)
    # Invalidate every report owned by this runner before any Docker operation.
    # Startup/cleanup can fail before pytest is reached, and CI uploads XML by
    # wildcard. Previous runs must not survive as apparently current evidence.
    for phase in PHASES:
        (raw / ('native-' + phase + '.xml')).unlink(missing_ok=True)
        (evidence / ('native-' + phase + '.xml')).unlink(missing_ok=True)
    report = {'layer': 'native_postgres18_pgbouncer', 'status': 'RUNNING', 'phases': [],
        'code_digests': code_digests(ROOT),
        'real_zabbix': 'NOT_RUN', 'deployment_gates': 'MANUAL_GATE_PENDING'}
    report_path = evidence / 'native-run.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    try:
        ensure_local_docker()
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        report['status'] = 'NOT_RUN'
        report['reason'] = str(error)
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
        return 2
    run_id = uuid4().hex[:10]
    for phase, targets in selected.items():
        project = 'ack-alpha-' + run_id + '-' + phase
        compose = ['docker', 'compose', '-p', project, '-f', 'lab/compose.postgres.yml', '--profile', 'pooling']
        print(json.dumps({'phase': phase, 'project': project, 'layer': 'native_postgres18_pgbouncer'}), flush=True)
        result = {'phase': phase, 'project': project, 'exit_code': 1, 'status': 'FAIL',
                  'diagnostic_dir': 'build/evidence-raw/native-' + phase + '-' + run_id}
        try:
            subprocess.run([*compose, 'up', '-d', '--build', '--wait', '--wait-timeout', '60'],
                           cwd=ROOT, check=True, timeout=600)
            env = {key: value for key, value in os.environ.items() if not key.startswith('ACK_TEST_')}
            # The Compose lab database is `alerta`; native tests read the actual
            # database name from the fixture (tests/conftest.py) rather than
            # assuming it, so this DSN and lab/local_runtime.py (`postgres`) both work.
            env.update(ACK_TEST_ADMIN_DSN='postgresql://lab_admin@127.0.0.1:55432/alerta',
                       ACK_TEST_DESTRUCTIVE_LAB='YES_ISOLATED_LAB',
                       ACK_TEST_PGBOUNCER_DSN='postgresql://zabalack_probe@127.0.0.1:56432/alerta',
                       TZ='UTC', HYPOTHESIS_STORAGE_DIRECTORY=str(ROOT / 'build/private/hypothesis'))
            raw_xml = raw / ('native-' + phase + '.xml')
            public_xml = evidence / raw_xml.name
            raw_xml.unlink(missing_ok=True)
            public_xml.unlink(missing_ok=True)
            with (raw / ('native-' + phase + '.log')).open('w') as log:
                code = subprocess.run([sys.executable, '-m', 'pytest', *targets, '-q',
                    '--junitxml=' + str(raw_xml), '--basetemp=' + str(raw / ('native-' + phase + '-' + run_id))], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600).returncode
            result['exit_code'] = code
            if raw_xml.exists():
                redact_junit(raw_xml, public_xml, ROOT)
                rows = cases(public_xml)
                result['counts'] = {s: sum(row['status'] == s for row in rows) for s in ('passed', 'failed', 'skipped')}
                result['junit'] = str(public_xml.relative_to(ROOT))
                if code == 0 and rows and all(row['status'] == 'passed' for row in rows):
                    result['status'] = 'PASS'
        except (subprocess.SubprocessError, OSError, ET.ParseError, ValueError) as error:
            result['status'] = 'FAIL'
            result['reason'] = type(error).__name__
        finally:
            # Never remove volumes or reset source acknowledgements. Fresh
            # generated projects prevent collision with an earlier lab run.
            try:
                stopped = subprocess.run([*compose, 'stop', '--timeout', '10'], cwd=ROOT, timeout=60)
                result['shutdown_exit_code'] = stopped.returncode
                if stopped.returncode:
                    result['status'] = 'FAIL'
            except (subprocess.SubprocessError, OSError) as error:
                result['status'] = 'FAIL'
                result['shutdown_reason'] = type(error).__name__
        report['phases'].append(result)
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        if result['status'] != 'PASS':
            break
    report['tree_unchanged'] = report['code_digests'] == code_digests(ROOT)
    report['status'] = 'PASS' if report['tree_unchanged'] and len(report['phases']) == len(selected) and all(
        item['status'] == 'PASS' for item in report['phases']) else 'FAIL'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
