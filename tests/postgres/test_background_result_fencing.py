"""Background result authority on fresh and populated schema-5/016 upgrades.

Only pg_lab's disposable database is used. SET ROLE proves callable privileges;
WASM execution does not establish native authentication or lock concurrency.
All synthetic rows and simulated old function bodies roll back after each case.
"""

from pathlib import Path
import uuid
import json
import re

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from conftest import connect_role

ROOT = Path(__file__).resolve().parents[2]
NEW = (ROOT / "sql/migrations/017_background_result_fencing.sql").read_text()
OLD = (ROOT / "sql/migrations/016_claim_delivery_order.sql").read_text()
OLD_BODY = OLD[OLD.index("CREATE OR REPLACE FUNCTION zabalack.worker_call") :]
OPS = ("record_reconciliation", "record_host_check", "record_host_inventory")


def seed(conn):
    cid, aid, eid = (str(uuid.uuid4()) for _ in range(3))
    instance = "background-" + aid
    conn.execute(
        "INSERT INTO zabalack.instance_control(instance,background_owner,background_generation,background_until) VALUES(%s,'worker',7,clock_timestamp()+interval '5 minutes')",
        (instance,),
    )
    conn.execute(
        "INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,host_ids,group_ids) VALUES('synthetic',%s,'123','1','1','[]','[]')",
        (instance,),
    )
    order = conn.execute(
        "INSERT INTO zabalack.captures(capture_id,alert_id,customer,snapshot,history_type,resulting_status) VALUES(%s,%s,'synthetic','{}','ack','ack') RETURNING capture_order",
        (cid, aid),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO zabalack.effects(effect_id,instance,event_id,generation,primary_action_id,latest_action_time) VALUES(%s,%s,'456',1,%s,clock_timestamp())",
        (eid, instance, aid),
    )
    conn.execute(
        "INSERT INTO zabalack.actions(action_id,capture_id,capture_order,effect_id,alert_id,customer,instance,event_id,trigger_id,actor_ref,cohort_revision,policy_version,issued_at,execute_before) VALUES(%s,%s,%s,%s,%s,'synthetic',%s,'456','123','synthetic-actor','1','1',clock_timestamp(),clock_timestamp()+interval '1 hour')",
        (aid, cid, order, eid, aid, instance),
    )
    return {
        "action_id": aid,
        "instance": instance,
        "worker_id": "worker",
        "background_generation": 7,
        "customer": "synthetic",
        "trigger_id": "123",
        "cohort_revision": "1",
        "state": "ok",
        "completion_evidence": "reference_observed",
        "drift": True,
        "next_cursor": 4,
    }


def snapshot(conn):
    # Small isolated fixture only; preserve all rows, not merely row counts.
    conn.execute("SET ROLE zabalack_owner")
    return {
        name: conn.execute(
            f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]') FROM zabalack.{name} t"
        ).fetchone()[0]
        for name in (
            "captures",
            "actions",
            "effects",
            "outcomes",
            "cohorts",
            "instance_control",
            "audit",
        )
    }


def call(conn, op, payload):
    conn.execute("SET ROLE zabalack_worker")
    assert conn.execute("SELECT current_user").fetchone()[0] == "zabalack_worker"
    return conn.execute(
        "SELECT zabalack.worker_call(%s,%s)", (op, Jsonb(payload))
    ).fetchone()[0]


def refuse(conn, op, payload, pattern):
    # Keep errors inside a PostgreSQL subtransaction, like the stateful-ledger
    # suite: the supplemental socket does not reliably handle client-side
    # savepoint recovery after an extended-protocol exception.
    conn.execute("SET ROLE zabalack_worker")
    assert conn.execute("SELECT current_user").fetchone()[0] == "zabalack_worker"
    conn.execute(
        sql.SQL("""DO $fence$
        DECLARE failure text := '';
        BEGIN
          BEGIN PERFORM zabalack.worker_call({},{}::jsonb);
          EXCEPTION WHEN OTHERS THEN failure := SQLERRM; END;
          PERFORM set_config('ack_test.background_failure',failure,true);
        END $fence$""").format(sql.Literal(op), sql.Literal(json.dumps(payload)))
    )
    failure = conn.execute(
        "SELECT current_setting('ack_test.background_failure')"
    ).fetchone()[0]
    assert re.fullmatch(pattern, failure), ("unexpected refusal or acceptance", failure)


