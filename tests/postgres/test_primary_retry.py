"""Primary-only retry on fresh and populated schema-5/017-routine upgrades."""

from pathlib import Path
import json

import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from conftest import connect_role
from test_background_result_fencing import seed, snapshot

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (ROOT / "sql/migrations/018_primary_action_retry.sql").read_text()
OLD = (ROOT / "sql/migrations/013_live_lease_expiry_guard.sql").read_text()
OLD_OPERATOR = OLD[OLD.index("CREATE OR REPLACE FUNCTION zabalack.operator_call") :]
RESEAL = (
    (ROOT / "sql/install/13_reseal.sql")
    .read_text()
    .replace("BEGIN;", "")
    .replace("COMMIT;", "")
)
AUDIT = {"actor_ref": "synthetic-operator", "change_ref": "LAB-RETRY-ONLY"}


def failure(conn, statement):
    conn.execute(
        sql.SQL("""DO $test$ DECLARE refusal text := ''; BEGIN
      BEGIN EXECUTE {}; EXCEPTION WHEN OTHERS THEN refusal := SQLERRM; END;
      PERFORM set_config('ack_test.retry_refusal',refusal,true);
    END $test$""").format(sql.Literal(statement))
    )
    return conn.execute("SELECT current_setting('ack_test.retry_refusal')").fetchone()[
        0
    ]


def retry(conn, aid, *, refuse=False):
    conn.execute("SET ROLE zabalack_operator")
    assert conn.execute("SELECT current_user").fetchone()[0] == "zabalack_operator"
    payload = AUDIT | {"action_id": aid}
    if refuse:
        statement = sql.SQL("SELECT zabalack.operator_call('retry',{}::jsonb)").format(
            sql.Literal(json.dumps(payload))
        )
        return failure(conn, statement.as_string(conn))
    return conn.execute(
        "SELECT zabalack.operator_call('retry',%s)", (Jsonb(payload),)
    ).fetchone()[0]


@pytest.fixture(params=["fresh", "populated-017-upgrade"])
def pair(pg_lab, request):
    with connect_role(pg_lab, "zabalack_owner") as conn:
        conn.prepare_threshold = None
        with conn.transaction(force_rollback=True):
            primary, secondary = seed(conn), seed(conn)
            conn.execute(
                "UPDATE zabalack.actions SET effect_id=(SELECT effect_id FROM zabalack.actions WHERE action_id=%s),instance=%s,state='held',reason='shared_source_effect' WHERE action_id=%s",
                (primary["action_id"], primary["instance"], secondary["action_id"]),
            )
            if request.param == "populated-017-upgrade":
                # 017 still carries the operator routine last shipped by 013.
                # This is a populated routine upgrade, not historical ledger execution.
                conn.execute(OLD_OPERATOR)
                conn.execute(RESEAL)
                before = snapshot(conn)
                conn.execute(MIGRATION)
                assert snapshot(conn) == before
                assert (
                    conn.execute(
                        "SELECT (zabalack.capture_health())->>'functions_ok'"
                    ).fetchone()[0]
                    == "false"
                )
                conn.execute(RESEAL)
            assert (
                conn.execute(
                    "SELECT (zabalack.capture_health())->>'functions_ok'"
                ).fetchone()[0]
                == "true"
            )
            yield conn, primary, secondary


def test_secondary_refuses_without_changing_any_action(pair):
    conn, primary, secondary = pair
    before = snapshot(conn)
    assert (
        retry(conn, secondary["action_id"], refuse=True)
        == "non-primary action cannot be retried; primary_action_id="
        + primary["action_id"]
    )
    assert snapshot(conn) == before


@pytest.mark.parametrize("state", ["ok", "skipped", "expired", "deadline-passed"])
def test_primary_terminal_or_expired_still_refuses(pair, state):
    conn, primary, _ = pair
    if state == "deadline-passed":
        conn.execute(
            "UPDATE zabalack.actions SET state='failed',execute_before=clock_timestamp()-interval '1 second' WHERE action_id=%s",
            (primary["action_id"],),
        )
    else:
        conn.execute(
            "UPDATE zabalack.actions SET state=%s WHERE action_id=%s",
            (state, primary["action_id"]),
        )
    before = snapshot(conn)
    assert (
        retry(conn, primary["action_id"], refuse=True)
        == "terminal/expired action cannot be replayed"
    )
    assert snapshot(conn) == before


@pytest.mark.parametrize("uncertain", [False, True])
def test_primary_retry_preserves_deadline_uncertainty_and_circuit(pair, uncertain):
    conn, primary, _ = pair
    conn.execute(
        "UPDATE zabalack.actions SET state='failed',uncertain=%s WHERE action_id=%s",
        (uncertain, primary["action_id"]),
    )
    conn.execute(
        "UPDATE zabalack.instance_control SET circuit_open=true WHERE instance=%s",
        (primary["instance"],),
    )
    deadline = conn.execute(
        "SELECT execute_before FROM zabalack.actions WHERE action_id=%s",
        (primary["action_id"],),
    ).fetchone()[0]
    assert retry(conn, primary["action_id"]) == {"operation": "retry", "recorded": True}
    conn.execute("SET ROLE zabalack_owner")
    row = conn.execute(
        "SELECT state,uncertain,execute_before FROM zabalack.actions WHERE action_id=%s",
        (primary["action_id"],),
    ).fetchone()
    assert row == ("unknown" if uncertain else "pending", uncertain, deadline)
    assert conn.execute(
        "SELECT circuit_open FROM zabalack.instance_control WHERE instance=%s",
        (primary["instance"],),
    ).fetchone()[0]
    conn.execute("SET ROLE zabalack_worker")
    assert (
        conn.execute(
            "SELECT zabalack.worker_call('claim_delivery',%s)",
            (Jsonb({"worker_id": "worker", "action_id": primary["action_id"]}),),
        ).fetchone()[0]
        is None
    )


@pytest.mark.parametrize(
    "problem,expected",
    [("schema", "018 requires schema 5"), ("drift", "routine body drift before 018")],
)
def test_migration_refuses_unqualified_predecessor(pg_lab, problem, expected):
    with connect_role(pg_lab, "zabalack_owner") as conn:
        conn.prepare_threshold = None
        with conn.transaction(force_rollback=True):
            conn.execute(
                "UPDATE zabalack.installation SET "
                + (
                    "schema_version=4"
                    if problem == "schema"
                    else "routine_bodies_md5='untrusted'"
                )
            )
            assert expected in failure(conn, MIGRATION)
