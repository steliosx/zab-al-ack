"""T30 supplemental batches: real Alerta HTTP/persistence + SQL worker + mocked source.

This exercises supported single-alert PUT/action 50/200 populations. It does not
claim native PostgreSQL concurrency, PHP-FPM, actual Zabbix or deployment G6.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import platform
import time
from uuid import uuid4

from flask import g
import pytest
from psycopg.types.json import Jsonb

from alerta.models.alert import Alert
from zabalack.config import Settings
from zabalack.worker import Worker

ROOT = Path(__file__).resolve().parents[2]

def load_test_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

api_helpers = load_test_module('batch_api_helpers', 'tests/alerta/test_postgres_http.py')
worker_helpers = load_test_module('batch_worker_helpers', 'tests/postgres/test_worker_path.py')
persisted_api = api_helpers.persisted_api
pytestmark = [pytest.mark.postgres, pytest.mark.alerta, pytest.mark.performance]


class BenchRegistry:
    def resolve(self, instance):
        assert instance == 'http-lab'
        return object()


class BenchSource:
    def __init__(self, already_acknowledged):
        self.already = set(already_acknowledged)
        self.reads, self.writes, self.validation_started, self.validation_write_seconds = [], [], {}, []
    async def event_get(self, event_id, cohort, **kwargs):
        self.validation_started[event_id] = time.monotonic()
        self.reads.append(event_id)
        return [{'eventid': event_id, 'objectid': '910002', 'source': '0', 'object': '0', 'value': '1',
                 'clock': '1700000000', 'acknowledged': '1' if event_id in self.already else '0',
                 'r_eventid': '0', 'hosts': [{'hostid': '910003'}]}]
    async def acknowledge(self, event_id, **kwargs):
        assert event_id not in self.already
        self.writes.append(event_id)
        self.validation_write_seconds.append(time.monotonic() - self.validation_started[event_id])
        self.already.add(event_id)


def distribution(values):
    values = sorted(values)
    if not values:
        return {'p50': None, 'p95': None, 'max': None}
    import math
    return {key: round(values[max(0, math.ceil(q * len(values)) - 1)], 6) for key, q in (('p50', .5), ('p95', .95), ('max', 1))}


@pytest.mark.parametrize('count', [50, 200])
@pytest.mark.asyncio
async def test_T30_real_per_alert_batches_supplemental(persisted_api, pg_lab, count, record_property):
    lab = persisted_api
    cohort = deepcopy(api_helpers.COHORT)
    audit = {'actor_ref': 'synthetic-benchmark-operator', 'change_ref': 'LAB-BATCH-ONLY'}
    with lab.pgdb('zabalack_operator') as operator:
        def control(operation, **payload):
            return operator.execute('SELECT zabalack.operator_call(%s,%s)', (operation, Jsonb(audit | payload))).fetchone()[0]
        control('configure_installation', deployment_id='isolated-benchmark', config_revision='http-r1')
        control('configure_cohort', cohort=cohort)
        control('pause', instance='http-lab')
        control('set_external_writes', enabled=True, confirmation='ENABLE_EXTERNAL_WRITES', gates_reviewed=True)
        control('resume', instance='http-lab')
    identities = []
    for index in range(count):
        source = deepcopy(api_helpers.SOURCE)
        source['zabbix_event_id'] = str(61000000 + count * 1000 + index)
        with lab.app.test_request_context('/synthetic-batch-setup'):
            g.login = 'http-ingest'
            alert = Alert('synthetic-batch-' + str(uuid4()), 'stable-trigger-family', attributes=source,
                          customer='http-lab-customer', severity='major', status='open')
            identities.append((alert.create().id, source['zabbix_event_id']))
    api_seconds, committed_at = [], {}
    started = time.monotonic()
    for identity, event_id in identities:
        before = time.monotonic()
        response = lab.client.put(f'/alert/{identity}/action', headers=lab.headers,
                                 json={'action': 'ack', 'text': 'Synthetic isolated batch'})
        assert response.status_code == 200, 'per-alert acknowledgement failed; inspect redacted API diagnostic separately'
        api_seconds.append(time.monotonic() - before)
        committed_at[event_id] = time.monotonic()
    with lab.pgdb('zabalack_owner') as owner:
        capture_count = owner.execute('SELECT count(*) FROM zabalack.captures WHERE alert_id=ANY(%s)',
                                      ([identity for identity, _ in identities],)).fetchone()[0]
        assert capture_count == count
        peak_queue = owner.execute("SELECT count(*) FROM zabalack.outbox WHERE state IN ('captured_unverified','verifier_hold')").fetchone()[0]
    already = {event for index, (_, event) in enumerate(identities) if index % 2 == 0}
    source_client = BenchSource(already)
    database = worker_helpers.RoleSelectedLabPostgres(pg_lab['admin_dsn'])
    settings = Settings(external_writes_enabled=True, cohorts=(cohort,), config_revision='http-r1',
                        deployment_id='isolated-benchmark', database_name=pg_lab['database_name'])
    worker = Worker(settings, database, {'lab': api_helpers.KEY}, registry=BenchRegistry(),
                    client_factory=lambda _: source_client)
    assert await worker.check_health()
    for _ in range(count):
        assert await worker.verify_once()
    completion_seconds = []
    for _ in range(count):
        assert await worker.delivery_once()
        event_id = source_client.reads[-1]
        completion_seconds.append(time.monotonic() - committed_at[event_id])
    with lab.pgdb('zabalack_owner') as owner:
        rows = owner.execute('SELECT state,completion_evidence FROM zabalack.actions WHERE alert_id=ANY(%s)',
                             ([identity for identity, _ in identities],)).fetchall()
        assert len(rows) == count and all(state == 'ok' for state, _ in rows)
        evidence_counts = dict(Counter(evidence for _, evidence in rows))
        slots_remaining = owner.execute("SELECT count(*) FROM zabalack.delivery_slots WHERE instance='http-lab'").fetchone()[0]
    with lab.pgdb('alerta_app') as application:
        projections = application.execute("SELECT count(*) FROM alerts WHERE id=ANY(%s) AND attributes->>'zabbix_ack_sync'='ok'",
                                           ([identity for identity, _ in identities],)).fetchone()[0]
    assert projections == count
    assert len(source_client.reads) == count
    assert len(source_client.writes) == count // 2 and not (set(source_client.writes) & already)
    assert slots_remaining == 0
    backend = pg_lab.get('backend', 'NOT_VERIFIED')
    report = {
        'test_id': 'T30', 'reference_result': 'PASS', 'catalogue_result': 'MANUAL_GATE_PENDING',
        'backend': backend, 'postgres_version': pg_lab.get('version', 'NOT_VERIFIED'),
        'alerta': '9.1.0', 'python': platform.python_version(),
        # OS family and CPU only; kernel build and libc strings fingerprint the lab host.
        'platform': platform.system() + '-' + platform.machine(),
        'evidence_layer': 'actual pinned Alerta Flask HTTP + PostgreSQL SQL persistence/worker, mocked source client',
        'count': count, 'api_concurrency': 1, 'delivery_concurrency': 1,
        'api_action_seconds': distribution(api_seconds),
        'capture_to_worker_completion_seconds': distribution(completion_seconds),
        'mock_validation_through_prewrite_SQL_to_mock_write_seconds': distribution(source_client.validation_write_seconds),
        'elapsed_seconds': round(time.monotonic() - started, 6), 'captured': capture_count,
        'peak_outbox_queue': peak_queue, 'source_validation_reads': len(source_client.reads),
        'source_writes': len(source_client.writes), 'already_acknowledged': len(already),
        'completion_evidence_counts': evidence_counts, 'projected_ok': projections,
        'api_error_count': 0, 'timed_out': 0, 'delivery_slots_remaining': slots_remaining,
        'unmeasured': ['PHP-FPM/frontend queue and utilisation', 'native PostgreSQL multi-backend concurrency/load',
                       'PgBouncer pooling', 'real Zabbix latency/permissions/notifications',
                       'cross-site failover', 'multi-tenant fairness'],
        'limitations': ['single tenant and single instance', 'synthetic data and mocked source replies',
                       'human JWT and probe success are injected lab fixtures',
                       'No G1-G8 deployment gate is satisfied'],
    }
    if 'wasm' in backend:
        report['limitations'].append('PGlite single WASM backend: no native authentication/TLS/cancellation/HA claims')
    output = ROOT / 'evidence' / f'batch-{count}-supplemental.json'
    output.write_text(json.dumps(report, indent=2) + '\n')
    record_property('evidence_layer', report['evidence_layer'])
    record_property('backend', backend)
    record_property('catalogue_result', 'MANUAL_GATE_PENDING')
