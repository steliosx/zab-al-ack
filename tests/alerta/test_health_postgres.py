"""Restricted-role actual health calls; native cancellation separated from WASM."""
from contextlib import contextmanager
import os
import re
import time

import psycopg
import pytest

from zabalack.health import CaptureHealthProbe, CaptureUnhealthy, ProbeConfig

pytestmark = [pytest.mark.postgres, pytest.mark.alerta]


@contextmanager
def slow_capture_function(pgdb):
    """Scoped isolated-lab fault; always restore the exact original definition."""
    with pgdb("zabalack_owner") as owner:
        original = owner.execute("SELECT pg_get_functiondef('zabalack.capture_health()'::regprocedure)").fetchone()[0]
        owner.execute("""CREATE OR REPLACE FUNCTION zabalack.capture_health() RETURNS jsonb LANGUAGE plpgsql
                         STABLE SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$ BEGIN
                         PERFORM pg_sleep(2); RETURN '{}'::jsonb; END $$""")
    try:
        yield
    finally:
        with pgdb("zabalack_owner") as owner:
            owner.execute(original)


def assert_bounded_timeout(probe):
    started = time.monotonic()
    with pytest.raises(CaptureUnhealthy):
        probe.check(force=True)
    elapsed = time.monotonic() - started
    assert .45 <= elapsed < 1.5
    assert probe._connection is None and probe._healthy_at is None
    return elapsed


@pytest.fixture
def real_probe(pg_lab, pgdb):
    with pgdb("zabalack_probe") as role:
        initial = role.execute("SELECT zabalack.capture_health()").fetchone()[0]

    async def connect(dsn):
        connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
        # SET ROLE is explicit in this lab harness. This proves execution under
        # restricted current_user; native login/inheritance remains a G1 gate.
        await connection.execute("SET ROLE zabalack_probe")
        cursor = await connection.execute("SELECT current_user")
        assert (await cursor.fetchone())[0] == "zabalack_probe"
        return connection

    probe = CaptureHealthProbe(ProbeConfig(pg_lab["admin_dsn"], initial["database_name"],
                               initial["deployment_id"], initial["config_revision"]), connector=connect)
    yield probe
    probe.close()


def test_T16_T21_real_probe_function_and_disabled_trigger(real_probe, pgdb):
    assert real_probe.check()["in_recovery"] is False
    with pgdb("alerta_owner") as installer:
        installer.execute("ALTER TABLE public.alerts DISABLE TRIGGER zabalack_capture")
    try:
        with pytest.raises(CaptureUnhealthy):
            real_probe.check(force=True)
        assert real_probe._healthy_at is None
        assert real_probe._connection is None
    finally:
        with pgdb("alerta_owner") as installer:
            installer.execute("ALTER TABLE public.alerts ENABLE TRIGGER zabalack_capture")
    assert real_probe.check(force=True)["trigger_ok"] is True


def test_T34_native_statement_timeout_rollback_and_reconnect(real_probe, pgdb, pg_lab):
    if pg_lab["backend"] != "native_postgres18":
        pytest.skip("NOT_RUN_NATIVE: WASM adapter ignores cancel; cannot establish native timeout/transaction-pool evidence")
    with slow_capture_function(pgdb):
        assert_bounded_timeout(real_probe)
    assert real_probe.check(force=True)["trigger_ok"] is True


def pool_connection_info(pool_dsn, expected_port="56432"):
    """Validate the declared lab endpoint before any pool connection."""
    if not re.fullmatch(r"[1-9][0-9]{0,4}", expected_port) or int(expected_port) > 65535:
        raise ValueError("Expected pool port must be a canonical decimal from 1 through 65535")
    pool_info = psycopg.conninfo.conninfo_to_dict(pool_dsn)
    if (pool_info.get("host") not in {"127.0.0.1", "::1"}
            or pool_info.get("hostaddr", pool_info["host"]) not in {"127.0.0.1", "::1"}
            or pool_info.get("port") != expected_port
            or pool_info.get("dbname") != "alerta"
            or pool_info.get("user") != "zabalack_probe"):
        raise ValueError("Pool DSN must select the declared loopback port, alerta database and dedicated probe user")
    return pool_info


