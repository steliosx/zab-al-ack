"""tools/runner_account_check.py: read-only host capability verification (R28).

Accounts, groups, sockets, files and commands are synthetic; nothing on the
host is read except through the injected values, and nothing is changed.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import runner_account_check as check


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A synthetic account with injectable groups, sudo output, sockets and files."""
    state = SimpleNamespace(groups=['zabalack-runner'], sudo='User zabalack-runner is not allowed to run sudo on host.\n',
                            euid=0, docker=None, contexts='', environment={})
    home = tmp_path / 'home'
    home.mkdir()
    entry = SimpleNamespace(pw_uid=1500, pw_gid=1500, pw_dir=str(home), pw_shell='/bin/bash')
    monkeypatch.setattr(check.pwd, 'getpwnam', lambda name: entry if name == 'zabalack-runner' else (_ for _ in ()).throw(KeyError(name)))
    monkeypatch.setattr(check.os, 'getgrouplist', lambda name, gid: list(range(1500, 1500 + len(state.groups))))
    def group_name(gid):
        if 1500 <= gid < 1500 + len(state.groups):
            return SimpleNamespace(gr_name=state.groups[gid - 1500])
        return SimpleNamespace(gr_name='hostgroup' + str(gid))
    monkeypatch.setattr(check.grp, 'getgrgid', group_name)
    monkeypatch.setattr(check.grp, 'getgrnam', lambda name: SimpleNamespace(gr_gid=1500 + state.groups.index(name)))
    monkeypatch.setattr(check.os, 'geteuid', lambda: state.euid)
    monkeypatch.setattr(check.shutil, 'which', lambda name: {'sudo': '/usr/bin/sudo', 'docker': state.docker}.get(name))

    def run(command, **kwargs):
        if command[0] == 'sudo':
            return subprocess.CompletedProcess(command, 0, state.sudo, '')
        if command[-2:] == ['--format', 'json']:
            return subprocess.CompletedProcess(command, 0, state.contexts, '')
        raise AssertionError('unexpected command ' + str(command))
    monkeypatch.setattr(check.subprocess, 'run', run)
    monkeypatch.setattr(check, 'SOCKETS', (str(tmp_path / 'docker.sock'),))
    monkeypatch.setattr(check, 'unit_settings', lambda pattern='actions.runner': {'units': state.__dict__.get('units', [])})
    monkeypatch.setattr(check, 'ROOT', tmp_path / 'checkout')
    (tmp_path / 'checkout/.github/workflows').mkdir(parents=True)
    state.home = home
    state.tmp = tmp_path
    return state


def report(state, policy='docker-free'):
    return check.build_report('zabalack-runner', state.tmp / 'runner', policy, dict(state.environment))


def test_conforming_docker_free_account_reports_no_findings(host):
    result = report(host)
    assert result['result'] == 'CONFORMS' and result['findings'] == []
    assert result['account'] == {'name': 'zabalack-runner', 'uid': 1500, 'gid': 1500, 'home': str(host.home),
                                 'shell': '/bin/bash', 'groups': ['zabalack-runner']}
    assert result['sudo'] == {'status': 'CHECKED', 'rules': [], 'any_rule': False}
    assert result['docker_cli'] == {'present': False, 'path': None}
    assert result['scope'].startswith('read-only')


def test_docker_group_membership_deviates_only_under_the_docker_free_policy(host):
    host.groups = ['zabalack-runner', 'docker']
    free = report(host)
    assert free['result'] == 'DEVIATES' and free['findings'] == ['account is a member of the docker group']
    allowed = report(host, 'docker-allowed')
    assert allowed['result'] == 'CONFORMS' and 'docker' in allowed['account']['groups']


def test_other_host_capable_groups_and_sudo_rules_always_deviate(host):
    host.groups = ['zabalack-runner', 'lxd', 'kvm']
    host.sudo = 'User zabalack-runner may run the following commands on host:\n    (ALL) NOPASSWD: ALL\n'
    result = report(host, 'docker-allowed')
    assert result['result'] == 'DEVIATES'
    assert result['findings'] == ['account holds host-capable groups: kvm, lxd', 'account has sudo rules']
    assert result['sudo']['rules'] == ['(ALL) NOPASSWD: ALL']


def test_sudo_rules_are_not_checked_without_root_and_the_report_is_incomplete(host):
    host.euid = 1000
    result = report(host)
    assert result['sudo'] == {'status': 'NOT_CHECKED', 'reason': 'requires root and sudo'}
    assert result['result'] == 'INCOMPLETE' and result['findings'] == []


