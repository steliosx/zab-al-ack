#!/usr/bin/env python3
"""Fresh native PostgreSQL 18 / PgBouncer phases on locally installed services.

Non-Docker form of tools/native_ci.py (docs/RELEASE_AMENDMENTS.md R14): the same
phases, the same pytest targets, the same ACK_TEST_* environment, a fresh
disposable cluster per phase through lab/local_runtime.py, PgBouncer from the
repository's own lab/pgbouncer.ini and lab/pgbouncer_users.txt on loopback
port 56432, and JUnit redaction through tools/evidence_utils. The record it
writes is labelled `not_docker: true` and names the service binaries it used;
it never claims Compose execution or image identities.

Requirements (never production services):
  ACK_LAB_PG_BIN        directory with PostgreSQL 18 pg_ctl/initdb/psql/postgres
  ACK_LAB_PGBOUNCER_BIN path of a PgBouncer executable (1.24 or newer)
Both are used only for the disposable loopback cluster; when run as root the
services are started as the unprivileged `nobody` user, exactly as
lab/local_runtime.py does. Nothing outside this checkout's .lab directory and
build/ is written.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

from evidence_utils import cases, code_digests, redact_junit
from native_ci import PHASES

ROOT = Path(__file__).resolve().parents[1]
PG_PORT = 55432
POOL_PORT = 56432
DATABASE = 'alerta'
LAYER = 'native_postgres18_pgbouncer'


def wait_port(port: int, timeout: float = 30) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                return True
        time.sleep(0.3)
    return False


def port_free(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', port)) != 0


def unprivileged(command: list[str]) -> list[str]:
    return ['runuser', '-u', 'nobody', '--', *command] if os.geteuid() == 0 else command


def local_runtime(action: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, 'lab/local_runtime.py', action, '--isolated-lab'],
                          cwd=ROOT, text=True, capture_output=True, timeout=180)


def binaries() -> tuple[Path, Path]:
    pg = os.environ.get('ACK_LAB_PG_BIN')
    pool = os.environ.get('ACK_LAB_PGBOUNCER_BIN')
    if not pg or not (Path(pg) / 'psql').is_file() or not pool or not Path(pool).is_file():
        raise RuntimeError('ACK_LAB_PG_BIN (PostgreSQL 18 bin directory) and ACK_LAB_PGBOUNCER_BIN (PgBouncer executable) required; native service tests NOT_RUN')
    return Path(pg), Path(pool)


def versions(pg: Path, pool: Path) -> dict[str, str]:
    postgres = subprocess.check_output([str(pg / 'postgres'), '--version'], text=True, timeout=10).strip()
    pgbouncer = subprocess.check_output([str(pool), '--version'], text=True, timeout=10).strip().splitlines()[0]
    if not postgres.startswith('postgres (PostgreSQL) 18.'):
        raise RuntimeError('PostgreSQL 18 required; found ' + postgres)
    if not pgbouncer.startswith('PgBouncer 1.'):
        raise RuntimeError('PgBouncer 1.x required; found ' + pgbouncer)
    return {'postgres': postgres, 'pgbouncer': pgbouncer}


def cluster_start(pg: Path) -> None:
    local_runtime('stop')
    shutil.rmtree(ROOT / '.lab', ignore_errors=True)
    result = local_runtime('start')
    if result.returncode:
        raise RuntimeError('cluster_start_failed: ' + (result.stdout + result.stderr)[-1500:])
    if not wait_port(PG_PORT):
        raise RuntimeError('cluster_not_listening')
    created = subprocess.run([str(pg / 'psql'), f'postgresql://lab_admin@127.0.0.1:{PG_PORT}/postgres', '-v', 'ON_ERROR_STOP=1',
                              '-Atc', f'CREATE DATABASE {DATABASE} OWNER lab_admin'], text=True, capture_output=True, timeout=60)
    if created.returncode:
        raise RuntimeError('lab_database_create_failed: ' + created.stderr[-500:])


def pgbouncer_start(pool: Path, state: Path) -> subprocess.Popen:
    if not port_free(POOL_PORT):
        raise RuntimeError('pool_port_in_use_before_start')
    state.mkdir(parents=True, exist_ok=True)
    for stale in (state / 'pgbouncer.pid', state / f'.s.PGSQL.{POOL_PORT}'):
        stale.unlink(missing_ok=True)
    ini = (ROOT / 'lab/pgbouncer.ini').read_text()
    replacements = (('host=postgres port=5432 dbname=alerta', f'host=127.0.0.1 port={PG_PORT} dbname={DATABASE}'),
                    ('listen_addr = 0.0.0.0', 'listen_addr = 127.0.0.1'),
                    ('listen_port = 6432', f'listen_port = {POOL_PORT}'),
                    ('auth_file = /etc/pgbouncer/users.txt', f'auth_file = {state}/users.txt'),
                    ('unix_socket_dir = /tmp', f'unix_socket_dir = {state}'),
                    ('pidfile = /tmp/pgbouncer.pid', f'pidfile = {state}/pgbouncer.pid'))
    for old, new in replacements:
        if old not in ini:
            raise RuntimeError('lab/pgbouncer.ini no longer contains the expected setting: ' + old)
        ini = ini.replace(old, new)
    (state / 'pgbouncer.ini').write_text(ini)
    shutil.copy(ROOT / 'lab/pgbouncer_users.txt', state / 'users.txt')
    if os.geteuid() == 0:
        for path in (state, state / 'pgbouncer.ini', state / 'users.txt'):
            shutil.chown(path, 'nobody', 'nogroup')
    log = (state / 'pgbouncer.log').open('a')
    process = subprocess.Popen(unprivileged([str(pool), str(state / 'pgbouncer.ini')]), stdout=log, stderr=subprocess.STDOUT)
    if not wait_port(POOL_PORT):
        process.kill()
        raise RuntimeError('pgbouncer_not_listening')
    return process


def pgbouncer_stop(process: subprocess.Popen, state: Path) -> None:
    try:
        os.kill(int((state / 'pgbouncer.pid').read_text().strip()), signal.SIGINT)
    except (OSError, ValueError):
        process.send_signal(signal.SIGINT)
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(5)
    end = time.monotonic() + 15
    while not port_free(POOL_PORT) and time.monotonic() < end:
        time.sleep(0.3)
    if not port_free(POOL_PORT):
        raise RuntimeError('pgbouncer_still_listening')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--isolated-lab', action='store_true', required=True)
    parser.add_argument('--phase', choices=['all', *PHASES], default='all')
    parser.add_argument('--plan', action='store_true', help='Print bounded phases without starting any service')
    args = parser.parse_args(argv)
    selected = PHASES if args.phase == 'all' else {args.phase: PHASES[args.phase]}
    if args.plan:
        print(json.dumps({'phases': selected, 'bind': '127.0.0.1', 'fresh_cluster_per_phase': True,
                          'services': 'lab/local_runtime.py cluster + PgBouncer from lab/pgbouncer.ini, not Docker',
                          'external_zabbix_writes': False}, indent=2))
        return 0
    evidence = ROOT / 'evidence'
    evidence.mkdir(exist_ok=True)
    raw = ROOT / 'build/evidence-raw'
    raw.mkdir(parents=True, exist_ok=True)
    state = ROOT / 'build/private/pgbouncer-local'
    for phase in PHASES:
        (raw / ('native-' + phase + '.xml')).unlink(missing_ok=True)
        (evidence / ('native-' + phase + '.xml')).unlink(missing_ok=True)
    report: dict = {'layer': LAYER, 'status': 'RUNNING', 'phases': [], 'code_digests': code_digests(ROOT),
                    'real_zabbix': 'NOT_RUN', 'deployment_gates': 'MANUAL_GATE_PENDING', 'not_docker': True,
                    'runner': 'tools/native_local.py: same phases, targets and ACK_TEST_* environment as tools/native_ci.py on '
                              'locally installed PostgreSQL 18 and PgBouncer (fresh loopback cluster per phase); see docs/RELEASE_AMENDMENTS.md R14'}
    report_path = evidence / 'native-run.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    try:
        pg, pool = binaries()
        report['services'] = versions(pg, pool)
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        report.update(status='NOT_RUN', reason=str(error)[:500])
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
        return 2
    run_id = uuid4().hex[:10]
    for phase, targets in selected.items():
        print(json.dumps({'phase': phase, 'layer': LAYER}), flush=True)
        result: dict = {'phase': phase, 'exit_code': 1, 'status': 'FAIL',
                        'started_at': datetime.now(timezone.utc).isoformat(),
                        'diagnostic_dir': 'build/evidence-raw/native-' + phase + '-' + run_id}
        pooler = None
        try:
            cluster_start(pg)
            pooler = pgbouncer_start(pool, state)
            env = {key: value for key, value in os.environ.items() if not key.startswith('ACK_TEST_')}
            env.update(ACK_TEST_ADMIN_DSN=f'postgresql://lab_admin@127.0.0.1:{PG_PORT}/{DATABASE}',
                       ACK_TEST_DESTRUCTIVE_LAB='YES_ISOLATED_LAB',
                       ACK_TEST_PGBOUNCER_DSN=f'postgresql://zabalack_probe@127.0.0.1:{POOL_PORT}/{DATABASE}',
                       TZ='UTC', HYPOTHESIS_STORAGE_DIRECTORY=str(ROOT / 'build/private/hypothesis'))
            raw_xml = raw / ('native-' + phase + '.xml')
            public_xml = evidence / raw_xml.name
            with (raw / ('native-' + phase + '.log')).open('w') as log:
                code = subprocess.run([sys.executable, '-m', 'pytest', *targets, '-q', '-rs', '--junitxml=' + str(raw_xml),
                                       '--basetemp=' + str(raw / ('native-' + phase + '-' + run_id))],
                                      cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900).returncode
            result['exit_code'] = code
            if raw_xml.exists():
                result['raw_junit_sha256'] = hashlib.sha256(raw_xml.read_bytes()).hexdigest()
                redact_junit(raw_xml, public_xml, ROOT)
                rows = cases(public_xml)
                result['counts'] = {status: sum(row['status'] == status for row in rows) for status in ('passed', 'failed', 'skipped')}
                result['junit'] = str(public_xml.relative_to(ROOT))
                if code == 0 and rows and all(row['status'] == 'passed' for row in rows):
                    result['status'] = 'PASS'
        except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as error:
            result['status'] = 'FAIL'
            result['reason'] = (type(error).__name__ + ': ' + str(error))[:500]
        finally:
            try:
                if pooler is not None:
                    pgbouncer_stop(pooler, state)
                result['shutdown_exit_code'] = local_runtime('stop').returncode
                if result['shutdown_exit_code']:
                    result['status'] = 'FAIL'
            except (RuntimeError, subprocess.SubprocessError, OSError) as error:
                result['status'] = 'FAIL'
                result['shutdown_reason'] = type(error).__name__
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        report['phases'].append(result)
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        if result['status'] != 'PASS':
            break
    report['tree_unchanged'] = report['code_digests'] == code_digests(ROOT)
    report['status'] = 'PASS' if report['tree_unchanged'] and len(report['phases']) == len(selected) and all(
        item['status'] == 'PASS' for item in report['phases']) else 'FAIL'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'overall': report['status'], 'phases': len(report['phases'])}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
