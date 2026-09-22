"""tools/runner_account_check.py: read-only, fail-closed host capability verification (R28).

Process, socket, unit and configuration inputs are synthetic or created under a
temporary root; the verdict logic is exercised on synthetic reports. Nothing on
the host is changed.
"""
import json
import os
from pathlib import Path
import pwd
import socket
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import runner_account_check as check

STATUS = ('Name:\tRunner.Listener\nUmask:\t0022\nState:\tS (sleeping)\nPid:\t4242\nPPid:\t1\n'
          'Uid:\t1500\t1500\t1500\t1500\nGid:\t1500\t1500\t1500\t1500\nGroups:\t1500 996 \n'
          'NoNewPrivs:\t0\nCapInh:\t0000000000000000\nCapPrm:\t0000000000000000\nCapEff:\t0000000000000000\n'
          'CapBnd:\t000001ffffffffff\nCapAmb:\t0000000000000000\n')


def acl_blob(*entries):
    """system.posix_acl_access bytes: version 2 then (tag, perm, id) entries."""
    tags = {'user_obj': 1, 'user': 2, 'group_obj': 4, 'group': 8, 'mask': 16, 'other': 32}
    raw = struct.pack('<I', 2)
    for tag, perm, ident in entries:
        raw += struct.pack('<HHI', tags[tag], perm, 0xFFFFFFFF if ident is None else ident)
    return raw


def test_process_status_parsing_exposes_identity_groups_and_capabilities():
    identity = check.parse_status(STATUS)
    assert identity['uid'] == {'real': 1500, 'effective': 1500, 'saved': 1500, 'fs': 1500}
    assert identity['supplementary_group_ids'] == [996, 1500] and identity['no_new_privs'] is False
    assert check.capability_names(identity['CapEff']) == []
    assert check.capability_names('0000000000200002') == ['CAP_DAC_OVERRIDE', 'CAP_SYS_ADMIN']
    with pytest.raises(ValueError):
        check.parse_status(STATUS.replace('Uid:\t1500\t1500\t1500\t1500', 'Uid:\t1500'))
    with pytest.raises(ValueError):
        check.parse_status(STATUS.replace('CapEff:\t0000000000000000', 'CapEff:\tnot-hex'))


def test_posix_acl_parsing_matches_the_kernel_layout():
    entries = check.parse_posix_acl(acl_blob(('user_obj', 6, None), ('user', 6, 996), ('group_obj', 6, None), ('mask', 6, None), ('other', 0, None)))
    assert entries[1] == {'tag': 'user', 'perm': 6, 'id': 996} and entries[4] == {'tag': 'other', 'perm': 0, 'id': None}
    with pytest.raises(ValueError):
        check.parse_posix_acl(b'\x01\x00\x00\x00')
    with pytest.raises(ValueError):
        check.parse_posix_acl(b'\x02\x00\x00\x00\x01')


@pytest.mark.parametrize('mode,acl,identity,expected', [
    (0o660, None, {'uid': 1500, 'gid': 1500, 'groups': [1500]}, False),
    (0o660, None, {'uid': 1500, 'gid': 1500, 'groups': [1500, 996]}, True),
    (0o666, None, {'uid': 1500, 'gid': 1500, 'groups': [1500]}, True),
    (0o660, [('user_obj', 6, None), ('user', 6, 1500), ('group_obj', 6, None), ('mask', 6, None), ('other', 0, None)],
     {'uid': 1500, 'gid': 1500, 'groups': [1500]}, True),
    (0o660, [('user_obj', 6, None), ('user', 6, 1500), ('group_obj', 6, None), ('mask', 4, None), ('other', 0, None)],
     {'uid': 1500, 'gid': 1500, 'groups': [1500]}, False),
    (0o660, [('user_obj', 6, None), ('group', 6, 1500), ('group_obj', 6, None), ('mask', 6, None), ('other', 0, None)],
     {'uid': 1501, 'gid': 1500, 'groups': [1500]}, True),
    (0o660, None, {'uid': 0, 'gid': 0, 'groups': [0]}, True),
], ids=['group-mode-not-member', 'group-mode-member', 'world-writable', 'acl-user-grant', 'acl-mask-blocks', 'acl-group-grant', 'root'])
def test_inferred_write_applies_mode_bits_named_entries_and_the_mask(mode, acl, identity, expected):
    info = SimpleNamespace(st_mode=0o140000 | mode, st_uid=0, st_gid=996)
    acl_record = {'status': 'OBSERVED', 'entries': acl and check.parse_posix_acl(acl_blob(*acl))}
    assert check.inferred_write(info, acl_record, identity) is expected