@pytest.fixture(params=["fresh", "populated-016-upgrade"])
def lab(pg_lab, request):
    with connect_role(pg_lab, "zabalack_owner") as conn:
        conn.prepare_threshold = None
        with conn.transaction(force_rollback=True):
            if request.param == "populated-016-upgrade":
                # 017 changes only worker_call: schema-5 structures already match.
                # Restore shipped 016's routine and pin, without rewriting any ledger.
                conn.execute(OLD_BODY)
                conn.execute("SELECT zabalack.seal_installation()")
            payload = seed(conn)
            before = snapshot(conn)
            if request.param == "populated-016-upgrade":
                conn.execute(NEW)
                assert snapshot(conn) == before, (
                    "migration must preserve populated evidence byte-for-byte"
                )
                assert (
                    conn.execute("SELECT zabalack.capture_health()").fetchone()[0][
                        "functions_ok"
                    ]
                    is False
                )
                conn.execute("SELECT zabalack.seal_installation()")
            health = conn.execute("SELECT zabalack.capture_health()").fetchone()[0]
            assert (
                health["zabalack_schema_version"] == 5
                and health["functions_ok"] is True
            )
            yield conn, payload


FLAWS = (
    "missing-worker",
    "null-worker",
    "empty-worker",
    "long-worker",
    "wrong-worker",
    "missing-generation",
    "null-generation",
    "wrong-generation",
    "null-owner",
    "null-expiry",
    "expired",
    "released-missing-worker",
    "released-null-worker",
    "empty-owner-and-worker",
)


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("flaw", FLAWS)
def test_invalid_background_authority_refuses_without_changes(lab, op, flaw):
    conn, original = lab
    payload = dict(original)
    conn.execute("SET ROLE zabalack_owner")
    if flaw == "missing-worker":
        payload.pop("worker_id")
    elif flaw == "null-worker":
        payload["worker_id"] = None
    elif flaw == "empty-worker":
        payload["worker_id"] = ""
    elif flaw == "long-worker":
        payload["worker_id"] = "w" * 129
    elif flaw == "wrong-worker":
        payload["worker_id"] = "another-worker"
    elif flaw == "missing-generation":
        payload.pop("background_generation")
    elif flaw == "null-generation":
        payload["background_generation"] = None
    elif flaw == "wrong-generation":
        payload["background_generation"] = 6
    elif flaw == "null-owner":
        conn.execute(
            "UPDATE zabalack.instance_control SET background_owner=NULL WHERE instance=%s",
            (payload["instance"],),
        )
    elif flaw == "null-expiry":
        conn.execute(
            "UPDATE zabalack.instance_control SET background_until=NULL WHERE instance=%s",
            (payload["instance"],),
        )
    elif flaw == "expired":
        conn.execute(
            "UPDATE zabalack.instance_control SET background_until=clock_timestamp()-interval '1 second' WHERE instance=%s",
            (payload["instance"],),
        )
    elif flaw.startswith("released-"):
        assert call(
            conn,
            "finish_background",
            {
                "instance": payload["instance"],
                "worker_id": "worker",
                "lease_generation": 7,
            },
        )["released"]
        if flaw == "released-missing-worker":
            payload.pop("worker_id")
        else:
            payload["worker_id"] = None
    elif flaw == "empty-owner-and-worker":
        conn.execute(
            "UPDATE zabalack.instance_control SET background_owner='' WHERE instance=%s",
            (payload["instance"],),
        )
        payload["worker_id"] = ""
    before = snapshot(conn)
    refuse(conn, op, payload, "worker_id required|background lease lost")
    assert snapshot(conn) == before


@pytest.mark.parametrize(
    "evidence", ["MISSING", None, "", "write_response", "invented"]
)
def test_reconciliation_success_requires_read_evidence(lab, evidence):
    conn, payload = lab
    if evidence == "MISSING":
        payload.pop("completion_evidence")
    else:
        payload["completion_evidence"] = evidence
    before = snapshot(conn)
    refuse(conn, "record_reconciliation", payload, "invalid reconciliation evidence")
    assert snapshot(conn) == before


