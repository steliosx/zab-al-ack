#!/usr/bin/env python3
"""Native deployment-path lab: the services tools/run_deployment_e2e_local.py needs, without Docker (R29).

`lab/deployment_e2e_runtime.py` runs unmodified against a fixed topology:
PostgreSQL 18 as host `postgres` port 5432 with password-authenticated logins
and an empty database `alerta`, a fresh Zabbix frontend at `http://zabbix-web:8080`
backed by a running `zabbix_server`, the hostnames `postgres`, `zabbix-web` and
`runner` resolving to loopback, `/opt/zabalack-lab` resolving to the checkout
and the private directory `/run/zabalack-e2e` (mode 0700) holding
`pg-password`. This script supplies the two services on one host from
installed artifacts and keeps them disposable:

  * a dedicated PostgreSQL 18 cluster under `.deployment-lab/pgdata` on
    loopback port 5432, `lab_admin` password from `pg-password`, host logins
    `scram-sha-256` only, database `alerta`;
  * the disposable Zabbix instance of `lab/zabbix_native_lab.py` with its
    frontend on port 8080 (its own PostgreSQL cluster on 55434 is the
    `zabbix-db` component).

Commands: `plan`, `state` (read-only), `start`, `reset` (fresh `alerta`
database and lab roles, fresh Zabbix instance; the runner's --reset-command),
`services` (the services file with component versions and package identities),
`prepare-runner` (a Python environment with requirements.lock and the wheel
installed non-editably), `run` (services file, then the local runner with the
reset command and the supplied-wheel identity), `cycle` (start, run, stop and
wipe in one invocation for the hosted job), `stop` and `wipe`. Mutation
requires --isolated-lab. Unmarked, ambiguous or occupied state refuses; `stop`
exits 0 only when every component is observed stopped. Everything runs as an
unprivileged user (as `nobody` when started as root). The host layout
(hostnames, link, private directory) is verified, never changed, by this
script; `plan` prints what is missing.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'tools'))
from cancellation import SIGNALS, Cancellation, describe, run_child, signal_name  # noqa: E402
from lab_retention import RetentionError, preserve  # noqa: E402
from deployment_evidence import validate_report  # noqa: E402
STATE = REPO / '.deployment-lab'
MARKER = STATE / 'ISOLATED_DEPLOYMENT_LAB'
PGDATA = STATE / 'pgdata'
PG_PORT = 5432
WEB_PORT = 8080
PRIVATE = Path('/run/zabalack-e2e')
LINK = Path('/opt/zabalack-lab')
HOSTNAMES = ('postgres', 'zabbix-web', 'runner')
DATABASE = 'alerta'
LAB_ROLES = ('alerta_app', 'migrator', 'zabalack_worker', 'zabalack_probe', 'zabalack_operator', 'zabalack_owner')
ZABBIX_LAB = REPO / 'lab/zabbix_native_lab.py'
RECORD = REPO / 'evidence/deployment-e2e-run.json'
NATIVE_LAB_BINDING = 'ZABALACK_NATIVE_LAB_BINDING'
RUNNER_DESCRIPTION = ('lab/deployment_native_lab.py: services from installed artifacts on this host for '
                      'tools/run_deployment_e2e_local.py; the public record is invalidated before any service work and '
                      'bound to the cycle outcome afterwards (R29)')
# Public reason codes for refusals; the full message goes to the job log, never into the record.
REFUSAL_CODES = (('BLOCKED_ENVIRONMENT', 'blocked_environment'), ('ACK_LAB_PG_BIN', 'postgres_binaries_missing'),
                 ('host layout not ready', 'layout_not_ready'), ('cycle requires an absent', 'lab_state_not_absent'),
                 ('run requires the running', 'lab_not_running'), ('Refusing to start', 'lab_state_refused'),
                 ('private directory not ready', 'private_directory_not_ready'), ('Zabbix lab start failed', 'zabbix_start_failed'),
                 ('deployment lab initdb failed', 'postgres_initdb_failed'), ('deployment lab PostgreSQL', 'postgres_start_failed'),
                 ('the Zabbix lab is not running at the pinned reference', 'zabbix_reference_mismatch'), ('interrupted', 'interrupted'))
# Diagnostics of both labs, preserved under build/private/retained-phases before a cycle's wipe.
CYCLE_LOGS = ('.deployment-lab/initdb.log', '.deployment-lab/postgres.log', '.deployment-lab/services.json',
              '.zabbix-lab/initdb.log', '.zabbix-lab/postgres.log', '.zabbix-lab/schema-load.log',
              '.zabbix-lab/zabbix_server.log', '.zabbix-lab/php.log')


def zabbix_module():
    spec = importlib.util.spec_from_file_location('zabbix_native_lab_for_deployment', ZABBIX_LAB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def zabbix_env() -> dict[str, str]:
    return dict(os.environ, ZABALACK_ZABBIX_WEB_PORT=str(WEB_PORT))


def zabbix(command: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ZABBIX_LAB), command, *args], cwd=REPO, env=zabbix_env(),
                          text=True, capture_output=True, check=check, timeout=900)


def unprivileged(command: list[str]) -> list[str]:
    return ['runuser', '-u', 'nobody', '--', *command] if os.geteuid() == 0 else command


def run(command: list[str], *, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(unprivileged(command), check=check, text=True, capture_output=True, **kwargs)


def own(path: Path) -> None:
    if os.geteuid() == 0:
        entry = pwd.getpwnam('nobody')
        os.chown(path, entry.pw_uid, entry.pw_gid)


def pg_bin() -> Path:
    value = os.environ.get('ACK_LAB_PG_BIN', '')
    binaries = Path(value) if value else None
    if binaries is None or not all((binaries / name).is_file() for name in ('pg_ctl', 'initdb', 'psql', 'postgres')):
        raise SystemExit('ACK_LAB_PG_BIN with PostgreSQL 18 pg_ctl/initdb/psql/postgres required; the deployment lab is NOT_RUN')
    return binaries


def port_listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def layout() -> dict:
    """The host layout the runtime fixes; verified, never changed."""
    record: dict = {'hostnames': {}, 'link': {}, 'private': {}, 'ready': True, 'missing': []}
    for name in HOSTNAMES:
        try:
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(name, None)})
        except socket.gaierror:
            addresses = []
        loopback = bool(addresses) and set(addresses) <= {'127.0.0.1', '::1'}
        record['hostnames'][name] = {'addresses': addresses, 'loopback': loopback}
        if not loopback:
            record['missing'].append(f'/etc/hosts: 127.0.0.1 {name}')
    resolves = (LINK.is_symlink() or LINK.is_dir()) and LINK.resolve() == REPO
    record['link'] = {'path': str(LINK), 'resolves_to_checkout': resolves}
    if not resolves:
        record['missing'].append(f'ln -s {REPO} {LINK}')
    try:
        info = PRIVATE.stat()
        private_ok = PRIVATE.is_dir() and (info.st_mode & 0o777) == 0o700 and info.st_uid == os.geteuid()
    except OSError:
        private_ok = False
    record['private'] = {'path': str(PRIVATE), 'ready': private_ok, 'pg_password': (PRIVATE / 'pg-password').is_file()}
    if not private_ok:
        try:
            owner = pwd.getpwuid(os.geteuid()).pw_name
        except KeyError:
            owner = str(os.geteuid())
        record['missing'].append(f'install -d -m 0700 -o {owner} {PRIVATE}')
    record['ready'] = not record['missing']
    return record


def postgres_state(binaries: Path | None) -> dict:
    record: dict = {'state': 'ambiguous', 'marker': MARKER.exists(), 'initialised': (PGDATA / 'PG_VERSION').exists(),
                    'port': PG_PORT, 'port_listening': port_listening(PG_PORT)}
    if not STATE.exists():
        record['state'] = 'ambiguous' if record['port_listening'] else 'absent'
        record['detail'] = 'port 5432 in use without a lab cluster (a packaged default cluster?)' if record['port_listening'] else 'no lab directory'
        return record
    if not record['marker']:
        record['state'] = 'unmarked'
        record['detail'] = 'existing .deployment-lab directory without the isolated-lab marker'
        return record
    if not record['initialised']:
        record['state'] = 'ambiguous' if record['port_listening'] else 'stopped'
        record['detail'] = 'marked lab without an initialised cluster'
        return record
    if binaries is None:
        record['detail'] = 'PostgreSQL binaries unavailable; cannot classify'
        return record
    status = run([str(binaries / 'pg_ctl'), '-D', str(PGDATA), 'status'], check=False).returncode
    record['pg_ctl_status'] = status
    if status == 0:
        record['state'] = 'running'
        record['detail'] = 'pg_ctl reports the lab cluster running'
    elif status == 3 and not record['port_listening']:
        record['state'] = 'stopped'
        record['detail'] = 'no server running from the lab cluster'
    elif status == 3:
        record['detail'] = 'port 5432 in use while the lab cluster is stopped'
    else:
        record['detail'] = 'pg_ctl could not classify the lab cluster (exit %d)' % status
    return record


def inspect(binaries: Path | None = None) -> dict:
    # In-process the module attributes are set explicitly; subprocesses get the
    # port through zabbix_env(). The verifier's own environment is never changed.
    module = zabbix_module()
    module.WEB_PORT = WEB_PORT
    module.PORTS['php'] = WEB_PORT
    module.API_URL = f'http://127.0.0.1:{WEB_PORT}/api_jsonrpc.php'
    postgres = postgres_state(binaries)
    zabbix_state = module.inspect()
    states = {postgres['state'], zabbix_state['state']}
    if 'unmarked' in states:
        combined = 'unmarked'
    elif 'ambiguous' in states:
        combined = 'ambiguous'
    elif states == {'absent'}:
        combined = 'absent'
    elif 'running' in states:
        combined = 'running' if states == {'running'} else 'partial'
    else:
        combined = 'stopped'
    return {'state': combined, 'postgres': postgres, 'zabbix': zabbix_state, 'layout': layout()}


def psql(binaries: Path, database: str, sql: str, *, user: str = 'lab_admin') -> str:
    password = (PRIVATE / 'pg-password').read_text().strip()
    result = subprocess.run([str(binaries / 'psql'), '-X', '-At', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1', '-p', str(PG_PORT),
                             '-U', user, '-d', database, '-c', sql], env={'PGPASSWORD': password, 'PATH': os.environ['PATH']},
                            text=True, capture_output=True, check=True, timeout=120)
    return result.stdout.strip()


def ensure_private() -> None:
    if not PRIVATE.is_dir() or (PRIVATE.stat().st_mode & 0o777) != 0o700 or PRIVATE.stat().st_uid != os.geteuid():
        raise SystemExit('private directory not ready: ' + ' ; '.join(layout()['missing']))
    password = PRIVATE / 'pg-password'
    if not password.is_file():
        fd = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_urlsafe(32))
    os.chmod(password, 0o600)


def start_postgres(binaries: Path) -> None:
    STATE.mkdir(mode=0o700, exist_ok=True)
    MARKER.write_text('Disposable zabalack deployment lab. Loopback port 5432, scram host logins.\n')
    own(STATE)
    own(MARKER)
    if not (PGDATA / 'PG_VERSION').exists():
        # initdb reads the superuser password from a file the service user can read.
        pwfile = STATE / 'initdb.pwfile'
        pwfile.write_text((PRIVATE / 'pg-password').read_text().strip() + '\n')
        os.chmod(pwfile, 0o600)
        own(pwfile)
        try:
            result = run([str(binaries / 'initdb'), '-D', str(PGDATA), '-U', 'lab_admin', f'--pwfile={pwfile}',
                          '--auth-host=scram-sha-256', '--auth-local=trust', '--encoding=UTF8', '--locale=C.UTF-8'])
        except subprocess.CalledProcessError as error:
            (STATE / 'initdb.log').write_text((error.stdout or '') + (error.stderr or ''))
            raise SystemExit('deployment lab initdb failed; see .deployment-lab/initdb.log') from error
        finally:
            pwfile.unlink(missing_ok=True)
        (STATE / 'initdb.log').write_text(result.stdout + result.stderr)
        own(STATE / 'initdb.log')
        with (PGDATA / 'postgresql.conf').open('a') as handle:
            handle.write(f"\n# Isolated deployment lab only; loopback, scram host logins.\nlisten_addresses='127.0.0.1'\nport={PG_PORT}\n"
                         "unix_socket_directories=''\npassword_encryption='scram-sha-256'\nmax_connections=100\nshared_buffers='128MB'\n"
                         "logging_collector=off\nlog_statement='none'\n")
    if run([str(binaries / 'pg_ctl'), '-D', str(PGDATA), 'status'], check=False).returncode != 0:
        result = run([str(binaries / 'pg_ctl'), '-D', str(PGDATA), '-l', str(STATE / 'postgres.log'), '-o', '-c unix_socket_directories=',
                      '-w', 'start'], check=False)
        if result.returncode:
            raise SystemExit('deployment lab PostgreSQL start failed:\n' + (result.stdout + result.stderr)[-2000:])
    deadline = time.monotonic() + 30
    while not port_listening(PG_PORT) and time.monotonic() < deadline:
        time.sleep(0.3)
    if not port_listening(PG_PORT):
        raise SystemExit('deployment lab PostgreSQL is not listening')


def fresh_alerta_database(binaries: Path) -> dict:
    """Drop the lab roles and database the runtime created and recreate an empty database."""
    psql(binaries, 'postgres', f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{DATABASE}' AND pid<>pg_backend_pid()")
    psql(binaries, 'postgres', f'DROP DATABASE IF EXISTS {DATABASE}')
    dropped = []
    for role in LAB_ROLES:
        if psql(binaries, 'postgres', f"SELECT 1 FROM pg_roles WHERE rolname='{role}'") == '1':
            psql(binaries, 'postgres', f'DROP OWNED BY {role}')
            psql(binaries, 'postgres', f'DROP ROLE {role}')
            dropped.append(role)
    psql(binaries, 'postgres', f'CREATE DATABASE {DATABASE} OWNER lab_admin ENCODING UTF8')
    return {'database': DATABASE, 'roles_dropped': dropped}


def stop_postgres(binaries: Path) -> dict:
    if not (PGDATA / 'PG_VERSION').exists() or run([str(binaries / 'pg_ctl'), '-D', str(PGDATA), 'status'], check=False).returncode != 0:
        return {'status': 'NOT_RUNNING'}
    result = run([str(binaries / 'pg_ctl'), '-D', str(PGDATA), '-m', 'fast', '-w', 'stop'], check=False)
    return {'status': 'STOPPED' if result.returncode == 0 else 'FAILED', 'exit_code': result.returncode}


def package_identity(binary: Path) -> dict:
    return zabbix_module().package_identity(binary)


def services_record(binaries: Path) -> dict:
    """The four component descriptions run_deployment_e2e_local.py --services-file requires."""
    status = zabbix('status')
    lab = json.loads(status.stdout)
    services = lab.get('services', {})
    if lab.get('state', {}).get('state') != 'running' or not services.get('reference_match'):
        raise SystemExit('the Zabbix lab is not running at the pinned reference; refusing to describe services')
    postgres = run([str(binaries / 'postgres'), '--version']).stdout.strip()
    identity = package_identity(binaries / 'postgres')

    def describe(name: str, item: dict, extra: str = '') -> str:
        ident = item.get('identity', {})
        who = (f"dpkg {ident['package']} {ident['version']} {ident['architecture']}" if ident.get('kind') == 'dpkg'
               else f"unpackaged executable sha256 {ident.get('sha256')}")
        return f"{name}: {item.get('version') or item.get('version_line') or item.get('ZABBIX_VERSION')}; {who}{extra}"
    zdb = services['postgres']
    return {
        'postgres': describe('PostgreSQL 18 (alerta database, scram host logins, loopback 5432)', {'version': postgres, 'identity': identity}),
        'zabbix-db': describe('PostgreSQL 18 (Zabbix database, loopback 55434)', zdb),
        'zabbix-server': describe('zabbix_server', services['zabbix_server'], f"; reference {services['reference']}, observed API {services['observed_api_version']}"),
        'zabbix-web': describe('Zabbix frontend', services['frontend'], f"; {services['frontend'].get('served_by')}; PHP {services['php'].get('version_line')}"),
    }


def prepare_runner(wheel: Path, venv: Path) -> dict:
    require_wheel(wheel)
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True, timeout=300)
    pip = [str(venv / 'bin/python'), '-m', 'pip', '--disable-pip-version-check', '-q']
    subprocess.run([*pip, 'install', '-r', str(REPO / 'requirements.lock')], check=True, timeout=1800)
    # --no-compile: the installed-file check refuses bytecode in the package,
    # which would otherwise run instead of the verified source.
    subprocess.run([*pip, 'install', '--no-deps', '--no-compile', str(wheel)], check=True, timeout=300)
    # -B: importing the package here must not leave the bytecode the installed-file check refuses.
    probe = subprocess.run([str(venv / 'bin/python'), '-B', '-c', 'import zabalack, json; print(json.dumps(zabalack.__file__))'],
                           cwd='/', capture_output=True, text=True, check=True, timeout=60)
    location = Path(json.loads(probe.stdout)).resolve()
    if location.is_relative_to(REPO.resolve()):
        raise SystemExit('runner imports the checkout, not the wheel')
    return {'runner_python': str(venv / 'bin/python'), 'wheel': str(wheel), 'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
            'import_origin': str(location)}


def require_wheel(wheel: Path) -> None:
    if not wheel.is_file() or not wheel.name.startswith('zabalack-') or not wheel.name.endswith('-py3-none-any.whl'):
        raise SystemExit('a built zabalack wheel (zabalack-<version>-py3-none-any.whl) is required')


def unprivileged_helper_available() -> bool:
    """Started as root, every service runs as `nobody`; that identity must be usable."""
    if os.geteuid() != 0:
        return True
    return subprocess.run(['runuser', '-u', 'nobody', '--', 'id', '-u'], capture_output=True).returncode == 0


def details(observed: dict) -> str:
    return json.dumps({key: value.get('detail') for key, value in observed.items() if isinstance(value, dict) and 'detail' in value})


def refuse_occupied(observed: dict, action: str) -> None:
    if observed['state'] in ('unmarked', 'ambiguous'):
        raise SystemExit(f'Refusing to {action}: ' + details(observed))


def write_services(binaries: Path, services_path: Path) -> Path:
    services_path.write_text(json.dumps(services_record(binaries), indent=2) + '\n')
    return services_path


def start_lab(binaries: Path, observed: dict) -> dict:
    refuse_occupied(observed, 'start')
    if not observed['layout']['ready']:
        raise SystemExit('host layout not ready: ' + ' ; '.join(observed['layout']['missing']))
    ensure_private()
    start_postgres(binaries)
    if psql(binaries, 'postgres', f"SELECT 1 FROM pg_database WHERE datname='{DATABASE}'") != '1':
        psql(binaries, 'postgres', f'CREATE DATABASE {DATABASE} OWNER lab_admin ENCODING UTF8')
    started = zabbix('start', '--isolated-lab', check=False)
    if started.returncode:
        raise SystemExit('Zabbix lab start failed:\n' + (started.stdout + started.stderr)[-1500:])
    return {'state': inspect(binaries)['state'], 'postgres': f'postgres:{PG_PORT}', 'zabbix': f'zabbix-web:{WEB_PORT}',
            'production_gate': 'NOT_VERIFIED'}


def reset_lab(binaries: Path, observed: dict) -> dict:
    refuse_occupied(observed, 'reset')
    if observed['postgres']['state'] != 'running':
        raise SystemExit("reset requires the running deployment lab; run 'start' first")
    reset = {'alerta': fresh_alerta_database(binaries)}
    for step in (('stop', '--isolated-lab'), ('reset', '--isolated-lab'), ('start', '--isolated-lab')):
        result = zabbix(*step, check=False)
        if result.returncode:
            raise SystemExit(f'Zabbix lab {step[0]} failed during reset:\n' + (result.stdout + result.stderr)[-1500:])
    reset['zabbix'] = 'fresh database from the schema, restarted at the pinned reference'
    return reset


def run_lab(binaries: Path, observed: dict, runner_python: Path, wheel: Path, services_path: Path, wheel_sha256: str | None) -> int:
    """Write the services file, then run tools/run_deployment_e2e_local.py; its exit code is returned unchanged."""
    if observed['state'] != 'running':
        raise SystemExit(f"run requires the running deployment lab (state {observed['state']}); run 'start' first")
    write_services(binaries, services_path)
    reset_command = f'{sys.executable} {ZABBIX_LAB.parent / "deployment_native_lab.py"} reset --isolated-lab'
    command = [sys.executable, str(REPO / 'tools/run_deployment_e2e_local.py'), '--isolated-lab', '--runner-python', str(runner_python),
               '--wheel', str(wheel), '--services-file', str(services_path), '--reset-command', reset_command]
    if wheel_sha256:
        command += ['--wheel-sha256', wheel_sha256]
    # The runner handles SIGTERM itself (its pytest group, its lab processes); a
    # cancellation here terminates it and waits for that cleanup, bounded.
    # NATIVE_LAB_BINDING makes the runner mark its record as pending this
    # command's outcome, so a record left behind by an abrupt end (SIGKILL,
    # power loss) can never validate as a passing native-lab record.
    return run_child(command, cwd=REPO, timeout=3600, grace=90, env={**os.environ, NATIVE_LAB_BINDING: '1'})


def stop_lab(binaries: Path, observed: dict) -> tuple[int, dict]:
    """Stop both components; exit 0 only when every component is observed stopped."""
    if observed['state'] in ('unmarked', 'ambiguous'):
        raise SystemExit('Refusing to stop: ' + details(observed))
    # Each component is stopped on its own: an unreadable report from the Zabbix
    # lab's stop never prevents the PostgreSQL stop, and it is not a clean stop.
    try:
        stopped = zabbix('stop', '--isolated-lab', check=False)
        reported = json.loads(stopped.stdout or '{}')
        zabbix_outcome = reported.get('cleanup', 'NOT_RUNNING') if isinstance(reported, dict) else 'UNREADABLE_OUTPUT'
        if stopped.returncode != 0:
            # A refusal or traceback in the Zabbix lab prints no report; never read that as NOT_RUNNING.
            zabbix_outcome = 'STOP_FAILED: exit ' + str(stopped.returncode)
    except ValueError:
        zabbix_outcome = 'UNREADABLE_OUTPUT'
    except (subprocess.SubprocessError, OSError) as error:  # a stop that timed out or could not run
        zabbix_outcome = 'STOP_FAILED: ' + type(error).__name__
    outcomes = {'zabbix': zabbix_outcome, 'postgres': stop_postgres(binaries)}
    after = inspect(binaries)
    failed = ((isinstance(zabbix_outcome, str) and (zabbix_outcome == 'UNREADABLE_OUTPUT' or zabbix_outcome.startswith('STOP_FAILED')))
              or (isinstance(zabbix_outcome, dict) and any(isinstance(item, dict) and item.get('status') not in ('STOPPED', 'NOT_RUNNING')
                                                           for item in zabbix_outcome.values())))
    clean = after['state'] in ('stopped', 'absent') and not failed
    return (0 if clean else 5), {'cleanup': outcomes, 'state': after['state']}


def wipe_lab(binaries: Path, observed: dict) -> dict:
    if observed['state'] not in ('stopped', 'absent'):
        raise SystemExit(f"Refusing wipe from state {observed['state']}")
    wiped = zabbix('reset', '--isolated-lab', '--wipe', check=False)
    if STATE.exists():
        shutil.rmtree(STATE, ignore_errors=False)
    after = inspect(binaries)['state']
    return {'wipe': 'done' if wiped.returncode == 0 and after == 'absent' else 'failed', 'zabbix_exit_code': wiped.returncode, 'state': after}


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def write_record(record: dict) -> None:
    RECORD.parent.mkdir(exist_ok=True)
    temporary = RECORD.with_name(RECORD.name + '.tmp')
    try:
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')
        os.replace(temporary, RECORD)
    except OSError:
        # A record that was never published must not be left beside the real one.
        temporary.unlink(missing_ok=True)
        raise


def fresh_record(command: str) -> dict:
    """Invalidate the checkout's record before any service work.

    The tracked historical PASS, or a previous run's record, must never be
    uploaded as this run's artifact: an early refusal leaves this NOT_RUN
    record in place, with its reason, instead.
    """
    record = {'layer': 'native_deployment_e2e', 'status': 'NOT_RUN', 'reason': 'lab_' + command + '_not_started', 'not_docker': True,
              'started_at': stamp(), 'runner': RUNNER_DESCRIPTION, 'deployment_gates': 'MANUAL_GATE_PENDING',
              'native_lab': {'command': command, 'final_state': None, 'cleanup_complete': False}}
    write_record(record)
    return record


def load_record() -> dict | None:
    try:
        data = json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def refusal_code(message: str) -> str:
    for needle, code in REFUSAL_CODES:
        if needle in message:
            return code
    return 'lab_refused'


EXECUTION_KEYS = ('counts', 'checks', 'components', 'junit', 'supplied_wheel', 'artifact_mode', 'runner_environment', 'code_digests', 'tree_unchanged',
                  'image_ids', 'versions', 'project', 'runner_shutdown_exit_code', 'shutdown_exit_code', 'observations')


def finalize_record(command: str, code: int, summary: dict) -> dict:
    """Bind the outcome to the public record and publish it (bind_record, then write_record)."""
    record = bind_record(command, code, summary)
    write_record(record)
    return record


def bind_record(command: str, code: int, summary: dict) -> dict:
    """Bind the outcome to the public record, without publishing it. A non-zero outcome can never leave PASS behind.

    The runner never ran (a refusal or an early failure): the record is NOT_RUN
    with the refusal's public code and carries no execution evidence. The
    runner ran: its own record stays, with the lab summary attached; when the
    runner passed but cleanup, retention or the wipe failed, the record becomes
    FAIL with that reason. A passing record carries the final lab state, which
    the validator requires to be `absent`.
    """
    record = load_record() or {'layer': 'native_deployment_e2e', 'not_docker': True, 'started_at': stamp(), 'runner': RUNNER_DESCRIPTION,
                               'deployment_gates': 'MANUAL_GATE_PENDING'}
    native = {'command': command, **{key: summary.get(key) for key in ('run_id', 'run_exit_code', 'cleanup', 'retention', 'wipe', 'final_state')}}
    wipe, retention = summary.get('wipe'), summary.get('retention')
    native['cleanup_complete'] = (summary.get('final_state') == 'absent' and isinstance(wipe, dict) and wipe.get('wipe') == 'done'
                                  and isinstance(retention, dict) and retention.get('status') == 'RETAINED')
    if summary.get('refusal'):
        native['refusal'] = refusal_code(str(summary['refusal']))
    record['native_lab'] = native
    interrupted = bool(summary.get('interrupted')) or (bool(summary.get('refusal')) and 'interrupted' in str(summary['refusal']))
    if interrupted:
        # A cancellation at any stage is a FAIL, never a NOT_RUN that could read as "nothing happened".
        native['interrupted'] = str(summary.get('refusal') or 'interrupted')
        if summary.get('run_exit_code') is None:
            for key in EXECUTION_KEYS:
                record.pop(key, None)
        record.update(status='FAIL', reason=native['interrupted'])
    elif summary.get('error'):
        # An unexpected error in this command: never a PASS, whatever the runner wrote.
        native['error'] = str(summary['error'])
        if summary.get('run_exit_code') is None and not summary.get('start'):
            for key in EXECUTION_KEYS:
                record.pop(key, None)
            record.update(status='NOT_RUN', reason=native['error'])
        else:
            record.update(status='FAIL', reason=native['error'])
    elif summary.get('run_exit_code') is None:
        for key in EXECUTION_KEYS:
            record.pop(key, None)
        record.update(status='NOT_RUN', reason=native.get('refusal', 'lab_' + command + '_not_started'))
    elif record.get('status') == 'PENDING_BINDING':
        # The runner passed provisionally. Only a cycle whose run exited 0 and whose
        # cleanup completed (stopped, retained, wiped, lab absent) turns that into a
        # PASS, and only if the final bound record then validates.
        if command == 'cycle' and code == 0 and summary.get('run_exit_code') == 0 and native['cleanup_complete']:
            final = {key: value for key, value in record.items() if key != 'provisional_status'}
            final.update(status='PASS', reason=None, finished_at=stamp())
            try:
                validate_final(final)
            except ValueError:
                record.update(status='FAIL', reason='final_evidence_validation_failed')
            else:
                record = final
        elif command == 'cycle':
            record.update(status='FAIL', reason=cleanup_reason(summary))
        else:
            record.update(status='NOT_BOUND', reason='only a cycle binds a final PASS')
        if record.get('status') != 'PASS':
            record.pop('provisional_status', None)
    elif record.get('status') == 'PASS':
        # The runner is told to leave its record pending; a plain PASS here was never
        # provisionally validated under this binding and is not accepted as final.
        record.update(status='FAIL', reason=cleanup_reason(summary) if code != 0 else 'runner_record_not_pending')
    elif code != 0 and command == 'cycle' and summary.get('final_state') != 'absent' and record.get('status') not in ('NOT_RUN', 'FAIL'):
        record.update(status='FAIL', reason=cleanup_reason(summary))
    record['finished_at'] = stamp()
    return record


def validate_final(record: dict) -> None:
    """The bound record, validated as the final evidence of this checkout (tools/deployment_evidence.py)."""
    validate_report(record, REPO)


def cleanup_reason(summary: dict) -> str:
    retention = summary.get('retention') or {}
    if isinstance(retention, dict) and retention.get('error'):
        return 'lab_retention_failed'
    wipe = summary.get('wipe') or {}
    if isinstance(wipe, dict) and wipe.get('wipe') == 'failed':
        return 'lab_wipe_failed'
    cleanup = summary.get('cleanup') or {}
    if isinstance(cleanup, dict) and cleanup.get('refusal'):
        return 'lab_cleanup_refused'
    if isinstance(cleanup, dict) and cleanup.get('error'):
        return 'lab_cleanup_error: ' + str(cleanup['error'])
    return 'lab_cleanup_incomplete: final state ' + str(summary.get('final_state'))


def cycle_lab(binaries: Path, observed: dict, runner_python: Path, wheel: Path, services_path: Path, wheel_sha256: str | None,
              cancellation: Cancellation | None = None, summary: dict | None = None) -> tuple[int, dict]:
    """start, run, stop and wipe in one invocation: the hosted job's whole service/test/cleanup step.

    The lab must be absent first (no reuse of a previous attempt). The run's
    exit code is reported unchanged; a run that passed but left a component
    running or a lab directory behind exits 5. Cleanup runs after every
    outcome, including a refusal or an interrupted run. Both labs' diagnostics
    are preserved (tools/lab_retention.py) before the wipe; a preservation
    failure leaves the stopped lab directories in place.
    """
    if observed['state'] != 'absent':
        raise SystemExit(f"cycle requires an absent deployment lab (state {observed['state']}); stop and wipe the previous lab first")
    run_id = os.urandom(6).hex()
    cancellation = cancellation or Cancellation()
    summary = summary if summary is not None else {}
    summary.update({'run_id': run_id, 'start': None, 'run_exit_code': None, 'cleanup': None, 'retention': None, 'wipe': None, 'final_state': None})
    code = 1
    try:
        summary['start'] = start_lab(binaries, observed)
        code = run_lab(binaries, inspect(binaries), runner_python, wheel, services_path, wheel_sha256)
        summary['run_exit_code'] = code
    except SystemExit as error:
        # A refusal or start failure before the runner ran: NOT_RUN semantics (exit 2).
        summary['refusal'] = str(error) if not isinstance(error.code, int) else f'exit {error.code}'
        code = 2
    except KeyboardInterrupt as error:
        summary['refusal'] = describe(error)
        code = 130
    finally:
        # Cleanup, retention and the wipe are one critical section: a further
        # signal is deferred until the outcome is recorded.
        with cancellation.critical():
            try:
                stop_code, summary['cleanup'] = stop_lab(binaries, inspect(binaries))
                if stop_code == 0:
                    try:
                        # The public record as the runner left it (its original provisional record), before
                        # this command binds and replaces it, is retained with the service logs.
                        summary['retention'] = preserve(REPO, run_id, 'deployment-cycle', [Path(name) for name in CYCLE_LOGS] + [RECORD])
                    except RetentionError as error:
                        summary['retention'] = {'error': str(error)}
                    else:
                        summary['wipe'] = wipe_lab(binaries, inspect(binaries))
            except SystemExit as error:
                summary['cleanup'] = {'refusal': str(error)}
            except KeyboardInterrupt as error:
                summary['cleanup'] = {'interrupted': describe(error), **(summary.get('cleanup') or {})}
            except Exception as error:  # noqa: BLE001 - any cleanup failure must reach the record, never escape it
                summary['cleanup'] = {'error': type(error).__name__, **(summary.get('cleanup') or {})}
            summary['final_state'] = inspect(binaries)['state']
    wipe = summary.get('wipe')
    if code == 0 and (summary['final_state'] != 'absent' or not isinstance(wipe, dict) or wipe.get('wipe') != 'done'):
        code = 5
    return code, summary


def publish(command: str, code: int, summary: dict, cancellation: Cancellation) -> tuple[int, bool]:
    """Bind the final record, commit it; return the command's exit code and whether the record was published.

    Three points in order. The cancellation cutoff is the last check for a
    termination signal, made with SIGTERM, SIGHUP and SIGINT blocked. The commit
    runs from the cutoff through write_record's atomic replace, still blocked.
    The record is published once that replace has completed.

    - A signal received before the cutoff is the outcome: the record is bound
      as interrupted (FAIL) and the exit is 130, whatever the run did.
    - A signal received during the commit is held until the commit ends; the
      committed record stands, the exit is 130 and stderr says the signal came
      during the final commit, after the cutoff.
    - A death (SIGKILL) before the replace completes leaves the runner's
      PENDING_BINDING record, which never validates; after it, the published one.
    - A failed replace publishes nothing: the runner's record stays, the
      unpublished temporary file is removed and the exit is 1.

    From the first line here a termination signal is only recorded, never
    raised; guarded() restores the handlers afterwards.
    """
    cancellation.deferring = True
    record = bind_record(command, code, summary)
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    during_commit = None
    try:
        # The cancellation cutoff: every signal received so far, including any held by the mask.
        arrived, cancellation.pending = cancellation.pending, None
        while (waiting := signal.sigtimedwait(SIGNALS, 0)) is not None:
            cancellation.received.append(waiting.si_signo)
            arrived = arrived if arrived is not None else waiting.si_signo
        if arrived is not None and not summary.get('interrupted'):
            summary = {**summary, 'refusal': 'interrupted (' + signal_name(arrived) + ')', 'interrupted': True}
            code = 130
            record = bind_record(command, code, summary)
        try:
            write_record(record)  # the record is published when this atomic replace has completed
        except OSError as error:
            while signal.sigtimedwait(SIGNALS, 0) is not None:
                pass  # held signals change nothing: there is no record to keep or correct
            print(f'the final record was not published ({type(error).__name__}); the runner\'s record remains and does not validate',
                  file=sys.stderr)
            return 1, False
        # Signals that arrived during the commit are still held: take them before unblocking.
        while (waiting := signal.sigtimedwait(SIGNALS, 0)) is not None:
            cancellation.received.append(waiting.si_signo)
            during_commit = during_commit if during_commit is not None else waiting.si_signo
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    shown = {key: value[:300] if key == 'refusal' and isinstance(value, str) else value for key, value in summary.items()}
    print(json.dumps({'status': record.get('status'), 'reason': record.get('reason'), **shown}))
    if during_commit is not None:
        print(f'interrupted ({signal_name(during_commit)}) during the final commit, after the cancellation cutoff; '
              'the committed record stands', file=sys.stderr)
        return 130, True
    if code == 0 and command == 'cycle' and record.get('status') != 'PASS':
        return 1, True  # the run and cleanup succeeded but the final bound record did not validate
    return code, True


def guarded(args, parser) -> int:
    """run/cycle: a fresh NOT_RUN record first, then the environment checks, then the command.

    Every refusal is NOT_RUN exit 2. SIGTERM, SIGHUP and SIGINT are routed into
    the interrupt path (cleanup, retention, a FAIL record, exit 130); a signal
    during cleanup is deferred until the cleanup is recorded. The outcome is
    committed once (publish(), which states the cancellation cutoff and the
    commit): a signal before the cutoff makes it an interrupted FAIL with exit
    130; a signal during the commit or after the publication leaves the record
    as committed and exits 130, saying which. None of these paths prints a
    traceback; a failed replace exits 1.
    """
    cancellation = Cancellation().install()
    summary: dict = {}
    outcome: tuple[int, dict] | None = None
    try:
        while True:
            try:
                if outcome is None:
                    outcome = run_command(args, cancellation, summary)
                code, published = publish(args.command, *outcome, cancellation)
                break
            except KeyboardInterrupt as error:
                # Delivered before publish() began deferring: nothing is published yet. The
                # command is never run again; its summary so far is kept.
                if outcome is None or not outcome[1].get('interrupted'):
                    outcome = (130, {**(outcome[1] if outcome else summary), 'refusal': describe(error), 'interrupted': True})
    finally:
        cancellation.restore()
    if cancellation.pending is not None and published and code != 130:
        print(f'interrupted ({signal_name(cancellation.pending)}) after the final record was published; that record stands',
              file=sys.stderr)
        return 130
    return code


def run_command(args, cancellation: Cancellation, summary: dict) -> tuple[int, dict]:
    """The command itself: (exit code, summary) for publish(). A cancellation is an outcome, not an escape."""
    fresh_record(args.command)
    services_path = args.services_file or STATE / 'services.json'
    try:
        if not unprivileged_helper_available():
            raise SystemExit('BLOCKED_ENVIRONMENT: the deployment lab requires a non-root OS identity')
        binaries = pg_bin()
        observed = inspect(binaries)
        if args.command == 'cycle':
            return cycle_lab(binaries, observed, args.runner_python, args.wheel, services_path, args.wheel_sha256,
                             cancellation=cancellation, summary=summary)
        summary['start'] = 'running lab (run command)'  # services exist: an error from here on is a FAIL
        code = run_lab(binaries, observed, args.runner_python, args.wheel, services_path, args.wheel_sha256)
        summary.update(run_exit_code=code, final_state=inspect(binaries)['state'])
        summary.pop('start', None)
        return code, summary
    except SystemExit as error:
        return 2, {**summary, 'refusal': str(error) if not isinstance(error.code, int) else f'exit {error.code}'}
    except KeyboardInterrupt as error:
        # Outside cycle_lab's own handling (before it started, or a deferred
        # signal raised after its cleanup): the record ends as FAIL.
        return 130, {**summary, 'refusal': describe(error), 'interrupted': True}
    except Exception as error:  # noqa: BLE001 - an unexpected error is recorded, never left behind a runner's PASS
        return 1, {**summary, 'error': 'lab_error: ' + type(error).__name__}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['plan', 'state', 'start', 'reset', 'services', 'prepare-runner', 'run', 'cycle', 'stop', 'wipe'])
    parser.add_argument('--isolated-lab', action='store_true')
    parser.add_argument('--wheel', type=Path, default=None)
    parser.add_argument('--wheel-sha256', default=None)
    parser.add_argument('--venv', type=Path, default=None, help='prepare-runner: environment directory to create')
    parser.add_argument('--runner-python', type=Path, default=None, help='run/cycle: interpreter of the prepared environment')
    parser.add_argument('--services-file', type=Path, default=None, help='run/cycle: write/use this services file (default .deployment-lab/services.json)')
    args = parser.parse_args(argv)
    if args.command in ('start', 'reset', 'run', 'cycle', 'stop', 'wipe', 'prepare-runner') and not args.isolated_lab:
        parser.error('mutation requires --isolated-lab')
    if args.command in ('run', 'cycle') and (args.runner_python is None or args.wheel is None):
        parser.error(f'{args.command} requires --runner-python and --wheel')
    if args.command == 'plan':
        print(json.dumps({'directory': str(STATE.relative_to(REPO)), 'postgres': {'host': 'postgres', 'port': PG_PORT, 'auth': 'scram-sha-256 host logins'},
                          'zabbix': {'host': 'zabbix-web', 'port': WEB_PORT, 'lab': 'lab/zabbix_native_lab.py'},
                          'private': str(PRIVATE), 'link': str(LINK), 'layout': layout(), 'starts_services': False}, indent=2))
        return 0
    if args.command == 'state':
        binaries = Path(os.environ['ACK_LAB_PG_BIN']) if os.environ.get('ACK_LAB_PG_BIN') else None
        print(json.dumps(inspect(binaries)))
        return 0
    if args.command == 'prepare-runner':
        if args.wheel is None or args.venv is None:
            parser.error('prepare-runner requires --wheel and --venv')
        print(json.dumps(prepare_runner(args.wheel.resolve(), args.venv.resolve())))
        return 0
    if args.command in ('run', 'cycle'):
        return guarded(args, parser)
    if not unprivileged_helper_available():
        raise SystemExit('BLOCKED_ENVIRONMENT: the deployment lab requires a non-root OS identity')
    binaries = pg_bin()
    observed = inspect(binaries)
    services_path = args.services_file or STATE / 'services.json'
    if args.command == 'services':
        print(json.dumps({'services_file': str(write_services(binaries, services_path))}))
        return 0
    if args.command == 'stop':
        code, summary = stop_lab(binaries, observed)
        print(json.dumps(summary))
        return code
    if args.command == 'wipe':
        print(json.dumps(wipe_lab(binaries, observed)))
        return 0
    if args.command == 'start':
        print(json.dumps(start_lab(binaries, observed)))
        return 0
    if args.command == 'reset':
        print(json.dumps(reset_lab(binaries, observed)))
        return 0
    parser.error('unhandled command ' + args.command)


if __name__ == '__main__':
    sys.exit(main())
