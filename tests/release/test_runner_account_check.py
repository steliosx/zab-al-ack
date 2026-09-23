"""tools/runner_account_check.py: read-only, fail-closed host capability verification (R28).

Process, socket, unit and configuration inputs are synthetic or created under a
temporary root; the verdict logic is exercised on synthetic reports. Nothing on
the host is changed.
"""
import json
import os
from pathlib import Path
import pwd
import re
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


# The runner unit as Opus observed it on zabalack-omarchy-x64 (2026-09-23): a systemd-run
# transient unit with its fragment under /run/systemd/transient, no drop-ins, no
# EnvironmentFiles, HOME in Environment, User/Group set and no supplementary groups.
TRANSIENT_UNIT = {
    'Id': ['actions.runner.zabalack-attended.service'], 'LoadState': ['loaded'], 'ActiveState': ['active'],
    'FragmentPath': ['/run/systemd/transient/actions.runner.zabalack-attended.service'], 'DropInPaths': [''], 'Transient': ['yes'],
    'User': ['zabalack-runner'], 'Group': ['zabalack-runner'], 'SupplementaryGroups': [''],
    'Environment': ['HOME=/var/lib/zabalack-runner'], 'EnvironmentFiles': [''],
    'ExecStart': ['{ path=/opt/zabalack-actions/bin/runsvc.sh ; argv[]=/opt/zabalack-actions/bin/runsvc.sh ; ignore_errors=no ; '
                  'start_time=[Tue 2026-09-22 23:07:56 UTC] ; stop_time=[n/a] ; pid=40502 ; code=(null) ; status=0/0 }'],
    'WorkingDirectory': ['/opt/zabalack-actions'], 'MainPID': ['40502'],
    'ControlGroup': ['/system.slice/actions.runner.zabalack-attended.service'], 'NoNewPrivileges': ['no'],
}


def show_of(units):
    """A systemctl show stand-in: unit name -> property lines; unknown units are not loaded."""
    def show(unit, properties):
        values = units.get(unit)
        if values is None:
            return {'LoadState': ['not-found']}
        return {key: list(value) for key, value in values.items() if key in properties}
    return show


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


def test_service_observation_reads_the_exact_transient_unit_through_systemctl_show(tmp_path):
    """R6: the runner is a systemd-run transient unit; directory scans never see it."""
    runner_root = tmp_path / 'runner'
    runner_root.mkdir()
    (runner_root / '.env').write_text('ACTIONS_RUNNER_HOOK_JOB_STARTED=/etc/zabalack-actions/job-start.sh\nLANG=C.UTF-8\n')
    unit = {**TRANSIENT_UNIT, 'WorkingDirectory': [str(runner_root)]}
    record = check.service_observation('actions.runner.zabalack-attended', None, show=show_of({'actions.runner.zabalack-attended.service': unit}))
    assert record['status'] == 'OBSERVED' and record['transient'] is True and record['drop_ins'] == []
    assert record['fragment_path'] == '/run/systemd/transient/actions.runner.zabalack-attended.service'
    assert record['user'] == 'zabalack-runner' and record['group'] == 'zabalack-runner' and record['supplementary_groups'] == []
    assert record['exec_start'] == '/opt/zabalack-actions/bin/runsvc.sh' and record['main_pid'] == 40502
    assert record['control_group'] == '/system.slice/actions.runner.zabalack-attended.service'
    # HOME and the hook paths are not Docker settings; the runner .env is read from the unit's WorkingDirectory.
    assert record['environment'] == {} and record['runner_env_file'] == {'path': str(runner_root / '.env'), 'settings': {}}
    assert 'HOME' not in json.dumps(record['environment']) and 'LANG' not in json.dumps(record)
    # The same name with the .service suffix is the same unit; a glob or a pattern is refused.
    assert check.service_observation('actions.runner.zabalack-attended.service', None, show=show_of({'actions.runner.zabalack-attended.service': unit}))['status'] == 'OBSERVED'
    for pattern in ('actions.runner.*', 'actions.runner.[a-z]', '', 'x y'):
        with pytest.raises(ValueError, match='exact unit name'):
            check.service_observation(pattern, None, show=show_of({}))
    # A unit systemd does not know, or no systemctl at all, is a failed observation.
    assert check.service_observation('actions.runner.other', None, show=show_of({}))['reason'].startswith('runner service unit not loaded')
    assert check.service_observation('actions.runner.other', None, show=lambda unit, properties: None)['reason'].startswith('systemctl unavailable')


