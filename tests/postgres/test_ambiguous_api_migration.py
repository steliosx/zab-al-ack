"""Native/WASM scope follows pg_lab; all simulated downgrades roll back."""
from pathlib import Path

import psycopg
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


def test_T43_forward_live_lease_expiry_migration_and_guards(pg_lab):
    """013's own preconditions, resealing requirement and grants, from a 012 tree."""
    old = (ROOT / 'sql/migrations/012_settled_attempt_suspension.sql').read_text()
    old_body = old[old.index('CREATE OR REPLACE FUNCTION zabalack.worker_call'):]
    controls = (ROOT / 'sql/migrations/004_controls.sql').read_text()
    old_operator = controls[controls.index('CREATE FUNCTION zabalack.operator_call'):controls.index('REVOKE ALL ON FUNCTION zabalack.operator_call')]
    old_operator = old_operator.replace('CREATE FUNCTION zabalack.operator_call', 'CREATE OR REPLACE FUNCTION zabalack.operator_call', 1)
    new = (ROOT / 'sql/migrations/013_live_lease_expiry_guard.sql').read_text()
    with connect_role(pg_lab, 'zabalack_owner') as conn:
        with conn.transaction(force_rollback=True):
            # 004's slice carries no digest re-record; 012's tail re-records
            # the routine pin over whatever bodies are live, so it runs last.
            conn.execute(old_operator)
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
            health = conn.execute('SELECT zabalack.capture_health()').fetchone()[0]
            assert health['zabalack_schema_version'] == 4
            assert health['functions_ok'] is False, 'upgrade must not silently reseal'
            worker = conn.execute("SELECT pg_get_functiondef('zabalack.worker_call(text,jsonb)'::regprocedure)").fetchone()[0]
            operator = conn.execute("SELECT pg_get_functiondef('zabalack.operator_call(text,jsonb)'::regprocedure)").fetchone()[0]
            # Both lifetime-expiry statements now read the delivery facts.
            guard = "t.operation='write' AND zabalack.attempt_unsettled(t.attempt_id)"
            assert worker.count(guard) == 1 and operator.count(guard) == 2
            assert "WHERE state IN ('authorised','pending','held') AND NOT uncertain AND execute_before<=now_at AND next_due<=now_at ORDER BY next_due" not in worker
            assert "CASE WHEN execute_before<=clock_timestamp() AND NOT uncertain THEN 'expired'" not in operator
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            for role, function, expected in (('alerta_app', 'worker_call', False), ('zabalack_worker', 'worker_call', True),
                                             ('alerta_app', 'operator_call', False), ('zabalack_operator', 'operator_call', True),
                                             ('zabalack_worker', 'operator_call', False)):
                assert conn.execute(f"SELECT has_function_privilege('{role}','zabalack.{function}(text,jsonb)','EXECUTE')").fetchone()[0] is expected


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

