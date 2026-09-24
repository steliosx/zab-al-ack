"""lab/lab_trust_validation.py fails closed: the installation audit gates everything, and PASS needs every required case.

Review of 8211b1b: installed mode discarded verify.sh's exit code and stderr and started the lab anyway, and a
prerequisite, cleanup or identity failure could leave a summary that looked complete. Every host effect is replaced
here; nothing runs as root and no service starts.
"""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def driver(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('lab_trust_validation_under_test', ROOT / 'lab/lab_trust_validation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    events: list = []
    module.events = events
    module.audit_exit = 0

    class FakeLab:
        def __init__(self, *unused):
            pass

        def start(self):
            events.append('lab.start')
            raise RuntimeError('synthetic: the lab stops here')

        def stop_and_wipe(self):
            events.append('lab.stop_and_wipe')
            return [{'argv_tail': ['stop'], 'exit': module.wipe_exit, 'stdout': ''}]

        def command(self, *args, check=True):
            events.append('lab.' + args[0])
            return subprocess.CompletedProcess(args, 0, '', '')

    def sh(*argv, **unused):
        events.append(' '.join(str(part) for part in argv[:2]))
        if argv[0] == 'sh' and str(argv[1]).endswith('/deploy/lab-trust/verify.sh'):
            return subprocess.CompletedProcess(argv, module.audit_exit, 'differs: synthetic\n' if module.audit_exit else 'ok\n',
                                               'synthetic stderr\n' if module.audit_exit else '')
        if argv[:2] == ('systemctl', 'show'):
            return subprocess.CompletedProcess(argv, 0, 'ActiveState=inactive\nInvocationID=\nMainPID=0\n', '')
        return subprocess.CompletedProcess(argv, 0, '', '')
    module.wipe_exit = 0
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module, 'sh', sh)
    monkeypatch.setattr(module.acceptance, 'Lab', FakeLab)
    monkeypatch.setattr(module, 'host_state', lambda: {'synthetic': 'unchanged'})
    monkeypatch.setattr(module, 'immutable', lambda snapshot: None)
    monkeypatch.setattr(module, 'listening', lambda port: False)
    monkeypatch.setattr(module.shutil, 'copytree', lambda *unused, **unused_too: events.append('copytree'))
    for name in ('RUNTIME_UNIT', 'INSTALLED_UNIT', 'STAGE'):
        monkeypatch.setattr(module, name, tmp_path / 'absent' / name)
    monkeypatch.setattr(module, 'CONF', tmp_path / 'absent/conf-dir/zabbix_server.conf')
    module.out = tmp_path / 'result.json'
    module.run = lambda mode: (module.main(['--mode', mode, '--checkout', str(ROOT), '--work', str(tmp_path / 'work'),
                                           '--out', str(module.out)]), json.loads(module.out.read_text()))
    return module


def test_a_failed_installation_audit_refuses_before_anything_starts(driver):
    driver.audit_exit = 1
    code, result = driver.run('installed')
    assert code == 1 and 'lab.start' not in driver.events and 'copytree' not in driver.events
    assert not any(event.startswith(('systemctl stop', 'runuser')) for event in driver.events)
    assert result['observations']['verify_installation'] == {'returncode': 1, 'stdout': 'differs: synthetic\n',
                                                             'stderr': 'synthetic stderr\n'}
    assert result['summary']['status'] == 'FAIL' and result['summary']['failed'] == ['installation-audit']
    assert 'daemon-identity-and-namespace' in result['summary']['missing'] and result['summary']['error'].startswith('Refused')


def test_a_mutable_snapshot_is_not_executed_from(driver, monkeypatch):
    monkeypatch.setattr(driver, 'immutable', lambda snapshot: f'{snapshot} is not root-owned and closed to other writers')
    code, result = driver.run('installed')
    assert code == 1 and not any('verify.sh' in event for event in driver.events) and 'lab.start' not in driver.events
    assert result['observations']['verify_installation']['returncode'] is None
    assert result['summary']['failed'] == ['installation-audit']


def test_a_passing_audit_proceeds_and_an_exception_is_the_recorded_error(driver):
    code, result = driver.run('installed')
    assert code == 1 and driver.events.index('lab.start') > driver.events.index('copytree')
    assert result['cases'][0] == {'case': 'installation-audit', 'returncode': 0, 'pass': True}
    assert result['summary']['error'] == 'RuntimeError: synthetic: the lab stops here' and result['summary']['status'] == 'FAIL'


def test_a_failed_cleanup_is_a_failed_case(driver):
    driver.wipe_exit = 1
    code, result = driver.run('installed')
    assert code == 1 and 'cleanup' in result['summary']['failed']


def test_an_existing_prerequisite_path_refuses_pre_install_before_any_change(driver):
    driver.STAGE.mkdir(parents=True)
    code, result = driver.run('pre-install')
    assert code == 1 and 'copytree' not in driver.events and 'lab.start' not in driver.events
    assert result['summary']['failed'] == ['prerequisites']


def cases(names, **overrides):
    return [{'case': name, 'pass': overrides.get(name, True)} for name in names]


def test_pass_needs_every_required_case_once_and_no_error(driver):
    required = driver.REQUIRED_CASES['installed']
    assert driver.summarize('installed', cases(required), None)['status'] == 'PASS'
    assert driver.summarize('installed', cases(required), 'AssertionError: ')['status'] == 'FAIL'
    assert driver.summarize('installed', cases(required[1:]), None)['missing'] == ['installation-audit']
    assert driver.summarize('installed', cases(required + ['cleanup']), None)['duplicated'] == ['cleanup']
    assert driver.summarize('installed', cases(required, **{'daemon-identity-and-namespace': False}), None)['status'] == 'FAIL'
    assert driver.summarize('pre-install', cases(driver.REQUIRED_CASES['pre-install']), None)['status'] == 'PASS'
