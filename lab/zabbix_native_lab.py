#!/usr/bin/env python3
"""Disposable native Zabbix 7.0 reference instance for the isolated lab (R27).

No Docker, no system service and no production access: one marked directory
(`.zabbix-lab/` in this checkout) holds a dedicated PostgreSQL 18 cluster with
the Zabbix schema, a `zabbix_server` started from a generated loopback-only
configuration, and a private copy of the packaged frontend served by the PHP
built-in server on the fixed lab API `http://127.0.0.1:58080/api_jsonrpc.php`
(`lab/real_zabbix_safety.py`). Everything runs as an unprivileged user; when
started as root the services run as `nobody`, as `lab/local_runtime.py` does.

The cluster accepts `scram-sha-256` logins only: `initdb` takes the generated
`lab_admin` password from a private file, the `zabbix` role is created with a
second generated password, and both live only in the marked lab directory with
mode 0600 (`pg-password`, `zabbix-db-password`); the generated `zabbix_server`
and frontend configurations carrying the role password are mode 0600 as well.
A cluster whose `pg_hba.conf` still allows unauthenticated access is refused.

Every PID file is verified before a signal: the process must be the one the lab
recorded when it started it (`<component>.identity.json`: PID, `comm`, start
time and uid from `/proc`), run as the service identity and carry the lab's own
arguments. A PID file naming anything else (a reused PID, a process another
checkout started) is classified `foreign_pid`, reported, and never signalled;
the lab does not signal process groups at all.

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
import secrets
import shutil
import signal
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
WEB_HOST = '127.0.0.1'
# The contract suite's fixed lab API is 58080 (lab/real_zabbix_safety.py); the
# deployment-path lab addresses the frontend as zabbix-web:8080 instead.
WEB_PORT = int(os.environ.get('ZABALACK_ZABBIX_WEB_PORT', '58080'))
API_URL = f'http://{WEB_HOST}:{WEB_PORT}/api_jsonrpc.php'
DATABASE = 'zabbix'
DB_USER = 'zabbix'
PID_FILES = {'postgres': PGDATA / 'postmaster.pid', 'zabbix_server': STATE / 'zabbix_server.pid', 'php': STATE / 'php.pid'}
PORTS = {'postgres': PG_PORT, 'zabbix_server': SERVER_PORT, 'php': WEB_PORT}
ADMIN_USER = 'lab_admin'
PASSWORD_FILE_NAMES = {ADMIN_USER: 'pg-password', DB_USER: 'zabbix-db-password'}
ACCEPTED_AUTH_METHODS = ('scram-sha-256', 'reject')
SERVER_CONF = '''# Generated disposable lab configuration; loopback only; never production.
ListenIP=127.0.0.1
ListenPort={server_port}
DBHost=127.0.0.1
DBPort={pg_port}
DBName={database}
DBUser={db_user}
DBPassword={db_password}
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
$DB['PASSWORD'] = '{db_password}';
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


def service_env() -> dict[str, str]:
    """The environment of zabbix_server and the PHP server: PATH, locale and time zone only.

    libcurl inside zabbix_server and PHP honour HTTPS_PROXY and its variants;
    an inherited proxy setting would route the lab's loopback webhook
    deliveries elsewhere. Nothing else from the verifier's environment is
    passed to a service.
    """
    return {'PATH': os.environ.get('PATH', '/usr/sbin:/usr/bin:/sbin:/bin'), 'TZ': 'UTC', 'LANG': 'C.UTF-8'}


def own(path: Path) -> None:
    if os.geteuid() == 0:
        entry = pwd.getpwnam('nobody')
        os.chown(path, entry.pw_uid, entry.pw_gid)


def service_user() -> dict:
    """Popen arguments that run a service as the unprivileged lab identity when the lab runs as root."""
    if os.geteuid() != 0:
        return {}
    entry = pwd.getpwnam('nobody')
    return {'user': entry.pw_uid, 'group': entry.pw_gid, 'extra_groups': []}


def expected_uid() -> int:
    """The uid every lab service runs as."""
    return pwd.getpwnam('nobody').pw_uid if os.geteuid() == 0 else os.geteuid()


def write_private(path: Path, text: str) -> None:
    """Write a file only the lab and its service identity read: mode 0600, owned by the service user when root."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(text)
    os.chmod(path, 0o600)
    own(path)