def test_service_observation_merges_environment_lines_and_files_from_the_unit(tmp_path):
    env_file = tmp_path / 'runner.env'
    env_file.write_text('LANG=C.UTF-8\nDOCKER_HOST=tcp://10.0.0.5:2376\n')
    runner_root = tmp_path / 'runner'
    runner_root.mkdir()
    (runner_root / '.env').write_text('DOCKER_CONFIG=/srv/docker-config\n')
    unit = {**TRANSIENT_UNIT, 'Transient': ['no'], 'FragmentPath': ['/etc/systemd/system/actions.runner.x.service'],
            'DropInPaths': ['/etc/systemd/system/actions.runner.x.service.d/10-override.conf'], 'SupplementaryGroups': ['docker'],
            'Environment': ['"LANG=C.UTF-8" DOCKER_TLS_VERIFY=1', 'DOCKER_CONTEXT=lab-remote'],
            'EnvironmentFiles': [f'{env_file} (ignore_errors=no)', f'{tmp_path}/absent.env (ignore_errors=yes)']}
    record = check.service_observation('actions.runner.x', runner_root, show=show_of({'actions.runner.x.service': unit}))
    assert record['status'] == 'OBSERVED' and record['transient'] is False and record['supplementary_groups'] == ['docker']
    assert record['drop_ins'] == ['/etc/systemd/system/actions.runner.x.service.d/10-override.conf']
    assert record['environment'] == {'DOCKER_TLS_VERIFY': '1', 'DOCKER_CONTEXT': 'lab-remote', 'DOCKER_HOST': 'tcp://10.0.0.5:2376',
                                     'DOCKER_CONFIG': '/srv/docker-config'}
    assert record['environment_files'][1] == {'path': f'{tmp_path}/absent.env', 'absent': True}
    assert 'LANG' not in json.dumps(record)
    # A required environment file that is missing is a failed observation, not an absent setting.
    unit['EnvironmentFiles'] = [f'{tmp_path}/required.env (ignore_errors=no)']
    assert check.service_observation('actions.runner.x', runner_root, show=show_of({'actions.runner.x.service': unit}))['status'] == 'NOT_OBSERVED'


@pytest.mark.skipif(os.geteuid() == 0, reason='root reads every file')
def test_unreadable_environment_file_is_not_observed(tmp_path):
    env_file = tmp_path / 'runner.env'
    env_file.write_text('DOCKER_HOST=x\n')
    env_file.chmod(0)
    unit = {**TRANSIENT_UNIT, 'EnvironmentFiles': [f'{env_file} (ignore_errors=no)']}
    assert check.service_observation('actions.runner.zabalack-attended', None, show=show_of({'actions.runner.zabalack-attended.service': unit}))['status'] == 'NOT_OBSERVED'


def test_unit_processes_come_from_the_cgroup(tmp_path, monkeypatch):
    monkeypatch.setattr(check, 'CGROUP_ROOT', tmp_path)
    (tmp_path / 'system.slice/actions.runner.zabalack-attended.service').mkdir(parents=True)
    (tmp_path / 'system.slice/actions.runner.zabalack-attended.service/cgroup.procs').write_text('40502\n40511\n')
    assert check.unit_processes('/system.slice/actions.runner.zabalack-attended.service') == ([40502, 40511], None)
    assert check.unit_processes(None)[0] is None and check.unit_processes('/system.slice/missing.service')[0] is None
    (tmp_path / 'system.slice/actions.runner.zabalack-attended.service/cgroup.procs').write_text('')
    assert check.unit_processes('/system.slice/actions.runner.zabalack-attended.service') == (None, 'unit cgroup holds no process')


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