@pytest.mark.parametrize("op", OPS)
def test_live_owner_records_and_released_owner_cannot_record_again(lab, op):
    conn, payload = lab
    assert call(conn, op, payload)["recorded"] is True
    conn.execute("SET ROLE zabalack_owner")
    if op == "record_reconciliation":
        assert conn.execute(
            "SELECT state,completion_evidence FROM zabalack.actions WHERE action_id=%s",
            (payload["action_id"],),
        ).fetchone() == ("ok", "reference_observed")
    else:
        assert conn.execute(
            "SELECT host_drift,host_cursor FROM zabalack.cohorts WHERE instance=%s",
            (payload["instance"],),
        ).fetchone() == (True, 4)
    assert call(
        conn,
        "finish_background",
        {"instance": payload["instance"], "worker_id": "worker", "lease_generation": 7},
    )["released"]
    before = snapshot(conn)
    refuse(conn, op, payload, "background lease lost")
    assert snapshot(conn) == before


def test_017_preconditions_and_caller_privileges(pg_lab):
    with connect_role(pg_lab, "zabalack_owner") as conn:
        conn.prepare_threshold = None
        with conn.transaction(force_rollback=True):
            for sql, reason in [
                (
                    "UPDATE zabalack.installation SET schema_version=4",
                    "requires schema 5",
                ),
                (
                    "UPDATE zabalack.installation SET routine_bodies_md5='wrong'",
                    "routine body drift",
                ),
            ]:
                with pytest.raises(psycopg.Error, match=reason):
                    with conn.transaction():
                        conn.execute(sql)
                        conn.execute(NEW)
            for role in ("alerta_app", "zabalack_probe", "zabalack_operator"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with conn.transaction():
                        conn.execute(f"SET ROLE {role}")
                        conn.execute(
                            "SELECT zabalack.worker_call('record_host_check','{}')"
                        )
            assert conn.execute(
                "SELECT has_function_privilege('zabalack_worker','zabalack.worker_call(text,jsonb)','EXECUTE')"
            ).fetchone()[0]


def test_016_counterexamples_are_reproduced_only_in_rolled_back_fixture(pg_lab):
    with connect_role(pg_lab, "zabalack_owner") as conn:
        conn.prepare_threshold = None
        with conn.transaction(force_rollback=True):
            conn.execute(OLD_BODY)
            payload = seed(conn)
            for op in OPS:
                with conn.transaction(force_rollback=True):
                    conn.execute("SET ROLE zabalack_owner")
                    conn.execute(
                        "UPDATE zabalack.instance_control SET background_owner=NULL,background_until=NULL WHERE instance=%s",
                        (payload["instance"],),
                    )
                    assert call(
                        conn, op, {k: v for k, v in payload.items() if k != "worker_id"}
                    )["recorded"]
            with conn.transaction(force_rollback=True):
                assert call(
                    conn,
                    "record_reconciliation",
                    {k: v for k, v in payload.items() if k != "completion_evidence"},
                )["recorded"]
                conn.execute("SET ROLE zabalack_owner")
                assert conn.execute(
                    "SELECT state,completion_evidence FROM zabalack.actions WHERE action_id=%s",
                    (payload["action_id"],),
                ).fetchone() == ("ok", None)


@pytest.mark.parametrize("op", OPS)
def test_reacquired_lease_rejects_previous_generation_even_for_same_owner(lab, op):
    conn, payload = lab
    assert call(
        conn,
        "finish_background",
        {"instance": payload["instance"], "worker_id": "worker", "lease_generation": 7},
    )["released"]
    lease = call(
        conn,
        "claim_background",
        {"instance": payload["instance"], "worker_id": "worker"},
    )
    assert lease["allowed"] and lease["lease_generation"] == 8
    before = snapshot(conn)
    refuse(conn, op, payload, "background lease lost")
    assert snapshot(conn) == before
    assert call(conn, op, payload | {"background_generation": 8})["recorded"]


def test_source_ack_observed_remains_valid_read_evidence(lab):
    conn, payload = lab
    assert call(
        conn,
        "record_reconciliation",
        payload | {"completion_evidence": "source_ack_observed"},
    )["recorded"]
    conn.execute("SET ROLE zabalack_owner")
    assert (
        conn.execute(
            "SELECT completion_evidence FROM zabalack.actions WHERE action_id=%s",
            (payload["action_id"],),
        ).fetchone()[0]
        == "source_ack_observed"
    )
