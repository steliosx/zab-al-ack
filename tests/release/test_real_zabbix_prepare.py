"""lab/real_zabbix_prepare.py: synthetic fixture preparation on the disposable instance (R27).

The JSON-RPC transport is injected; no Zabbix instance, network or token exists.
"""
import json
import os
from pathlib import Path
import stat
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lab import real_zabbix_prepare as prepare  # noqa: E402
from lab.real_zabbix_safety import LAB_API_URL  # noqa: E402
from lab.zabbix_reference import VERSION as PIN  # noqa: E402


class FakeZabbix:
    """Answers the provisioning calls in order and records every request."""

    def __init__(self, *, enabled_actions=False, existing_hosts=False, tokens=('a' * 64, 'b' * 64), version=PIN):
        self.calls = []
        self.version = version
        self.enabled_actions = enabled_actions
        self.existing_hosts = existing_hosts
        self.tokens = list(tokens)
        self.counter = 100
        self.pushed = set()
        self.role_rules = {}

    def next_id(self):
        self.counter += 1
        return str(self.counter)

    def __call__(self, url, body, headers):
        request = json.loads(body)
        method, params = request['method'], request['params']
        self.calls.append((method, params, headers.get('Authorization')))
        result = self.answer(method, params)
        return {'jsonrpc': '2.0', 'id': request['id'], 'result': result}

    def answer(self, method, params):
        if method == 'apiinfo.version':
            return self.version
        if method == 'user.login':
            return 'c' * 32
        if method == 'action.get':
            return [{'actionid': '1'}] if self.enabled_actions else []
        if method == 'host.get':
            return [{'hostid': '9'}] if self.existing_hosts else []
        if method in ('hostgroup.create', 'host.create', 'item.create', 'trigger.create', 'usergroup.create', 'user.create', 'token.create'):
            key = {'hostgroup.create': 'groupids', 'host.create': 'hostids', 'item.create': 'itemids', 'trigger.create': 'triggerids',
                   'usergroup.create': 'usrgrpids', 'user.create': 'userids', 'token.create': 'tokenids'}[method]
            return {key: [self.next_id()]}
        if method == 'role.create':
            role_id = self.next_id()
            self.role_rules[role_id] = params['rules']
            return {'roleids': [role_id]}
        if method == 'role.get':
            role_id = params['roleids'][0]
            return [{'roleid': role_id, 'type': '1', 'rules': self.role_rules[role_id]}]
        if method == 'token.generate':
            return [{'tokenid': params[0], 'token': self.tokens.pop(0)}]
        if method == 'history.push':
            self.pushed.add(str(params[0]['itemid']))
            return {'response': 'success', 'data': [{'itemid': params[0]['itemid']}]}
        if method == 'event.get':
            trigger = params['objectids'][0]
            return [{'eventid': '5' + trigger, 'objectid': trigger, 'value': '1', 'acknowledged': '0'}]
        raise AssertionError('unexpected method ' + method)


def test_prepare_provisions_cohort_outside_scope_roles_tokens_and_problems_privately(tmp_path, monkeypatch, capsys):
    zabbix = FakeZabbix()
    monkeypatch.setattr(prepare.time, 'sleep', lambda seconds: None)
    private = tmp_path / 'private'
    fixture = prepare.prepare(prepare.Rpc(LAB_API_URL, transport=zabbix), private)
    methods = [call[0] for call in zabbix.calls]
    assert methods[:4] == ['apiinfo.version', 'user.login', 'action.get', 'host.get']
    assert methods.count('hostgroup.create') == 2 and methods.count('host.create') == 2
    assert methods.count('item.create') == 3 and methods.count('trigger.create') == 3
    assert methods.count('role.create') == 2 and methods.count('role.get') == 2
    assert methods.count('user.create') == 2 and methods.count('token.create') == 2 and methods.count('token.generate') == 2
    assert methods.count('history.push') == 3 and len(zabbix.pushed) == 3
    assert zabbix.calls[0][2] is None and zabbix.calls[1][2] is None
    assert all(call[2] == 'Bearer ' + 'c' * 32 for call in zabbix.calls[2:])
    assert fixture['prepared_against_api_version'] == PIN
    # The restricted role is the shared source rule set; the witness has reads only and no action.
    roles = [call[1] for call in zabbix.calls if call[0] == 'role.create']
    assert roles[0]['rules']['api'] == ['event.get', 'event.acknowledge', 'host.get']
    assert roles[1]['rules']['api'] == ['event.get', 'host.get']
    assert all(rule['status'] == 0 for rule in roles[1]['rules']['actions'])
    groups = [call[1] for call in zabbix.calls if call[0] == 'usergroup.create']
    assert groups[0]['hostgroup_rights'] == [{'id': fixture['cohort']['group_ids'][0], 'permission': 3}]
    assert {right['id'] for right in groups[1]['hostgroup_rights']} == {fixture['cohort']['group_ids'][0], fixture['outside_scope_source']['group_ids'][0]}
    assert all(right['permission'] == 2 for right in groups[1]['hostgroup_rights'])
    # Fixture shape for tests/real_zabbix and private token files.
    assert fixture['url'] == LAB_API_URL and fixture['isolated_lab'] is True and fixture['fresh_instance_no_notifications'] is True
    assert fixture['source']['zabbix_event_id'] == '5' + fixture['source']['zabbix_trigger_id']
    assert fixture['end_to_end']['zabbix_event_id'] == '5' + fixture['end_to_end']['zabbix_trigger_id']
    assert fixture['outside_scope_source']['zabbix_event_id'].startswith('5')
    assert json.loads((private / 'fixture.json').read_text()) == fixture
    assert (private / 'source.token').read_text() == 'a' * 64 + '\n' and (private / 'witness.token').read_text() == 'b' * 64 + '\n'
    if os.name == 'posix':
        assert stat.S_IMODE(private.stat().st_mode) == 0o700
        for name in ('source.token', 'witness.token', 'fixture.json'):
            assert stat.S_IMODE((private / name).stat().st_mode) == 0o600
    assert 'a' * 64 not in capsys.readouterr().out


