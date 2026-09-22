"""Native/WASM scope follows pg_lab; all simulated downgrades roll back."""
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
import pytest

from conftest import connect_role

ROOT = Path(__file__).resolve().parents[2]


def test_T28_T29_forward_application_error_migration_and_guards(pg_lab):
    old = (ROOT / 'sql/migrations/009_abandoned_attempt_sweep.sql').read_text()
    old_body = old[old.index('CREATE OR REPLACE FUNCTION zabalack.worker_call'):]
    new = (ROOT / 'sql/migrations/010_ambiguous_api_failure.sql').read_text()
    with connect_role(pg_lab, 'zabalack_owner') as conn:
        with conn.transaction(force_rollback=True):
            conn.execute(old_body)
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            assert 'api_error_recovery_version' not in conn.execute("SELECT zabalack.worker_call('doctor','{}')").fetchone()[0]
            with pytest.raises(psycopg.Error, match='found schema 3'):
                with conn.transaction():
                    conn.execute('UPDATE zabalack.installation SET schema_version=3 WHERE singleton')
                    conn.execute(new)
            with pytest.raises(psycopg.Error, match='routine body drift'):
                with conn.transaction():
                    conn.execute("UPDATE zabalack.installation SET routine_bodies_md5='not-the-live-digest' WHERE singleton")
                    conn.execute(new)
            conn.execute(new)
            assert conn.execute("SELECT zabalack.worker_call('doctor','{}')").fetchone()[0]['api_error_recovery_version'] == 1
            health = conn.execute('SELECT zabalack.capture_health()').fetchone()[0]
            assert health['zabalack_schema_version'] == 4
            assert health['functions_ok'] is False, 'upgrade must not silently reseal'
            body = conn.execute("SELECT pg_get_functiondef('zabalack.worker_call(text,jsonb)'::regprocedure)").fetchone()[0]
            assert "OR p->>'reason'='api_application_error'" in body
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            assert not conn.execute("SELECT has_function_privilege('alerta_app','zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0]
            assert conn.execute("SELECT has_function_privilege('zabalack_worker','zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0]


def test_T29_T31_forward_late_suspension_migration_and_guards(pg_lab):
    """011's own preconditions, resealing requirement and grants, from a 010 tree.

    Without this the file's refusals are unexercised: a migration whose guards
    nothing executes is a guard nobody has seen refuse anything.
    """
    old = (ROOT / 'sql/migrations/010_ambiguous_api_failure.sql').read_text()
    old_body = old[old.index('CREATE OR REPLACE FUNCTION zabalack.worker_call'):]
    new = (ROOT / 'sql/migrations/011_late_ambiguous_suspension.sql').read_text()
    with connect_role(pg_lab, 'zabalack_owner') as conn:
        with conn.transaction(force_rollback=True):
            conn.execute(old_body)
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            with pytest.raises(psycopg.Error, match='found schema 3'):
                with conn.transaction():
                    conn.execute('UPDATE zabalack.installation SET schema_version=3 WHERE singleton')
                    conn.execute(new)
            with pytest.raises(psycopg.Error, match='routine body drift'):
                with conn.transaction():
                    conn.execute("UPDATE zabalack.installation SET routine_bodies_md5='not-the-live-digest' WHERE singleton")
                    conn.execute(new)
            conn.execute(new)
            # The capability gate is deliberately unchanged: 011 strengthens
            # behaviour 010 already advertises, so a worker pinning 1 runs here.
            assert conn.execute("SELECT zabalack.worker_call('doctor','{}')").fetchone()[0]['api_error_recovery_version'] == 1
            health = conn.execute('SELECT zabalack.capture_health()').fetchone()[0]
            assert health['zabalack_schema_version'] == 4
            assert health['functions_ok'] is False, 'upgrade must not silently reseal'
            body = conn.execute("SELECT pg_get_functiondef('zabalack.worker_call(text,jsonb)'::regprocedure)").fetchone()[0]
            assert "IF p->>'reason'='api_application_error'" in body
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            assert not conn.execute("SELECT has_function_privilege('alerta_app','zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0]
            assert conn.execute("SELECT has_function_privilege('zabalack_worker','zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0]


def test_T29_T31_forward_settled_attempt_migration_and_guards(pg_lab):
    """012's own preconditions, resealing requirement and grants, from an 011 tree."""
    old = (ROOT / 'sql/migrations/011_late_ambiguous_suspension.sql').read_text()
    old_body = old[old.index('CREATE OR REPLACE FUNCTION zabalack.worker_call'):]
    new = (ROOT / 'sql/migrations/012_settled_attempt_suspension.sql').read_text()
    with connect_role(pg_lab, 'zabalack_owner') as conn:
        with conn.transaction(force_rollback=True):
            conn.execute(old_body)
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            with pytest.raises(psycopg.Error, match='found schema 3'):
                with conn.transaction():
                    conn.execute('UPDATE zabalack.installation SET schema_version=3 WHERE singleton')
                    conn.execute(new)
            with pytest.raises(psycopg.Error, match='routine body drift'):
                with conn.transaction():
                    conn.execute("UPDATE zabalack.installation SET routine_bodies_md5='not-the-live-digest' WHERE singleton")
                    conn.execute(new)
            conn.execute(new)
            assert conn.execute("SELECT zabalack.worker_call('doctor','{}')").fetchone()[0]['api_error_recovery_version'] == 1
            health = conn.execute('SELECT zabalack.capture_health()').fetchone()[0]
            assert health['zabalack_schema_version'] == 4
            assert health['functions_ok'] is False, 'upgrade must not silently reseal'
            body = conn.execute("SELECT pg_get_functiondef('zabalack.worker_call(text,jsonb)'::regprocedure)").fetchone()[0]
            # The instance decision now reads the attempt, as the action update does.
            assert "zabalack.attempt_unsettled((p->>'attempt_id')::uuid) THEN" in body
            assert "a.state IN ('authorised','pending','held','delivery_claimed','unknown') THEN\n     UPDATE zabalack.instance_control" not in body
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            assert not conn.execute("SELECT has_function_privilege('alerta_app','zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0]
            assert conn.execute("SELECT has_function_privilege('zabalack_worker','zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0]


def test_T29_forward_background_fence_migration_and_guards(pg_lab):
    """013's own preconditions, resealing requirement and grants, from a 012 tree.

    The body must differ from 012's in exactly the three predicates 013 names:
    the leased operations join the worker_id allowlist, an absent lease is
    refused at both fence sites, and the reconciliation evidence guard coalesces.
    """
    old = (ROOT / 'sql/migrations/012_settled_attempt_suspension.sql').read_text()
    old_body = old[old.index('CREATE OR REPLACE FUNCTION zabalack.worker_call'):]
    new = (ROOT / 'sql/migrations/013_background_fence_null_safety.sql').read_text()
    with connect_role(pg_lab, 'zabalack_owner') as conn:
        with conn.transaction(force_rollback=True):
            conn.execute(old_body)
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            before = conn.execute("SELECT pg_get_functiondef('zabalack.worker_call(text,jsonb)'::regprocedure)").fetchone()[0]
            with pytest.raises(psycopg.Error, match='found schema 3'):
                with conn.transaction():
                    conn.execute('UPDATE zabalack.installation SET schema_version=3 WHERE singleton')
                    conn.execute(new)
            with pytest.raises(psycopg.Error, match='routine body drift'):
                with conn.transaction():
                    conn.execute("UPDATE zabalack.installation SET routine_bodies_md5='not-the-live-digest' WHERE singleton")
                    conn.execute(new)
            conn.execute(new)
            assert conn.execute("SELECT zabalack.worker_call('doctor','{}')").fetchone()[0]['api_error_recovery_version'] == 1
            health = conn.execute('SELECT zabalack.capture_health()').fetchone()[0]
            assert health['zabalack_schema_version'] == 4
            assert health['functions_ok'] is False, 'upgrade must not silently reseal'
            body = conn.execute("SELECT pg_get_functiondef('zabalack.worker_call(text,jsonb)'::regprocedure)").fetchone()[0]
            assert "'record_reconciliation','record_host_check','record_host_inventory')\n  AND (worker IS NULL" in body
            assert body.count('IF ctl.background_owner IS NULL OR ctl.background_until IS NULL OR ctl.background_owner IS DISTINCT FROM worker') == 2
            assert "coalesce(p->>'completion_evidence','') NOT IN ('reference_observed','source_ack_observed')" in body
            # Nothing else moved: undoing the three edits gives 012's body back.
            reverted = body.replace(",\n           'record_reconciliation','record_host_check','record_host_inventory')", ')')
            reverted = reverted.replace('IF ctl.background_owner IS NULL OR ctl.background_until IS NULL OR ctl.background_owner IS DISTINCT FROM worker', 'IF ctl.background_owner IS DISTINCT FROM worker')
            reverted = reverted.replace("coalesce(p->>'completion_evidence','') NOT IN ('reference_observed','source_ack_observed')", "p->>'completion_evidence' NOT IN ('reference_observed','source_ack_observed')")
            assert reverted == before
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            assert not conn.execute("SELECT has_function_privilege('alerta_app','zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0]
            assert conn.execute("SELECT has_function_privilege('zabalack_worker','zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0]


def test_T29_background_leased_operations_refuse_anonymous_callers_and_absent_leases(pg_lab):
    """013's fence semantics on the live tree, as the worker role.

    Before 013 a payload without worker_id met `background_owner IS DISTINCT
    FROM NULL` on an instance holding no lease, which is FALSE, and an
    absent `background_until` compared NULL, so the fence was not taken and
    the call went on to write. The evidence guard is covered by the T29 block
    of assertions_extended.sql, which owns an action to reconcile.
    """
    instance = 'fence-013-lab'
    with connect_role(pg_lab, 'zabalack_owner') as owner:
        owner.execute('INSERT INTO zabalack.instance_control(instance) VALUES(%s) ON CONFLICT DO NOTHING', (instance,))
        generation, = owner.execute('SELECT background_generation FROM zabalack.instance_control WHERE instance=%s', (instance,)).fetchone()
        audit_rows, = owner.execute("SELECT count(*) FROM zabalack.audit WHERE object_ref=%s", (instance,)).fetchone()
    host = {'instance': instance, 'background_generation': generation, 'customer': 'nobody', 'trigger_id': '1',
            'cohort_revision': 'r0', 'reason': 'host_inventory_ok', 'missing_ids': [], 'next_cursor': 0}
    reconcile = {'instance': instance, 'background_generation': generation,
                 'action_id': '00000000-0000-4000-8000-000000000013', 'state': 'failed', 'reason': 'anonymous'}
    def call(operation, payload):
        # One connection per call, as the other lab tests do: the WASM relay
        # does not resynchronise a session after a raised error.
        with connect_role(pg_lab, 'zabalack_worker') as worker:
            return worker.execute('SELECT zabalack.worker_call(%s,%s)', (operation, Jsonb(payload))).fetchone()[0]
    for operation, payload in (('record_reconciliation', reconcile), ('record_host_check', host), ('record_host_inventory', host)):
        with pytest.raises(psycopg.errors.RaiseException, match='worker_id required'):
            call(operation, payload)
    # A named caller against an instance that holds no lease is refused by
    # the fence itself, not by the later action lookup.
    for operation, payload in (('record_reconciliation', reconcile), ('record_host_check', host), ('record_host_inventory', host)):
        with pytest.raises(psycopg.errors.RaiseException, match='background lease lost'):
            call(operation, dict(payload, worker_id='named-but-leaseless'))
    # The holder keeps working, and loses the fence the moment it releases.
    lease = call('claim_background', {'instance': instance, 'worker_id': 'holder'})
    assert lease['allowed'] is True
    assert call('record_host_inventory', dict(host, worker_id='holder', background_generation=lease['lease_generation'])) == {'recorded': True}
    assert call('finish_background', {'instance': instance, 'worker_id': 'holder', 'lease_generation': lease['lease_generation']}) == {'released': True}
    with pytest.raises(psycopg.errors.RaiseException, match='background lease lost'):
        call('record_host_inventory', dict(host, worker_id='holder', background_generation=lease['lease_generation']))
    with connect_role(pg_lab, 'zabalack_owner') as owner:
        recorded, = owner.execute("SELECT count(*) FROM zabalack.audit WHERE object_ref=%s", (instance,)).fetchone()
        assert recorded == audit_rows + 1, 'only the leased call may leave an audit row'

