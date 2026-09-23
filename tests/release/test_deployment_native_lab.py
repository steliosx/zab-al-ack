"""lab/deployment_native_lab.py: state, refusals, reset sequencing and the run command (R29).

PostgreSQL, the Zabbix lab, psql and the local runner are mocked; nothing starts.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('review_deployment_native_lab', ROOT / 'lab/deployment_native_lab.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = tmp_path / '.deployment-lab'
    for key, value in [('REPO', tmp_path), ('STATE', state), ('MARKER', state / 'ISOLATED_DEPLOYMENT_LAB'), ('PGDATA', state / 'pgdata'),
                       ('PRIVATE', tmp_path / 'private'), ('LINK', tmp_path / 'link'), ('RECORD', tmp_path / 'evidence/deployment-e2e-run.json')]:
        monkeypatch.setattr(module, key, value)
    monkeypatch.setattr(module, 'port_listening', lambda port: False)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1001)
    monkeypatch.setattr(module, 'unprivileged_helper_available', lambda: True)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    for name in ('pg_ctl', 'initdb', 'psql', 'postgres'):
        (binaries / name).write_text('')
    monkeypatch.setenv('ACK_LAB_PG_BIN', str(binaries))
    zabbix = SimpleNamespace(state='absent', calls=[])
    monkeypatch.setattr(module, 'zabbix_module', lambda: SimpleNamespace(
        WEB_PORT=8080, PORTS={'php': 58080}, API_URL='', inspect=lambda: {'state': zabbix.state, 'detail': 'synthetic ' + zabbix.state},
        package_identity=lambda binary: {'kind': 'unpackaged', 'sha256': 'a' * 64}))

    def zabbix_command(command, *args, check=True):
        zabbix.calls.append(command)
        if command == 'status':
            return subprocess.CompletedProcess(command, 0, json.dumps({'state': {'state': 'running'}, 'services': {
                'reference': '7.0.31', 'observed_api_version': '7.0.31', 'reference_match': True,
                'zabbix_server': {'version_line': 'zabbix_server (Zabbix) 7.0.31', 'identity': {'kind': 'dpkg', 'package': 'zabbix-server-pgsql', 'version': '1:7.0.31-1+ubuntu24.04', 'architecture': 'amd64'}},
                'frontend': {'ZABBIX_VERSION': '7.0.31', 'identity': {'kind': 'unpackaged', 'sha256': 'b' * 64}, 'served_by': 'PHP built-in server'},
                'postgres': {'version': 'postgres (PostgreSQL) 18.3', 'identity': {'kind': 'unpackaged', 'sha256': 'c' * 64}},
                'php': {'version_line': 'PHP 8.3.6 (cli)', 'identity': {'kind': 'dpkg', 'package': 'php8.3-cli', 'version': '8.3.6', 'architecture': 'amd64'}}}}), '')
        if command == 'start':
            zabbix.state = 'running'
        if command == 'stop':
            zabbix.state = 'stopped'
        if command == 'reset' and '--wipe' in args:
            zabbix.state = 'absent'
        return subprocess.CompletedProcess(command, 0, json.dumps({'cleanup': {'php': {'status': 'STOPPED'}}}), '')
    monkeypatch.setattr(module, 'zabbix', zabbix_command)
    return SimpleNamespace(module=module, tmp=tmp_path, binaries=binaries, zabbix=zabbix)


def marked(lab, *, initialised=True):
    lab.module.STATE.mkdir()
    lab.module.MARKER.write_text('marker\n')
    if initialised:
        lab.module.PGDATA.mkdir()
        (lab.module.PGDATA / 'PG_VERSION').write_text('18\n')


def ready_layout(lab, monkeypatch):
    private = lab.module.PRIVATE
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    (private / 'pg-password').write_text('synthetic-password\n')
    lab.module.LINK.symlink_to(lab.tmp)
    monkeypatch.setattr(lab.module.socket, 'getaddrinfo', lambda name, port: [(None, None, None, None, ('127.0.0.1', 0))])
    monkeypatch.setattr(lab.module.os, 'geteuid', lambda: private.stat().st_uid)


@pytest.mark.parametrize('layout_kind,pg_status,port,zabbix_state,expected', [
    ('none', 3, False, 'absent', 'absent'),
    ('none', 3, True, 'absent', 'ambiguous'),
    ('unmarked', 3, False, 'absent', 'unmarked'),
    ('marked', 3, False, 'absent', 'stopped'),
    ('marked', 0, True, 'running', 'running'),
    ('marked', 0, True, 'stopped', 'partial'),
    ('marked', 3, False, 'running', 'partial'),
    ('marked', 3, True, 'stopped', 'ambiguous'),
    ('marked', 4, False, 'stopped', 'ambiguous'),
], ids=['absent', 'foreign-5432', 'unmarked', 'stopped', 'running', 'zabbix-stopped', 'postgres-stopped', 'stopped-port-busy', 'inaccessible'])
def test_state_combines_the_cluster_the_zabbix_lab_and_the_layout(lab, monkeypatch, layout_kind, pg_status, port, zabbix_state, expected):
    if layout_kind == 'unmarked':
        lab.module.STATE.mkdir()
        (lab.module.STATE / 'stranger').write_text('x')
    elif layout_kind == 'marked':
        marked(lab)
    # The host under test may resolve the lab hostnames; this case fixes them unresolved.
    monkeypatch.setattr(lab.module.socket, 'getaddrinfo', lambda name, port: (_ for _ in ()).throw(lab.module.socket.gaierror()))
    monkeypatch.setattr(lab.module, 'port_listening', lambda p: port and p == lab.module.PG_PORT)
    monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, pg_status, '', ''))
    lab.zabbix.state = zabbix_state
    observed = lab.module.inspect(lab.binaries)
    assert observed['state'] == expected
    assert observed['layout']['ready'] is False and len(observed['layout']['missing']) >= 3  # nothing prepared in tmp
    if layout_kind == 'none' and port:
        assert 'packaged default cluster' in observed['postgres']['detail']


def test_inspect_addresses_the_zabbix_lab_on_8080_without_changing_the_process_environment(lab, monkeypatch):
    monkeypatch.delenv('ZABALACK_ZABBIX_WEB_PORT', raising=False)
    seen = {}
    monkeypatch.setattr(lab.module, 'zabbix_module', lambda: seen.setdefault('module', SimpleNamespace(
        WEB_PORT=58080, PORTS={'php': 58080}, API_URL='', inspect=lambda: {'state': 'absent'})))
    lab.module.inspect(lab.binaries)
    assert seen['module'].WEB_PORT == 8080 and seen['module'].PORTS['php'] == 8080 and ':8080/' in seen['module'].API_URL
    assert 'ZABALACK_ZABBIX_WEB_PORT' not in os.environ  # a later in-process import of the Zabbix lab keeps 58080
    assert lab.module.zabbix_env()['ZABALACK_ZABBIX_WEB_PORT'] == '8080'  # subprocesses get it explicitly


def test_layout_lists_every_missing_prerequisite_without_changing_anything(lab, monkeypatch):
    monkeypatch.setattr(lab.module.socket, 'getaddrinfo', lambda name, port: (_ for _ in ()).throw(lab.module.socket.gaierror()))
    record = lab.module.layout()
    assert record['ready'] is False
    assert record['missing'][:3] == ['/etc/hosts: 127.0.0.1 postgres', '/etc/hosts: 127.0.0.1 zabbix-web', '/etc/hosts: 127.0.0.1 runner']
    assert any(item.startswith('ln -s') for item in record['missing']) and any('install -d -m 0700' in item for item in record['missing'])
    assert not lab.module.PRIVATE.exists() and not lab.module.LINK.exists()
    ready_layout(lab, monkeypatch)
    assert lab.module.layout()['ready'] is True


@pytest.mark.parametrize('command', ['start', 'reset', 'run', 'cycle', 'stop', 'wipe', 'prepare-runner'])
def test_mutation_requires_the_isolated_lab_flag(lab, command):
    with pytest.raises(SystemExit):
        lab.module.main([command])


def test_start_refuses_unmarked_state_and_an_unready_layout(lab, monkeypatch):
    lab.module.STATE.mkdir()
    (lab.module.STATE / 'stranger').write_text('x')
    monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: pytest.fail('nothing may run'))
    with pytest.raises(SystemExit, match='Refusing to start'):
        lab.module.main(['start', '--isolated-lab'])
    (lab.module.STATE / 'stranger').unlink()
    lab.module.STATE.rmdir()
    with pytest.raises(SystemExit, match='host layout not ready'):
        lab.module.main(['start', '--isolated-lab'])
    assert lab.zabbix.calls == []


def test_start_initialises_scram_cluster_creates_alerta_and_starts_the_zabbix_lab_on_8080(lab, monkeypatch, capsys):
    ready_layout(lab, monkeypatch)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if Path(command[0]).name == 'initdb':
            assert '--auth-host=scram-sha-256' in command and any(arg.startswith('--pwfile=') for arg in command)
            pwfile = Path(next(arg for arg in command if arg.startswith('--pwfile=')).split('=', 1)[1])
            assert pwfile.read_text() == 'synthetic-password\n'
            lab.module.PGDATA.mkdir(parents=True)
            (lab.module.PGDATA / 'PG_VERSION').write_text('18\n')
            (lab.module.PGDATA / 'postgresql.conf').write_text('')
        code = 3 if command[-1] == 'status' and not commands.count(command) > 1 else 0
        return subprocess.CompletedProcess(command, code, '', '')
    monkeypatch.setattr(lab.module, 'run', run)
    monkeypatch.setattr(lab.module, 'port_listening', lambda port: port == lab.module.PG_PORT and any(c[-1] == 'start' for c in commands))
    executed = []
    monkeypatch.setattr(lab.module, 'psql', lambda binaries, database, sql, **kwargs: executed.append(sql) or '')
    assert lab.module.main(['start', '--isolated-lab']) == 0
    conf = (lab.module.PGDATA / 'postgresql.conf').read_text()
    assert "listen_addresses='127.0.0.1'" in conf and 'port=5432' in conf and "password_encryption='scram-sha-256'" in conf
    assert not (lab.module.STATE / 'initdb.pwfile').exists()  # the password copy does not outlive initdb
    assert any('CREATE DATABASE alerta' in sql for sql in executed)
    assert lab.zabbix.calls == ['start'] and lab.module.MARKER.exists()
    out = json.loads(capsys.readouterr().out)
    assert out['zabbix'] == 'zabbix-web:8080' and out['postgres'] == 'postgres:5432'


def test_reset_recreates_the_database_drops_lab_roles_and_refreshes_the_zabbix_instance(lab, monkeypatch, capsys):
    ready_layout(lab, monkeypatch)
    marked(lab)
    lab.zabbix.state = 'running'
    monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '', ''))
    monkeypatch.setattr(lab.module, 'port_listening', lambda port: port == lab.module.PG_PORT)
    executed = []

    def psql(binaries, database, sql, **kwargs):
        executed.append(sql)
        if sql.startswith("SELECT 1 FROM pg_roles"):
            return '1' if 'alerta_app' in sql or 'zabalack_owner' in sql else ''
        return ''
    monkeypatch.setattr(lab.module, 'psql', psql)
    assert lab.module.main(['reset', '--isolated-lab']) == 0
    assert executed[0].startswith('SELECT pg_terminate_backend') and executed[1] == 'DROP DATABASE IF EXISTS alerta'
    assert 'DROP ROLE alerta_app' in executed and 'DROP ROLE zabalack_owner' in executed and 'DROP ROLE migrator' not in executed
    assert executed[-1] == 'CREATE DATABASE alerta OWNER lab_admin ENCODING UTF8'
    assert lab.zabbix.calls == ['stop', 'reset', 'start']
    out = json.loads(capsys.readouterr().out)
    assert out['alerta']['roles_dropped'] == ['alerta_app', 'zabalack_owner']


def test_reset_and_run_require_the_running_lab(lab, monkeypatch):
    ready_layout(lab, monkeypatch)
    marked(lab)
    monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 3, '', ''))
    with pytest.raises(SystemExit, match='reset requires the running deployment lab'):
        lab.module.main(['reset', '--isolated-lab'])
    # run is guarded: the refusal becomes the public NOT_RUN record and exit 2.
    assert lab.module.main(['run', '--isolated-lab', '--runner-python', '/venv/bin/python', '--wheel', 'zabalack-0.1.0a18-py3-none-any.whl']) == 2
    assert record(lab)['status'] == 'NOT_RUN' and record(lab)['reason'] == 'lab_not_running'


def test_services_file_describes_the_four_components_with_identities(lab, monkeypatch, capsys):
    ready_layout(lab, monkeypatch)
    marked(lab)
    lab.zabbix.state = 'running'
    monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, 'postgres (PostgreSQL) 18.3\n', ''))
    monkeypatch.setattr(lab.module, 'port_listening', lambda port: port == lab.module.PG_PORT)
    assert lab.module.main(['services']) == 0
    services = json.loads((lab.module.STATE / 'services.json').read_text())
    assert set(services) == {'postgres', 'zabbix-db', 'zabbix-server', 'zabbix-web'}
    assert services['postgres'].startswith('PostgreSQL 18 (alerta database, scram host logins, loopback 5432): postgres (PostgreSQL) 18.3; unpackaged executable sha256 ' + 'a' * 64)
    assert 'dpkg zabbix-server-pgsql 1:7.0.31-1+ubuntu24.04 amd64; reference 7.0.31, observed API 7.0.31' in services['zabbix-server']
    assert 'PHP 8.3.6 (cli)' in services['zabbix-web'] and '55434' in services['zabbix-db']


def test_run_writes_services_then_invokes_the_local_runner_with_reset_and_wheel_identity(lab, monkeypatch):
    ready_layout(lab, monkeypatch)
    marked(lab)
    lab.zabbix.state = 'running'
    monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, 'postgres (PostgreSQL) 18.3\n', ''))
    monkeypatch.setattr(lab.module, 'port_listening', lambda port: port == lab.module.PG_PORT)
    launched = []
    monkeypatch.setattr(lab.module, 'run_child', lambda command, **kwargs: launched.append((command, kwargs)) or 3)
    code = lab.module.main(['run', '--isolated-lab', '--runner-python', '/venv/bin/python', '--wheel', '/dist/zabalack-0.1.0a18-py3-none-any.whl',
                            '--wheel-sha256', 'f' * 64])
    assert code == 3
    (command, kwargs), = launched
    assert command[1].endswith('tools/run_deployment_e2e_local.py') and '--isolated-lab' in command
    assert command[command.index('--runner-python') + 1] == '/venv/bin/python'
    assert command[command.index('--wheel-sha256') + 1] == 'f' * 64
    reset = command[command.index('--reset-command') + 1]
    assert reset.endswith('deployment_native_lab.py reset --isolated-lab')
    assert command[command.index('--services-file') + 1] == str(lab.module.STATE / 'services.json')
    assert (lab.module.STATE / 'services.json').exists() and kwargs['cwd'] == lab.tmp
    # `run` leaves the lab running by design; its record says so and cannot pass as a cycle artifact.
    record = json.loads(lab.module.RECORD.read_text())
    assert record['native_lab']['command'] == 'run' and record['native_lab']['final_state'] == 'running'
    assert record['native_lab']['cleanup_complete'] is False


def test_stop_reports_both_cleanups_and_exit_code_from_the_observed_state(lab, monkeypatch, capsys):
    marked(lab)
    lab.zabbix.state = 'running'
    statuses = iter([0, 0, 3])  # observed running, stop, observed stopped

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, next(statuses) if command[-1] == 'status' else 0, '', '')
    monkeypatch.setattr(lab.module, 'run', run)
    assert lab.module.main(['stop', '--isolated-lab']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['cleanup']['postgres'] == {'status': 'STOPPED', 'exit_code': 0} and out['state'] == 'stopped' and lab.zabbix.calls == ['stop']


@pytest.mark.parametrize('zabbix_state,combined', [('running', 'running'), ('stopped', 'partial')])
def test_wipe_refuses_while_any_component_runs(lab, monkeypatch, zabbix_state, combined):
    marked(lab)
    lab.zabbix.state = zabbix_state
    monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '', ''))
    monkeypatch.setattr(lab.module, 'port_listening', lambda port: port == lab.module.PG_PORT)
    with pytest.raises(SystemExit, match='Refusing wipe from state ' + combined):
        lab.module.main(['wipe', '--isolated-lab'])
    assert lab.module.MARKER.exists() and lab.zabbix.calls == []


def test_a_root_verifier_without_a_usable_unprivileged_identity_is_blocked_before_any_service_command(lab, monkeypatch):
    monkeypatch.setattr(lab.module, 'unprivileged_helper_available', lambda: False)
    monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: pytest.fail('nothing may run'))
    for argv in (['stop', '--isolated-lab'], ['services']):
        with pytest.raises(SystemExit, match='BLOCKED_ENVIRONMENT'):
            lab.module.main(argv)
    # run and cycle produce the public NOT_RUN record and exit 2 instead of a bare traceback.
    assert lab.module.main(['cycle', '--isolated-lab', '--runner-python', '/v/bin/python', '--wheel', 'zabalack-0.1.0a18-py3-none-any.whl']) == 2
    assert record(lab)['reason'] == 'blocked_environment'
    assert lab.zabbix.calls == []


def test_unprivileged_helper_is_only_consulted_as_root(lab, monkeypatch):
    spec = importlib.util.spec_from_file_location('review_deployment_native_lab_helper', ROOT / 'lab/deployment_native_lab.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1001)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: pytest.fail('no helper probe for an unprivileged verifier'))
    assert module.unprivileged_helper_available() is True
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module.subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 1))
    assert command_probe(module) is False


def command_probe(module):
    return module.unprivileged_helper_available()


def cycle_environment(lab, monkeypatch, *, runner_exit=0, stop_exit=0):
    """Mocked services for the cycle: start_postgres marks and 'runs' the cluster; pg_ctl stop may fail."""
    ready_layout(lab, monkeypatch)
    live = {'postgres': False}

    def start_postgres(binaries):
        marked(lab)
        (lab.module.STATE / 'postgres.log').write_text('synthetic postgres log\n')
        (lab.tmp / '.zabbix-lab').mkdir(exist_ok=True)
        (lab.tmp / '.zabbix-lab/zabbix_server.log').write_text('synthetic server log\n')
        live['postgres'] = True
    monkeypatch.setattr(lab.module, 'start_postgres', start_postgres)

    def run(command, **kwargs):
        if command[-1] == 'status':
            return subprocess.CompletedProcess(command, 0 if live['postgres'] else 3, '', '')
        if command[-1] == 'stop':
            live['postgres'] = stop_exit != 0
            return subprocess.CompletedProcess(command, stop_exit, '', '')
        return subprocess.CompletedProcess(command, 0, 'postgres (PostgreSQL) 18.3\n', '')
    monkeypatch.setattr(lab.module, 'run', run)
    monkeypatch.setattr(lab.module, 'port_listening', lambda port: port == lab.module.PG_PORT and live['postgres'])
    monkeypatch.setattr(lab.module, 'psql', lambda binaries, database, sql, **kwargs: '1')
    launched = []

    def inner_record(status, **extra):
        """What the inner runner leaves behind: a record like tools/run_deployment_e2e_local.py writes."""
        lab.module.RECORD.parent.mkdir(exist_ok=True)
        lab.module.RECORD.write_text(json.dumps({'layer': 'native_deployment_e2e', 'status': status, 'not_docker': True,
                                                 'counts': {'passed': 4, 'failed': 0, 'skipped': 0}, 'tree_unchanged': True,
                                                 'artifact_mode': 'supplied_wheel', 'code_digests': {'source': 'a' * 64, 'harness': 'b' * 64},
                                                 'started_at': '2026-09-23T00:00:00+00:00', **extra}))

    def runner(command, **kwargs):
        launched.append(command)
        inner_record('PASS' if runner_exit == 0 else ('NOT_RUN' if runner_exit == 2 else 'FAIL'), reason=None if runner_exit == 0 else 'synthetic')
        return runner_exit
    monkeypatch.setattr(lab.module, 'run_child', runner)
    lab.inner_record = inner_record
    return launched


def stale_pass_record(lab):
    """The tracked historical PASS record a checkout carries before any run."""
    lab.module.RECORD.parent.mkdir(exist_ok=True)
    lab.module.RECORD.write_text(json.dumps({'layer': 'native_deployment_e2e', 'status': 'PASS', 'not_docker': True, 'tree_unchanged': True,
                                             'counts': {'passed': 4, 'failed': 0, 'skipped': 0}, 'checks': {'x': True}, 'components': {'postgres': 'old'},
                                             'supplied_wheel': {'sha256': 'f' * 64}, 'artifact_mode': 'supplied_wheel',
                                             'code_digests': {'source': 'c' * 64, 'harness': 'd' * 64}, 'project': 'old-project',
                                             'started_at': '2026-01-01T00:00:00+00:00', 'finished_at': '2026-01-01T00:10:00+00:00'}))


def record(lab):
    return json.loads(lab.module.RECORD.read_text())


CYCLE = ['cycle', '--isolated-lab', '--runner-python', '/venv/bin/python', '--wheel', '/dist/zabalack-0.1.0a18-py3-none-any.whl', '--wheel-sha256', 'f' * 64]


def test_cycle_starts_runs_stops_and_wipes_and_reports_the_run_exit_code(lab, monkeypatch, capsys):
    launched = cycle_environment(lab, monkeypatch, runner_exit=3)
    assert lab.module.main(CYCLE) == 3
    out = json.loads(capsys.readouterr().out)
    assert out['start']['state'] == 'running' and out['run_exit_code'] == 3
    assert out['cleanup'] == {'cleanup': {'zabbix': {'php': {'status': 'STOPPED'}}, 'postgres': {'status': 'STOPPED', 'exit_code': 0}}, 'state': 'stopped'}
    assert out['wipe'] == {'wipe': 'done', 'zabbix_exit_code': 0, 'state': 'absent'} and out['final_state'] == 'absent'
    assert not lab.module.STATE.exists()
    assert lab.zabbix.calls == ['start', 'status', 'stop', 'reset']
    # Both labs' diagnostics were preserved with digests before the wipe removed them.
    retained = lab.tmp / 'build/private/retained-phases' / out['run_id'] / 'deployment-cycle'
    manifest = json.loads((retained / 'manifest.json').read_text())
    assert out['retention']['label'] == 'deployment-cycle'
    assert {'.deployment-lab/postgres.log', '.deployment-lab/services.json', '.zabbix-lab/zabbix_server.log'} <= {
        item['source'] for item in manifest['files'].values()}
    assert (retained / '.zabbix-lab__zabbix_server.log').read_text() == 'synthetic server log\n'
    assert (retained.parent.parent.stat().st_mode & 0o777) == 0o700
    (command,) = launched
    assert command[1].endswith('tools/run_deployment_e2e_local.py') and command[command.index('--wheel-sha256') + 1] == 'f' * 64


def test_cycle_refuses_unless_the_lab_is_absent_and_touches_nothing(lab, monkeypatch):
    marked(lab)
    monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 3, '', ''))
    monkeypatch.setattr(lab.module, 'start_postgres', lambda binaries: pytest.fail('nothing may start'))
    assert lab.module.main(CYCLE) == 2
    assert record(lab)['status'] == 'NOT_RUN' and record(lab)['reason'] == 'lab_state_not_absent'
    assert lab.zabbix.calls == [] and lab.module.MARKER.exists()


def test_cycle_exits_5_when_a_passing_run_leaves_a_component_running(lab, monkeypatch, capsys):
    cycle_environment(lab, monkeypatch, runner_exit=0, stop_exit=1)
    assert lab.module.main(CYCLE) == 5
    out = json.loads(capsys.readouterr().out)
    assert out['run_exit_code'] == 0 and out['cleanup']['cleanup']['postgres'] == {'status': 'FAILED', 'exit_code': 1}
    assert out['wipe'] is None and out['final_state'] == 'partial' and lab.module.MARKER.exists()


@pytest.mark.parametrize('runner_exit,expected', [(0, 5), (1, 1)])
def test_cycle_refuses_the_wipe_when_diagnostics_are_not_preserved(lab, monkeypatch, capsys, runner_exit, expected):
    cycle_environment(lab, monkeypatch, runner_exit=runner_exit)
    monkeypatch.setattr(lab.module, 'preserve', lambda *args, **kwargs: (_ for _ in ()).throw(lab.module.RetentionError('disk full')))
    assert lab.module.main(CYCLE) == expected
    out = json.loads(capsys.readouterr().out)
    assert out['retention'] == {'error': 'disk full'} and out['wipe'] is None and out['final_state'] == 'stopped'
    assert lab.module.MARKER.exists() and (lab.module.STATE / 'postgres.log').exists()  # nothing removed
    assert lab.zabbix.calls == ['start', 'status', 'stop']


def test_a_passing_cycle_binds_the_absent_final_state_to_the_record(lab, monkeypatch, capsys):
    cycle_environment(lab, monkeypatch, runner_exit=0)
    assert lab.module.main(CYCLE) == 0
    value = record(lab)
    assert value['status'] == 'PASS' and value['native_lab']['final_state'] == 'absent' and value['native_lab']['cleanup_complete'] is True
    assert value['native_lab']['run_exit_code'] == 0 and value['native_lab']['wipe']['wipe'] == 'done' and 'finished_at' in value
    assert json.loads(capsys.readouterr().out)['status'] == 'PASS'


@pytest.mark.parametrize('failure', ['stop', 'retention', 'wipe'])
def test_a_non_zero_cycle_after_an_inner_pass_rewrites_the_record_to_fail(lab, monkeypatch, capsys, failure):
    """R1: the uploaded artifact can never read PASS when stop, retention or the wipe failed."""
    cycle_environment(lab, monkeypatch, runner_exit=0, stop_exit=1 if failure == 'stop' else 0)
    if failure == 'retention':
        monkeypatch.setattr(lab.module, 'preserve', lambda *args, **kwargs: (_ for _ in ()).throw(lab.module.RetentionError('disk full')))
    if failure == 'wipe':
        real = lab.module.zabbix

        def zabbix(command, *args, check=True):
            result = real(command, *args, check=check)
            return subprocess.CompletedProcess(command, 7, '{}', '') if command == 'reset' else result
        monkeypatch.setattr(lab.module, 'zabbix', zabbix)
    assert lab.module.main(CYCLE) == 5
    value = record(lab)
    assert value['status'] == 'FAIL'
    assert value['reason'] == {'stop': 'lab_cleanup_incomplete: final state partial', 'retention': 'lab_retention_failed', 'wipe': 'lab_wipe_failed'}[failure]
    native = value['native_lab']
    assert native['run_exit_code'] == 0 and native['cleanup_complete'] is False
    assert failure != 'stop' or native['final_state'] != 'absent'
    assert native['cleanup'] is not None and 'retention' in native and 'wipe' in native
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'FAIL'


@pytest.mark.parametrize('refusal', ['layout', 'occupied', 'blocked', 'binaries', 'zabbix-start'])
def test_a_fresh_not_run_record_replaces_the_stale_pass_before_any_service_work(lab, monkeypatch, capsys, refusal):
    """R13: an early refusal must never upload the tracked historical PASS record."""
    stale_pass_record(lab)
    expected = {'layout': 'layout_not_ready', 'occupied': 'lab_state_not_absent', 'blocked': 'blocked_environment',
                'binaries': 'postgres_binaries_missing', 'zabbix-start': 'zabbix_start_failed'}[refusal]
    if refusal == 'layout':
        monkeypatch.setattr(lab.module.socket, 'getaddrinfo', lambda name, port: (_ for _ in ()).throw(lab.module.socket.gaierror()))
        monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 3, '', ''))
    elif refusal == 'occupied':
        marked(lab)
        monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 3, '', ''))
    elif refusal == 'blocked':
        monkeypatch.setattr(lab.module, 'unprivileged_helper_available', lambda: False)
    elif refusal == 'binaries':
        monkeypatch.delenv('ACK_LAB_PG_BIN')
    else:
        cycle_environment(lab, monkeypatch)
        real = lab.module.zabbix
        monkeypatch.setattr(lab.module, 'zabbix', lambda command, *args, check=True: subprocess.CompletedProcess(command, 1, '', 'boom')
                            if command == 'start' else real(command, *args, check=check))
    monkeypatch.setattr(lab.module, 'run_child', lambda command, **kwargs: pytest.fail('the runner must not start: ' + str(command)))
    assert lab.module.main(CYCLE) == 2
    value = record(lab)
    assert value['status'] == 'NOT_RUN' and value['reason'] == expected
    assert value['native_lab']['run_exit_code'] is None
    for key in ('counts', 'checks', 'components', 'supplied_wheel', 'artifact_mode', 'code_digests', 'tree_unchanged', 'project'):
        assert key not in value, key
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'NOT_RUN' and out['reason'] == expected


def test_the_record_is_invalidated_before_the_first_service_command(lab, monkeypatch):
    stale_pass_record(lab)
    seen = {}

    def helper():
        seen['record_at_first_check'] = record(lab)['status']
        return False
    monkeypatch.setattr(lab.module, 'unprivileged_helper_available', helper)
    assert lab.module.main(CYCLE) == 2
    assert seen['record_at_first_check'] == 'NOT_RUN'


def test_a_failed_inner_run_keeps_its_own_record_with_the_lab_summary_attached(lab, monkeypatch, capsys):
    cycle_environment(lab, monkeypatch, runner_exit=1)
    assert lab.module.main(CYCLE) == 1
    value = record(lab)
    assert value['status'] == 'FAIL' and value['reason'] == 'synthetic' and value['native_lab']['final_state'] == 'absent'
    assert value['native_lab']['run_exit_code'] == 1


def test_wipe_reports_a_failed_zabbix_wipe(lab, monkeypatch, capsys):
    marked(lab)
    lab.zabbix.state = 'stopped'
    monkeypatch.setattr(lab.module, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 3, '', ''))
    real = lab.module.zabbix
    monkeypatch.setattr(lab.module, 'zabbix', lambda command, *args, check=True: subprocess.CompletedProcess(command, 7, '{}', '')
                        if command == 'reset' else real(command, *args, check=check))
    assert lab.module.main(['wipe', '--isolated-lab']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['wipe'] == 'failed' and out['zabbix_exit_code'] == 7


def test_a_cancellation_during_the_run_records_fail_interrupted_after_a_full_cleanup(lab, monkeypatch, capsys):
    """R8: SIGTERM/SIGHUP take the same path as Ctrl-C: cleanup, retention, wipe, a FAIL record, exit 130."""
    import signal as signal_module
    launched = cycle_environment(lab, monkeypatch, runner_exit=0)
    cancelled = lab.module.Cancellation()

    from cancellation import Cancelled   # on sys.path once the lab module has loaded

    def runner(command, **kwargs):
        launched.append(command)
        lab.inner_record('RUNNING')
        raise Cancelled(signal_module.SIGTERM)
    monkeypatch.setattr(lab.module, 'run_child', runner)
    monkeypatch.setattr(lab.module, 'Cancellation', lambda: cancelled)
    assert lab.module.main(CYCLE) == 130
    value = record(lab)
    assert value['status'] == 'FAIL' and value['reason'] == 'interrupted (SIGTERM)'
    native = value['native_lab']
    assert native['run_exit_code'] is None and native['refusal'] == 'interrupted' and native['interrupted'] == 'interrupted (SIGTERM)'
    assert native['cleanup']['state'] == 'stopped' and native['wipe']['wipe'] == 'done' and native['final_state'] == 'absent'
    assert lab.zabbix.calls == ['start', 'status', 'stop', 'reset']  # services file written, then the runner was cancelled
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'FAIL' and out['refusal'] == 'interrupted (SIGTERM)'


def test_a_signal_during_cleanup_is_deferred_until_the_record_is_written(lab, monkeypatch, capsys):
    """R8/R9: a second signal inside stop/retention/wipe never cuts the cleanup or the record short."""
    import signal as signal_module
    cycle_environment(lab, monkeypatch, runner_exit=0)
    cancelled = lab.module.Cancellation()
    monkeypatch.setattr(lab.module, 'Cancellation', lambda: cancelled)
    real_stop = lab.module.stop_lab

    def stop(binaries, observed):
        cancelled._handler(signal_module.SIGTERM, None)   # arrives while stopping: deferred, not raised
        return real_stop(binaries, observed)
    monkeypatch.setattr(lab.module, 'stop_lab', stop)
    assert lab.module.main(CYCLE) == 130
    value = record(lab)
    assert value['status'] == 'FAIL' and value['reason'] == 'interrupted (SIGTERM)'
    assert value['native_lab']['cleanup']['state'] == 'stopped' and value['native_lab']['wipe']['wipe'] == 'done'
    assert value['native_lab']['final_state'] == 'absent' and lab.zabbix.calls == ['start', 'status', 'stop', 'reset']


def test_cycle_cleans_up_after_a_start_refusal_and_keeps_the_failure(lab, monkeypatch, capsys):
    cycle_environment(lab, monkeypatch)
    monkeypatch.setattr(lab.module, 'start_postgres', lambda binaries: (_ for _ in ()).throw(SystemExit('deployment lab PostgreSQL start failed')))
    assert lab.module.main(CYCLE) == 2
    out = json.loads(capsys.readouterr().out)
    assert out['refusal'] == 'deployment lab PostgreSQL start failed' and out['run_exit_code'] is None
    assert record(lab)['status'] == 'NOT_RUN' and record(lab)['reason'] == 'postgres_start_failed'
    # PostgreSQL failed before the Zabbix lab started; cleanup still stops and wipes both.
    assert out['final_state'] == 'absent' and lab.zabbix.calls == ['stop', 'reset']


def test_prepare_runner_requires_a_named_zabalack_wheel(lab, tmp_path):
    with pytest.raises(SystemExit, match='built zabalack wheel'):
        lab.module.prepare_runner(tmp_path / 'other.whl', tmp_path / 'venv')


@pytest.mark.parametrize('how', ['wipe-permission-error', 'zabbix-stop-malformed-output'])
def test_an_unexpected_cleanup_error_after_a_passing_run_is_recorded_as_fail(lab, monkeypatch, how):
    """Only SystemExit and KeyboardInterrupt were handled: any other cleanup error escaped and left the runner's PASS."""
    cycle_environment(lab, monkeypatch, runner_exit=0)
    if how == 'wipe-permission-error':
        def rmtree(path, ignore_errors=False):
            raise PermissionError(13, 'Permission denied', str(path))
        monkeypatch.setattr(lab.module.shutil, 'rmtree', rmtree)
        error = 'PermissionError'
    else:
        real = lab.module.zabbix

        def zabbix(command, *args, check=True):
            result = real(command, *args, check=check)
            return subprocess.CompletedProcess(command, 0, 'warning: not json\n{', '') if command == 'stop' else result
        monkeypatch.setattr(lab.module, 'zabbix', zabbix)
        error = None
    code = lab.module.main(CYCLE)
    value = record(lab)
    assert code == 5
    assert value['status'] == 'FAIL' and value['native_lab']['cleanup_complete'] is False
    if error:
        assert value['reason'] == 'lab_cleanup_error: ' + error and value['native_lab']['cleanup']['error'] == error
    else:
        # An unreadable Zabbix stop report still lets PostgreSQL stop, and is not a clean cleanup.
        assert value['native_lab']['cleanup']['cleanup'] == {'zabbix': 'UNREADABLE_OUTPUT', 'postgres': {'status': 'STOPPED', 'exit_code': 0}}
        assert value['reason'] == 'lab_cleanup_incomplete: final state stopped'


