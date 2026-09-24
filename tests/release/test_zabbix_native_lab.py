"""lab/zabbix_native_lab.py: state classification, refusals, generated configuration, scram authentication
and PID identity verification (R27, R29).

Every service and port is mocked; no PostgreSQL, zabbix_server or PHP starts. The PID identity tests
use real throwaway sleeper processes so `/proc` is read for real.
"""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
SLEEPER = 'import time\ntime.sleep(120)\n'


@pytest.fixture
def lab(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('review_zabbix_native_lab', ROOT / 'lab/zabbix_native_lab.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = tmp_path / '.zabbix-lab'
    for key, value in [('REPO', tmp_path), ('STATE', state), ('MARKER', state / 'ISOLATED_ZABBIX_LAB'),
                       ('PGDATA', state / 'pgdata'), ('UI', state / 'ui')]:
        monkeypatch.setattr(module, key, value)
    monkeypatch.setattr(module, 'PID_FILES', {'postgres': state / 'pgdata/postmaster.pid',
                                               'zabbix_server': state / 'zabbix_server.pid', 'php': state / 'php.pid'})
    monkeypatch.setattr(module, 'reference', lambda: {'version': '7.0.31'})
    monkeypatch.setattr(module, 'port_listening', lambda port: False)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1001)
    return module


def marked(lab, *, initialised=True):
    lab.STATE.mkdir()
    lab.MARKER.write_text('marker\n')
    lab.password('lab_admin', create=True)
    lab.password('zabbix', create=True)
    if initialised:
        lab.PGDATA.mkdir()
        (lab.PGDATA / 'PG_VERSION').write_text('18\n')
        (lab.PGDATA / 'pg_hba.conf').write_text('# lab\nlocal all all scram-sha-256\nhost all all 127.0.0.1/32 scram-sha-256\n')


def tree(root):
    return sorted((str(path.relative_to(root)), path.read_bytes() if path.is_file() else None) for path in root.rglob('*'))


def live(lab, monkeypatch, name, pid=None):
    """Make `name` a verified running component: PID file, recorded identity, and the lab's expectations met."""
    pid = os.getpid() if pid is None else pid
    lab.PID_FILES[name].write_text(f'{pid}\n')
    assert lab.record_identity(name, pid) is not None
    monkeypatch.setattr(lab, 'expected_uid', lambda: lab.process_identity(pid)['uid'])
    monkeypatch.setattr(lab, 'expected_argv', lambda component: [])


@pytest.fixture
def sleeper():
    """A real process in its own session, with the lab's PHP arguments on its command line."""
    started = []

    def start(*extra):
        process = subprocess.Popen([sys.executable, '-c', SLEEPER, *extra], start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        started.append(process)
        return process
    yield start
    for process in started:
        if process.poll() is None:
            process.kill()
        process.wait(10)


@pytest.mark.parametrize('layout,pids,listening,expected', [
    ('none', {}, {}, 'absent'),
    ('none', {}, {'php': True}, 'ambiguous'),
    ('unmarked', {}, {}, 'unmarked'),
    ('marked', {}, {}, 'stopped'),
    ('marked', {'zabbix_server': 'alive'}, {'zabbix_server': True}, 'running'),
    ('marked', {'postgres': 'alive', 'php': 'alive'}, {'postgres': True, 'php': True}, 'running'),
    ('marked', {'zabbix_server': 'dead'}, {}, 'stopped'),
    ('marked', {'zabbix_server': 'dead'}, {'zabbix_server': True}, 'ambiguous'),
    ('marked', {'php': 'garbage'}, {}, 'ambiguous'),
    ('marked', {}, {'postgres': True}, 'ambiguous'),
    ('marked', {'php': 'reused'}, {}, 'stopped'),
    ('marked', {'php': 'reused'}, {'php': True}, 'ambiguous'),
    ('marked', {'zabbix_server': 'unrecorded'}, {'zabbix_server': True}, 'ambiguous'),
], ids=['absent', 'port-without-lab', 'unmarked', 'stopped', 'server-running', 'two-running', 'stale-pid',
        'stale-pid-port-busy', 'unreadable-pid', 'foreign-listener', 'reused-pid', 'reused-pid-port-busy', 'unrecorded-pid'])
def test_inspect_classifies_the_lab_without_touching_it(lab, tmp_path, monkeypatch, sleeper, layout, pids, listening, expected):
    if layout == 'unmarked':
        lab.STATE.mkdir()
        (lab.STATE / 'stranger').write_text('not ours')
    elif layout == 'marked':
        marked(lab)
    foreign = []
    for name, kind in pids.items():
        if kind == 'alive':
            live(lab, monkeypatch, name)
        elif kind == 'reused':
            foreign.append(sleeper())                      # a live process the lab never recorded holds the PID
            lab.PID_FILES[name].write_text(f'{foreign[-1].pid}\n')
        elif kind == 'unrecorded':
            lab.PID_FILES[name].write_text(f'{os.getpid()}\n')  # live, but no recorded identity
        else:
            lab.PID_FILES[name].write_text({'dead': '999999\n', 'garbage': 'x\n'}[kind])
    monkeypatch.setattr(lab, 'port_listening', lambda port: listening.get(next(n for n, p in lab.PORTS.items() if p == port), False))
    before = tree(tmp_path)
    observed = lab.inspect()
    assert observed['state'] == expected, observed
    assert observed['api_url'] == 'http://127.0.0.1:58080/api_jsonrpc.php'
    assert tree(tmp_path) == before
    if expected == 'running':
        assert all(observed['components'][name] == 'running' for name in pids)
    for name, kind in pids.items():
        if kind in ('reused', 'unrecorded'):
            assert observed['components'][name] == 'foreign_pid'
    for process in foreign:
        assert process.poll() is None, 'inspect must not signal a foreign process'


def test_state_and_plan_need_no_inputs_and_start_nothing(lab, monkeypatch, capsys):
    monkeypatch.setattr(lab, 'inputs', lambda: pytest.fail('inputs must not be resolved for plan/state'))
    assert lab.main(['plan']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['starts_services'] is False and plan['reference'] == '7.0.31' and plan['api_url'] == lab.API_URL
    assert lab.main(['state']) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'absent'


@pytest.mark.parametrize('command', ['start', 'stop', 'reset'])
def test_mutation_requires_the_isolated_lab_flag(lab, command):
    with pytest.raises(SystemExit):
        lab.main([command])


@pytest.fixture
def paths(tmp_path):
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    for name in ('pg_ctl', 'initdb', 'psql', 'postgres'):
        (binaries / name).write_text('')
    server = tmp_path / 'zabbix_server'
    server.write_text('')
    ui = tmp_path / 'ui'
    (ui / 'include').mkdir(parents=True)
    (ui / 'api_jsonrpc.php').write_text('<?php')
    (ui / 'include/defines.inc.php').write_text("<?php\ndefine('ZABBIX_VERSION',\t\t'7.0.31');\n")
    schema = tmp_path / 'server.sql.gz'
    schema.write_bytes(b'')
    php = tmp_path / 'php'
    php.write_text('')
    return {'pg_bin': binaries, 'zabbix_server': server, 'ui': ui, 'schema': schema, 'php': php}


@pytest.mark.parametrize('state', ['unmarked', 'ambiguous'])
def test_start_and_stop_refuse_unowned_or_ambiguous_state(lab, paths, monkeypatch, state):
    lab.STATE.mkdir()
    if state == 'unmarked':
        (lab.STATE / 'stranger').write_text('not ours')
    else:
        lab.MARKER.write_text('marker\n')
        (lab.STATE / 'php.pid').write_text('garbage\n')
    monkeypatch.setattr(lab, 'inputs', lambda: paths)
    calls = []
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0, '', ''))
    monkeypatch.setattr(lab.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('no service may start'))
    before = tree(lab.STATE)
    for command in ('start', 'stop'):
        with pytest.raises(SystemExit, match='Refusing'):
            lab.main([command, '--isolated-lab'])
    with pytest.raises(SystemExit, match='Refusing reset'):
        lab.main(['reset', '--isolated-lab'])
    assert tree(lab.STATE) == before and calls == []


def test_reset_refuses_a_running_lab_and_wipes_only_a_stopped_one(lab, paths, monkeypatch, capsys):
    marked(lab)
    live(lab, monkeypatch, 'zabbix_server')
    monkeypatch.setattr(lab, 'port_listening', lambda port: port == lab.SERVER_PORT)
    monkeypatch.setattr(lab, 'inputs', lambda: paths)
    with pytest.raises(SystemExit, match='Refusing reset from state running'):
        lab.main(['reset', '--isolated-lab', '--wipe'])
    assert lab.MARKER.exists()
    (lab.STATE / 'zabbix_server.pid').unlink()
    monkeypatch.setattr(lab, 'port_listening', lambda port: False)
    assert lab.main(['reset', '--isolated-lab', '--wipe']) == 0
    assert not lab.STATE.exists()
    assert json.loads(capsys.readouterr().out) == {'reset': 'wiped', 'state': 'absent'}


def test_stop_exit_code_reflects_the_observed_state_after_cleanup(lab, paths, monkeypatch, capsys):
    marked(lab)
    (lab.STATE / 'php.pid').write_text('999999\n')  # stale
    (lab.STATE / 'zabbix_server.pid').write_text('999998\n')
    monkeypatch.setattr(lab, 'inputs', lambda: paths)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 3 if command[-1] == 'status' else 0, '', '')
    monkeypatch.setattr(lab, 'run', run)
    assert lab.main(['stop', '--isolated-lab']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['cleanup'] == {'php': {'status': 'NOT_RUNNING', 'pid_file': 'stale_pid'},
                              'zabbix_server': {'status': 'NOT_RUNNING', 'pid_file': 'stale_pid'},
                              'postgres': {'status': 'NOT_RUNNING', 'pid_file': 'stopped'}} and out['state']['state'] == 'stopped'
    assert not (lab.STATE / 'php.pid').exists() and calls == []
    # The lab port still held after the stop leaves exit 5.
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '', ''))
    monkeypatch.setattr(lab, 'port_listening', lambda port: port == lab.PG_PORT)
    live(lab, monkeypatch, 'postgres')
    assert lab.main(['stop', '--isolated-lab']) == 5


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_generated_configurations_are_loopback_only_private_and_keep_the_database_policy_default(lab, paths, monkeypatch):
    marked(lab)
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '', ''))
    monkeypatch.setattr(lab, 'port_listening', lambda port: True)
    # zabbix_server writes its PID file when it starts; the lab then records that process.
    lab.PID_FILES['zabbix_server'].write_text(f'{os.getpid()}\n')
    monkeypatch.setattr(lab, 'expected_uid', lambda: os.getuid())
    monkeypatch.setattr(lab, 'expected_argv', lambda component: [])
    lab.start_server(paths)
    conf = (lab.STATE / 'zabbix_server.conf').read_text()
    assert 'ListenIP=127.0.0.1' in conf and f'ListenPort={lab.SERVER_PORT}' in conf and 'DBHost=127.0.0.1' in conf
    assert f'DBPort={lab.PG_PORT}' in conf and 'AllowRoot=0' in conf and 'AllowUnsupportedDBVersions' not in conf
    db_password = lab.password('zabbix')
    assert f'DBPassword={db_password}\n' in conf and mode(lab.STATE / 'zabbix_server.conf') == 0o600
    assert lab.password('lab_admin') not in conf, 'the services never carry the superuser password'
    assert json.loads((lab.STATE / 'zabbix_server.identity.json').read_text())['pid'] == os.getpid()
    lab.stage_frontend(paths)
    front = (lab.UI / 'conf/zabbix.conf.php').read_text()
    assert "$DB['SERVER']   = '127.0.0.1';" in front and f"$DB['PORT']     = '{lab.PG_PORT}';" in front
    assert f"$DB['PASSWORD'] = '{db_password}';" in front and mode(lab.UI / 'conf/zabbix.conf.php') == 0o600
    assert f"$ZBX_SERVER_PORT = '{lab.SERVER_PORT}';" in front and "$ZBX_SERVER      = '127.0.0.1';" in front
    assert (lab.UI / 'api_jsonrpc.php').exists() and (lab.UI / 'include/defines.inc.php').exists()
    assert lab.PHP_INI[lab.PHP_INI.index('-d') + 1] == 'memory_limit=128M' and 'max_execution_time=300' in lab.PHP_INI
    for role in ('lab_admin', 'zabbix'):
        assert mode(lab.password_file(role)) == 0o600 and lab.password_file(role).parent == lab.STATE
    assert lab.password('lab_admin') != db_password