def password_file(role: str) -> Path:
    return STATE / PASSWORD_FILE_NAMES[role]


def password(role: str, *, create: bool = False) -> str:
    """The generated synthetic password of a lab role, kept only in the marked lab directory with mode 0600."""
    path = password_file(role)
    if not path.is_file():
        if not create:
            raise SystemExit(f'Zabbix lab password file missing: {path}; reset --wipe and start again')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_urlsafe(32) + '\n')
    os.chmod(path, 0o600)
    value = path.read_text().strip()
    if not value or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise SystemExit(f'Zabbix lab password file is not a generated token: {path}')
    return value


# PostgreSQL's pg_isblank (src/backend/libpq/hba.c): only space, TAB and CR separate
# pg_hba.conf tokens. Other characters Python calls whitespace (U+00A0, VT, FF, ...)
# are part of a token for PostgreSQL, and so here.
HBA_BLANKS = ' \t\r'
HBA_HOST_TYPES = ('host', 'hostssl', 'hostnossl', 'hostgssenc', 'hostnogssenc')
IP_MASK = re.compile(r'[0-9]{1,3}(?:\.[0-9]{1,3}){3}|[0-9A-Fa-f:]*:[0-9A-Fa-f:]*')


def hba_fields(line: str) -> list[tuple[str, bool]] | None:
    """PostgreSQL's pg_hba.conf field rules: (text, quoted) per field; None when the line cannot be read safely.

    Fields are separated by whitespace outside double quotes; a quoted part may
    contain whitespace, '#' and '""' (a literal quote); a field may join quoted
    and unquoted parts (comma lists); '#' outside quotes starts a comment. A
    trailing backslash (continuation) or an unterminated quote is refused.
    """
    fields: list[tuple[str, bool]] = []
    text, quoted, in_field, index = '', False, False, 0
    while index < len(line):
        char = line[index]
        if char == '"':
            closing = index + 1
            part = ''
            while True:
                end = line.find('"', closing)
                if end < 0:
                    return None  # unterminated quote
                part += line[closing:end]
                if line[end + 1:end + 2] == '"':
                    part += '"'
                    closing = end + 2
                    continue
                break
            text, quoted, in_field, index = text + part, True, True, end + 1
            continue
        if char == '#':
            break
        if char in HBA_BLANKS:
            if in_field:
                fields.append((text, quoted))
                text, quoted, in_field = '', False, False
        else:
            text, in_field = text + char, True
        index += 1
    if in_field:
        fields.append((text, quoted))
    if fields and not fields[-1][1] and fields[-1][0].endswith('\\'):
        return None  # a continuation line: not read here
    return fields


def hba_method(fields: list[tuple[str, bool]]) -> str | None:
    """The METHOD field of one pg_hba.conf record, by position; None when the record is not understood.

    local  DATABASE USER METHOD [OPTIONS]
    host*  DATABASE USER ADDRESS METHOD [OPTIONS], or ADDRESS as an address and a separate mask
    The connection type and the method must be single unquoted keywords (a quoted keyword loses its
    meaning in PostgreSQL); anything else refuses.
    """
    if not fields or fields[0][1]:
        return None
    kind = fields[0][0]
    if kind == 'local' and len(fields) >= 4:
        position = 3
    elif kind in HBA_HOST_TYPES and len(fields) >= 5:
        address, next_field = fields[3], fields[4]
        separate_mask = not address[1] and '/' not in address[0] and not next_field[1] and IP_MASK.fullmatch(next_field[0])
        position = 5 if separate_mask and len(fields) >= 6 else 4
    else:
        return None  # include directives, unknown connection types, short records
    text, was_quoted = fields[position]
    if was_quoted or ',' in text:
        return None
    # PostgreSQL rejects an option that is not name=value ("authentication option not in
    # name=value format"); such a record is refused here rather than read as its method.
    if any('=' not in option for option, _ in fields[position + 1:]):
        return None
    return text