def status_with(**fields):
    text = STATUS
    for key, value in fields.items():
        text = re.sub(r'^' + key + r':.*$', key + ':\t' + value, text, flags=re.M)
    return text


def fake_proc(monkeypatch, statuses):
    real_text, real_bytes = check.Path.read_text, check.Path.read_bytes

    def read_text(self, *args, **kwargs):
        name = str(self)
        if name.startswith('/proc/') and name.endswith('/status') and name.split('/')[2].isdigit():
            pid = int(name.split('/')[2])
            if pid in statuses:
                return statuses[pid]
        return real_text(self, *args, **kwargs)

    def read_bytes(self):
        name = str(self)
        if name.startswith('/proc/') and name.endswith('/cmdline') and int(name.split('/')[2]) in statuses:
            return b'/opt/zabalack-actions/bin/Runner.Listener\0run\0'
        return real_bytes(self)
    monkeypatch.setattr(check.Path, 'read_text', read_text)
    monkeypatch.setattr(check.Path, 'read_bytes', read_bytes)


ACCOUNT = {'status': 'OBSERVED', 'name': 'zabalack-runner', 'uid': 1500, 'gid': 1500, 'group_ids': [1500], 'groups': ['zabalack-runner']}


@pytest.mark.parametrize('field,value', [('Uid', '1500\t1500\t0\t1500'), ('Uid', '1500\t1500\t1500\t0'), ('Gid', '0\t1500\t1500\t1500'),
                                         ('Gid', '1500\t1500\t0\t1500')], ids=['saved-uid-root', 'fs-uid-root', 'real-gid-root', 'saved-gid-root'])
def test_every_uid_and_gid_field_must_match_the_account(monkeypatch, field, value):
    """R15: a saved or fs uid, or any gid field, that differs from the account is not the account."""
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker'}.get(gid, str(gid)))
    fake_proc(monkeypatch, {100: status_with(Groups='1500 ', **{field: value})})
    record = check.process_observation([100], ACCOUNT)
    assert record['status'] == 'OBSERVED' and record['matches_configured_account'] is False
    assert record['uid_fields_uniform'] is (field != 'Uid') and record['gid_fields_uniform'] is (field != 'Gid')
    result, deviations, _ = check.evaluate(synthetic_report(runner_process=record), 'docker-free')
    assert result == 'DEVIATES' and 'not the configured account' in ' '.join(deviations)
    fake_proc(monkeypatch, {100: status_with(Groups='1500 ')})
    assert check.process_observation([100], ACCOUNT)['matches_configured_account'] is True


def test_candidates_differing_in_capabilities_or_ids_are_ambiguous_and_all_are_reported(monkeypatch):
    """R15: every unit process is evaluated; a capability held by one candidate is never masked by another."""
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker'}.get(gid, str(gid)))
    clean = status_with(Groups='1500 ')
    privileged = status_with(Groups='1500 ', CapPrm='0000000000200000', CapEff='0000000000200000')
    fake_proc(monkeypatch, {100: clean, 200: privileged})
    record = check.process_observation([100, 200], ACCOUNT)
    assert record['status'] == 'NOT_OBSERVED' and 'ambiguous' in record['reason']
    assert [item['pid'] for item in record['candidates']] == [100, 200] and record['candidates'][1]['CapEff'] == '0000000000200000'
    assert check.evaluate(synthetic_report(runner_process=record), 'docker-free')[0] == 'INCOMPLETE'
    fake_proc(monkeypatch, {100: privileged, 200: privileged})
    record = check.process_observation([100, 200], ACCOUNT)
    assert record['status'] == 'OBSERVED' and record['pids'] == [100, 200] and record['capabilities']['CapEff'] == ['CAP_SYS_ADMIN']
    assert check.evaluate(synthetic_report(runner_process=record), 'docker-free')[0] == 'DEVIATES'


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


def no_activation(path):
    return {'status': 'OBSERVED', 'active': False, 'units': []}