@pytest.mark.parametrize('problem', ['enabled-actions', 'existing-hosts', 'rejected-role'])
def test_prepare_refuses_a_non_fresh_instance_or_a_role_that_does_not_read_back(tmp_path, monkeypatch, problem):
    zabbix = FakeZabbix(enabled_actions=problem == 'enabled-actions', existing_hosts=problem == 'existing-hosts')
    if problem == 'rejected-role':
        original = zabbix.answer

        def answer(method, params):
            result = original(method, params)
            if method == 'role.get':
                result[0]['rules'] = dict(result[0]['rules'], actions=[])
            return result
        zabbix.answer = answer
    monkeypatch.setattr(prepare.time, 'sleep', lambda seconds: None)
    with pytest.raises((prepare.PrepareError, ValueError)):
        prepare.prepare(prepare.Rpc(LAB_API_URL, transport=zabbix), tmp_path / 'private')
    assert not (tmp_path / 'private').exists()
    assert 'history.push' not in [call[0] for call in zabbix.calls]


def test_rpc_accepts_only_the_fixed_lab_api_and_rejects_uncorrelated_or_error_envelopes():
    with pytest.raises(prepare.PrepareError, match='fixed lab API'):
        prepare.Rpc('http://127.0.0.1:58080/other.php', transport=lambda *args: {})
    with pytest.raises(prepare.PrepareError, match='fixed lab API'):
        prepare.Rpc('http://zabbix.example.invalid/api_jsonrpc.php', transport=lambda *args: {})
    rpc = prepare.Rpc(LAB_API_URL, transport=lambda url, body, headers: {'jsonrpc': '2.0', 'id': 99, 'result': 'x'})
    with pytest.raises(prepare.PrepareError, match='rejected'):
        rpc('apiinfo.version', {}, authenticated=False)
    rpc = prepare.Rpc(LAB_API_URL, transport=lambda url, body, headers: {'jsonrpc': '2.0', 'id': 1, 'error': {'data': 'PRIVATE'}})
    with pytest.raises(prepare.PrepareError, match='rejected: PRIVATE'):
        rpc('apiinfo.version', {}, authenticated=False)
    with pytest.raises(prepare.PrepareError, match='login required'):
        prepare.Rpc(LAB_API_URL, transport=lambda *args: {})('host.get', {})


def test_cli_prints_identifiers_but_never_a_token(tmp_path, monkeypatch, capsys):
    zabbix = FakeZabbix()
    monkeypatch.setattr(prepare.time, 'sleep', lambda seconds: None)
    real = prepare.Rpc
    monkeypatch.setattr(prepare, 'Rpc', lambda url: real(url, transport=zabbix))
    assert prepare.main(['--isolated-lab', '--private', str(tmp_path / 'private')]) == 0
    out = capsys.readouterr().out
    record = json.loads(out)
    assert record['status'] == 'PREPARED' and record['fixture'].endswith('fixture.json')
    assert record['api_version'] == PIN == record['reference']
    assert 'a' * 64 not in out and 'b' * 64 not in out


@pytest.mark.parametrize('version', ['7.0.30', '7.2.0', '6.0.31', '', None, 7])
def test_prepare_refuses_an_instance_that_is_not_the_pinned_reference_before_logging_in(tmp_path, monkeypatch, capsys, version):
    zabbix = FakeZabbix(version=version)
    monkeypatch.setattr(prepare.time, 'sleep', lambda seconds: None)
    with pytest.raises(prepare.PrepareError, match='pinned reference|result rejected'):
        prepare.prepare(prepare.Rpc(LAB_API_URL, transport=zabbix), tmp_path / 'private')
    assert [call[0] for call in zabbix.calls] == ['apiinfo.version'], 'nothing after the version check, not even the login'
    assert not (tmp_path / 'private').exists()
    real = prepare.Rpc
    monkeypatch.setattr(prepare, 'Rpc', lambda url: real(url, transport=FakeZabbix(version=version)))
    assert prepare.main(['--isolated-lab', '--private', str(tmp_path / 'private')]) == 1
    record = json.loads(capsys.readouterr().out)
    assert record['status'] == 'FAIL' and ('pinned reference' in record['reason'] or 'result rejected' in record['reason'])