def test_initdb_requires_scram_logins_and_keeps_the_generated_password_in_the_lab_directory(lab, paths, monkeypatch):
    marked(lab, initialised=False)
    calls, seen = [], {}

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0].endswith('initdb'):
            pwfile = next(arg for arg in command if arg.startswith('--pwfile=')).split('=', 1)[1]
            seen['pwfile'] = Path(pwfile).read_text()
            seen['pwfile_mode'] = mode(Path(pwfile))
            lab.PGDATA.mkdir()
            (lab.PGDATA / 'PG_VERSION').write_text('18\n')
            (lab.PGDATA / 'postgresql.conf').write_text('# generated\n')
            (lab.PGDATA / 'pg_hba.conf').write_text('local all all scram-sha-256\nhost all all 127.0.0.1/32 scram-sha-256\n')
        if command[-1] == 'start':
            lab.PID_FILES['postgres'].write_text(f'{os.getpid()}\n')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(lab, 'run', run)
    monkeypatch.setattr(lab, 'port_listening', lambda port: True)
    monkeypatch.setattr(lab, 'expected_uid', lambda: os.getuid())
    monkeypatch.setattr(lab, 'expected_argv', lambda component: [])
    lab.start_postgres(paths)
    initdb = calls[0][0]
    assert initdb[0].endswith('initdb') and '--auth-host=scram-sha-256' in initdb and '--auth-local=scram-sha-256' in initdb
    assert not any('trust' in arg for arg in initdb) and 'env' not in calls[0][1]
    assert seen['pwfile'] == lab.password('lab_admin') + '\n' and seen['pwfile_mode'] == 0o600
    assert not (lab.STATE / 'initdb.pwfile').exists(), 'the initdb password file is removed after use'
    assert "password_encryption='scram-sha-256'" in (lab.PGDATA / 'postgresql.conf').read_text()
    assert mode(lab.password_file('lab_admin')) == 0o600
    assert [command[-1] for command, _ in calls] == ['--locale=C.UTF-8', 'start']
    assert json.loads((lab.STATE / 'postgres.identity.json').read_text())['pid'] == os.getpid()