def test_service_observation_merges_fragment_drop_ins_and_environment_files(tmp_path):
    root = tmp_path / 'systemd'
    (root / 'actions.runner.x.service.d').mkdir(parents=True)
    env_file = tmp_path / 'runner.env'
    env_file.write_text('LANG=C.UTF-8\nDOCKER_HOST=tcp://10.0.0.5:2376\n')
    (root / 'actions.runner.x.service').write_text(f'[Service]\nUser=zabalack-runner\nSupplementaryGroups=docker\nExecStart=/opt/run.sh --once\n'
                                                   f'Environment="LANG=C.UTF-8" DOCKER_TLS_VERIFY=1\nEnvironmentFile={env_file}\nEnvironmentFile=-{tmp_path}/absent.env\n')
    (root / 'actions.runner.x.service.d/10-override.conf').write_text('[Service]\nEnvironment=DOCKER_CONTEXT=lab-remote\n')
    runner_root = tmp_path / 'runner'
    runner_root.mkdir()
    (runner_root / '.env').write_text('DOCKER_CONFIG=/srv/docker-config\n')
    record = check.service_observation('actions.runner.*', str(root), runner_root)
    assert record['status'] == 'OBSERVED' and record['user'] == 'zabalack-runner' and record['supplementary_groups'] == ['docker']
    assert record['exec_start'] == '/opt/run.sh' and len(record['drop_ins']) == 1
    assert record['environment'] == {'DOCKER_TLS_VERIFY': '1', 'DOCKER_HOST': 'tcp://10.0.0.5:2376', 'DOCKER_CONTEXT': 'lab-remote',
                                     'DOCKER_CONFIG': '/srv/docker-config'}
    assert record['environment_files'][1] == {'path': f'{tmp_path}/absent.env', 'absent': True}
    assert 'LANG' not in json.dumps(record)
    # A required environment file that is missing is a failed observation, not an absent setting.
    (root / 'actions.runner.x.service').write_text(f'[Service]\nEnvironmentFile={tmp_path}/required.env\n')
    assert check.service_observation('actions.runner.*', str(root), runner_root)['status'] == 'NOT_OBSERVED'
    assert check.service_observation('actions.runner.*', str(tmp_path / 'empty'), runner_root)['reason'].startswith('runner service unit not found')


@pytest.mark.skipif(os.geteuid() == 0, reason='root reads every file')
def test_unreadable_unit_or_environment_file_is_not_observed(tmp_path):
    root = tmp_path / 'systemd'
    root.mkdir()
    unit = root / 'actions.runner.x.service'
    unit.write_text('[Service]\nUser=x\n')
    unit.chmod(0)
    assert check.service_observation('actions.runner.x', str(root), None)['status'] == 'NOT_OBSERVED'


def test_docker_config_of_the_intended_account_reports_contexts_but_never_auth_values(tmp_path):
    config = tmp_path / '.docker'
    (config / 'contexts/meta/abc').mkdir(parents=True)
    (config / 'config.json').write_text(json.dumps({'currentContext': 'lab-remote', 'auths': {'registry.example': {'auth': 'SECRET-VALUE'}}}))
    (config / 'contexts/meta/abc/meta.json').write_text(json.dumps({'Name': 'lab-remote', 'Endpoints': {'docker': {'Host': 'tcp://10.0.0.5:2376'}}}))
    record = check.docker_config_observation(config)
    assert record['status'] == 'OBSERVED' and record['config_json'] == {'present': True, 'currentContext': 'lab-remote', 'stored_auths': 1}
    assert record['contexts'] == [{'name': 'lab-remote', 'endpoint': 'tcp://10.0.0.5:2376'}] and record['remote_context'] is True
    assert 'SECRET-VALUE' not in json.dumps(record) and 'registry.example' not in json.dumps(record)
    absent = check.docker_config_observation(tmp_path / 'nothing')
    assert absent['status'] == 'OBSERVED' and absent['config_json'] == {'present': False} and absent['contexts'] == []
    (config / 'config.json').write_text('{not json')
    assert check.docker_config_observation(config)['status'] == 'NOT_OBSERVED'


@pytest.fixture
def child():
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], env={'PATH': os.environ.get('PATH', ''), 'DOCKER_HOST': 'unix:///synthetic.sock', 'SECRET_TOKEN': 'private'})
    yield process
    process.kill()
    process.wait()


