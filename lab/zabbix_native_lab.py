#!/usr/bin/env python3
"""Disposable native Zabbix 7.0 reference instance for the isolated lab (R27).

No Docker, no system service and no production access: one marked directory
(`.zabbix-lab/` in this checkout) holds a dedicated PostgreSQL 18 cluster with
the Zabbix schema, a `zabbix_server` started from a generated loopback-only
configuration, and a private copy of the packaged frontend served by the PHP
built-in server on the fixed lab API `http://127.0.0.1:58080/api_jsonrpc.php`
(`lab/real_zabbix_safety.py`). Everything runs as an unprivileged user; when
started as root the services run as `nobody`, as `lab/local_runtime.py` does.

Inputs name the installed artifacts; the pin in `lab/zabbix_reference.json`
is enforced against the running frontend's `apiinfo.version`:

  ACK_LAB_PG_BIN                PostgreSQL 18 binaries (pg_ctl, initdb, psql, postgres)
  ZABALACK_ZABBIX_SERVER        zabbix_server executable (package: /usr/sbin/zabbix_server)
  ZABALACK_ZABBIX_UI            frontend directory (package: /usr/share/zabbix)
  ZABALACK_ZABBIX_SCHEMA        PostgreSQL schema: the packaged server.sql.gz, or a
                                directory with schema.sql, images.sql and data.sql
  ZABALACK_PHP                  PHP CLI executable (default: php on PATH)

Commands: `plan` (print the layout, start nothing), `state` (read-only
classification), `start`, `stop`, `reset` and `status`. Mutation requires
`--isolated-lab`. `start` refuses an unmarked or ambiguous directory, `stop`
exits 0 only when every component is observed stopped, and `reset` (a fresh
database from the schema, or `--wipe` for the whole directory) refuses anything
but a marked, stopped lab. This is a loopback lab convenience; the PHP built-in
server is not an official Zabbix deployment configuration.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib import request

REPO = Path(__file__).resolve().parents[1]
STATE = REPO / '.zabbix-lab'
MARKER = STATE / 'ISOLATED_ZABBIX_LAB'
PGDATA = STATE / 'pgdata'
UI = STATE / 'ui'
PG_PORT = 55434
SERVER_PORT = 20051  # zabbix_server accepts 1024..32767; the default 10051 is left to any host service
WEB_HOST, WEB_PORT = '127.0.0.1', 58080
API_URL = f'http://{WEB_HOST}:{WEB_PORT}/api_jsonrpc.php'
DATABASE = 'zabbix'
DB_USER = 'zabbix'
PID_FILES = {'postgres': PGDATA / 'postmaster.pid', 'zabbix_server': STATE / 'zabbix_server.pid', 'php': STATE / 'php.pid'}
PORTS = {'postgres': PG_PORT, 'zabbix_server': SERVER_PORT, 'php': WEB_PORT}
SERVER_CONF = '''# Generated disposable lab configuration; loopback only; never production.
ListenIP=127.0.0.1
ListenPort={server_port}
DBHost=127.0.0.1
DBPort={pg_port}
DBName={database}
DBUser={db_user}
LogFile={state}/zabbix_server.log
LogFileSize=10
PidFile={state}/zabbix_server.pid
SocketDir={state}/run
CacheUpdateFrequency=10
StartPollers=2
StartPollersUnreachable=1
StartTrappers=2
StartPingers=0
StartDiscoverers=0
StartHTTPPollers=0
StartPreprocessors=1
StartHistoryPollers=1
StartLLDProcessors=1
StartTimers=1
StartEscalators=1
StartAlerters=1
Timeout=4
AllowRoot=0
'''
FRONTEND_CONF = '''<?php
// Generated disposable lab configuration; loopback only; never production.
$DB['TYPE']     = 'POSTGRESQL';
$DB['SERVER']   = '127.0.0.1';
$DB['PORT']     = '{pg_port}';
$DB['DATABASE'] = '{database}';
$DB['USER']     = '{db_user}';
$DB['PASSWORD'] = '';
$DB['SCHEMA']   = '';
$DB['ENCRYPTION'] = false;
$DB['DOUBLE_IEEE754'] = true;
$ZBX_SERVER      = '127.0.0.1';
$ZBX_SERVER_PORT = '{server_port}';
$ZBX_SERVER_NAME = 'zabalack isolated lab';
$IMAGE_FORMAT_DEFAULT = IMAGE_FORMAT_PNG;
'''
PHP_INI = ['-d', 'memory_limit=128M', '-d', 'post_max_size=16M', '-d', 'upload_max_filesize=2M',
           '-d', 'max_execution_time=300', '-d', 'max_input_time=300', '-d', 'session.auto_start=0',
           '-d', 'arg_separator.output=&', '-d', 'date.timezone=UTC']


def reference() -> dict:
    return json.loads((REPO / 'lab/zabbix_reference.json').read_text())['selected']


def inputs() -> dict[str, Path]:
    values = {
        'pg_bin': os.environ.get('ACK_LAB_PG_BIN', ''),
        'zabbix_server': os.environ.get('ZABALACK_ZABBIX_SERVER', '/usr/sbin/zabbix_server'),
        'ui': os.environ.get('ZABALACK_ZABBIX_UI', '/usr/share/zabbix'),
        'schema': os.environ.get('ZABALACK_ZABBIX_SCHEMA', '/usr/share/zabbix-sql-scripts/postgresql/server.sql.gz'),
        'php': os.environ.get('ZABALACK_PHP') or shutil.which('php') or '',
    }
    missing = [name for name, value in values.items() if not value or not Path(value).exists()]
    if missing:
        raise SystemExit('Zabbix lab inputs missing or absent: ' + ', '.join(missing) + '; the native Zabbix lab is NOT_RUN')
    paths = {name: Path(value) for name, value in values.items()}
    for name in ('pg_ctl', 'initdb', 'psql', 'postgres'):
        if not (paths['pg_bin'] / name).is_file():
            raise SystemExit(f'ACK_LAB_PG_BIN lacks {name}')
    return paths


def unprivileged(command: list[str]) -> list[str]:
    return ['runuser', '-u', 'nobody', '--', *command] if os.geteuid() == 0 else command


def run(command: list[str], *, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(unprivileged(command), check=check, text=True, capture_output=True, **kwargs)


def own(path: Path) -> None:
    if os.geteuid() == 0:
        entry = pwd.getpwnam('nobody')
        os.chown(path, entry.pw_uid, entry.pw_gid)


def port_listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        return None


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def inspect() -> dict:
    """Classify the lab without touching it: absent, stopped, running, unmarked, ambiguous."""
    record: dict = {'state': 'ambiguous', 'marker': MARKER.exists(), 'components': {}, 'api_url': API_URL}
    listening = {name: port_listening(port) for name, port in PORTS.items()}
    record['ports_listening'] = listening
    if not STATE.exists():
        record['state'] = 'ambiguous' if any(listening.values()) else 'absent'
        record['detail'] = 'lab port in use without a lab directory' if any(listening.values()) else 'no lab directory'
        return record
    if not record['marker']:
        record['state'] = 'unmarked'
        record['detail'] = 'existing .zabbix-lab directory without the isolated-lab marker'
        return record
    live, stale, unreadable = [], [], []
    for name, path in PID_FILES.items():
        if not path.exists():
            record['components'][name] = 'stopped'
            continue
        pid = read_pid(path)
        if pid is None:
            unreadable.append(name)
            record['components'][name] = 'unreadable_pid'
        elif pid_alive(pid):
            live.append(name)
            record['components'][name] = 'running'
        else:
            stale.append(name)
            record['components'][name] = 'stale_pid'
    if unreadable:
        record['detail'] = 'unreadable PID file: ' + ','.join(unreadable)
        return record
    foreign = [name for name, on in listening.items() if on and name not in live]
    if foreign:
        record['detail'] = 'lab port in use by something other than the marked component: ' + ','.join(foreign)
        return record
    if live:
        record['state'] = 'running'
        record['detail'] = 'components running: ' + ','.join(live)
    else:
        record['state'] = 'stopped'
        record['detail'] = 'no component running' + ('; stale PID file: ' + ','.join(stale) if stale else '')
    return record


def wait_until(predicate, timeout: float, interval: float = 0.5) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def api_version() -> str | None:
    body = json.dumps({'jsonrpc': '2.0', 'method': 'apiinfo.version', 'params': {}, 'id': 1}).encode()
    try:
        with request.urlopen(request.Request(API_URL, data=body, headers={'Content-Type': 'application/json-rpc'}), timeout=5) as response:
            envelope = json.loads(response.read().decode())
    except (OSError, ValueError):
        return None
    result = envelope.get('result') if isinstance(envelope, dict) else None
    return result if isinstance(result, str) else None


def wait_for_api(timeout: float) -> str | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        version = api_version()
        if version is not None:
            return version
        time.sleep(1)
    return api_version()


def schema_sql(schema: Path) -> bytes:
    if schema.is_dir():
        return b''.join((schema / name).read_bytes() for name in ('schema.sql', 'images.sql', 'data.sql'))
    if schema.suffix == '.gz':
        return gzip.decompress(schema.read_bytes())
    return schema.read_bytes()


def psql(paths: dict[str, Path], database: str, sql: str, *, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    command = [str(paths['pg_bin'] / 'psql'), '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1', '-p', str(PG_PORT),
               '-U', 'lab_admin', '-d', database]
    if stdin is None:
        return run([*command, '-c', sql])
    return subprocess.run(unprivileged([*command, '-f', '-']), input=stdin, check=True, capture_output=True)


def start_postgres(paths: dict[str, Path]) -> None:
    if not (PGDATA / 'PG_VERSION').exists():
        try:
            result = run([str(paths['pg_bin'] / 'initdb'), '-D', str(PGDATA), '-U', 'lab_admin', '--auth-local=trust',
                          '--auth-host=trust', '--encoding=UTF8', '--locale=C.UTF-8'])
        except subprocess.CalledProcessError as error:
            (STATE / 'initdb.log').write_text((error.stdout or '') + (error.stderr or ''))
            raise SystemExit('Zabbix lab initdb failed:\n' + ((error.stdout or '') + (error.stderr or ''))[-2000:]) from error
        (STATE / 'initdb.log').write_text(result.stdout + result.stderr)
        own(STATE / 'initdb.log')
        with (PGDATA / 'postgresql.conf').open('a') as handle:
            handle.write(f"\n# Isolated Zabbix lab only; never copy authentication to production.\nlisten_addresses='127.0.0.1'\n"
                         f"port={PG_PORT}\nunix_socket_directories=''\nmax_connections=100\nshared_buffers='128MB'\n"
                         "logging_collector=off\nlog_statement='none'\n")
    if run([str(paths['pg_bin'] / 'pg_ctl'), '-D', str(PGDATA), 'status'], check=False).returncode != 0:
        result = run([str(paths['pg_bin'] / 'pg_ctl'), '-D', str(PGDATA), '-l', str(STATE / 'postgres.log'),
                      '-o', '-c unix_socket_directories=', '-w', 'start'], check=False)
        if result.returncode:
            raise SystemExit('Zabbix lab PostgreSQL start failed:\n' + (result.stdout + result.stderr)[-2000:])
    if not wait_until(lambda: port_listening(PG_PORT), 30):
        raise SystemExit('Zabbix lab PostgreSQL is not listening')


def ensure_database(paths: dict[str, Path]) -> bool:
    """Create role and database when absent and load the schema once; True when loaded now."""
    roles = run([str(paths['pg_bin'] / 'psql'), '-X', '-At', '-h', '127.0.0.1', '-p', str(PG_PORT), '-U', 'lab_admin', '-d', 'postgres',
                 '-c', f"SELECT 1 FROM pg_roles WHERE rolname='{DB_USER}'"]).stdout.strip()
    if roles != '1':
        psql(paths, 'postgres', f'CREATE ROLE {DB_USER} LOGIN')
    databases = run([str(paths['pg_bin'] / 'psql'), '-X', '-At', '-h', '127.0.0.1', '-p', str(PG_PORT), '-U', 'lab_admin', '-d', 'postgres',
                     '-c', f"SELECT 1 FROM pg_database WHERE datname='{DATABASE}'"]).stdout.strip()
    if databases != '1':
        psql(paths, 'postgres', f'CREATE DATABASE {DATABASE} OWNER {DB_USER} ENCODING UTF8')
    loaded = run([str(paths['pg_bin'] / 'psql'), '-X', '-At', '-h', '127.0.0.1', '-p', str(PG_PORT), '-U', DB_USER, '-d', DATABASE,
                  '-c', "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='config'"]).stdout.strip()
    if loaded == '1':
        return False
    sql = schema_sql(paths['schema'])
    (STATE / 'schema.sha256').write_text(hashlib.sha256(sql).hexdigest() + '\n')
    command = [str(paths['pg_bin'] / 'psql'), '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1', '-p', str(PG_PORT),
               '-U', DB_USER, '-d', DATABASE, '-f', '-']
    result = subprocess.run(unprivileged(command), input=sql, capture_output=True)
    (STATE / 'schema-load.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit('Zabbix lab schema load failed; see .zabbix-lab/schema-load.log')
    return True


def component_alive(name: str) -> bool:
    pid = read_pid(PID_FILES[name])
    return pid is not None and pid_alive(pid)


def start_server(paths: dict[str, Path]) -> None:
    if component_alive('zabbix_server'):
        return
    (STATE / 'run').mkdir(exist_ok=True)
    own(STATE / 'run')
    conf = STATE / 'zabbix_server.conf'
    conf.write_text(SERVER_CONF.format(server_port=SERVER_PORT, pg_port=PG_PORT, database=DATABASE, db_user=DB_USER, state=STATE))
    own(conf)
    result = run([str(paths['zabbix_server']), '-c', str(conf)], check=False)
    if result.returncode:
        raise SystemExit('zabbix_server failed to start:\n' + (result.stdout + result.stderr)[-2000:])
    if not wait_until(lambda: port_listening(SERVER_PORT), 60):
        raise SystemExit('zabbix_server is not listening on the lab port; see .zabbix-lab/zabbix_server.log')


def stage_frontend(paths: dict[str, Path]) -> None:
    if not (UI / 'api_jsonrpc.php').exists():
        shutil.copytree(paths['ui'], UI, symlinks=False, dirs_exist_ok=True)
    (UI / 'conf').mkdir(exist_ok=True)
    (UI / 'conf/zabbix.conf.php').write_text(FRONTEND_CONF.format(pg_port=PG_PORT, database=DATABASE, db_user=DB_USER, server_port=SERVER_PORT))
    if os.geteuid() == 0:
        for path in (UI, *UI.rglob('*')):
            own(path)


def start_frontend(paths: dict[str, Path]) -> None:
    if component_alive('php'):
        return
    log = (STATE / 'php.log').open('a')
    process = subprocess.Popen(unprivileged([str(paths['php']), *PHP_INI, '-S', f'{WEB_HOST}:{WEB_PORT}', '-t', str(UI)]),
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (STATE / 'php.pid').write_text(f'{process.pid}\n')
    own(STATE / 'php.pid')
    if not wait_until(lambda: port_listening(WEB_PORT), 30):
        process.kill()
        raise SystemExit('PHP built-in server is not listening; see .zabbix-lab/php.log')


def package_identity(binary: Path) -> dict:
    real = binary.resolve()
    if real.is_file():
        with real.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    else:
        digest = None
    if shutil.which('dpkg') and shutil.which('dpkg-query'):
        owner = subprocess.run(['dpkg', '-S', str(real)], capture_output=True, text=True, timeout=30)
        if owner.returncode == 0 and ': ' in owner.stdout:
            package = owner.stdout.strip().splitlines()[0].rsplit(': ', 1)[0].split(':')[0].strip()
            query = subprocess.run(['dpkg-query', '-W', '-f', '${Package}\t${Version}\t${Architecture}\t${Status}', package],
                                   capture_output=True, text=True, timeout=30)
            if query.returncode == 0 and query.stdout.count('\t') == 3:
                name, version, architecture, status = query.stdout.split('\t')
                return {'kind': 'dpkg', 'package': name, 'version': version, 'architecture': architecture,
                        'status': status.strip(), 'sha256': digest}
    return {'kind': 'unpackaged', 'sha256': digest}


def status_record(paths: dict[str, Path], observed_api: str | None) -> dict:
    server_version = run([str(paths['zabbix_server']), '-V'], check=False).stdout.strip().splitlines()[:1]
    defines = (UI / 'include/defines.inc.php').read_text() if (UI / 'include/defines.inc.php').exists() else ''
    ui_version = re.search(r"define\('ZABBIX_VERSION',\s*'([^']+)'\)", defines)
    postgres = run([str(paths['pg_bin'] / 'postgres'), '--version'], check=False).stdout.strip()
    php = run([str(paths['php']), '-v'], check=False).stdout.strip().splitlines()[:1]
    schema = paths['schema']
    record = {
        'reference': reference()['version'], 'observed_api_version': observed_api, 'api_url': API_URL,
        'zabbix_server': {'version_line': server_version[0] if server_version else None, 'identity': package_identity(paths['zabbix_server'])},
        'frontend': {'ZABBIX_VERSION': ui_version.group(1) if ui_version else None, 'identity': package_identity(paths['ui'] / 'api_jsonrpc.php'),
                     'served_by': 'PHP built-in server (loopback lab convenience, not an official deployment configuration)'},
        'schema': {'source': schema.name, 'identity': package_identity(schema if schema.is_file() else schema / 'schema.sql'),
                   'loaded_sha256': (STATE / 'schema.sha256').read_text().strip() if (STATE / 'schema.sha256').exists() else None},
        'postgres': {'version': postgres, 'identity': package_identity(paths['pg_bin'] / 'postgres'), 'port': PG_PORT},
        'php': {'version_line': php[0] if php else None, 'identity': package_identity(paths['php'])},
    }
    matches = observed_api == record['reference'] and (ui_version.group(1) if ui_version else None) == record['reference']
    record['reference_match'] = bool(matches)
    return record


def stop_all(paths: dict[str, Path]) -> dict:
    outcomes: dict[str, dict] = {}
    pid = read_pid(STATE / 'php.pid')
    if pid is not None and pid_alive(pid):
        try:
            os.killpg(pid, 15)
        except (ProcessLookupError, PermissionError):
            os.kill(pid, 15)
        outcomes['php'] = {'status': 'STOPPED' if wait_until(lambda: not pid_alive(pid), 15) else 'FAILED'}
    else:
        outcomes['php'] = {'status': 'NOT_RUNNING'}
    if outcomes['php']['status'] != 'FAILED':
        (STATE / 'php.pid').unlink(missing_ok=True)
    pid = read_pid(STATE / 'zabbix_server.pid')
    if pid is not None and pid_alive(pid):
        os.kill(pid, 15)
        outcomes['zabbix_server'] = {'status': 'STOPPED' if wait_until(lambda: not pid_alive(pid), 60) else 'FAILED'}
    else:
        outcomes['zabbix_server'] = {'status': 'NOT_RUNNING'}
    if (PGDATA / 'PG_VERSION').exists() and run([str(paths['pg_bin'] / 'pg_ctl'), '-D', str(PGDATA), 'status'], check=False).returncode == 0:
        result = run([str(paths['pg_bin'] / 'pg_ctl'), '-D', str(PGDATA), '-m', 'fast', '-w', 'stop'], check=False)
        outcomes['postgres'] = {'status': 'STOPPED' if result.returncode == 0 else 'FAILED', 'exit_code': result.returncode}
    else:
        outcomes['postgres'] = {'status': 'NOT_RUNNING'}
    return outcomes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['plan', 'state', 'start', 'stop', 'reset', 'status'])
    parser.add_argument('--isolated-lab', action='store_true')
    parser.add_argument('--wipe', action='store_true', help='reset: remove the whole lab directory instead of reloading the database')
    args = parser.parse_args(argv)
    if args.command in ('start', 'stop', 'reset') and not args.isolated_lab:
        parser.error('mutation requires --isolated-lab')
    if args.command == 'plan':
        print(json.dumps({'directory': str(STATE.relative_to(REPO)), 'postgres_port': PG_PORT, 'server_port': SERVER_PORT,
                          'api_url': API_URL, 'reference': reference()['version'], 'starts_services': False}, indent=2))
        return 0
    if args.command == 'state':
        print(json.dumps(inspect()))
        return 0
    if os.geteuid() == 0 and subprocess.run(['runuser', '-u', 'nobody', '--', 'id', '-u'], capture_output=True).returncode:
        raise SystemExit('BLOCKED_ENVIRONMENT: the Zabbix lab requires a non-root OS identity')
    paths = inputs()
    observed = inspect()
    if args.command == 'status':
        record = status_record(paths, api_version()) if observed['state'] == 'running' else {'reference': reference()['version']}
        print(json.dumps({'state': observed, 'services': record}, indent=2))
        return 0
    if args.command == 'stop':
        if observed['state'] in ('unmarked', 'ambiguous'):
            raise SystemExit('Refusing to stop: ' + observed['detail'])
        if observed['state'] == 'absent':
            print(json.dumps({'state': 'absent'}))
            return 0
        outcomes = stop_all(paths)
        after = inspect()
        print(json.dumps({'cleanup': outcomes, 'state': after}))
        return 0 if after['state'] == 'stopped' and all(item['status'] != 'FAILED' for item in outcomes.values()) else 5
    if args.command == 'reset':
        if observed['state'] != 'stopped':
            raise SystemExit(f"Refusing reset from state {observed['state']}: {observed['detail']}")
        if args.wipe:
            shutil.rmtree(STATE)
            print(json.dumps({'reset': 'wiped', 'state': inspect()['state']}))
            return 0
        start_postgres(paths)
        psql(paths, 'postgres', f'DROP DATABASE IF EXISTS {DATABASE}')
        loaded = ensure_database(paths)
        run([str(paths['pg_bin'] / 'pg_ctl'), '-D', str(PGDATA), '-m', 'fast', '-w', 'stop'])
        print(json.dumps({'reset': 'database reloaded from schema', 'schema_loaded': loaded, 'state': inspect()['state']}))
        return 0
    # start
    if observed['state'] in ('unmarked', 'ambiguous'):
        raise SystemExit('Refusing to start: ' + observed['detail'])
    # A marked lab that is stopped, or running only in part, continues from the
    # components it lacks; each step skips what is already up.
    STATE.mkdir(mode=0o700, exist_ok=True)
    MARKER.write_text(f'Disposable zabalack Zabbix lab. Loopback API {API_URL}.\n')
    own(STATE)
    own(MARKER)
    start_postgres(paths)
    ensure_database(paths)
    start_server(paths)
    stage_frontend(paths)
    start_frontend(paths)
    observed_api = wait_for_api(60)
    if observed_api is None:
        raise SystemExit('frontend API did not answer apiinfo.version; see .zabbix-lab/php.log')
    record = status_record(paths, observed_api)
    (STATE / 'status.json').write_text(json.dumps(record, indent=2) + '\n')
    if not record['reference_match']:
        raise SystemExit(f"running Zabbix {observed_api!r} is not the pinned reference {record['reference']!r}; the lab is not the reference")
    print(json.dumps({'state': inspect()['state'], 'api_url': API_URL, 'observed_api_version': observed_api,
                      'reference': record['reference'], 'production_gate': 'NOT_VERIFIED'}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