def test_a_writable_container_socket_is_a_deviation_and_a_plain_file_is_not(host):
    path = host.tmp / 'docker.sock'
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    try:
        # world-writable: reachable regardless of group membership
        os.chmod(path, 0o666)
        result = report(host)
        item = result['sockets'][str(path)]
        assert item['socket'] is True and item['account_can_write'] is True
        assert 'account can write the container socket ' + str(path) in result['findings']
        os.chmod(path, 0o600)
        result = report(host)
        assert result['sockets'][str(path)]['account_can_write'] is False and result['result'] == 'CONFORMS'
    finally:
        listener.close()
        path.unlink()
    path.write_text('not a socket')
    result = report(host)
    assert result['sockets'][str(path)] == {'present': True, 'socket': False} and result['result'] == 'CONFORMS'


def test_remote_daemon_settings_from_any_source_are_reported(host):
    host.environment = {'DOCKER_HOST': 'tcp://10.0.0.5:2376', 'PATH': '/usr/bin'}
    runner = host.tmp / 'runner'
    runner.mkdir()
    (runner / '.env').write_text('LANG=C.UTF-8\nDOCKER_CONTEXT=remote-lab\n')
    (host.home / '.docker').mkdir()
    (host.home / '.docker/config.json').write_text(json.dumps({'currentContext': 'remote-lab'}))
    host.units = [{'unit': '/etc/systemd/system/actions.runner.x.service', 'docker_settings': {'DOCKER_HOST': 'Environment=DOCKER_HOST=tcp://x'}}]
    host.docker = '/usr/bin/docker'
    host.contexts = json.dumps({'Name': 'default', 'Current': False, 'DockerEndpoint': 'unix:///var/run/docker.sock'}) + '\n' + \
        json.dumps({'Name': 'remote-lab', 'Current': True, 'DockerEndpoint': 'tcp://10.0.0.5:2376'}) + '\n'
    result = report(host)
    assert result['environment'] == {'settings': {'DOCKER_HOST': 'tcp://10.0.0.5:2376'}}
    assert result['runner_env_file'] == {'present': True, 'settings': {'DOCKER_CONTEXT': 'remote-lab'}}
    assert result['docker_cli']['remote_context'] is True and len(result['docker_cli']['contexts']) == 2
    assert result['docker_config'] == {'present': True, 'currentContext': 'remote-lab'}
    assert result['result'] == 'DEVIATES'
    assert set(result['findings']) == {'Docker daemon settings present: DOCKER_CONTEXT, DOCKER_HOST',
                                       'runner unit carries Docker daemon settings',
                                       'a Docker context names a remote daemon',
                                       'account docker config selects a non-default context'}
    assert 'PATH' not in json.dumps(result)


def test_docker_requiring_workflows_are_listed_from_commands_not_comments(host):
    workflows = host.tmp / 'checkout/.github/workflows'
    (workflows / 'ci.yml').write_text('jobs:\n  native:\n    steps:\n      # runner account docker access comment\n'
                                      '      - run: python tools/native_local.py --isolated-lab\n')
    (workflows / 'deployment-e2e.yml').write_text("on: [workflow_dispatch]\njobs:\n  lab:\n    if: ${{ vars.X == 'true' }}\n"
                                                  '    steps:\n      - run: python tools/run_deployment_e2e.py --isolated-lab\n')
    result = report(host)
    assert result['docker_workflows'] == [{'workflow': 'deployment-e2e.yml', 'docker_lines': 1, 'manual_and_variable_gated': True}]
    assert result['result'] == 'CONFORMS'


def test_cli_exit_codes_follow_the_verdict_and_an_unknown_account_is_incomplete(host, capsys):
    assert check.main(['--account', 'zabalack-runner', '--runner-root', str(host.tmp / 'runner')]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['result'] == 'CONFORMS' and out['policy'] == 'docker-free'
    host.groups = ['zabalack-runner', 'docker']
    assert check.main(['--account', 'zabalack-runner', '--runner-root', str(host.tmp / 'runner')]) == 1
    capsys.readouterr()
    assert check.main(['--account', 'nobody-here', '--runner-root', str(host.tmp / 'runner')]) == 2
    assert json.loads(capsys.readouterr().out) == {'result': 'INCOMPLETE', 'findings': ['account does not exist: nobody-here']}
