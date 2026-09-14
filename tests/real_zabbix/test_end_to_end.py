"""Full path on real components: Alerta HTTP acknowledgement → PostgreSQL capture →
worker verification → real Zabbix 7.0 read/acknowledge → ledger → projection.

Requires both the disposable PostgreSQL lab (ACK_TEST_ADMIN_DSN) and the explicit
real-Zabbix fixture with write opt-in (lab/real_zabbix.md). The worker uses the
production client and endpoint registry, not a mock; only the lab flag permits
plain-HTTP loopback. NOT_RUN without both fixtures. Not G1–G8 evidence.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path

import httpx
import pytest
from psycopg.types.json import Jsonb

from zabalack.config import Settings
from zabalack.worker import Worker
from lab.real_zabbix_safety import validate_lab_fixture, validate_version_envelope

ROOT = Path(__file__).resolve().parents[2]


def load_test_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api_helpers = load_test_module('e2e_api_helpers', 'tests/alerta/test_postgres_http.py')
worker_helpers = load_test_module('e2e_worker_helpers', 'tests/postgres/test_worker_path.py')
persisted_api = api_helpers.persisted_api
pytestmark = [pytest.mark.real_zabbix, pytest.mark.postgres, pytest.mark.alerta]


@pytest.fixture(scope='module')
def real_lab():
    filename = os.environ.get('ACK_TEST_REAL_ZABBIX_FIXTURE')
    if not filename:
        pytest.skip('NOT_RUN: disposable real Zabbix fixture not supplied; see lab/real_zabbix.md')
    if os.environ.get('ACK_TEST_REAL_ZABBIX_LAB') != 'YES_ISOLATED_LAB':
        pytest.fail('Real Zabbix end-to-end requires explicit isolated-lab selection')
    if os.environ.get('ACK_TEST_REAL_ZABBIX_WRITE') != 'ACKNOWLEDGE_SYNTHETIC_LAB_EVENT':
        pytest.skip('NOT_RUN: explicit synthetic event write opt-in absent')
    data = json.loads(Path(filename).read_text())
    try:
        validate_lab_fixture(data)
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)
    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
        response = client.post(data['url'], json={'jsonrpc': '2.0', 'method': 'apiinfo.version', 'params': {}, 'id': 'e2e-version'})
        response.raise_for_status()
        envelope = response.json()
    try:
        observed = validate_version_envelope(envelope, 'e2e-version')
    except ValueError as error:
        pytest.fail(str(error), pytrace=False)
    if not isinstance(data.get('end_to_end'), dict) or not data['end_to_end'].get('zabbix_event_id') or not data['end_to_end'].get('zabbix_trigger_id'):
        pytest.skip('NOT_RUN: fixture has no separate end_to_end problem (event and trigger); see lab/real_zabbix.md')
    data['observed_api_version'] = observed
    return data


@pytest.mark.asyncio
async def test_T1_T13_T25_T38_end_to_end_real_alerta_capture_worker_real_zabbix(persisted_api, pg_lab, real_lab, record_property):
    lab = persisted_api
    instance = real_lab['source']['zabbix_instance']
    e2e = real_lab['end_to_end']
    cohort = {'customer': 'http-lab-customer', 'zabbix_instance': instance,
              'zabbix_trigger_id': e2e['zabbix_trigger_id'],
              'host_ids': list(real_lab['cohort']['host_ids']), 'group_ids': list(real_lab['cohort']['group_ids']),
              'revision': 'e2e-r1', 'policy_revision': '1', 'enabled': True, 'one_to_one': True,
              'comment_enabled': True, 'allow_admin': False}
    source = {'source_system': 'zabbix', 'source_contract_version': '1', 'zabbix_instance': instance,
              'zabbix_event_id': e2e['zabbix_event_id'], 'zabbix_trigger_id': e2e['zabbix_trigger_id'],
              'zabbix_cause_event_id': None, 'zabbix_recovery_event_id': None}
    # The signer plugin and the worker share one configuration revision and cohort.
    lab.signer.settings = Settings(enabled=True, config_revision='e2e-r1', signing_kid='lab', cohorts=(cohort,),
                                   ingestion_mappings={'http-ingest': {'customer': 'http-lab-customer', 'instance': instance}})
    audit = {'actor_ref': 'synthetic-e2e-operator', 'change_ref': 'LAB-E2E-ONLY'}
    with lab.pgdb('zabalack_operator') as operator:
        def control(operation, **payload):
            return operator.execute('SELECT zabalack.operator_call(%s,%s)', (operation, Jsonb(audit | payload))).fetchone()[0]
        control('configure_installation', deployment_id='isolated-e2e', config_revision='e2e-r1')
        control('configure_cohort', cohort=cohort)
        control('pause', instance=instance)
        control('set_external_writes', enabled=True, confirmation='ENABLE_EXTERNAL_WRITES', gates_reviewed=True)
        control('resume', instance=instance)
    # 1. A real Alerta alert for the real Zabbix problem, acknowledged through the real HTTP action route.
    from flask import g
    from alerta.models.alert import Alert
    from uuid import uuid4
    with lab.app.test_request_context('/synthetic-e2e-setup'):
        g.login = 'http-ingest'
        alert_id = Alert('synthetic-e2e-' + str(uuid4()), 'stable-trigger-family', attributes=source,
                         customer='http-lab-customer', severity='major', status='open').create().id
    response = lab.client.put(f'/alert/{alert_id}/action', headers=lab.headers,
                              json={'action': 'ack', 'text': 'Synthetic isolated end-to-end'})
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    captures = api_helpers.capture_rows(lab, alert_id)
    assert len(captures) == 1 and captures[0][10] == 'ack'
    # 2. The production worker: real verifier, real endpoint registry, real ZabbixClient.
    settings = Settings(external_writes_enabled=True, cohorts=(cohort,), config_revision='e2e-r1',
                        deployment_id='isolated-e2e', database_name=pg_lab['database_name'], lab_mode=True,
                        approved_versions=(real_lab['observed_api_version'],),
                        endpoint_registry={instance: {'logical_instance': instance, 'url': real_lab['url'],
                                                      'token_file': real_lab['token_file'],
                                                      'api_version': real_lab['observed_api_version']}})
    database = worker_helpers.RoleSelectedLabPostgres(pg_lab['admin_dsn'])
    worker = Worker(settings, database, {'lab': api_helpers.KEY})
    assert await worker.check_health()
    assert await worker.verify_once()
    assert await worker.delivery_once()
    # 3. Ledger and projection.
    with lab.pgdb('zabalack_owner') as owner:
        state, reason, evidence, effect_id = owner.execute(
            'SELECT state,reason,completion_evidence,effect_id FROM zabalack.actions WHERE alert_id=%s', (alert_id,)).fetchone()
        attempts = owner.execute("SELECT count(*) FROM zabalack.attempts t JOIN zabalack.actions a ON a.action_id=t.action_id WHERE a.alert_id=%s AND t.operation='write'", (alert_id,)).fetchone()[0]
    assert state == 'ok', (state, reason)
    assert evidence == 'write_response'
    assert attempts == 1
    with lab.pgdb('alerta_app') as application:
        attributes = application.execute('SELECT attributes FROM alerts WHERE id=%s', (alert_id,)).fetchone()[0]
    assert attributes.get('zabbix_ack_sync') == 'ok'
    assert attributes.get('zabbix_ack_sync_effect_id') == str(effect_id)
    assert attributes.get('zabbix_event_id') == source['zabbix_event_id']  # source identity preserved
    # 4. The real Zabbix problem is acknowledged exactly once, with the neutral public-reference comment.
    with httpx.Client(timeout=5, trust_env=False) as client:
        token = Path(real_lab['token_file']).read_text().strip()
        envelope = client.post(real_lab['url'], headers={'Authorization': 'Bearer ' + token},
                               json={'jsonrpc': '2.0', 'method': 'event.get', 'id': 'e2e-readback',
                                     'params': {'eventids': [source['zabbix_event_id']], 'output': ['eventid', 'acknowledged'],
                                                'selectAcknowledges': ['action', 'message']}}).json()
    event = envelope['result'][0]
    assert str(event['acknowledged']) == '1'
    acknowledges = [a for a in event['acknowledges'] if a['message'].startswith('[zabalack]')]
    assert len(acknowledges) == 1
    with lab.pgdb('zabalack_owner') as owner:
        public_reference = owner.execute('SELECT t.public_reference FROM zabalack.attempts t JOIN zabalack.actions a ON a.action_id=t.action_id WHERE a.alert_id=%s', (alert_id,)).fetchone()[0]
    assert public_reference and public_reference in acknowledges[0]['message']
    # 5. A second delivery pass finds nothing to send and writes nothing more.
    assert not await worker.delivery_once()
    record_property('zabbix_api_version', real_lab['observed_api_version'])
    record_property('backend', pg_lab['backend'])
    record_property('evidence_layer', 'real Alerta HTTP + PostgreSQL capture/ledger + production worker/client + real Zabbix API')