def verify_authentication(pgdata: Path) -> None:
    """Refuse a cluster whose pg_hba.conf allows a login without a scram password.

    Each record's METHOD field is read by position (a method name elsewhere on
    the line, as a database, user or option, proves nothing); every record
    must use scram-sha-256 or reject, and a record that cannot be read refuses.
    """
    # The raw bytes, split at '\n' only, as PostgreSQL reads the file: read_text() would
    # also end a line at a bare CR, which PostgreSQL treats as a blank inside the record.
    try:
        raw = (pgdata / 'pg_hba.conf').read_bytes()
    except OSError as error:
        raise SystemExit(f'Zabbix lab cluster authentication unreadable: {error}') from error
    try:
        lines = raw.decode('utf-8').split('\n')
    except UnicodeDecodeError as error:
        raise SystemExit('Zabbix lab cluster authentication unreadable: pg_hba.conf is not UTF-8') from error
    for line in lines:
        fields = hba_fields(line)
        if fields == []:
            continue
        method = hba_method(fields) if fields is not None else None
        if method not in ACCEPTED_AUTH_METHODS:
            raise SystemExit('Zabbix lab cluster allows unauthenticated or non-scram local access (pg_hba.conf: '
                             + line.strip() + '); reset --wipe and start again')


def port_listening(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().splitlines()[0].strip())
    except (OSError, ValueError, IndexError):
        return None


def process_identity(pid: int) -> dict | None:
    """What /proc records for a live process: comm, start time, real uid, state and argv; None when absent."""
    proc = Path('/proc') / str(pid)
    try:
        stat = (proc / 'stat').read_text()
        status = (proc / 'status').read_text()
        argv = (proc / 'cmdline').read_bytes().decode(errors='replace').split('\0')
    except OSError:
        return None
    head, _, tail = stat.rpartition(')')
    fields = tail.split()
    uid = [line.split()[1] for line in status.splitlines() if line.startswith('Uid:')]
    if '(' not in head or len(fields) < 20 or not uid:
        return None
    if argv and argv[-1] == '':
        argv.pop()
    return {'pid': pid, 'comm': head.split('(', 1)[1], 'starttime': int(fields[19]), 'uid': int(uid[0]),
            'state': fields[0], 'argv': argv}


def identity_file(name: str) -> Path:
    return STATE / f'{name}.identity.json'


def record_identity(name: str, pid: int) -> dict | None:
    """Remember the process the lab started for a component; only that process is ever signalled."""
    identity = process_identity(pid)
    if identity is None:
        return None
    recorded = {key: identity[key] for key in ('pid', 'comm', 'starttime', 'uid')}
    identity_file(name).write_text(json.dumps(recorded) + '\n')
    return recorded


def expected_argv(name: str) -> list[str]:
    """Arguments the lab passes to a component; a verified process carries every one of them."""
    return {'postgres': ['-D', str(PGDATA)], 'zabbix_server': ['-c', str(STATE / 'zabbix_server.conf')],
            'php': ['-S', f'{WEB_HOST}:{WEB_PORT}', '-t', str(UI)]}[name]


def verified_pid(name: str) -> tuple[dict | None, str]:
    """The component's live identity, or None with why: stopped, unreadable_pid, stale_pid or foreign_pid.

    A PID file is trusted only when the process it names is the one the lab
    recorded at start (same PID, comm, start time and uid), runs as the
    service identity and carries the lab's own arguments. A reused PID or a
    process the lab never recorded is foreign and is never signalled.
    """
    path = PID_FILES[name]
    if not path.exists():
        return None, 'stopped'
    pid = read_pid(path)
    if pid is None:
        return None, 'unreadable_pid'
    identity = process_identity(pid)
    if identity is None or identity['state'] == 'Z':
        return None, 'stale_pid'
    try:
        recorded = json.loads(identity_file(name).read_text())
    except (OSError, ValueError):
        recorded = None
    if not isinstance(recorded, dict) or any(recorded.get(key) != identity[key] for key in ('pid', 'comm', 'starttime', 'uid')):
        return None, 'foreign_pid'
    if identity['uid'] != expected_uid() or not all(argument in identity['argv'] for argument in expected_argv(name)):
        return None, 'foreign_pid'
    return identity, 'running'


