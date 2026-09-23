#!/usr/bin/env python3
"""Prepare the synthetic fixture for tests/real_zabbix on the disposable instance (R27).

Runs only against the fixed lab API (`lab/real_zabbix_safety.LAB_API_URL`) with
the instance's default `Admin` account. Before logging in it reads
`apiinfo.version` and refuses an instance that is not the pinned reference
(`lab/zabbix_reference.json`); it also refuses an instance that already
carries the synthetic host or an enabled action. It creates, in this order:

  * a cohort host group, a monitored host without interfaces, two trapper items
    and two single-host triggers (source problem and end-to-end problem);
  * an outside host group with its own host, item and trigger (T11a);
  * the restricted source role from `lab/zabbix_roles.role_rules()` (read back
    and verified), a user group with read-write rights on the cohort group, the
    restricted user and its API token;
  * a read-only witness role (`event.get`, `host.get`, no action), a user group
    with read rights on both groups, the witness user and token;
  * three problems raised through `history.push`, waited for as events.

It writes the fixture JSON that `tests/real_zabbix` consumes and the two token
files (mode 0600) into a private directory, prints the fixture path and never
prints a token. Everything it creates is synthetic lab data; nothing here is
production access or permission evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab.real_zabbix_safety import LAB_API_URL  # noqa: E402
from lab.zabbix_reference import VERSION as REFERENCE_VERSION  # noqa: E402
from lab.zabbix_roles import role_rules, verify_role  # noqa: E402

HOST = 'zabalack-real-lab-synthetic'
OUTSIDE_HOST = 'zabalack-real-lab-outside'
PASSWORD = 'Synthetic-lab-only-7!'


class PrepareError(RuntimeError):
    pass


class Rpc:
    def __init__(self, url: str, transport=None) -> None:
        if url != LAB_API_URL:
            raise PrepareError('only the fixed lab API is accepted')
        self.url = url
        self.session: str | None = None
        self.sequence = 0
        self.transport = transport or self._http

    @staticmethod
    def _http(url: str, body: bytes, headers: dict) -> dict:
        from urllib import request
        with request.urlopen(request.Request(url, data=body, headers=headers), timeout=60) as response:
            return json.loads(response.read().decode())

    def __call__(self, method: str, params: Any, *, authenticated: bool = True) -> Any:
        self.sequence += 1
        headers = {'Content-Type': 'application/json-rpc'}
        if authenticated:
            if not self.session:
                raise PrepareError('login required')
            headers['Authorization'] = 'Bearer ' + self.session
        body = json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params, 'id': self.sequence}).encode()
        data = self.transport(self.url, body, headers)
        if (not isinstance(data, dict) or data.get('jsonrpc') != '2.0' or data.get('id') != self.sequence
                or 'result' not in data or 'error' in data):
            detail = data.get('error', {}).get('data') if isinstance(data, dict) and isinstance(data.get('error'), dict) else None
            raise PrepareError(f'{method} rejected' + (f': {detail}' if isinstance(detail, str) else ''))
        return data['result']


def created(result: Any, key: str) -> str:
    ids = result.get(key) if isinstance(result, dict) else None
    if not isinstance(ids, list) or len(ids) != 1 or not re.fullmatch(r'[0-9]+', str(ids[0])):
        raise PrepareError('unexpected create result for ' + key)
    return str(ids[0])


def token(rpc: Rpc, user_id: str, name: str) -> str:
    token_id = created(rpc('token.create', {'name': name, 'userid': user_id, 'expires_at': int(time.time()) + 6 * 3600}), 'tokenids')
    generated = rpc('token.generate', [token_id])
    if (not isinstance(generated, list) or len(generated) != 1 or not isinstance(generated[0], dict)
            or not isinstance(generated[0].get('token'), str) or not re.fullmatch(r'[a-f0-9]{64}', generated[0]['token'])):
        raise PrepareError('token generation result rejected')
    return generated[0]['token']


def trapper(rpc: Rpc, host_id: str, host: str, key: str, label: str) -> tuple[str, str]:
    item_id = created(rpc('item.create', {'hostid': host_id, 'name': label, 'key_': key, 'type': 2, 'value_type': 3,
                                          'delay': '0', 'trapper_hosts': ''}), 'itemids')
    trigger_id = created(rpc('trigger.create', {'description': label + ' problem', 'expression': f'last(/{host}/{key})=1',
                                                'priority': 2}), 'triggerids')
    return item_id, trigger_id


def raise_problem(rpc: Rpc, item_id: str, trigger_id: str, timeout: float = 120) -> str:
    deadline = time.monotonic() + timeout
    pushed = False
    while time.monotonic() < deadline:
        if not pushed:
            result = rpc('history.push', [{'itemid': item_id, 'value': 1}])
            rows = result.get('data') if isinstance(result, dict) else None
            pushed = (isinstance(result, dict) and result.get('response') == 'success' and isinstance(rows, list)
                      and len(rows) == 1 and isinstance(rows[0], dict) and 'error' not in rows[0])
        if pushed:
            events = rpc('event.get', {'output': ['eventid', 'objectid', 'value', 'acknowledged'], 'source': 0, 'object': 0,
                                       'objectids': [trigger_id], 'value': 1})
            if events:
                if len(events) != 1 or events[0].get('acknowledged') != '0':
                    raise PrepareError('problem identity rejected')
                return str(events[0]['eventid'])
        time.sleep(1)
    raise PrepareError('problem not observed for trigger ' + trigger_id)


def observed_reference(rpc: Rpc) -> str:
    """The instance's apiinfo.version; anything but the pinned reference is refused before login."""
    version = rpc('apiinfo.version', {}, authenticated=False)
    if not isinstance(version, str) or re.fullmatch(r'7\.0\.[0-9]+', version) is None:
        raise PrepareError('apiinfo.version result rejected')
    if version != REFERENCE_VERSION:
        raise PrepareError(f'instance reports Zabbix {version}, not the pinned reference {REFERENCE_VERSION}; not a qualifying lab')
    return version


