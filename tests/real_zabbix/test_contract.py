"""Optional REAL Zabbix7.0 contract checks; no mock transport or production host.

These remain NOT_RUN without a specifically prepared disposable Compose fixture.
Positive API checks do not establish G3 notification or G5 tenant permission gates.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets

import httpx
import pytest
from zabalack.rpc import Endpoint, ZabbixClient, validate_event, observed_reference
from lab.real_zabbix_safety import validate_lab_fixture, validate_version_envelope

pytestmark = pytest.mark.real_zabbix


@pytest.fixture(scope='module')
def real_fixture():
    filename = os.environ.get('ACK_TEST_REAL_ZABBIX_FIXTURE')
    if not filename:
        pytest.skip('NOT_RUN: disposable real Zabbix fixture not supplied; see lab/real_zabbix.md')
    if os.environ.get('ACK_TEST_REAL_ZABBIX_LAB') != 'YES_ISOLATED_LAB':
        pytest.fail('Real Zabbix contract tests require explicit isolated-lab selection')
    data = json.loads(Path(filename).read_text())
    try:
        validate_lab_fixture(data)
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)
    with httpx.Client(timeout=5,follow_redirects=False,trust_env=False) as client:
        response = client.post(data['url'],json={'jsonrpc':'2.0','method':'apiinfo.version','params':{},'id':'lab-reference-version'})
        response.raise_for_status()
        envelope=response.json()
    # Reference pin is 7.0.0; any 7.0.x patch release of the same LTS line is
    # accepted for the disposable lab (the client itself accepts 7.0.[0-9]+).
    # The observed version is recorded in the JUnit properties of each test.
    try:
        observed = validate_version_envelope(envelope, 'lab-reference-version')
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)
    endpoint=Endpoint('isolated-real-zabbix',data['url'],data['token_file'],None,api_version=observed,lab=True)
    data['observed_api_version']=observed
    return data,ZabbixClient(endpoint)


@pytest.fixture(autouse=True)
def _record_api_version(request, record_property):
    if 'real_fixture' in request.fixturenames:
        record_property('zabbix_api_version', request.getfixturevalue('real_fixture')[0]['observed_api_version'])


@pytest.mark.asyncio
async def test_T13_real_exact_original_event_read(real_fixture):
    data,client=real_fixture
    result=await client.event_get(data['source']['zabbix_event_id'],data['cohort'],history=True)
    event=validate_event(result,data['source'],data['cohort'])
    assert str(event['eventid']) == data['source']['zabbix_event_id']


@pytest.mark.asyncio
async def test_T33_real_approved_host_inventory(real_fixture):
    data,client=real_fixture
    hosts=await client.host_get(data['cohort']['host_ids'])
    assert {str(h['hostid']) for h in hosts} == set(data['cohort']['host_ids'])
    assert all('hostgroups' in h for h in hosts)


@pytest.mark.asyncio
async def test_T11a_real_negative_visibility_scoped_token(real_fixture, record_property):
    data,client=real_fixture
    outside=data.get('outside_scope_source')
    if not outside:
        pytest.skip('NOT_RUN: independently established outside-scope event and restricted lab token required')
    if not outside.get('witness_token_file'):
        pytest.skip('NOT_RUN: outside-scope witness token absent; an empty read alone does not prove a permission boundary (lab/real_zabbix.md)')
    # Positive proof first, from a separate read-only witness token that can see
    # both host groups: the outside event exists, belongs to the outside group and
    # not to the in-scope cohort group. Only then is the restricted token's empty
    # read meaningful. The witness is never used for a write.
    witness=ZabbixClient(Endpoint('isolated-real-zabbix-witness',data['url'],outside['witness_token_file'],None,
                                  api_version=data['observed_api_version'],lab=True))
    seen=await witness.event_get(outside['zabbix_event_id'],{'group_ids':outside['group_ids']})
    assert len(seen) == 1 and str(seen[0]['eventid']) == outside['zabbix_event_id'], 'Witness token cannot establish the outside-scope event'
    assert await witness.event_get(outside['zabbix_event_id'],data['cohort']) == [], 'Outside-scope event is inside the cohort group'
    assert len(await witness.event_get(data['source']['zabbix_event_id'],data['cohort'])) == 1, 'Witness token cannot see the cohort group'
    # The restricted lab token must return a valid empty visibility result, not a
    # generic request rejection, for the event the witness just proved to exist.
    # This validates only the supplied lab token/event pair. All actual customer
    # boundaries still require independent deployment permission evidence.
    result=await client.event_get(outside['zabbix_event_id'],{'group_ids':outside['group_ids']})
    assert result == [], 'Restricted lab token unexpectedly sees outside-scope event'
    record_property('outside_scope_witness', 'existence and group association established by a separate read-only witness token before the negative read')


@pytest.mark.asyncio
async def test_T1_T25_real_acknowledge_and_reference_contract(real_fixture):
    data,client=real_fixture
    if os.environ.get('ACK_TEST_REAL_ZABBIX_WRITE') != 'ACKNOWLEDGE_SYNTHETIC_LAB_EVENT':
        pytest.skip('NOT_RUN: explicit synthetic event write opt-in absent')
    result=await client.event_get(data['source']['zabbix_event_id'],data['cohort'],history=True)
    event=validate_event(result,data['source'],data['cohort'])
    if str(event['acknowledged']) == '1':
        pytest.skip('NOT_RUN: prepare a fresh unacknowledged synthetic problem; suite never unacknowledges')
    public_reference=secrets.token_urlsafe(18)
    issued_at=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    await client.acknowledge(data['source']['zabbix_event_id'],comment=True,public_reference=public_reference,issued_at=issued_at)
    # Contract check intentionally reads after one write. Production worker does
    # not require a post-write read for a valid response to establish completion.
    final=validate_event(await client.event_get(data['source']['zabbix_event_id'],data['cohort'],history=True),data['source'],data['cohort'])
    assert str(final['acknowledged']) == '1'
    assert observed_reference(final,public_reference=public_reference,issued_at=issued_at)
    matches=[a for a in final['acknowledges'] if public_reference in a.get('message','')]
    assert len(matches)==1


@pytest.mark.asyncio
async def test_A4_restricted_token_excluded_actions_denied_by_role_permission(real_fixture, record_property):
    """A4: prove the fixture token's excluded actions are denied by the ROLE.

    Amendment A4 evidence, not a T-catalogue case: a Zabbix 7.0 role keeps the
    enabled default of an omitted action rule, so `actions.default_access: 0`
    alone leaves suppression available. A generic "referred object" rejection
    comes from host rights and proves nothing about the action permission, so
    this case requires the specific upstream permission denial. The production
    client cannot express action 32 at all, so the request is issued directly.
    """
    data,_=real_fixture
    if os.environ.get('ACK_TEST_REAL_ZABBIX_WRITE') != 'ACKNOWLEDGE_SYNTHETIC_LAB_EVENT':
        pytest.skip('NOT_RUN: explicit synthetic event write opt-in absent')
    event_id=data['source']['zabbix_event_id']
    token=Path(data['token_file']).read_text().strip()
    def rpc(params):
        with httpx.Client(timeout=10,follow_redirects=False,trust_env=False) as client:
            response=client.post(data['url'],json={'jsonrpc':'2.0','method':'event.acknowledge','params':params,'id':'lab-role-boundary'},
                                 headers={'Content-Type':'application/json-rpc','Authorization':'Bearer '+token})
            response.raise_for_status()
            return response.json()
    envelope=rpc({'eventids':event_id,'action':32,'suppress_until':int(datetime.now(timezone.utc).timestamp())+120})
    if 'error' not in envelope:
        # Fail closed: revert the unintended effect on this synthetic event first.
        rpc({'eventids':event_id,'action':64})
        pytest.fail('Lab role permits suppression (A4): enumerate every action rule and verify role.get', pytrace=False)
    reason=str(envelope['error'].get('data',''))
    assert 'no permissions to suppress problems' in reason.lower(), \
        'Expected the upstream suppression-permission denial, not ' + reason[:120]
    record_property('excluded_action_denial','action 32 refused by the effective role permission, not by host rights')