def process_gone(identity: dict) -> bool:
    current = process_identity(identity['pid'])
    return current is None or current['state'] == 'Z' or current['starttime'] != identity['starttime']


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
    live, stale, foreign, unreadable = [], [], [], []
    for name in PID_FILES:
        identity, classification = verified_pid(name)
        record['components'][name] = classification
        buckets = {'running': live, 'stale_pid': stale, 'foreign_pid': foreign, 'unreadable_pid': unreadable}
        if classification in buckets:
            buckets[classification].append(name)
    if unreadable:
        record['detail'] = 'unreadable PID file: ' + ','.join(unreadable)
        return record
    busy = [name for name, on in listening.items() if on and name not in live]
    if busy:
        record['detail'] = 'lab port in use by something other than the marked component: ' + ','.join(busy)
        return record
    if live:
        record['state'] = 'running'
        record['detail'] = 'components running: ' + ','.join(live)
    else:
        record['state'] = 'stopped'
        record['detail'] = 'no component running' + ('; stale PID file: ' + ','.join(stale) if stale else '') + (
            '; PID file names a process the lab did not record (never signalled): ' + ','.join(foreign) if foreign else '')
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


def psql_command(paths: dict[str, Path], user: str, database: str, *arguments: str) -> list[str]:
    return [str(paths['pg_bin'] / 'psql'), '-X', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1', '-p', str(PG_PORT),
            '-U', user, '-d', database, *arguments]


def psql_env(user: str) -> dict[str, str]:
    """psql authenticates with the stored role password; it never appears on a command line."""
    return {'PGPASSWORD': password(user), 'PATH': os.environ.get('PATH', '/usr/sbin:/usr/bin:/sbin:/bin')}


def psql(paths: dict[str, Path], database: str, sql: str | None = None, *, user: str = ADMIN_USER,
         stdin: bytes | None = None) -> subprocess.CompletedProcess:
    if stdin is None:
        return run(psql_command(paths, user, database, '-q', '-c', sql or ''), env=psql_env(user))
    return subprocess.run(unprivileged(psql_command(paths, user, database, '-q', '-f', '-')), input=stdin, check=True,
                          capture_output=True, env=psql_env(user))


def query(paths: dict[str, Path], user: str, database: str, sql: str) -> str:
    return run(psql_command(paths, user, database, '-At', '-c', sql), env=psql_env(user)).stdout.strip()


def start_postgres(paths: dict[str, Path]) -> None:
    if not (PGDATA / 'PG_VERSION').exists():
        # initdb reads the generated superuser password from a private file the
        # service user can read; the cluster then accepts scram logins only.
        pwfile = STATE / 'initdb.pwfile'
        write_private(pwfile, password(ADMIN_USER, create=True) + '\n')
        try:
            result = run([str(paths['pg_bin'] / 'initdb'), '-D', str(PGDATA), '-U', ADMIN_USER, f'--pwfile={pwfile}',
                          '--auth-local=scram-sha-256', '--auth-host=scram-sha-256', '--encoding=UTF8', '--locale=C.UTF-8'])
        except subprocess.CalledProcessError as error:
            (STATE / 'initdb.log').write_text((error.stdout or '') + (error.stderr or ''))
            raise SystemExit('Zabbix lab initdb failed:\n' + ((error.stdout or '') + (error.stderr or ''))[-2000:]) from error
        finally:
            pwfile.unlink(missing_ok=True)
        (STATE / 'initdb.log').write_text(result.stdout + result.stderr)
        own(STATE / 'initdb.log')
        with (PGDATA / 'postgresql.conf').open('a') as handle:
            handle.write(f"\n# Isolated Zabbix lab only; loopback, scram logins; never copy authentication to production.\n"
                         f"listen_addresses='127.0.0.1'\nport={PG_PORT}\nunix_socket_directories=''\n"
                         "password_encryption='scram-sha-256'\nmax_connections=100\nshared_buffers='128MB'\n"
                         "logging_collector=off\nlog_statement='none'\n")
    verify_authentication(PGDATA)
    identity, classification = verified_pid('postgres')
    if identity is None:
        if classification == 'foreign_pid':
            raise SystemExit('Zabbix lab postmaster.pid names a process that is not the recorded lab postmaster; '
                             'it is never signalled: stop it yourself or reset --wipe')
        result = run([str(paths['pg_bin'] / 'pg_ctl'), '-D', str(PGDATA), '-l', str(STATE / 'postgres.log'),
                      '-o', '-c unix_socket_directories=', '-w', 'start'], check=False)
        if result.returncode:
            raise SystemExit('Zabbix lab PostgreSQL start failed:\n' + (result.stdout + result.stderr)[-2000:])
        pid = read_pid(PID_FILES['postgres'])
        if pid is None or record_identity('postgres', pid) is None or verified_pid('postgres')[0] is None:
            raise SystemExit('Zabbix lab PostgreSQL started but its postmaster.pid does not name the lab postmaster')
    if not wait_until(lambda: port_listening(PG_PORT), 30):
        raise SystemExit('Zabbix lab PostgreSQL is not listening')