def test_running_process_identity_is_read_from_proc_and_compared_with_the_account(child):
    me = pwd.getpwuid(os.geteuid())
    account = check.account_observation(me.pw_name)
    record = check.process_observation([child.pid], account)
    assert record['status'] == 'OBSERVED' and record['pids'] == [child.pid]
    assert record['uid']['effective'] == os.geteuid() and record['matches_configured_account'] is True
    assert set(record['supplementary_group_ids']) <= set(account['group_ids']) | {os.getegid()}
    environment = check.process_environment(child.pid)
    assert environment == {'status': 'OBSERVED', 'settings': {'DOCKER_HOST': 'unix:///synthetic.sock'}}
    assert check.process_observation([], account)['status'] == 'NOT_OBSERVED'
    assert check.process_observation([999999999], account)['status'] == 'NOT_OBSERVED'


def test_socket_access_is_demonstrated_under_the_current_identity_and_agrees_with_inference(tmp_path):
    path = tmp_path / 'lab.sock'
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen(1)
    try:
        identity = {'uid': os.geteuid(), 'gid': os.getegid(), 'groups': sorted(set(os.getgroups()))}
        record = check.socket_observation(str(path), identity)
        assert record['status'] == 'OBSERVED' and record['socket'] is True
        assert record['demonstrated'] == {'status': 'OBSERVED', 'connected': True, 'permission_denied': False, 'no_listener': False}
        if record['acl']['status'] == 'OBSERVED':
            assert record['inferred_write'] is True
        assert check.socket_observation(str(path), None)['demonstrated']['status'] == 'NOT_OBSERVED'
    finally:
        listener.close()
    absent = check.socket_observation(str(tmp_path / 'none.sock'), identity)
    assert absent == {'status': 'OBSERVED', 'present': False}
    plain = tmp_path / 'plain'
    plain.write_text('x')
    assert check.socket_observation(str(plain), identity)['socket'] is False


@pytest.mark.skipif(os.geteuid() != 0, reason='identity switching requires root')
def test_root_verifier_demonstrates_access_as_another_account_and_acl_grants_are_honoured(tmp_path):
    try:
        entry = pwd.getpwnam('nobody')
    except KeyError:
        pytest.skip('no unprivileged account')
    import tempfile
    base = Path(tempfile.mkdtemp(prefix='zabalack-sock-', dir='/var/tmp'))  # traversable by the other account
    base.chmod(0o755)
    path = base / 'lab.sock'
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen(1)
    os.chmod(path, 0o600)
    identity = {'uid': entry.pw_uid, 'gid': entry.pw_gid, 'groups': [entry.pw_gid]}
    try:
        denied = check.socket_observation(str(path), identity)
        assert denied['demonstrated']['status'] == 'OBSERVED' and denied['demonstrated']['connected'] is False
        if os.getxattr and subprocess.run(['which', 'setfacl'], capture_output=True).returncode == 0:
            subprocess.run(['setfacl', '-m', f'u:{entry.pw_name}:rw', str(path)], check=True)
            granted = check.socket_observation(str(path), identity)
            assert granted['acl']['entries'] and granted['inferred_write'] is True
            assert granted['demonstrated']['connected'] is True
        listener.close()
        stale = check.socket_observation(str(path), identity)
        assert stale['demonstrated']['connected'] is False and stale['demonstrated']['no_listener'] is True
    finally:
        listener.close()
        path.unlink(missing_ok=True)
        base.rmdir()