def test_a_symlinked_socket_is_resolved_and_its_target_evaluated_never_skipped(tmp_path, monkeypatch):
    """R2: a connectable socket reached through a link is a deviation, not a skipped path."""
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker'}.get(gid, str(gid)))
    real = tmp_path / 'real.sock'
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(real))
    listener.listen(1)
    link = tmp_path / 'docker.sock'
    link.symlink_to(real)
    identity = {'uid': os.geteuid(), 'gid': os.getegid(), 'groups': sorted(set(os.getgroups()))}
    try:
        record = check.socket_observation(str(link), identity, activation=no_activation)
        assert record['status'] == 'OBSERVED' and record['symlink'] is True and record['target'] == str(real.resolve())
        assert record['socket'] is True and record['demonstrated']['connected'] is True
        assert check.evaluate(synthetic_report(**{'sockets': {str(link): record}}), 'docker-free')[0] == 'DEVIATES'
    finally:
        listener.close()
    real.unlink()
    dangling = check.socket_observation(str(link), identity, activation=no_activation)
    assert dangling['status'] == 'NOT_OBSERVED' and 'no target' in dangling['reason']
    assert check.evaluate(synthetic_report(**{'sockets': {str(link): dangling}}), 'docker-free')[0] == 'INCOMPLETE'
    real.write_text('not a socket')
    assert 'non-socket' in check.socket_observation(str(link), identity, activation=no_activation)['reason']


@pytest.mark.parametrize('result,expected', [
    ('ok', {'status': 'OBSERVED', 'connected': True, 'permission_denied': False, 'no_listener': False}),
    ('errno:13', {'status': 'OBSERVED', 'connected': False, 'errno': 13, 'permission_denied': True, 'no_listener': False}),
    ('errno:111', {'status': 'OBSERVED', 'connected': False, 'errno': 111, 'permission_denied': False, 'no_listener': True}),
    ('errno:11', None), ('errno:110', None), ('unknown', None), ('', None), ('garbage', None),
], ids=['ok', 'eacces', 'econnrefused', 'eagain', 'etimedout', 'unknown', 'empty', 'garbage'])
def test_only_success_denial_and_no_listener_are_classified_everything_else_is_not_observed(result, expected):
    """R3: EAGAIN on a full accept queue, a timeout or a dead child is never "no access"."""
    record = check.classify_connect(result)
    if expected is None:
        assert record['status'] == 'NOT_OBSERVED'
    else:
        assert record == expected


def test_a_full_accept_queue_yields_an_unclassified_connect_and_an_incomplete_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker'}.get(gid, str(gid)))
    full = tmp_path / 'full.sock'
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(full))
    listener.listen(0)
    held = []
    try:
        for _ in range(8):
            client = socket.socket(socket.AF_UNIX)
            client.setblocking(False)
            try:
                client.connect(str(full))
                held.append(client)
            except BlockingIOError:
                client.close()
                break
        identity = {'uid': os.geteuid(), 'gid': os.getegid(), 'groups': sorted(set(os.getgroups()))}
        record = check.socket_observation(str(full), identity, activation=no_activation)
        if record['demonstrated']['status'] == 'OBSERVED':
            pytest.skip('this kernel accepted the connect despite the full backlog')
        assert 'errno 11' in record['demonstrated']['reason'] or 'unknown' in record['demonstrated']['reason']
        report = synthetic_report(**{'sockets': {'/run/docker.sock': record}})
        if record.get('inferred_write') is True:
            assert check.evaluate(report, 'docker-free')[0] == 'DEVIATES'
        else:
            assert check.evaluate(report, 'docker-free')[0] == 'INCOMPLETE'
    finally:
        for client in held:
            client.close()
        listener.close()