@pytest.mark.parametrize('hba', ['host all all 127.0.0.1/32 trust', 'local all all trust', 'host all all 127.0.0.1/32 md5',
                                 'host all all 127.0.0.1/32 scram-sha-256 # ok\nhost zabbix zabbix 127.0.0.1/32 trust'])
def test_start_refuses_a_cluster_that_allows_unauthenticated_local_access(lab, paths, monkeypatch, hba):
    marked(lab)
    (lab.PGDATA / 'pg_hba.conf').write_text(hba + '\n')
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: pytest.fail('nothing may start: ' + str(command)))
    with pytest.raises(SystemExit, match='unauthenticated or non-scram'):
        lab.start_postgres(paths)
    (lab.PGDATA / 'pg_hba.conf').write_text('local all all scram-sha-256\nhost all all 127.0.0.1/32 scram-sha-256\n'
                                             'host all all ::1/128 reject\n')
    lab.verify_authentication(lab.PGDATA)


def test_every_database_call_authenticates_with_the_stored_password_off_the_command_line(lab, paths, monkeypatch):
    marked(lab)
    calls, stdin_calls = [], []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        sql = command[-1]
        if 'pg_roles' in sql or 'pg_database' in sql or 'pg_tables' in sql:
            return subprocess.CompletedProcess(command, 0, '', '')
        if 'FROM users' in sql:
            return subprocess.CompletedProcess(command, 0, '3\n', '')
        return subprocess.CompletedProcess(command, 0, '', '')

    def subprocess_run(command, **kwargs):
        stdin_calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b'', b'')
    monkeypatch.setattr(lab, 'run', run)
    monkeypatch.setattr(lab.subprocess, 'run', subprocess_run)
    assert lab.ensure_database(paths) is True
    admin, zabbix = lab.password('lab_admin'), lab.password('zabbix')
    for command, kwargs in calls:
        user = command[command.index('-U') + 1]
        assert set(kwargs['env']) == {'PGPASSWORD', 'PATH'} and kwargs['env']['PGPASSWORD'] == {'lab_admin': admin, 'zabbix': zabbix}[user]
        assert admin not in ' '.join(command) and zabbix not in ' '.join(command)
    role_creation, schema_load = stdin_calls
    assert role_creation[1]['input'] == f"CREATE ROLE zabbix LOGIN PASSWORD '{zabbix}';\n".encode()
    assert zabbix not in ' '.join(role_creation[0]) and role_creation[1]['env']['PGPASSWORD'] == admin
    assert schema_load[0][schema_load[0].index('-U') + 1] == 'zabbix' and schema_load[1]['env']['PGPASSWORD'] == zabbix
    users = [command[command.index('-U') + 1] for command, _ in calls]
    assert users == ['lab_admin', 'lab_admin', 'lab_admin', 'zabbix', 'zabbix']  # role, database, create, tables, users