@pytest.mark.pgbouncer
def test_T34_native_pgbouncer_timeout_role_and_clean_reuse(pg_lab, pgdb, record_property):
    """Run with the declared loopback `pooling` Compose profile and probe DSN.

    ACK_TEST_PGBOUNCER_DSN must log in as zabalack_probe. There is no SET
    ROLE fallback: a pool forcing a broader database identity fails this test.
    The Docker runner declares its held relay port in
    ACK_TEST_PGBOUNCER_EXPECTED_PORT; local services retain the 56432 default.
    This port comparison checks the runner's declared reservation, not
    independent endpoint discovery. Pool configuration, connected-role and
    target checks below establish their separate identity boundaries.
    The profile's lab administrator is used only for read-only SHOW commands;
    fault installation remains on the separately scoped direct lab connection.
    """
    pool_dsn = os.environ.get("ACK_TEST_PGBOUNCER_DSN")
    if not pool_dsn:
        pytest.skip("NOT_RUN_PGBOUNCER: ACK_TEST_PGBOUNCER_DSN for the isolated loopback pooling profile is absent")
    if pg_lab["backend"] != "native_postgres18":
        pytest.skip("NOT_RUN_NATIVE_PGBOUNCER: WASM cannot establish transaction pooling, authentication or cancellation")
    try:
        pool_info = pool_connection_info(pool_dsn, os.environ.get("ACK_TEST_PGBOUNCER_EXPECTED_PORT", "56432"))
    except ValueError as error:
        pytest.fail(str(error))

    # Read the running pool's actual configuration. Credentials derive solely
    # from the explicitly authorised synthetic lab administrator connection.
    admin_info = psycopg.conninfo.conninfo_to_dict(pg_lab["admin_dsn"])
    for name in ("host", "hostaddr", "port", "sslmode"):
        admin_info.pop(name, None)
        if name in pool_info:
            admin_info[name] = pool_info[name]
    admin_info["dbname"] = "pgbouncer"
    admin_info["connect_timeout"] = "2"
    with psycopg.connect(**admin_info, autocommit=True) as control:
        version = control.execute("SHOW VERSION").fetchone()[0]
        assert "PgBouncer" in version
        config = {row[0]: row[1] for row in control.execute("SHOW CONFIG").fetchall()}
        assert config["pool_mode"] == "transaction"
        cursor = control.execute("SHOW DATABASES")
        names = [column.name for column in cursor.description]
        database = next(dict(zip(names, row)) for row in cursor.fetchall() if row[0] == "alerta")
        assert database.get("pool_mode") in (None, "", "transaction")
        assert database.get("force_user") in (None, "", "zabalack_probe")
    record_property("pgbouncer_version", version)

    with psycopg.connect(pool_dsn, autocommit=True, connect_timeout=2) as connection:
        assert connection.execute("SELECT current_user").fetchone()[0] == "zabalack_probe"
        initial = connection.execute("SELECT zabalack.capture_health()").fetchone()[0]
    with pgdb("zabalack_probe") as direct:
        expected = direct.execute("SELECT zabalack.capture_health()").fetchone()[0]
    for field in ("database_name", "deployment_id", "config_revision"):
        assert initial[field] == expected[field]

    async def connect(dsn):
        connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True, connect_timeout=2)
        try:
            cursor = await connection.execute("SELECT current_user")
            assert (await cursor.fetchone())[0] == "zabalack_probe"
            return connection
        except BaseException:
            await connection.close()
            raise

    probe = CaptureHealthProbe(ProbeConfig(pool_dsn, initial["database_name"], initial["deployment_id"],
                               initial["config_revision"]), connector=connect)
    try:
        assert probe.check(force=True)["trigger_ok"] is True
        with slow_capture_function(pgdb):
            elapsed = assert_bounded_timeout(probe)
        record_property("timeout_elapsed_seconds", round(elapsed, 4))
        assert probe.check(force=True)["trigger_ok"] is True
        reconnected = probe._connection
        assert probe.check(force=True)["trigger_ok"] is True
        assert probe._connection is reconnected

        async def clean_session():
            cursor = await reconnected.execute(
                "SELECT current_user,current_setting('statement_timeout'),current_setting('transaction_read_only'),1")
            return await cursor.fetchone()

        # Same post-timeout probe client, after two committed pooled refreshes:
        # no INERROR leak, no surviving SET LOCAL timeout or read-only transaction.
        assert probe._loop.run_until_complete(clean_session()) == ("zabalack_probe", "0", "off", 1)
    finally:
        probe.close()