def test_no_connect_is_attempted_while_systemd_socket_activation_owns_the_path(tmp_path, monkeypatch):
    """R16: a permitted connect on an activated docker.socket would start dockerd; the inferred permission decides."""
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker'}.get(gid, str(gid)))
    monkeypatch.setattr(check, 'demonstrate_connect', lambda path, identity: pytest.fail('a connect was attempted'))
    path = tmp_path / 'docker.sock'
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen(1)
    identity = {'uid': os.geteuid(), 'gid': os.getegid(), 'groups': sorted(set(os.getgroups()))}
    show = show_of({'docker.socket': {'LoadState': ['loaded'], 'ActiveState': ['active'], 'Listen': [f'{path} (Stream)']}})
    try:
        record = check.socket_observation(str(path), identity, activation=lambda target: check.socket_activation(target, show=show))
        assert record['activation'] == {'status': 'OBSERVED', 'active': True, 'unit': 'docker.socket', 'units': [{'unit': 'docker.socket', 'active': True}]}
        assert record['demonstrated']['status'] == 'SKIPPED'
        report = synthetic_report(**{'sockets': {str(path): record}})
        if record['acl']['status'] == 'OBSERVED':
            # This test's own identity owns the socket: inferred write, a deviation under docker-free.
            assert record['inferred_write'] is True and check.evaluate(report, 'docker-free')[0] == 'DEVIATES'
        record['inferred_write'] = False
        record['acl'] = {'status': 'OBSERVED', 'entries': None}
        assert check.evaluate(synthetic_report(**{'sockets': {str(path): record}}), 'docker-free') == ('CONFORMS', [], [])
        record['acl'] = {'status': 'NOT_OBSERVED', 'reason': 'ACL unreadable: permission denied'}
        record['inferred_write'] = None
        assert check.evaluate(synthetic_report(**{'sockets': {str(path): record}}), 'docker-free')[0] == 'INCOMPLETE'
    finally:
        listener.close()
    # An inactive or unrelated socket unit does not stop the demonstration.
    inactive = show_of({'docker.socket': {'LoadState': ['loaded'], 'ActiveState': ['inactive'], 'Listen': [f'{path} (Stream)']}})
    assert check.socket_activation(str(path), show=inactive) == {'status': 'OBSERVED', 'active': False, 'units': [{'unit': 'docker.socket', 'active': False}]}
    assert check.socket_activation(str(path), show=show_of({}))['active'] is False
    # No systemctl under a systemd init: the state is unknown and the connect is not attempted.
    monkeypatch.setattr(check.Path, 'read_text', lambda self, *a, **k: 'systemd\n' if str(self) == '/proc/1/comm' else Path.read_text(self, *a, **k))
    unknown = check.socket_activation(str(path), show=lambda unit, properties: None)
    assert unknown['status'] == 'NOT_OBSERVED'


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
    socket_record = {'status': 'OBSERVED', 'present': True, 'socket': True, 'symlink': False, 'mode': '0o660', 'owner_uid': 0, 'group': 'docker',
                     'acl': {'status': 'OBSERVED', 'entries': None}, 'inferred_write': False,
                     'activation': {'status': 'OBSERVED', 'active': False, 'units': []},
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
    ({'sockets::/var/run/docker.sock::demonstrated': {'status': 'NOT_OBSERVED', 'reason': 'connect returned errno 11: access could not be classified'}}, 'errno 11'),
    ({'sockets::/var/run/docker.sock': {'status': 'NOT_OBSERVED', 'reason': 'symlinked socket path has no target: /x', 'symlink': True}}, 'no target'),
    ({'service': {'status': 'NOT_OBSERVED', 'reason': 'unit file unreadable'}}, 'service:'),
    ({'runner_process_environment': {'status': 'NOT_OBSERVED', 'reason': 'insufficient permission'}}, 'runner_process_environment:'),
    ({'docker_config': {'status': 'NOT_OBSERVED', 'reason': 'docker config.json unreadable'}}, 'docker_config:'),
], ids=['no-account', 'no-process', 'ambiguous-process', 'sudo-not-root', 'acl-unreadable', 'access-not-demonstrated', 'connect-unclassified',
        'symlink-dangling', 'unit-unreadable', 'process-env-unreadable', 'config-unreadable'])
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
    ({'sockets::/var/run/docker.sock::inferred_write': True}, 'docker-free', 'inferred write permission'),
    ({'sockets::/var/run/docker.sock::inferred_write': True, 'sockets::/var/run/docker.sock::demonstrated': {'status': 'NOT_OBSERVED', 'reason': 'connect returned errno 11'}}, 'docker-free', 'inferred write permission'),
    ({'sockets::/var/run/docker.sock::demonstrated': {'status': 'OBSERVED', 'connected': True, 'permission_denied': False, 'no_listener': False}}, 'docker-allowed', 'disagree'),
    ({'service::environment': {'DOCKER_HOST': 'tcp://x'}}, 'docker-allowed', 'service environment'),
    ({'service::supplementary_groups': ['docker']}, 'docker-free', 'unit grants'),
    ({'service::user': 'someone-else'}, 'docker-allowed', 'different user'),
    ({'runner_process_environment::settings': {'DOCKER_CONTEXT': 'x'}}, 'docker-allowed', 'running runner environment'),
    ({'docker_config::remote_context': True}, 'docker-allowed', 'remote daemon'),
    ({'docker_config::config_json': {'present': True, 'currentContext': 'lab', 'stored_auths': 0}}, 'docker-allowed', 'non-default context'),
], ids=['process-docker-group', 'process-kvm', 'process-other-account', 'process-capabilities', 'account-docker-group', 'sudo-rule',
        'socket-connect', 'socket-inferred-write', 'socket-inferred-write-unclassified-connect', 'socket-inference-disagrees', 'service-env',
        'unit-groups', 'unit-user', 'process-env', 'remote-context', 'current-context'])