def test_a_missing_password_file_is_named_not_regenerated(lab, paths, monkeypatch):
    marked(lab)
    lab.password_file('lab_admin').unlink()
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: pytest.fail('psql must not run without its password'))
    with pytest.raises(SystemExit, match='password file missing'):
        lab.ensure_database(paths)
    assert not lab.password_file('lab_admin').exists()


def test_status_record_names_versions_identities_and_reference_match(lab, paths, monkeypatch):
    marked(lab)
    lab.stage_frontend(paths)
    outputs = {'-V': 'zabbix_server (Zabbix) 7.0.31\nRevision fc62e96 22 September 2026, compilation time: synthetic\n',
               '--version': 'postgres (PostgreSQL) 18.3\n', '-v': 'PHP 8.3.6 (cli) (built: synthetic)\n'}
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, outputs.get(command[-1], ''), ''))
    monkeypatch.setattr(lab, 'package_identity', lambda binary: {'kind': 'unpackaged', 'sha256': 'a' * 64})
    record = lab.status_record(paths, '7.0.31')
    assert record['reference'] == '7.0.31' and record['observed_api_version'] == '7.0.31' and record['reference_match'] is True
    assert record['zabbix_server']['version_line'] == 'zabbix_server (Zabbix) 7.0.31'
    assert record['frontend']['ZABBIX_VERSION'] == '7.0.31' and record['postgres']['version'] == 'postgres (PostgreSQL) 18.3'
    assert record['php']['version_line'].startswith('PHP 8.3.6')
    assert lab.status_record(paths, '7.0.30')['reference_match'] is False
    assert lab.status_record(paths, None)['reference_match'] is False


def test_start_refuses_a_running_instance_that_is_not_the_pinned_reference(lab, paths, monkeypatch):
    monkeypatch.setattr(lab, 'inputs', lambda: paths)
    monkeypatch.setattr(lab.subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, '', ''))
    for name in ('start_postgres', 'ensure_database', 'start_server', 'stage_frontend', 'start_frontend'):
        monkeypatch.setattr(lab, name, lambda paths: None)
    monkeypatch.setattr(lab, 'wait_for_api', lambda timeout: '7.0.30')
    monkeypatch.setattr(lab, 'status_record', lambda paths, observed: {'reference': '7.0.31', 'reference_match': False})
    with pytest.raises(SystemExit, match='not the pinned reference'):
        lab.main(['start', '--isolated-lab'])
    assert lab.MARKER.exists()
    assert json.loads((lab.STATE / 'status.json').read_text())['reference_match'] is False


