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

    # The real Lab (and so its real stop/collect/wipe sequencing); only its runuser commands and start are replaced.
    module.unit = {'ActiveState': 'inactive', 'InvocationID': ''}
    module.on_start = None

    class FakeLab(module.acceptance.Lab):
        def start(self):
            events.append('lab.start')
            if module.on_start:
                module.on_start(self)
            raise RuntimeError('synthetic: the lab stops here')

        def command(self, *args, check=True):
            events.append('lab.' + args[0])
            return subprocess.CompletedProcess(args, module.wipe_exit if args[0] == 'stop' else 0, '', '')

    def sh(*argv, **unused):
        events.append(' '.join(str(part) for part in argv[:2]))
        if argv[0] == 'sh' and str(argv[1]).endswith('/deploy/lab-trust/verify.sh'):
            return subprocess.CompletedProcess(argv, module.audit_exit, 'differs: synthetic\n' if module.audit_exit else 'ok\n',
                                               'synthetic stderr\n' if module.audit_exit else '')
        if argv[:2] == ('systemctl', 'show'):
            shown = f"ActiveState={module.unit['ActiveState']}\nInvocationID={module.unit['InvocationID']}\nMainPID=0\n"
            return subprocess.CompletedProcess(argv, 0, shown, '')
        if argv[:2] == ('systemctl', 'start'):
            module.unit.update(ActiveState='active', InvocationID='d' * 32)
        if argv[:2] == ('systemctl', 'stop'):
            module.unit.update(ActiveState='inactive')
        return subprocess.CompletedProcess(argv, 0, '', '')
    module.wipe_exit = 0
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module, 'sh', sh)
    monkeypatch.setattr(module.acceptance, 'Lab', FakeLab)
    monkeypatch.setattr(module, 'host_state', lambda: {'synthetic': 'unchanged'})
    monkeypatch.setattr(module, 'immutable', lambda snapshot: None)
    monkeypatch.setattr(module, 'listening', lambda port: False)
    def copytree(source, target, **unused):
        events.append('copytree')
        Path(target).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(module.shutil, 'copytree', copytree)
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
    assert [event for event in driver.events if not event.startswith('sh ')] == []  # after a failed audit, no host call at all
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


def test_every_attempt_is_collected_privately_before_the_wipe(driver, monkeypatch, tmp_path):
    checkout = tmp_path / 'work/checkout'
    (checkout / '.zabbix-lab').mkdir(parents=True)
    (checkout / '.zabbix-lab/zabbix_server.log').write_text('synthetic service log\n')
    (checkout / '.zabbix-lab/zabbix-db-password').write_text('not collected\n')
    real_collector = driver.acceptance.collector

    def recording_collector(bundle, attempt, sources, new=False):
        run = real_collector(bundle, attempt, sources, new)
        def collect():
            driver.events.append('collect')
            return run()
        return collect
    monkeypatch.setattr(driver.acceptance, 'collector', recording_collector)
    code, result = driver.run('installed')
    assert driver.events.index('lab.stop') < driver.events.index('collect') < driver.events.index('lab.reset')
    bundle = Path(result['summary']['private_evidence'])
    assert bundle.parent == tmp_path / 'result.json.private-evidence' and (bundle.parent / 'PRIVATE.txt').exists()
    manifest = json.loads((bundle / 'MANIFEST.json').read_text())
    assert [entry['path'] for entry in manifest['files']] == ['.zabbix-lab/zabbix_server.log', 'result.json']
    assert (bundle.stat().st_mode & 0o777) == 0o700 and 'cleanup' not in result['summary']['failed']


def test_a_refused_attempt_is_collected_too(driver):
    driver.audit_exit = 1
    code, result = driver.run('installed')
    manifest = json.loads((Path(result['summary']['private_evidence']) / 'MANIFEST.json').read_text())
    assert manifest['absent'][0]['path'] == 'evidence/deployment-e2e-run.json'  # nothing existed yet: recorded, not skipped
    assert [entry['path'] for entry in manifest['files']] == ['result.json']


# Review of 97925db: `started` was set before lab.start(), and the finally block stopped the unit as root even
# when that start had refused a unit this attempt never started. The driver now requires the unit idle before
# anything starts, records the invocations this attempt creates, and stops only those.
@pytest.mark.parametrize('state', ['active', 'activating', 'deactivating', ''])
def test_a_busy_or_unobservable_unit_refuses_the_attempt_before_anything_starts(driver, state):
    driver.unit.update(ActiveState=state, InvocationID='f' * 32 if state else '')
    code, result = driver.run('installed')
    assert code == 1 and result['summary']['failed'] == ['prerequisites']
    assert 'copytree' not in driver.events and 'lab.start' not in driver.events
    assert 'systemctl stop' not in driver.events and 'systemctl start' not in driver.events
    assert driver.unit['InvocationID'] == ('f' * 32 if state else '') and driver.unit['ActiveState'] == state


def test_a_foreign_invocation_during_a_refused_start_is_not_stopped(driver):
    driver.on_start = lambda lab: driver.unit.update(ActiveState='activating', InvocationID='f' * 32)
    code, result = driver.run('installed')
    assert code == 1 and 'systemctl stop' not in driver.events
    assert result['cleanup']['unit']['action'] == 'left_running_not_this_attempt'
    assert result['cleanup']['invocations_created'] == [] and 'cleanup' in result['summary']['failed']
    assert driver.unit == {'ActiveState': 'activating', 'InvocationID': 'f' * 32}


def test_the_invocation_this_attempt_created_is_stopped(driver, tmp_path):
    def bind(lab):  # the lab's own start binds the invocation it produced, then the run fails
        (lab.checkout / '.zabbix-lab').mkdir(parents=True, exist_ok=True)
        (lab.checkout / '.zabbix-lab/trust-unit.owner.json').write_text(json.dumps({'invocation_id': 'c' * 32}))
        driver.unit.update(ActiveState='active', InvocationID='c' * 32)
    driver.on_start = bind
    code, result = driver.run('installed')
    assert driver.events.count('systemctl stop') == 1 and result['cleanup']['unit']['action'] == 'stopped'
    assert driver.unit['ActiveState'] == 'inactive' and 'cleanup' not in result['summary']['failed']


def test_an_incomplete_collection_leaves_the_lab_state_in_place(driver, monkeypatch, tmp_path):
    checkout = tmp_path / 'work/checkout'
    (checkout / '.zabbix-lab').mkdir(parents=True)
    log = checkout / '.zabbix-lab/zabbix_server.log'
    log.write_bytes(b'the only copy of this attempt\'s server log\n')
    tool = driver.acceptance.evidence_tool()
    def failing_fsync(fd):
        raise OSError(5, 'synthetic destination I/O failure')
    monkeypatch.setattr(tool.os, 'fsync', failing_fsync)
    code, result = driver.run('installed')
    steps = result['cleanup']['lab']
    assert code == 1 and 'lab.stop' in driver.events and 'lab.reset' not in driver.events  # stopped, never wiped
    assert steps[1]['collect']['status'] == 'INCOMPLETE' and steps[2]['wipe'] == 'SKIPPED'
    assert log.read_bytes() == b'the only copy of this attempt\'s server log\n' and 'cleanup' in result['summary']['failed']