def test_each_deviation_is_named_and_wins_over_incompleteness(monkeypatch, override, policy, fragment):
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker', 1600: 'kvm'}.get(gid, str(gid)))
    report = synthetic_report(**override)
    result, deviations, _ = check.evaluate(report, policy)
    assert result == 'DEVIATES' and any(fragment in item for item in deviations)
    # A deviation keeps the verdict DEVIATES even when other observations are missing.
    report['service'] = {'status': 'NOT_OBSERVED', 'reason': 'unit file unreadable'}
    report['docker_config']['remote_context'] = True
    result, deviations, incomplete = check.evaluate(report, policy)
    assert result == 'DEVIATES' and 'service: unit file unreadable' in incomplete


def test_socket_access_under_docker_allowed_is_reported_not_a_deviation(monkeypatch):
    monkeypatch.setattr(check, 'group_name', lambda gid: {1500: 'zabalack-runner', 996: 'docker'}.get(gid, str(gid)))
    report = synthetic_report(**{'sockets::/var/run/docker.sock::demonstrated': {'status': 'OBSERVED', 'connected': True, 'permission_denied': False, 'no_listener': False},
                                 'sockets::/var/run/docker.sock::inferred_write': True, 'account::groups': ['docker', 'zabalack-runner']})
    assert check.evaluate(report, 'docker-allowed') == ('CONFORMS', [], [])
    assert check.evaluate(report, 'docker-free')[0] == 'DEVIATES'


def test_cli_fails_closed_on_an_unknown_account_and_never_prints_secrets(tmp_path, capsys, child, monkeypatch):
    unit = {**TRANSIENT_UNIT, 'Id': ['actions.runner.t.service'], 'WorkingDirectory': [str(tmp_path)],
            'ControlGroup': ['/system.slice/actions.runner.t.service']}
    monkeypatch.setattr(check, 'systemctl_show', show_of({'actions.runner.t.service': unit}))
    monkeypatch.setattr(check, 'CGROUP_ROOT', tmp_path / 'cgroup')
    (tmp_path / 'cgroup/system.slice/actions.runner.t.service').mkdir(parents=True)
    (tmp_path / 'cgroup/system.slice/actions.runner.t.service/cgroup.procs').write_text(f'{child.pid}\n')
    with pytest.raises(SystemExit):
        check.main(['--account', 'no-such-account-here', '--unit', 'actions.runner.*', '--socket', str(tmp_path / 'none.sock')])
    capsys.readouterr()
    code = check.main(['--account', 'no-such-account-here', '--unit', 'actions.runner.t', '--socket', str(tmp_path / 'none.sock')])
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report['service']['transient'] is True and report['runner_process']['selected_by'] == 'unit cgroup /system.slice/actions.runner.t.service'
    assert report['runner_process']['pids'] == [child.pid]
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
    assert result == 'DEVIATES' and deviations == ['runner identity holds inferred write permission on the container socket /var/run/docker.sock',
                                                   'runner identity may open the container socket /var/run/docker.sock (no daemon listening now)']
    assert check.evaluate(report, 'docker-allowed') == ('CONFORMS', [], [])