@pytest.mark.parametrize('users,outcome', [('0', 'refused'), ('', 'refused'), ('1', 'loaded')])
def test_schema_load_refuses_a_schema_without_its_initial_data(lab, paths, monkeypatch, users, outcome):
    marked(lab)
    queries = []

    def run(command, **kwargs):
        sql = command[-1]
        queries.append(sql)
        if 'pg_roles' in sql or 'pg_database' in sql:
            return subprocess.CompletedProcess(command, 0, '1\n', '')
        if 'pg_tables' in sql:
            return subprocess.CompletedProcess(command, 0, '', '')   # not loaded yet
        if 'FROM users' in sql:
            return subprocess.CompletedProcess(command, 0, users + '\n', '')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(lab, 'run', run)
    monkeypatch.setattr(lab.subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, b'', b''))
    if outcome == 'refused':
        with pytest.raises(SystemExit, match='without its initial data'):
            lab.ensure_database(paths)
    else:
        assert lab.ensure_database(paths) is True
    assert queries[-1] == 'SELECT count(*) FROM users' and (lab.STATE / 'schema.sha256').exists()


def test_services_start_with_a_minimal_environment_without_inherited_proxy_settings(lab, paths, monkeypatch):
    """libcurl in zabbix_server and PHP honour HTTPS_PROXY; the lab's loopback webhooks must not be proxied."""
    marked(lab)
    for key in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'NO_PROXY', 'ALL_PROXY', 'PGPASSWORD'):
        monkeypatch.setenv(key, 'inherited-value')
    lab.PID_FILES['zabbix_server'].write_text(f'{os.getpid()}\n')   # written by zabbix_server once started
    monkeypatch.setattr(lab, 'expected_uid', lambda: os.getuid())
    monkeypatch.setattr(lab, 'expected_argv', lambda component: [])
    launched = {}

    def run(command, **kwargs):
        launched['server'] = kwargs
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(lab, 'run', run)
    monkeypatch.setattr(lab, 'wait_until', lambda predicate, timeout: True)
    lab.start_server(paths)

    class Process:
        pid = 4242

        def kill(self):
            pytest.fail('the server should be listening')

    def popen(command, **kwargs):
        launched['php'] = kwargs
        return Process()
    monkeypatch.setattr(lab.subprocess, 'Popen', popen)
    monkeypatch.setattr(lab, 'component_alive', lambda name: False)
    lab.start_frontend(paths)
    for name in ('server', 'php'):
        env = launched[name]['env']
        assert set(env) == {'PATH', 'TZ', 'LANG'} and env['TZ'] == 'UTC'
        assert not any('proxy' in key.lower() for key in env)
    assert 'PGPASSWORD' not in launched['server']['env'] and 'PGPASSWORD' not in launched['php']['env']
    assert 'user' not in launched['php'], 'not root: no identity switch'


def test_the_php_server_starts_directly_as_the_service_identity_when_root(lab, paths, monkeypatch):
    marked(lab)
    monkeypatch.setattr(lab.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(lab.os, 'chown', lambda path, uid, gid: None)
    launched = {}

    class Process:
        pid = 4242

    def popen(command, **kwargs):
        launched['command'] = command
        launched.update(kwargs)
        return Process()
    monkeypatch.setattr(lab.subprocess, 'Popen', popen)
    monkeypatch.setattr(lab, 'wait_until', lambda predicate, timeout: True)
    monkeypatch.setattr(lab, 'record_identity', lambda name, pid: {'pid': pid})
    lab.start_frontend(paths)
    nobody = lab.pwd.getpwnam('nobody')
    assert launched['command'][0] == str(paths['php']) and 'runuser' not in launched['command']
    assert launched['user'] == nobody.pw_uid and launched['group'] == nobody.pw_gid and launched['extra_groups'] == []
    assert launched['start_new_session'] is True


def test_inputs_are_required_and_named_when_absent(lab, monkeypatch):
    for key in ('ACK_LAB_PG_BIN', 'ZABALACK_ZABBIX_SERVER', 'ZABALACK_ZABBIX_UI', 'ZABALACK_ZABBIX_SCHEMA', 'ZABALACK_PHP'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(lab.shutil, 'which', lambda name: None)
    with pytest.raises(SystemExit, match='NOT_RUN'):
        lab.inputs()


def test_start_continues_a_partially_started_lab_without_restarting_live_components(lab, paths, monkeypatch):
    marked(lab)
    live(lab, monkeypatch, 'zabbix_server')   # server already up, recorded by the lab
    monkeypatch.setattr(lab, 'port_listening', lambda port: port == lab.SERVER_PORT)
    monkeypatch.setattr(lab, 'inputs', lambda: paths)
    steps = []
    monkeypatch.setattr(lab, 'start_postgres', lambda paths: steps.append('postgres'))
    monkeypatch.setattr(lab, 'ensure_database', lambda paths: steps.append('database') or False)
    monkeypatch.setattr(lab, 'stage_frontend', lambda paths: steps.append('frontend-files'))
    monkeypatch.setattr(lab, 'start_frontend', lambda paths: steps.append('php'))
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: pytest.fail('zabbix_server must not be started twice: ' + str(command)))
    monkeypatch.setattr(lab, 'wait_for_api', lambda timeout: '7.0.31')
    monkeypatch.setattr(lab, 'status_record', lambda paths, observed: {'reference': '7.0.31', 'reference_match': True})
    assert lab.main(['start', '--isolated-lab']) == 0
    assert steps == ['postgres', 'database', 'frontend-files', 'php']
    assert (lab.STATE / 'zabbix_server.pid').read_text() == f'{os.getpid()}\n'


def test_stop_never_signals_a_pid_the_lab_did_not_record(lab, paths, monkeypatch, sleeper, capsys):
    """A stale php.pid or zabbix_server.pid whose PID a foreign process now holds is reported, not signalled."""
    marked(lab)
    php_impostor = sleeper('-S', f'127.0.0.1:{lab.WEB_PORT}', '-t', str(lab.UI))   # even carrying the lab's arguments
    server_impostor = sleeper()
    (lab.STATE / 'php.pid').write_text(f'{php_impostor.pid}\n')
    (lab.STATE / 'zabbix_server.pid').write_text(f'{server_impostor.pid}\n')
    monkeypatch.setattr(lab, 'expected_uid', lambda: os.getuid())
    monkeypatch.setattr(lab, 'inputs', lambda: paths)
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: pytest.fail('pg_ctl must not run: ' + str(command)))
    signalled, real_kill = [], os.kill
    monkeypatch.setattr(lab.os, 'kill', lambda pid, sig: signalled.append((pid, sig)) if sig != 9 else real_kill(pid, sig))
    monkeypatch.setattr(lab.os, 'killpg', lambda *args: pytest.fail(f'os.killpg called with {args}'))
    observed = lab.inspect()
    assert observed['state'] == 'stopped' and observed['components'] == {'postgres': 'stopped', 'zabbix_server': 'foreign_pid',
                                                                          'php': 'foreign_pid'}
    assert 'did not record' in observed['detail']
    outcomes = lab.stop_all(paths)
    assert outcomes['php'] == {'status': 'NOT_RUNNING', 'pid_file': 'foreign_pid', 'detail': outcomes['php']['detail']}
    assert 'not signalled' in outcomes['php']['detail'] and outcomes['zabbix_server']['pid_file'] == 'foreign_pid'
    assert php_impostor.poll() is None and server_impostor.poll() is None
    assert lab.main(['stop', '--isolated-lab']) == 0
    assert json.loads(capsys.readouterr().out)['state']['state'] == 'stopped'
    assert php_impostor.poll() is None and server_impostor.poll() is None, 'the foreign processes outlive the stop'
    assert signalled == []


