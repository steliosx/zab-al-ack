"""Native tests must not be reported passed by the supplemental WASM runner."""
from pathlib import Path
import concurrent.futures
import pytest
from psycopg.types.json import Jsonb
from conftest import connect_role

ROOT=Path(__file__).resolve().parents[2]
pytestmark=pytest.mark.postgres

def test_T1_T6_T8_T14_T21_T22_T35_T38_T39_real_composite_and_exact_roles(pg_lab):
    with connect_role(pg_lab,'zabalack_worker') as conn:
        conn.execute('RESET ROLE')
        conn.execute((ROOT/'tests/postgres/assertions.sql').read_text())
        conn.execute('SET ROLE zabalack_operator')
        conn.execute("SELECT zabalack.operator_call('capture_bypass','{\"enabled\":false,\"config_revision\":\"lab-v1\",\"actor_ref\":\"lab-engineer\",\"change_ref\":\"LAB-test-cleanup\"}')")
        conn.execute("SELECT zabalack.operator_call('resume','{\"instance\":\"lab-zabbix\",\"actor_ref\":\"lab-engineer\",\"change_ref\":\"LAB-test-cleanup\"}')")

def test_T39_migration_rerun_detects_drift(pg_lab):
    if pg_lab['backend']!='native_postgres18':
        pytest.skip('NOT_RUN: supplemental runner installs SQL directly; migration checksum test uses native harness')
    import importlib.util
    spec=importlib.util.spec_from_file_location('ack_migration_test',ROOT/'sql/migrate.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    with connect_role(pg_lab,'zabalack_owner') as conn:
        mod.migrate(conn)  # identical checksums skip
        with conn.transaction(force_rollback=True):
            conn.execute("UPDATE zabalack.schema_migrations SET sha256='deliberate-test-drift' WHERE version='001_core.sql'")
            with pytest.raises(RuntimeError,match='Migration drift'):
                mod.migrate(conn)

def test_T35_probe_has_no_inherited_memberships(pg_lab):
    with connect_role(pg_lab,'zabalack_probe') as conn:
        assert conn.execute("SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member='zabalack_probe'::regrole)").fetchone()==(False,)
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL statement_timeout='500ms'")
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['in_recovery'] is False
        assert conn.execute('SELECT 1').fetchone()==(1,)

def test_T37_native_concurrent_verifier_claims_never_duplicate(pg_lab):
    if pg_lab['backend']!='native_postgres18':
        pytest.skip('BLOCKED_ENVIRONMENT: native multisession concurrency cannot be proved by serialized WASM backend')
    # Seed untrusted structurally captured rows as owner solely for this queue
    # concurrency assertion, not as evidence for plugin/source permissions.
    with connect_role(pg_lab,'zabalack_owner') as conn:
        conn.execute("INSERT INTO zabalack.captures(alert_id,customer,snapshot,history_type,resulting_status) SELECT gen_random_uuid()::text,'synthetic-flood','{}','ack','ack' FROM generate_series(1,60)")
        conn.execute("INSERT INTO zabalack.outbox(capture_id) SELECT capture_id FROM zabalack.captures WHERE customer='synthetic-flood'")
    def claim(n):
        with connect_role(pg_lab,'zabalack_worker') as conn:
            return conn.execute('SELECT zabalack.worker_call(%s,%s)',('claim_verification',Jsonb({'worker_id':f'concurrent-{n}'}))).fetchone()[0]
    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as pool:
        rows=list(pool.map(claim,range(40)))
    ids=[r['capture_id'] for r in rows if r]
    assert len(ids)==len(set(ids))
    assert len(ids)<=32
    # The flood holds the global verification-lease bound (32 live leases for
    # ten seconds); release it so later tests in the same lab database can
    # claim verification immediately instead of failing on the bound.
    with connect_role(pg_lab,'zabalack_owner') as conn:
        conn.execute("DELETE FROM zabalack.verification_leases WHERE capture_id IN (SELECT capture_id FROM zabalack.captures WHERE customer='synthetic-flood')")
        conn.execute("DELETE FROM zabalack.outbox WHERE capture_id IN (SELECT capture_id FROM zabalack.captures WHERE customer='synthetic-flood')")
        conn.execute("DELETE FROM zabalack.captures WHERE customer='synthetic-flood'")
        assert conn.execute("SELECT count(*) FROM zabalack.verification_leases WHERE owner LIKE 'concurrent-%%' AND expires_at>clock_timestamp()").fetchone()[0]==0

@pytest.mark.parametrize('script', [
    'sql/install/00_roles.sql', 'sql/migrations/001_core.sql',
    'sql/install/10_trigger.sql', 'sql/install/11_enable_trigger.sql',
])
def test_T39_rename_installation_refuses_legacy_schema_without_touching_it(pg_lab, script):
    import psycopg
    from psycopg import sql
    with connect_role(pg_lab, 'zabalack_owner') as conn:
        conn.execute('RESET ROLE')
        # Only this fresh disposable fixture creates the synthetic legacy data.
        # The whole transaction rolls back; no real retained schema is touched.
        with conn.transaction(force_rollback=True):
            conn.execute('CREATE SCHEMA ackline')
            conn.execute('CREATE TABLE ackline.retained_evidence(value text)')
            conn.execute("INSERT INTO ackline.retained_evidence VALUES ('synthetic-unchanged')")
            if '001_core' in script:
                conn.execute('SET LOCAL ROLE zabalack_owner')
            statement = (ROOT / script).read_text().replace('BEGIN;\n', '').replace('COMMIT;', '')
            statement = statement.replace(':"database_name"', sql.Identifier(pg_lab['database_name']).as_string(conn))
            statement = statement.replace(':"alert_owner"', 'alerta_owner')
            with pytest.raises(psycopg.errors.RaiseException, match='legacy_ackline_schema_present'):
                with conn.transaction():
                    conn.execute(statement)
            conn.execute('RESET ROLE')
            assert conn.execute('SELECT value FROM ackline.retained_evidence').fetchall() == [('synthetic-unchanged',)]
        assert conn.execute("SELECT pg_catalog.to_regnamespace('ackline')").fetchone() == (None,)


def test_T39_rename_migrator_refuses_legacy_schema_before_checksum_mutations(pg_lab):
    import importlib.util
    spec = importlib.util.spec_from_file_location('rename_migration_test', ROOT / 'sql/migrate.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with connect_role(pg_lab, 'zabalack_owner') as conn:
        conn.execute('RESET ROLE')
        with conn.transaction(force_rollback=True):
            conn.execute('CREATE SCHEMA ackline')
            before = conn.execute("SELECT pg_catalog.to_regclass('zabalack.schema_migrations')").fetchone()
            conn.execute('SET LOCAL ROLE zabalack_owner')
            with pytest.raises(RuntimeError, match='legacy_ackline_schema_present'):
                mod.migrate(conn)
            assert conn.execute("SELECT pg_catalog.to_regclass('zabalack.schema_migrations')").fetchone() == before
        assert conn.execute("SELECT pg_catalog.to_regnamespace('ackline')").fetchone() == (None,)
