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
            body = conn.execute("SELECT pg_get_functiondef('zabalack.worker_call(text,jsonb)'::regprocedure)").fetchone()[0]
            assert "OR p->>'reason'='api_application_error'" in body
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            assert not conn.execute("SELECT has_function_privilege('alerta_app','zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0]
            assert conn.execute("SELECT has_function_privilege('zabalack_worker','zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0]