def test_start_refuses_a_postmaster_pid_file_the_lab_did_not_record(lab, paths, monkeypatch, sleeper):
    marked(lab)
    impostor = sleeper('-D', str(lab.PGDATA))
    lab.PID_FILES['postgres'].write_text(f'{impostor.pid}\n')
    monkeypatch.setattr(lab, 'expected_uid', lambda: os.getuid())
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: pytest.fail('pg_ctl must not run: ' + str(command)))
    with pytest.raises(SystemExit, match='not the recorded lab postmaster'):
        lab.start_postgres(paths)
    assert impostor.poll() is None
    outcomes = lab.stop_all(paths)
    assert outcomes['postgres']['pid_file'] == 'foreign_pid' and impostor.poll() is None


@pytest.mark.parametrize('case', ['recorded', 'no-record', 'other-process-record', 'wrong-arguments', 'wrong-uid', 'dead'])
def test_only_the_recorded_process_with_the_lab_arguments_and_identity_is_signalled(lab, paths, monkeypatch, sleeper, case):
    marked(lab)
    arguments = ['-S', f'127.0.0.1:{lab.WEB_PORT}', '-t', str(lab.UI)]
    recorded = None
    if case == 'other-process-record':
        earlier = sleeper(*arguments)
        recorded = lab.record_identity('php', earlier.pid)       # the lab recorded a different process...
        earlier.kill()
        earlier.wait(10)
        time.sleep(0.05)                                         # ...started in an earlier clock tick
    process = sleeper(*(['--other'] if case == 'wrong-arguments' else arguments))
    (lab.STATE / 'php.pid').write_text(f'{process.pid}\n')
    identity = lab.process_identity(process.pid)
    monkeypatch.setattr(lab, 'expected_uid', lambda: identity['uid'] + (1 if case == 'wrong-uid' else 0))
    if recorded is not None:
        recorded['pid'] = process.pid                            # ...whose PID the current one now holds
        assert recorded['starttime'] < identity['starttime'] and recorded['comm'] == identity['comm']
        lab.identity_file('php').write_text(json.dumps(recorded))
    elif case != 'no-record':
        lab.record_identity('php', process.pid)
    if case == 'dead':
        process.kill()
        process.wait(10)
    live_identity, classification = lab.verified_pid('php')
    expected = {'recorded': 'running', 'no-record': 'foreign_pid', 'other-process-record': 'foreign_pid',
                'wrong-arguments': 'foreign_pid', 'wrong-uid': 'foreign_pid', 'dead': 'stale_pid'}[case]
    assert classification == expected
    outcome = lab.stop_component('php', 10)
    if case == 'recorded':
        assert live_identity['pid'] == process.pid and live_identity['starttime'] > 0
        assert outcome == {'status': 'STOPPED', 'pid': process.pid} and process.wait(10) == -15
    else:
        assert outcome['status'] == 'NOT_RUNNING' and outcome['pid_file'] == expected
        if case != 'dead':
            assert process.poll() is None, 'never signalled'


