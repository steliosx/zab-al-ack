#!/usr/bin/env python3
"""Fresh native PostgreSQL 18 / PgBouncer phases on locally installed services.

Non-Docker form of tools/native_ci.py (docs/RELEASE_AMENDMENTS.md R14, R26): the
same phases, the same pytest targets, the same ACK_TEST_* environment, a fresh
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

Lifecycle (R26): before any destructive step the runner inspects the lab through
`lab/local_runtime.py state`. An absent or safely stopped lab may be reset; a
running marked lab is stopped and re-inspected first; an unmarked, ambiguous or
failed-to-stop lab refuses the reset and fails the phase. Every reset and every
phase end first preserves the phase diagnostics under
build/private/retained-phases/ (tools/lab_retention.py); a preservation failure
also refuses the reset. PgBouncer and PostgreSQL are cleaned up independently
and both outcomes are recorded next to the phase's original failure.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from functools import partial
from typing import Callable
from uuid import uuid4

from evidence_utils import cases, code_digests, redact_junit
from lab_retention import RetentionError, preserve
from native_ci import PHASES, retain_phase_exception

ROOT = Path(__file__).resolve().parents[1]
PG_PORT = 55432
POOL_PORT = 56432
DATABASE = 'alerta'
LAYER = 'native_postgres18_pgbouncer'
IDENTITIES = Path('lab/native_service_identities.json')
LAB_LOGS = (Path('.lab/postgres.log'), Path('.lab/initdb.log'))
POOL_STATE = Path('build/private/pgbouncer-local')
RESETTABLE = {'absent', 'stopped'}


class LabStateError(RuntimeError):
    """The lab is not in a state that permits the requested destructive step."""


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


def reviewed_identities() -> tuple[dict, str]:
    raw = (ROOT / IDENTITIES).read_bytes()
    record = json.loads(raw)
    policy = record['policy']
    reviewed = record['reviewed']
    if not (isinstance(policy.get('postgres'), dict) and isinstance(policy.get('pgbouncer'), dict)
            and isinstance(reviewed.get('postgres'), list) and isinstance(reviewed.get('pgbouncer'), list)):
        raise RuntimeError('lab/native_service_identities.json is malformed')
    return record, hashlib.sha256(raw).hexdigest()


def package_identity(binary: Path) -> dict:
    """The dpkg package providing the executable actually used, or its digest."""
    real = binary.resolve()
    with real.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    if shutil.which('dpkg') and shutil.which('dpkg-query'):
        owner = subprocess.run(['dpkg', '-S', str(real)], capture_output=True, text=True, timeout=30)
        if owner.returncode == 0 and ': ' in owner.stdout:
            package = owner.stdout.strip().splitlines()[0].rsplit(': ', 1)[0].split(':')[0].strip()
            query = subprocess.run(['dpkg-query', '-W', '-f', '${Package}\t${Version}\t${Architecture}\t${Status}', package],
                                   capture_output=True, text=True, timeout=30)
            if query.returncode == 0 and query.stdout.count('\t') == 3:
                name, version, architecture, status = query.stdout.split('\t')
                if status.strip() != 'install ok installed':
                    raise RuntimeError('service package is not cleanly installed: ' + name)
                return {'kind': 'dpkg', 'package': name, 'version': version, 'architecture': architecture, 'sha256': digest}
    return {'kind': 'unpackaged', 'sha256': digest}


def review_status(service: str, identity: dict, reviewed: list) -> str:
    for entry in reviewed:
        if not isinstance(entry, dict):
            continue
        if entry.get('kind') == 'binary' and entry.get('sha256') == identity['sha256']:
            return 'REVIEWED'
        if (entry.get('kind') == 'dpkg' and identity['kind'] == 'dpkg'
                and all(entry.get(key) == identity[key] for key in ('package', 'version', 'architecture'))):
            return 'REVIEWED'
    if identity['kind'] == 'dpkg':
        raise RuntimeError(f"{service}_package_not_reviewed: {identity['package']} {identity['version']} "
                           f"{identity['architecture']} is not in lab/native_service_identities.json")
    return 'UNREVIEWED'


def service_identities(pg: Path, pool: Path) -> dict:
    """Version policy and reviewed-identity checks for the binaries actually used."""
    record, digest = reviewed_identities()
    postgres = subprocess.check_output([str(pg / 'postgres'), '--version'], text=True, timeout=10).strip()
    pgbouncer = subprocess.check_output([str(pool), '--version'], text=True, timeout=10).strip().splitlines()[0]
    major = record['policy']['postgres']['major']
    if not postgres.startswith(f'postgres (PostgreSQL) {major}.'):
        raise RuntimeError(f'PostgreSQL {major} required; found ' + postgres)
    pool_policy = record['policy']['pgbouncer']
    match = re.fullmatch(r'PgBouncer (\d+)\.(\d+)(?:\.(\d+))?(?:\D.*)?', pgbouncer)
    if (match is None or int(match[1]) != pool_policy['major'] or int(match[2]) < pool_policy['minimum_minor']):
        raise RuntimeError(f"PgBouncer {pool_policy['major']}.{pool_policy['minimum_minor']} or newer required; found " + pgbouncer)
    services: dict = {'identities_file': IDENTITIES.as_posix(), 'identities_sha256': digest}
    for name, binary, version in (('postgres', pg / 'postgres', postgres), ('pgbouncer', pool, pgbouncer)):
        identity = package_identity(binary)
        services[name] = {'version': version, 'identity': identity,
                          'review': review_status(name, identity, record['reviewed'][name])}
    return services


def lab_state() -> dict:
    result = local_runtime('state')
    if result.returncode:
        raise LabStateError('lab_state_unavailable: ' + (result.stdout + result.stderr)[-500:])
    try:
        observed = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise LabStateError('lab_state_unreadable') from error
    if not isinstance(observed, dict) or not isinstance(observed.get('state'), str):
        raise LabStateError('lab_state_unreadable')
    return observed


def reset_lab(run_id: str, phase: str) -> dict:
    """Return the lab to `absent`, refusing every unowned, ambiguous or failed state.

    Preservation of the lab logs happens before the directory is removed; a
    preservation failure refuses the reset as well.
    """
    observed = lab_state()
    record: dict = {'before': observed['state']}
    if observed['state'] == 'running':
        stopped = local_runtime('stop')
        record['stop_exit_code'] = stopped.returncode
        observed = lab_state()
        if stopped.returncode or observed['state'] != 'stopped':
            raise LabStateError(f"lab_stop_failed_reset_refused: {observed['state']}: {observed.get('detail', '')}")
    if observed['state'] not in RESETTABLE:
        raise LabStateError(f"lab_state_{observed['state']}_reset_refused: {observed.get('detail', '')}")
    record['retention'] = preserve(ROOT, run_id, 'pre-reset-' + phase, LAB_LOGS)
    if observed['state'] == 'stopped':
        shutil.rmtree(ROOT / '.lab')
    record['after'] = 'absent'
    return record


def cluster_start(pg: Path, run_id: str, phase: str) -> dict:
    record = reset_lab(run_id, phase)
    result = local_runtime('start')
    if result.returncode:
        raise RuntimeError('cluster_start_failed: ' + (result.stdout + result.stderr)[-1500:])
    if not wait_port(PG_PORT):
        raise RuntimeError('cluster_not_listening')
    created = subprocess.run([str(pg / 'psql'), f'postgresql://lab_admin@127.0.0.1:{PG_PORT}/postgres', '-v', 'ON_ERROR_STOP=1',
                              '-Atc', f'CREATE DATABASE {DATABASE} OWNER lab_admin'], text=True, capture_output=True, timeout=60)
    if created.returncode:
        raise RuntimeError('lab_database_create_failed: ' + created.stderr[-500:])
    return record


def cluster_stop() -> dict:
    """Leave the lab stopped; never touch an unowned or ambiguous directory."""
    observed = lab_state()
    if observed['state'] == 'running':
        stopped = local_runtime('stop')
        observed = lab_state()
        status = 'STOPPED' if stopped.returncode == 0 and observed['state'] == 'stopped' else 'FAILED'
        return {'status': status, 'exit_code': stopped.returncode, 'state': observed['state']}
    if observed['state'] == 'stopped':
        return {'status': 'STOPPED', 'exit_code': 0, 'state': 'stopped'}
    if observed['state'] == 'absent':
        return {'status': 'NOT_STARTED', 'exit_code': 0, 'state': 'absent'}
    return {'status': 'REFUSED', 'state': observed['state'], 'detail': str(observed.get('detail', ''))[:200]}


def pid_alive(path: Path) -> bool:
    try:
        pid = int(path.read_text().strip().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pgbouncer_start(pool: Path, state: Path) -> subprocess.Popen:
    if not port_free(POOL_PORT):
        raise RuntimeError('pool_port_in_use_before_start')
    if pid_alive(state / 'pgbouncer.pid'):
        raise RuntimeError('pool_pid_alive_before_start')
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
    # One log per phase; the previous phase's log was preserved at its end.
    log = (state / 'pgbouncer.log').open('w')
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


def outcome(action: Callable[[], object]) -> dict:
    """Run one cleanup step on its own; a failure never skips the other step."""
    try:
        value = action()
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as error:
        return {'status': 'FAILED', 'error': type(error).__name__}
    return value if isinstance(value, dict) else {'status': 'STOPPED'}


def previous_run_sources(evidence: Path, raw: Path) -> list[Path]:
    sources = [evidence / 'native-run.json', *LAB_LOGS, POOL_STATE / 'pgbouncer.log']
    for phase in PHASES:
        sources += [evidence / f'native-{phase}.xml', raw / f'native-{phase}.xml', raw / f'native-{phase}.log']
    sources += sorted(raw.glob('native-*-exception.log')) if raw.is_dir() else []
    return sources


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
                          'lab_state_inspection': 'lab/local_runtime.py state before every reset; unowned or ambiguous state refuses',
                          'retention': 'build/private/retained-phases before every reset and at every phase end',
                          'external_zabbix_writes': False}, indent=2))
        return 0
    evidence = ROOT / 'evidence'
    raw = ROOT / 'build/evidence-raw'
    state = ROOT / POOL_STATE
    run_id = uuid4().hex[:10]
    # Preserve whatever an earlier run left before this run invalidates it. A
    # preservation failure leaves every previous file, including the previous
    # report, untouched: nothing is invalidated and nothing is started.
    try:
        previous = preserve(ROOT, run_id, 'previous-run', previous_run_sources(evidence, raw))
    except RetentionError as error:
        print(json.dumps({'layer': LAYER, 'status': 'NOT_RUN', 'reason': 'retention_failed: ' + str(error)[:300],
                          'not_docker': True, 'previous_reports': 'left in place'}))
        return 2
    evidence.mkdir(exist_ok=True)
    raw.mkdir(parents=True, exist_ok=True)
    for phase in PHASES:
        (raw / ('native-' + phase + '.xml')).unlink(missing_ok=True)
        (evidence / ('native-' + phase + '.xml')).unlink(missing_ok=True)
    report: dict = {'layer': LAYER, 'status': 'RUNNING', 'phases': [], 'code_digests': code_digests(ROOT),
                    'real_zabbix': 'NOT_RUN', 'deployment_gates': 'MANUAL_GATE_PENDING', 'not_docker': True,
                    'runner': 'tools/native_local.py: same phases, targets and ACK_TEST_* environment as tools/native_ci.py on '
                              'locally installed PostgreSQL 18 and PgBouncer (fresh loopback cluster per phase); see docs/RELEASE_AMENDMENTS.md R14 and R26',
                    'retention': {'previous_run': previous}}
    report_path = evidence / 'native-run.json'
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    try:
        pg, pool = binaries()
        report['services'] = service_identities(pg, pool)
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
        report.update(status='NOT_RUN', reason=str(error)[:500])
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
        return 2
    for phase, targets in selected.items():
        print(json.dumps({'phase': phase, 'layer': LAYER}), flush=True)
        result: dict = {'phase': phase, 'exit_code': 1, 'status': 'FAIL',
                        'started_at': datetime.now(timezone.utc).isoformat(),
                        'diagnostic_dir': 'build/evidence-raw/native-' + phase + '-' + run_id}
        pooler = None
        stage = 'lab-reset'
        try:
            result['lab_reset'] = cluster_start(pg, run_id, phase)
            stage = 'pgbouncer-startup'
            pooler = pgbouncer_start(pool, state)
            stage = 'pytest'
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
            stage = 'junit-processing'
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
            result['stage'] = stage
            retain_phase_exception(raw, phase, run_id, stage, error)
        finally:
            # Each service is cleaned up on its own; the original failure above
            # is never replaced and both outcomes are recorded.
            cleanup = {'pgbouncer': outcome(partial(pgbouncer_stop, pooler, state)) if pooler is not None
                       else {'status': 'NOT_STARTED'},
                       'postgres': outcome(cluster_stop)}
            result['cleanup'] = cleanup
            failed = [name for name, item in cleanup.items() if item['status'] not in ('STOPPED', 'NOT_STARTED')]
            result['shutdown_exit_code'] = 1 if failed else 0
            if failed:
                result['status'] = 'FAIL'
                result['shutdown_reason'] = ','.join(failed)
            try:
                result['retention'] = preserve(ROOT, run_id, 'phase-' + phase, [
                    raw / ('native-' + phase + '.xml'), raw / ('native-' + phase + '.log'),
                    raw / f'native-{phase}-{run_id}-exception.log', state / 'pgbouncer.log'])
            except RetentionError as error:
                result['retention'] = {'status': 'FAILED', 'error': str(error)[:200]}
                result['status'] = 'FAIL'
                result.setdefault('reason', 'RetentionError: phase diagnostics were not preserved')
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