def prepare(rpc: Rpc, private: Path) -> dict:
    version = observed_reference(rpc)
    session = rpc('user.login', {'username': 'Admin', 'password': 'zabbix'}, authenticated=False)
    if not isinstance(session, str) or not re.fullmatch(r'[a-f0-9]{32}', session):
        raise PrepareError('login result rejected')
    rpc.session = session
    if rpc('action.get', {'output': ['actionid'], 'filter': {'status': 0}}):
        raise PrepareError('instance has enabled actions; not a fresh lab')
    if rpc('host.get', {'output': ['hostid'], 'filter': {'host': [HOST, OUTSIDE_HOST]}}):
        raise PrepareError('synthetic hosts already exist; reset the lab first')
    group_id = created(rpc('hostgroup.create', {'name': 'Zab-al-ack real lab cohort'}), 'groupids')
    host_id = created(rpc('host.create', {'host': HOST, 'groups': [{'groupid': group_id}], 'interfaces': []}), 'hostids')
    source_item, source_trigger = trapper(rpc, host_id, HOST, 'zabalack.real.source', 'Zab-al-ack synthetic source')
    e2e_item, e2e_trigger = trapper(rpc, host_id, HOST, 'zabalack.real.e2e', 'Zab-al-ack synthetic end-to-end')
    outside_group = created(rpc('hostgroup.create', {'name': 'Zab-al-ack real lab outside scope'}), 'groupids')
    outside_host = created(rpc('host.create', {'host': OUTSIDE_HOST, 'groups': [{'groupid': outside_group}], 'interfaces': []}), 'hostids')
    outside_item, outside_trigger = trapper(rpc, outside_host, OUTSIDE_HOST, 'zabalack.real.outside', 'Zab-al-ack synthetic outside')
    rules = role_rules()
    role_id = created(rpc('role.create', {'name': 'Zab-al-ack real lab restricted source', 'type': 1, 'rules': rules}), 'roleids')
    verify_role(rpc('role.get', {'roleids': [role_id], 'output': ['roleid', 'type'], 'selectRules': list(rules)}), role_id, rules)
    witness_rules = role_rules(methods=('event.get', 'host.get'), actions=())
    witness_role = created(rpc('role.create', {'name': 'Zab-al-ack real lab witness', 'type': 1, 'rules': witness_rules}), 'roleids')
    verify_role(rpc('role.get', {'roleids': [witness_role], 'output': ['roleid', 'type'], 'selectRules': list(witness_rules)}),
                witness_role, witness_rules)
    source_usergroup = created(rpc('usergroup.create', {'name': 'Zab-al-ack real lab source readers', 'gui_access': 1,
                                                        'hostgroup_rights': [{'id': group_id, 'permission': 3}]}), 'usrgrpids')
    witness_usergroup = created(rpc('usergroup.create', {'name': 'Zab-al-ack real lab witnesses', 'gui_access': 1,
                                                         'hostgroup_rights': [{'id': group_id, 'permission': 2},
                                                                              {'id': outside_group, 'permission': 2}]}), 'usrgrpids')
    source_user = created(rpc('user.create', {'username': 'zabalack-real-source', 'passwd': PASSWORD, 'roleid': role_id,
                                              'usrgrps': [{'usrgrpid': source_usergroup}]}), 'userids')
    witness_user = created(rpc('user.create', {'username': 'zabalack-real-witness', 'passwd': PASSWORD, 'roleid': witness_role,
                                               'usrgrps': [{'usrgrpid': witness_usergroup}]}), 'userids')
    source_token = token(rpc, source_user, 'Zab-al-ack real lab source token')
    witness_token = token(rpc, witness_user, 'Zab-al-ack real lab witness token')
    # The server's configuration cache must hold the new items before a push is accepted.
    source_event = raise_problem(rpc, source_item, source_trigger)
    e2e_event = raise_problem(rpc, e2e_item, e2e_trigger)
    outside_event = raise_problem(rpc, outside_item, outside_trigger)
    private.mkdir(mode=0o700, exist_ok=True)
    os.chmod(private, 0o700)
    token_file, witness_file = private / 'source.token', private / 'witness.token'
    for path, value in ((token_file, source_token), (witness_file, witness_token)):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(value + '\n')
        os.chmod(path, 0o600)
    fixture = {
        'isolated_lab': True, 'fresh_instance_no_notifications': True, 'url': LAB_API_URL, 'token_file': str(token_file),
        'prepared_against_api_version': version,
        'source': {'zabbix_instance': 'isolated-real-zabbix', 'zabbix_event_id': source_event, 'zabbix_trigger_id': source_trigger},
        'cohort': {'host_ids': [host_id], 'group_ids': [group_id]},
        'outside_scope_source': {'zabbix_event_id': outside_event, 'group_ids': [outside_group], 'witness_token_file': str(witness_file)},
        'end_to_end': {'zabbix_trigger_id': e2e_trigger, 'zabbix_event_id': e2e_event},
    }
    (private / 'fixture.json').write_text(json.dumps(fixture, indent=2) + '\n')
    os.chmod(private / 'fixture.json', 0o600)
    return fixture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--isolated-lab', action='store_true', required=True)
    parser.add_argument('--private', type=Path, required=True, help='mode-0700 directory for the fixture and token files')
    args = parser.parse_args(argv)
    try:
        fixture = prepare(Rpc(LAB_API_URL), args.private)
    except (PrepareError, ValueError, OSError) as error:
        print(json.dumps({'status': 'FAIL', 'reason': str(error)[:300]}))
        return 1
    print(json.dumps({'status': 'PREPARED', 'fixture': str(args.private / 'fixture.json'),
                      'api_version': fixture['prepared_against_api_version'], 'reference': REFERENCE_VERSION,
                      'source_event': fixture['source']['zabbix_event_id'],
                      'outside_event': fixture['outside_scope_source']['zabbix_event_id'],
                      'end_to_end_event': fixture['end_to_end']['zabbix_event_id']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