def test_process_identity_reads_proc_start_time_comm_uid_and_arguments(lab, sleeper):
    process = sleeper('--marker')
    identity = lab.process_identity(process.pid)
    assert identity['pid'] == process.pid and identity['comm'].startswith('python') and identity['uid'] == os.getuid()
    assert identity['argv'][-1] == '--marker' and identity['state'] in ('S', 'R') and isinstance(identity['starttime'], int)
    stat_fields = Path(f'/proc/{process.pid}/stat').read_text().rpartition(')')[2].split()
    assert identity['starttime'] == int(stat_fields[19])
    process.kill()
    process.wait(10)
    assert lab.process_gone(identity)
    assert lab.process_identity(2 ** 22 - 1) is None


@pytest.mark.parametrize('hba, accepted', [
    ('host scram-sha-256 all 127.0.0.1/32 password', False),
    ('host all reject 0.0.0.0/0 md5', False),
    ('local all all peer map=scram-sha-256', False),
    ('host all all 127.0.0.1 255.255.255.255 trust', False),
    ('include_dir conf.d', False),
    ('host all all 127.0.0.1 255.255.255.255 scram-sha-256', True),
    ('hostssl all all ::1/128 scram-sha-256 clientcert=verify-full', True),
    ('local replication all scram-sha-256', True),
], ids=['method-name-as-database', 'method-name-as-user', 'method-name-as-option', 'address-and-mask-trust', 'include',
        'address-and-mask-scram', 'hostssl-with-options', 'replication'])
def test_the_pg_hba_method_is_read_by_position(lab, hba, accepted):
    """Re-review New-3: a line was accepted when 'scram-sha-256' or 'reject' appeared anywhere on it."""
    marked(lab)
    (lab.PGDATA / 'pg_hba.conf').write_text(hba + '\n')
    if accepted:
        lab.verify_authentication(lab.PGDATA)
    else:
        with pytest.raises(SystemExit, match='unauthenticated or non-scram'):
            lab.verify_authentication(lab.PGDATA)


@pytest.mark.parametrize('hba, accepted', [
    ('local all "a scram-sha-256 b" trust', False),
    ('local all all "scram-sha-256"', False),
    ('host all all 127.0.0.1/32 "scram-sha-256"', False),
    ('local "trust" all trust', False),
    ('local all all trust # scram-sha-256', False),
    ('local all "user""with""quotes scram-sha-256" md5', False),
    ('local all all scram-sha-256,trust', False),
    ('local all "unterminated scram-sha-256', False),
    ('local all all \\', False),
    ('"local" all all scram-sha-256', False),
    ('local all "trust" scram-sha-256', True),
    ('local "all" all scram-sha-256', True),
    ('local "db one","db two" "user one" scram-sha-256', True),
    ('host all "a#b" 127.0.0.1/32 scram-sha-256 # comment', True),
    ('local all "x ""scram-sha-256"" y" scram-sha-256', True),
], ids=['quoted-user-contains-method', 'quoted-method', 'quoted-method-host', 'quoted-database-named-trust-method-trust',
        'method-in-comment', 'escaped-quotes-then-md5', 'method-list', 'unterminated-quote', 'continuation', 'quoted-connection-type',
        'user-named-trust-scram', 'quoted-all-database', 'quoted-lists', 'hash-inside-quotes', 'escaped-quotes-then-scram'])
def test_pg_hba_fields_follow_postgresql_quoting_rules(lab, hba, accepted):
    """Review of O9: a whitespace split read quoted values as fields ('local all "a scram-sha-256 b" trust' passed).

    Fields follow PostgreSQL's rules: quotes group whitespace, '""' is a literal quote, '#' starts a comment
    only outside quotes, and the method must be one unquoted keyword; anything unreadable refuses."""
    marked(lab)
    (lab.PGDATA / 'pg_hba.conf').write_text(hba + '\n')
    if accepted:
        lab.verify_authentication(lab.PGDATA)
    else:
        with pytest.raises(SystemExit, match='unauthenticated or non-scram'):
            lab.verify_authentication(lab.PGDATA)


def test_pg_hba_fields_tokenise_like_postgresql(lab):
    assert lab.hba_fields('local all "a b" scram-sha-256') == [('local', False), ('all', False), ('a b', True), ('scram-sha-256', False)]
    assert lab.hba_fields('local all "x ""y"" z" md5 # c') == [('local', False), ('all', False), ('x "y" z', True), ('md5', False)]
    assert lab.hba_fields('   # only a comment') == []
    assert lab.hba_fields('local all "open') is None