def synthetic_report(**overrides):
    account = {'status': 'OBSERVED', 'name': 'zabalack-runner', 'uid': 1500, 'gid': 1500, 'home': '/var/lib/r', 'shell': '/bin/bash',
               'group_ids': [1500], 'groups': ['zabalack-runner']}
    process = {'status': 'OBSERVED', 'pids': [7], 'uid': {'real': 1500, 'effective': 1500, 'saved': 1500, 'fs': 1500},
               'gid': {'real': 1500, 'effective': 1500, 'saved': 1500, 'fs': 1500}, 'supplementary_group_ids': [1500],
               'supplementary_groups': ['zabalack-runner'], 'capabilities': {'CapEff': [], 'CapPrm': [], 'CapAmb': [], 'CapInh': []},
               'no_new_privs': False, 'matches_configured_account': True, 'groups_not_configured': [], 'configured_groups_not_held': []}
    socket_record = {'status': 'OBSERVED', 'present': True, 'socket': True, 'mode': '0o660', 'owner_uid': 0, 'group': 'docker',
                     'acl': {'status': 'OBSERVED', 'entries': None}, 'inferred_write': False,
                     'demonstrated': {'status': 'OBSERVED', 'connected': False, 'errno': 13, 'permission_denied': True, 'no_listener': False}}
    report = {'account': account, 'runner_process': process, 'runner_process_environment': {'status': 'OBSERVED', 'settings': {}},
              'sudo': {'status': 'OBSERVED', 'listed': [], 'file_mentions': [], 'any_rule': False},
              'sockets': {'/var/run/docker.sock': socket_record},
              'service': {'status': 'OBSERVED', 'environment': {}, 'supplementary_groups': [], 'user': 'zabalack-runner'},
              'docker_config': {'status': 'OBSERVED', 'config_json': {'present': False}, 'contexts': [], 'remote_context': False}}
    for key, value in overrides.items():
        target = report
        parts = key.split('::')
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    return report


def test_a_fully_observed_conforming_host_conforms_under_both_policies(monkeypatch):
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker'}.get(gid, str(gid)))
    for policy in ('docker-free', 'docker-allowed'):
        assert check.evaluate(synthetic_report(), policy) == ('CONFORMS', [], [])


@pytest.mark.parametrize('override,fragment', [
    ({'account': {'status': 'NOT_OBSERVED', 'reason': 'account does not exist: x'}}, 'account:'),
    ({'runner_process': {'status': 'NOT_OBSERVED', 'reason': 'runner process not running'}}, 'runner_process:'),
    ({'runner_process': {'status': 'NOT_OBSERVED', 'reason': 'ambiguous process identity'}}, 'ambiguous'),
    ({'sudo': {'status': 'NOT_OBSERVED', 'reason': 'sudo rules require root'}}, 'sudo:'),
    ({'sockets::/var/run/docker.sock::acl': {'status': 'NOT_OBSERVED', 'reason': 'ACL unreadable: permission denied'}}, 'ACL'),
    ({'sockets::/var/run/docker.sock::demonstrated': {'status': 'NOT_OBSERVED', 'reason': 'cannot assume the runner identity without root'}}, 'not demonstrated'),
    ({'service': {'status': 'NOT_OBSERVED', 'reason': 'unit file unreadable'}}, 'service:'),
    ({'runner_process_environment': {'status': 'NOT_OBSERVED', 'reason': 'insufficient permission'}}, 'runner_process_environment:'),
    ({'docker_config': {'status': 'NOT_OBSERVED', 'reason': 'docker config.json unreadable'}}, 'docker_config:'),
], ids=['no-account', 'no-process', 'ambiguous-process', 'sudo-not-root', 'acl-unreadable', 'access-not-demonstrated',
        'unit-unreadable', 'process-env-unreadable', 'config-unreadable'])
def test_a_missing_required_observation_is_incomplete_never_conforms(monkeypatch, override, fragment):
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker'}.get(gid, str(gid)))
    result, deviations, incomplete = check.evaluate(synthetic_report(**override), 'docker-free')
    assert result == 'INCOMPLETE' and deviations == [] and any(fragment in item for item in incomplete)


