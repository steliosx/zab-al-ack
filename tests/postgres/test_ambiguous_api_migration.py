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
