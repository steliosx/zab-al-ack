"""Independent T8/T9/T11/T14/T37 tests at the Python verifier boundary.

Database and target client are spies here. These tests prove ordering and policy;
real PostgreSQL uniqueness/leases and installed tenant permission remain separate.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from zabalack.crypto import (BindingError, build_intent, canonical_bytes,
    sign_intent, timestamp, verify_capture)
from zabalack.worker import Worker


def cohort(customer='lab-A', trigger='201'):
    return {'customer': customer, 'zabbix_instance': 'lab-instance', 'zabbix_trigger_id': trigger,
            'host_ids': ['301'], 'group_ids': ['401'], 'revision': 'cohort-1', 'policy_revision': 'policy-1',
            'enabled': True, 'one_to_one': True, 'comment_enabled': True, 'allow_admin': False}


def signed_capture(customer='lab-A', trigger='201', actor_customers=None, issued=None):
    issued = issued or datetime.now(timezone.utc)
    intent = build_intent(str(uuid4()), customer,
        {'zabbix_instance': 'lab-instance', 'zabbix_event_id': '101', 'zabbix_trigger_id': trigger},
        {'login': 'synthetic-human', 'customers': actor_customers or ['lab-A'], 'admin': False, 'auth_type': 'user'},
        'cohort-1', 'policy-1', now=issued)
    key = bytes(range(32))
    capture = {'capture_id': str(uuid4()), 'intent': intent, 'marker': sign_intent(intent, 'lab-key', key),
               'alert_id': intent['alert_id'], 'customer': intent['customer'], 'source': deepcopy(intent['source']),
               'history_type': 'ack', 'resulting_status': 'ack', 'history_user': 'synthetic-human',
               'captured_at': timestamp(issued + timedelta(seconds=1)), 'lease_generation': 1}
    return capture, key


class NoTargetRegistry:
    def __init__(self):
        self.lookups = 0
    def resolve(self, instance):
        self.lookups += 1
        raise AssertionError('verification must not resolve target or credential')


class VerificationDB:
    def __init__(self, captures):
        self.captures = list(captures)
        self.calls = []
    async def call(self, operation, payload):
        self.calls.append((operation, payload))
        if operation == 'claim_verification':
            return self.captures.pop(0) if self.captures else None
        if operation == 'verify_complete':
            return {'action_id': payload['intent']['action_id']}
        return {}


def verifier(captures, keys, cohorts):
    db, registry = VerificationDB(captures), NoTargetRegistry()
    worker = Worker(SimpleNamespace(cohorts=cohorts, endpoint_registry={}, lab_mode=True), db, keys,
                    registry=registry)
    return worker, db, registry


async def test_T8_T37_forged_genuine_action_identity_cannot_reserve_verified_action():
    real, key = signed_capture()
    forged = deepcopy(real)
    forged['capture_id'] = str(uuid4())
    forged['marker'] = sign_intent(forged['intent'], 'lab-key', bytes(reversed(range(32))))
    worker, db, registry = verifier([forged, real], {'lab-key': key}, [cohort()])
    assert await worker.verify_once()
    assert await worker.verify_once()
    completions = [p for op, p in db.calls if op == 'verify_complete']
    rejections = [p for op, p in db.calls if op == 'reject_capture']
    assert len(completions) == len(rejections) == 1
    assert rejections[0]['capture_id'] == forged['capture_id']
    assert completions[0]['capture_id'] == real['capture_id']
    assert registry.lookups == 0
    assert not any(op in {'claim_delivery', 'start_attempt'} for op, _ in db.calls)


async def test_T11a_cross_customer_scope_rejected_before_target_resolution():
    capture, key = signed_capture(customer='lab-B', actor_customers=['lab-A'])
    worker, db, registry = verifier([capture], {'lab-key': key}, [cohort(customer='lab-B')])
    assert await worker.verify_once()
    assert any(op == 'reject_capture' for op, _ in db.calls)
    assert not any(op == 'verify_complete' for op, _ in db.calls)
    assert registry.lookups == 0


async def test_T11b_authorised_other_cohort_selection_remains_trusted_operator_scope():
    capture, key = signed_capture(trigger='202')
    worker, db, registry = verifier([capture], {'lab-key': key}, [cohort(), cohort(trigger='202')])
    assert await worker.verify_once()
    complete = [p for op, p in db.calls if op == 'verify_complete']
    assert len(complete) == 1 and complete[0]['intent']['source']['zabbix_trigger_id'] == '202'
    assert registry.lookups == 0
    # This is deliberate accepted scope, not ingestion-lineage verification.


def test_T9_worker_delay_does_not_change_database_capture_lifetime():
    capture, key = signed_capture(issued=datetime.now(timezone.utc) - timedelta(minutes=2))
    assert verify_capture(capture, {'lab-key': key})['action_id'] == capture['intent']['action_id']


@pytest.mark.parametrize('field', ['customer', 'source', 'history_user', 'history_type', 'resulting_status'])
def test_T10_T14_capture_snapshot_or_history_mutation_rejected(field):
    capture, key = signed_capture()
    capture[field] = {'zabbix_event_id': '999'} if field == 'source' else 'unrelated'
    with pytest.raises(BindingError):
        verify_capture(capture, {'lab-key': key})


@pytest.mark.parametrize('mutation', ['extra_intent_key', 'customers_string', 'service_actor', 'unsupported_route'])
def test_T14_unexpected_intent_schema_never_canonicalises(mutation):
    capture, key = signed_capture()
    intent = capture['intent']
    if mutation == 'extra_intent_key':
        intent['free_text'] = 'do not retain this unrelated value'
    elif mutation == 'customers_string':
        intent['actor']['customers'] = 'lab-A'
    elif mutation == 'service_actor':
        intent['actor']['auth_type'] = 'api_key'
    else:
        intent['route'] = f"/alert/{intent['alert_id']}/status"
    with pytest.raises(BindingError):
        canonical_bytes(intent)


async def test_T37_malformed_flood_only_claims_verification_leases():
    capture, key = signed_capture()
    captures = []
    for _ in range(100):
        bad = deepcopy(capture)
        bad['capture_id'], bad['marker'] = str(uuid4()), '[zabalack:truncated'
        captures.append(bad)
    captures.append(capture)
    worker, db, registry = verifier(captures, {'lab-key': key}, [cohort()])
    for _ in captures:
        assert await worker.verify_once()
    assert sum(op == 'reject_capture' for op, _ in db.calls) == 100
    assert sum(op == 'verify_complete' for op, _ in db.calls) == 1
    assert registry.lookups == 0
    assert not any(op in {'claim_delivery', 'start_attempt'} for op, _ in db.calls)

@pytest.mark.parametrize('relabel', [False, True])
async def test_T8_rename_rejects_legacy_domain_before_target_access(relabel):
    import hashlib
    import hmac
    import json
    capture, key = signed_capture()
    intent = capture['intent']
    # Independently reproduce the historical domain. Replacing its visible
    # prefix must not turn the old signature into a new valid acknowledgement.
    old_bytes = b'ackline\x00v1\x00' + json.dumps(
        intent, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    mac = hmac.new(key, old_bytes, hashlib.sha256).hexdigest()
    prefix = 'zabalack' if relabel else 'ackline'
    capture['marker'] = f'[{prefix}:v1 action={intent["action_id"]} kid=lab-key mac={mac}]'
    worker, db, registry = verifier([capture], {'lab-key': key}, [cohort()])
    assert await worker.verify_once()
    assert any(op == 'reject_capture' for op, _ in db.calls)
    assert not any(op in {'verify_complete', 'claim_delivery', 'start_attempt'} for op, _ in db.calls)
    assert registry.lookups == 0