def ensure_database(paths: dict[str, Path]) -> bool:
    """Create role and database when absent and load the schema once; True when loaded now."""
    if query(paths, ADMIN_USER, 'postgres', f"SELECT 1 FROM pg_roles WHERE rolname='{DB_USER}'") != '1':
        # The role password travels on psql's stdin, never on a command line.
        statement = f"CREATE ROLE {DB_USER} LOGIN PASSWORD '{password(DB_USER, create=True)}';\n"
        psql(paths, 'postgres', stdin=statement.encode())
    if query(paths, ADMIN_USER, 'postgres', f"SELECT 1 FROM pg_database WHERE datname='{DATABASE}'") != '1':
        psql(paths, 'postgres', f'CREATE DATABASE {DATABASE} OWNER {DB_USER} ENCODING UTF8')
    if query(paths, DB_USER, DATABASE, "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='config'") == '1':
        return False
    sql = schema_sql(paths['schema'])
    (STATE / 'schema.sha256').write_text(hashlib.sha256(sql).hexdigest() + '\n')
    result = subprocess.run(unprivileged(psql_command(paths, DB_USER, DATABASE, '-q', '-f', '-')), input=sql,
                            capture_output=True, env=psql_env(DB_USER))
    (STATE / 'schema-load.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit('Zabbix lab schema load failed; see .zabbix-lab/schema-load.log')
    # zabbix_server refuses a database whose users table is empty; a bare
    # schema.sql (tables only) is not an input, the packaged server.sql.gz or
    # the schema directory with images.sql and data.sql is.
    users = query(paths, DB_USER, DATABASE, 'SELECT count(*) FROM users')
    if users in ('', '0'):
        raise SystemExit('Zabbix schema loaded without its initial data (users table empty): ZABALACK_ZABBIX_SCHEMA must be the '
                         'packaged server.sql.gz or the directory holding schema.sql, images.sql and data.sql')
    return True


def component_alive(name: str) -> bool:
    return verified_pid(name)[0] is not None


def start_server(paths: dict[str, Path]) -> None:
    if component_alive('zabbix_server'):
        return
    (STATE / 'run').mkdir(exist_ok=True)
    own(STATE / 'run')
    conf = STATE / 'zabbix_server.conf'
    write_private(conf, SERVER_CONF.format(server_port=SERVER_PORT, pg_port=PG_PORT, database=DATABASE, db_user=DB_USER,
                                           db_password=password(DB_USER), state=STATE))
    result = run([str(paths['zabbix_server']), '-c', str(conf)], check=False, env=service_env())
    if result.returncode:
        raise SystemExit('zabbix_server failed to start:\n' + (result.stdout + result.stderr)[-2000:])
    if not wait_until(lambda: port_listening(SERVER_PORT), 60):
        raise SystemExit('zabbix_server is not listening on the lab port; see .zabbix-lab/zabbix_server.log')
    pid = read_pid(PID_FILES['zabbix_server'])
    if pid is None or record_identity('zabbix_server', pid) is None or verified_pid('zabbix_server')[0] is None:
        raise SystemExit('zabbix_server is listening but its PID file does not name the lab server process')


def stage_frontend(paths: dict[str, Path]) -> None:
    if not (UI / 'api_jsonrpc.php').exists():
        # The packaged frontend (zabbix-frontend-php) ships conf/zabbix.conf.php as a
        # link to /etc/zabbix/web/zabbix.conf.php, which exists only once the system
        # frontend is configured. The lab writes its own private configuration there,
        # so that one entry is not copied; every other link is followed as before.
        source_conf = paths['ui'] / 'conf'

        def packaged_configuration(directory: str, names: list[str]) -> list[str]:
            return ['zabbix.conf.php'] if Path(directory) == source_conf and 'zabbix.conf.php' in names else []
        shutil.copytree(paths['ui'], UI, symlinks=False, dirs_exist_ok=True, ignore=packaged_configuration)
    (UI / 'conf').mkdir(exist_ok=True)
    write_private(UI / 'conf/zabbix.conf.php', FRONTEND_CONF.format(pg_port=PG_PORT, database=DATABASE, db_user=DB_USER,
                                                                    db_password=password(DB_USER), server_port=SERVER_PORT))
    if os.geteuid() == 0:
        for path in (UI, *UI.rglob('*')):
            own(path)


def start_frontend(paths: dict[str, Path]) -> None:
    if component_alive('php'):
        return
    log = (STATE / 'php.log').open('a')
    # Started directly as the service identity (no wrapper process), so the
    # PID file names the PHP server itself and its identity can be recorded.
    process = subprocess.Popen([str(paths['php']), *PHP_INI, '-S', f'{WEB_HOST}:{WEB_PORT}', '-t', str(UI)],
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=service_env(), **service_user())
    (STATE / 'php.pid').write_text(f'{process.pid}\n')
    own(STATE / 'php.pid')
    record_identity('php', process.pid)
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


def unverified(classification: str) -> dict:
    outcome = {'status': 'NOT_RUNNING', 'pid_file': classification}
    if classification == 'foreign_pid':
        outcome['detail'] = 'PID file names a process the lab did not record (reused PID or foreign process); not signalled'
    return outcome


def stop_component(name: str, timeout: float) -> dict:
    """SIGTERM the verified component process and wait for it; an unverified PID file is never signalled."""
    identity, classification = verified_pid(name)
    if identity is None:
        return unverified(classification)
    try:
        os.kill(identity['pid'], signal.SIGTERM)
    except ProcessLookupError:
        return {'status': 'STOPPED', 'pid': identity['pid']}
    except PermissionError:
        return {'status': 'FAILED', 'pid': identity['pid'], 'detail': 'not permitted to signal the component'}
    stopped = wait_until(lambda: process_gone(identity), timeout)
    return {'status': 'STOPPED' if stopped else 'FAILED', 'pid': identity['pid']}


def stop_all(paths: dict[str, Path]) -> dict:
    outcomes: dict[str, dict] = {'php': stop_component('php', 15)}
    if outcomes['php']['status'] != 'FAILED':
        PID_FILES['php'].unlink(missing_ok=True)
        identity_file('php').unlink(missing_ok=True)
    outcomes['zabbix_server'] = stop_component('zabbix_server', 60)
    if outcomes['zabbix_server']['status'] == 'STOPPED':
        identity_file('zabbix_server').unlink(missing_ok=True)
    identity, classification = verified_pid('postgres')
    if identity is not None:
        result = run([str(paths['pg_bin'] / 'pg_ctl'), '-D', str(PGDATA), '-m', 'fast', '-w', 'stop'], check=False)
        outcomes['postgres'] = {'status': 'STOPPED' if result.returncode == 0 else 'FAILED', 'exit_code': result.returncode,
                                'pid': identity['pid']}
        if result.returncode == 0:
            identity_file('postgres').unlink(missing_ok=True)
    else:
        outcomes['postgres'] = unverified(classification)
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
                          'postgres_auth': 'scram-sha-256 only; generated passwords stay in the lab directory (mode 0600)',
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
