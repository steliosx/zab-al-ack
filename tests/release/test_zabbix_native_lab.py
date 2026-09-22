"""lab/zabbix_native_lab.py: state classification, refusals and generated configuration (R27).

Every service, PID and port is mocked; no PostgreSQL, zabbix_server or PHP starts.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


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
    if initialised:
        lab.PGDATA.mkdir()
        (lab.PGDATA / 'PG_VERSION').write_text('18\n')


def tree(root):
    return sorted((str(path.relative_to(root)), path.read_bytes() if path.is_file() else None) for path in root.rglob('*'))


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
], ids=['absent', 'port-without-lab', 'unmarked', 'stopped', 'server-running', 'two-running', 'stale-pid',
        'stale-pid-port-busy', 'unreadable-pid', 'foreign-listener'])
def test_inspect_classifies_the_lab_without_touching_it(lab, tmp_path, monkeypatch, layout, pids, listening, expected):
    if layout == 'unmarked':
        lab.STATE.mkdir()
        (lab.STATE / 'stranger').write_text('not ours')
    elif layout == 'marked':
        marked(lab)
    for name, kind in pids.items():
        lab.PID_FILES[name].write_text({'alive': f'{os.getpid()}\n', 'dead': '999999\n', 'garbage': 'x\n'}[kind])
    monkeypatch.setattr(lab, 'port_listening', lambda port: listening.get(next(n for n, p in lab.PORTS.items() if p == port), False))
    before = tree(tmp_path)
    observed = lab.inspect()
    assert observed['state'] == expected, observed
    assert observed['api_url'] == 'http://127.0.0.1:58080/api_jsonrpc.php'
    assert tree(tmp_path) == before
    if expected == 'running':
        assert all(observed['components'][name] == 'running' for name in pids)


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
    (lab.STATE / 'zabbix_server.pid').write_text(f'{os.getpid()}\n')
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
    assert out['cleanup'] == {'php': {'status': 'NOT_RUNNING'}, 'zabbix_server': {'status': 'NOT_RUNNING'},
                              'postgres': {'status': 'NOT_RUNNING'}} and out['state']['state'] == 'stopped'
    assert not (lab.STATE / 'php.pid').exists()
    # A component that pg_ctl still reports running after the stop leaves exit 5.
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '', ''))
    monkeypatch.setattr(lab, 'port_listening', lambda port: port == lab.PG_PORT)
    (lab.PGDATA / 'postmaster.pid').write_text(f'{os.getpid()}\n')
    assert lab.main(['stop', '--isolated-lab']) == 5


def test_generated_configurations_are_loopback_only_and_keep_the_database_policy_default(lab, paths, monkeypatch):
    marked(lab)
    monkeypatch.setattr(lab, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0, '', ''))
    monkeypatch.setattr(lab, 'port_listening', lambda port: True)
    lab.start_server(paths)
    conf = (lab.STATE / 'zabbix_server.conf').read_text()
    assert 'ListenIP=127.0.0.1' in conf and f'ListenPort={lab.SERVER_PORT}' in conf and 'DBHost=127.0.0.1' in conf
    assert f'DBPort={lab.PG_PORT}' in conf and 'AllowRoot=0' in conf and 'AllowUnsupportedDBVersions' not in conf
    assert 'DBPassword' not in conf
    lab.stage_frontend(paths)
    front = (lab.UI / 'conf/zabbix.conf.php').read_text()
    assert "$DB['SERVER']   = '127.0.0.1';" in front and f"$DB['PORT']     = '{lab.PG_PORT}';" in front
    assert f"$ZBX_SERVER_PORT = '{lab.SERVER_PORT}';" in front and "$ZBX_SERVER      = '127.0.0.1';" in front
    assert (lab.UI / 'api_jsonrpc.php').exists() and (lab.UI / 'include/defines.inc.php').exists()
    assert lab.PHP_INI[lab.PHP_INI.index('-d') + 1] == 'memory_limit=128M' and 'max_execution_time=300' in lab.PHP_INI


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


def test_inputs_are_required_and_named_when_absent(lab, monkeypatch):
    for key in ('ACK_LAB_PG_BIN', 'ZABALACK_ZABBIX_SERVER', 'ZABALACK_ZABBIX_UI', 'ZABALACK_ZABBIX_SCHEMA', 'ZABALACK_PHP'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(lab.shutil, 'which', lambda name: None)
    with pytest.raises(SystemExit, match='NOT_RUN'):
        lab.inputs()


def test_start_continues_a_partially_started_lab_without_restarting_live_components(lab, paths, monkeypatch):
    marked(lab)
    (lab.STATE / 'zabbix_server.pid').write_text(f'{os.getpid()}\n')   # server already up
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