def test_socket_activation_reads_the_listen_format_systemctl_actually_prints(tmp_path, monkeypatch):
    """systemctl show prints 'Listen=/run/docker.sock (Stream)', not 'Stream:/run/docker.sock'.

    The runner host's docker.socket is active; a connect there would start dockerd.
    """
    path = tmp_path / 'docker.sock'
    path.touch()
    link = tmp_path / 'var-run-docker.sock'
    link.symlink_to(path)

    def fake_run(argv, **kwargs):
        unit = argv[-1]
        stdout = (f'Listen={path} (Stream)\nActiveState=active\nLoadState=loaded\n' if unit == 'docker.socket'
                  else 'LoadState=not-found\n')
        return subprocess.CompletedProcess(argv, 0, stdout, '')

    monkeypatch.setattr(check.shutil, 'which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(check.subprocess, 'run', fake_run)
    for target in (path, link):
        assert check.socket_activation(str(target)) == {
            'status': 'OBSERVED', 'active': True, 'unit': 'docker.socket', 'units': [{'unit': 'docker.socket', 'active': True}]}


@pytest.mark.parametrize('entries, expected', [
    (['[::]:2375 (Stream)'], 'inactive'),
    (['/run/other.sock (Stream)', '/run/docker.sock (Stream)'], 'active'),
    (['Stream:/run/docker.sock'], 'unknown'),
    (['/run/docker.sock'], 'unknown'),
], ids=['tcp-listener-only', 'second-listen-entry', 'unrecognised-format', 'missing-type'])
def test_an_unreadable_listen_entry_of_a_loaded_socket_unit_is_not_observed(entries, expected):
    show = show_of({'docker.socket': {'LoadState': ['loaded'], 'ActiveState': ['active'], 'Listen': entries}})
    result = check.socket_activation('/run/docker.sock', show=show)
    if expected == 'unknown':
        assert result['status'] == 'NOT_OBSERVED' and 'Listen entry not understood' in result['reason']
    else:
        assert result['status'] == 'OBSERVED' and result['active'] is (expected == 'active')


@pytest.mark.parametrize('working_directory, located', [
    ('!{root}', True), ('-{root}', True), ('{root}', True), ('~', False), ('', False), ('relative/dir', False),
], ids=['bang-prefix', 'dash-prefix', 'plain', 'home', 'unset', 'relative'])
def test_the_runner_env_is_located_from_the_working_directory_or_the_observation_is_incomplete(tmp_path, working_directory, located):
    """A DOCKER_HOST in the runner .env reaches the listener; an unlocated .env must not pass silently."""
    runner_root = tmp_path / 'runner'
    runner_root.mkdir()
    (runner_root / '.env').write_text('DOCKER_HOST=tcp://127.0.0.1:2375\n')
    unit = {**TRANSIENT_UNIT, 'WorkingDirectory': [working_directory.format(root=runner_root)]}
    show = show_of({'actions.runner.zabalack-attended.service': unit})
    record = check.service_observation('actions.runner.zabalack-attended', None, show=show)
    if located:
        assert record['status'] == 'OBSERVED' and record['environment'] == {'DOCKER_HOST': 'tcp://127.0.0.1:2375'}
    else:
        assert record['status'] == 'NOT_OBSERVED' and record['reason'].startswith('runner .env location unknown')
        # An explicit runner root still locates it.
        explicit = check.service_observation('actions.runner.zabalack-attended', runner_root, show=show)
        assert explicit['status'] == 'OBSERVED' and explicit['environment'] == {'DOCKER_HOST': 'tcp://127.0.0.1:2375'}