# The results PostgreSQL 18 reports in pg_hba_file_rules for these lines (observed with the packaged
# 18.6 on the runner host and with 18.3 in the review): only space, TAB and CR are blanks; U+00A0, VT
# and FF stay inside the token, so the method is the next field.
@pytest.mark.parametrize('hba, fields, accepted', [
    ('local all all scram-sha-256', ['local', 'all', 'all', 'scram-sha-256'], True),
    ('local\tall\tall\tscram-sha-256', ['local', 'all', 'all', 'scram-sha-256'], True),
    ('local all all scram-sha-256\r', ['local', 'all', 'all', 'scram-sha-256'], True),
    ('local all all scram-sha-256 trust', ['local', 'all', 'all scram-sha-256', 'trust'], False),
    ('local all all\x0bscram-sha-256 trust', ['local', 'all', 'all\x0bscram-sha-256', 'trust'], False),
    ('local all all\x0cscram-sha-256 trust', ['local', 'all', 'all\x0cscram-sha-256', 'trust'], False),
    ('local all all x scram-sha-256', ['local', 'all', 'all x', 'scram-sha-256'], True),
], ids=['space', 'tab', 'cr', 'nbsp', 'vertical-tab', 'form-feed', 'nbsp-inside-a-user-then-scram'])
def test_pg_hba_blanks_are_postgresql_blanks_only(lab, hba, fields, accepted):
    """Review of b57bebb (LOW): str.isspace() split on U+00A0, VT and FF, where PostgreSQL 18 does not,
    so a record PostgreSQL applies as trust was accepted as scram-sha-256."""
    assert [text for text, _ in lab.hba_fields(hba)] == fields
    marked(lab)
    (lab.PGDATA / 'pg_hba.conf').write_bytes((hba + '\n').encode())
    if accepted:
        lab.verify_authentication(lab.PGDATA)
    else:
        with pytest.raises(SystemExit, match='unauthenticated or non-scram'):
            lab.verify_authentication(lab.PGDATA)


def test_pg_hba_lines_end_at_newline_only(lab):
    """Python's splitlines() also ends lines at VT, FF, U+2028 and others; PostgreSQL does not."""
    marked(lab)
    (lab.PGDATA / 'pg_hba.conf').write_bytes('local all all scram-sha-256 local all all trust\n'.encode())
    with pytest.raises(SystemExit, match='unauthenticated or non-scram'):
        lab.verify_authentication(lab.PGDATA)


def test_staging_the_packaged_frontend_skips_its_unconfigured_configuration_link(lab, paths, tmp_path):
    """Live cycle on the runner host (O20): zabbix-frontend-php ships conf/zabbix.conf.php as a link to
    /etc/zabbix/web/zabbix.conf.php, absent until the system frontend is configured; copytree failed on it
    and the lab never started. The lab's own private configuration takes its place; other links are followed."""
    marked(lab)
    ui = paths['ui']
    (ui / 'conf').mkdir()
    (ui / 'conf/zabbix.conf.php').symlink_to(tmp_path / 'etc-zabbix-web' / 'zabbix.conf.php')  # dangling, as packaged
    font = tmp_path / 'alternatives-font.ttf'
    font.write_bytes(b'font bytes')
    (ui / 'assets/fonts').mkdir(parents=True)
    (ui / 'assets/fonts/graphfont.ttf').symlink_to(font)  # resolvable, as packaged via /etc/alternatives
    lab.stage_frontend(paths)
    staged = lab.UI / 'conf/zabbix.conf.php'
    assert staged.is_file() and not staged.is_symlink() and "$DB['TYPE']" in staged.read_text()
    assert (staged.stat().st_mode & 0o777) == 0o600
    copied = lab.UI / 'assets/fonts/graphfont.ttf'
    assert copied.is_file() and not copied.is_symlink() and copied.read_bytes() == b'font bytes'


# Raw pg_hba.conf bytes through the actual reader (verify_authentication), with the verdict PostgreSQL
# 18.6 gives for the same bytes (pg_hba_file_rules, packaged server on the x64 runner host): a bare CR
# is a blank inside the record, never a line end; a record PostgreSQL rejects is refused.
@pytest.mark.parametrize('raw, accepted', [
    (b'local all all scram-sha-256\r\n', True),                    # PG18: scram-sha-256
    (b'local\rall\rall\rscram-sha-256\n', True),                   # CR_strong. PG18: scram-sha-256
    (b'local all all scram-sha-256\r', True),                      # PG18: scram-sha-256
    (b'local all "a\rb" scram-sha-256\n', True),                   # PG18: scram-sha-256
    (b'host\tall\tall\t127.0.0.1/32\rscram-sha-256\n', True),      # PG18: scram-sha-256
    (b'\r\nlocal all all scram-sha-256\n', True),                  # PG18: scram-sha-256 on line 2
    (b'# note\rlocal all all trust\n', True),                      # PG18: no rule (the comment runs to '\n')
    (b'local\rall\rall\rtrust\n', False),                          # PG18: trust
    (b'local all all scram-sha-256\rlocal all all trust\n', False),  # PG18: error, option not name=value
    (b'local all all scram-sha-256 # \xff\n', False),              # not UTF-8: refused, never a crash
], ids=['crlf', 'cr-strong', 'cr-at-eof', 'cr-in-quotes', 'cr-host', 'cr-blank-line', 'cr-in-comment',
        'cr-weak', 'cr-joined-records', 'non-utf8'])
def test_pg_hba_raw_bytes_are_read_as_postgresql_reads_them(lab, raw, accepted):
    """Review of e8ef82b (F2): read_text() ended a line at a bare CR, which PostgreSQL treats as a blank."""
    marked(lab)
    (lab.PGDATA / 'pg_hba.conf').write_bytes(raw)
    if accepted:
        lab.verify_authentication(lab.PGDATA)
    else:
        with pytest.raises(SystemExit, match='unauthenticated or non-scram|not UTF-8'):
            lab.verify_authentication(lab.PGDATA)