@pytest.mark.parametrize('override,policy,fragment', [
    ({'runner_process::supplementary_group_ids': [1500, 996], 'runner_process::supplementary_groups': ['docker', 'zabalack-runner'],
      'runner_process::groups_not_configured': ['docker']}, 'docker-free', 'running runner holds the docker group'),
    ({'runner_process::supplementary_groups': ['zabalack-runner', 'kvm'], 'runner_process::supplementary_group_ids': [1500, 1600]}, 'docker-allowed', 'host-capable groups'),
    ({'runner_process::matches_configured_account': False}, 'docker-allowed', 'not the configured account'),
    ({'runner_process::capabilities': {'CapEff': ['CAP_SYS_ADMIN'], 'CapPrm': ['CAP_SYS_ADMIN'], 'CapAmb': [], 'CapInh': []}}, 'docker-allowed', 'capabilities'),
    ({'account::groups': ['docker', 'zabalack-runner']}, 'docker-free', 'configured account is a member of the docker group'),
    ({'sudo::any_rule': True}, 'docker-allowed', 'sudo'),
    ({'sockets::/var/run/docker.sock::demonstrated': {'status': 'OBSERVED', 'connected': True, 'permission_denied': False, 'no_listener': False}, 'sockets::/var/run/docker.sock::inferred_write': True}, 'docker-free', 'can connect'),
    ({'sockets::/var/run/docker.sock::demonstrated': {'status': 'OBSERVED', 'connected': True, 'permission_denied': False, 'no_listener': False}}, 'docker-allowed', 'disagree'),
    ({'service::environment': {'DOCKER_HOST': 'tcp://x'}}, 'docker-allowed', 'service environment'),
    ({'service::supplementary_groups': ['docker']}, 'docker-free', 'unit grants'),
    ({'service::user': 'someone-else'}, 'docker-allowed', 'different user'),
    ({'runner_process_environment::settings': {'DOCKER_CONTEXT': 'x'}}, 'docker-allowed', 'running runner environment'),
    ({'docker_config::remote_context': True}, 'docker-allowed', 'remote daemon'),
    ({'docker_config::config_json': {'present': True, 'currentContext': 'lab', 'stored_auths': 0}}, 'docker-allowed', 'non-default context'),
], ids=['process-docker-group', 'process-kvm', 'process-other-account', 'process-capabilities', 'account-docker-group', 'sudo-rule',
        'socket-connect', 'socket-inference-disagrees', 'service-env', 'unit-groups', 'unit-user', 'process-env', 'remote-context', 'current-context'])
def test_each_deviation_is_named_and_wins_over_incompleteness(monkeypatch, override, policy, fragment):
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker', 1600: 'kvm'}.get(gid, str(gid)))
    report = synthetic_report(**override)
    result, deviations, _ = check.evaluate(report, policy)
    assert result == 'DEVIATES' and any(fragment in item for item in deviations)
    # A deviation keeps the verdict DEVIATES even when other observations are missing.
    report['service'] = {'status': 'NOT_OBSERVED', 'reason': 'unit file unreadable'}
    report['docker_config']['remote_context'] = True
    result, deviations, incomplete = check.evaluate(report, policy)
    assert result == 'DEVIATES' and incomplete == ['service: unit file unreadable']


def test_socket_access_under_docker_allowed_is_reported_not_a_deviation(monkeypatch):
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker'}.get(gid, str(gid)))
    report = synthetic_report(**{'sockets::/var/run/docker.sock::demonstrated': {'status': 'OBSERVED', 'connected': True, 'permission_denied': False, 'no_listener': False},
                                 'sockets::/var/run/docker.sock::inferred_write': True, 'account::groups': ['docker', 'zabalack-runner']})
    assert check.evaluate(report, 'docker-allowed') == ('CONFORMS', [], [])
    assert check.evaluate(report, 'docker-free')[0] == 'DEVIATES'


def test_cli_fails_closed_on_an_unknown_account_and_never_prints_secrets(tmp_path, capsys, child):
    root = tmp_path / 'systemd'
    root.mkdir()
    (root / 'actions.runner.t.service').write_text('[Service]\nUser=zabalack-runner\n')
    code = check.main(['--account', 'no-such-account-here', '--systemd-root', str(root), '--unit', 'actions.runner.t',
                       '--runner-root', str(tmp_path), '--runner-pid', str(child.pid), '--socket', str(tmp_path / 'none.sock')])
    out = capsys.readouterr().out
    report = json.loads(out)
    # The synthetic runner process is this test's own child, so as root it holds
    # root's groups and capabilities and is reported as deviating; either way the
    # missing account is a failed observation and the verdict is never CONFORMS.
    assert report['result'] in ('INCOMPLETE', 'DEVIATES') and code == {'INCOMPLETE': 2, 'DEVIATES': 1}[report['result']]
    assert any('account does not exist' in item for item in report['incomplete'])
    assert 'SECRET_TOKEN' not in out and 'private' not in out


def test_a_stale_permissive_socket_is_a_latent_grant_not_a_disagreement(monkeypatch):
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker'}.get(gid, str(gid)))
    report = synthetic_report(**{'sockets::/var/run/docker.sock::demonstrated': {'status': 'OBSERVED', 'connected': False, 'errno': 111,
                                                                                 'permission_denied': False, 'no_listener': True},
                                 'sockets::/var/run/docker.sock::inferred_write': True})
    result, deviations, _ = check.evaluate(report, 'docker-free')
    assert result == 'DEVIATES' and deviations == ['runner identity may open the container socket /var/run/docker.sock (no daemon listening now)']
    assert check.evaluate(report, 'docker-allowed') == ('CONFORMS', [], [])