def test_an_unexpected_error_before_the_run_is_recorded_and_never_leaves_pass(lab, monkeypatch):
    cycle_environment(lab, monkeypatch, runner_exit=0)
    stale_pass_record(lab)

    def start_postgres(binaries):
        raise RuntimeError('synthetic start failure')
    monkeypatch.setattr(lab.module, 'start_postgres', start_postgres)
    assert lab.module.main(CYCLE) == 1
    value = record(lab)
    assert value['status'] == 'NOT_RUN' and value['reason'] == 'lab_error: RuntimeError'
    assert 'supplied_wheel' not in value and 'artifact_mode' not in value and 'counts' not in value


def test_the_inner_runner_is_told_to_leave_its_record_pending_until_the_cycle_binds_it(lab, monkeypatch):
    launched = cycle_environment(lab, monkeypatch, runner_exit=0)
    seen = {}
    runner = lab.module.run_child

    def capture(command, **kwargs):
        seen.update(kwargs.get('env') or {})
        return runner(command, **kwargs)
    monkeypatch.setattr(lab.module, 'run_child', capture)
    assert lab.module.main(CYCLE) == 0
    assert launched and seen.get('ZABALACK_NATIVE_LAB_BINDING') == '1'
    assert 'pending' not in record(lab)['native_lab'] and record(lab)['native_lab']['final_state'] == 'absent'
