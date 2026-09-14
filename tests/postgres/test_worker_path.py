"""End-to-end SQL/worker path with mock Zabbix transport.

When ACK_TEST_WASM=1 this is supplementary PostgreSQL18 WASM engine evidence,
NOT native authentication, concurrency, PgBouncer, real Zabbix or HA evidence.
Runtime calls execute as current_user=zabalack_worker, never as the lab admin.
"""
from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb
import pytest

from zabalack.config import Settings
from zabalack.crypto import build_intent, sign_intent
from zabalack.db import Postgres
from zabalack.rpc import RPCError
from zabalack.reconciler import Reconciler
from zabalack.worker import Worker
from conftest import connect_role

KEY = b"synthetic-postgres-path-test-key!!"
COHORT = {"customer": "SQL Worker Synthetic", "zabbix_instance": "lab-worker-path", "zabbix_trigger_id": "81200",
          "host_ids": ["81100"], "group_ids": ["81300"], "revision": "worker-path-v1", "policy_revision": "1",
          "enabled": True, "one_to_one": True, "comment_enabled": True, "allow_admin": False}


class RoleSelectedLabPostgres(Postgres):
    """Lab-only bridge because WASM does not prove login authentication."""
    async def _call(self, function, operation, payload):
        statement = {"worker": "SELECT zabalack.worker_call(%s,%s)", "operator": "SELECT zabalack.operator_call(%s,%s)"}[function]
        role = {"worker": "zabalack_worker", "operator": "zabalack_operator"}[function]
        conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        try:
            await conn.execute("SET ROLE " + role)
            async with conn.transaction():
                assert (await (await conn.execute("SELECT current_user")).fetchone())[0] == role
                await conn.execute("SET LOCAL statement_timeout='3000ms'")
                row = await (await conn.execute(statement, (operation, Jsonb(payload)))).fetchone()
                return row[0]
        finally:
            await conn.close()


class SourceClient:
    def __init__(self, *, lose_response=False):
        self.reads, self.writes, self.lose_response = [], [], lose_response
        self.acknowledged = "0"
        self.message = None

    async def event_get(self, event_id, cohort, **kwargs):
        self.reads.append((event_id, kwargs))
        row = {"eventid": event_id, "objectid": "81200", "source": "0", "object": "0", "value": "1",
               "clock": "1700000000", "acknowledged": self.acknowledged, "r_eventid": "0", "hosts": [{"hostid": "81100"}]}
        if kwargs.get("history"):
            row["acknowledges"] = [{"action": "6", "message": self.message}] if self.message else []
        return [row]

    async def acknowledge(self, event_id, **kwargs):
        from zabalack.rpc import neutral_comment
        self.writes.append(event_id)
        self.acknowledged = "1"
        self.message = neutral_comment(kwargs["issued_at"], kwargs["public_reference"])
        if self.lose_response:
            raise RPCError("outcome_unknown", transient=True, uncertain=True)


class Registry:
    def resolve(self, instance):
        assert instance == "lab-worker-path"
        return object()


def seed(pg_lab, event_id):
    with connect_role(pg_lab, "zabalack_operator") as conn:
        audit = {"actor_ref": "lab-operator", "change_ref": "LAB-WORKER-ONLY"}
        def op(name, **data):
            return conn.execute("SELECT zabalack.operator_call(%s,%s)", (name, Jsonb(audit | data))).fetchone()[0]
        op("capture_bypass", enabled=False, config_revision="worker-path-v1")
        op("configure_installation", deployment_id="isolated-worker-test", config_revision="worker-path-v1")
        op("configure_cohort", cohort=COHORT)
        op("set_external_writes", enabled=True, confirmation="ENABLE_EXTERNAL_WRITES", gates_reviewed=True)
        op("resume", instance=COHORT["zabbix_instance"])
    intent = build_intent(str(uuid.uuid4()), COHORT["customer"],
        {"zabbix_instance": COHORT["zabbix_instance"], "zabbix_trigger_id": "81200", "zabbix_event_id": event_id},
        {"login": "synthetic-worker-engineer", "customers": [COHORT["customer"]], "admin": False, "auth_type": "user"},
        "worker-path-v1", "1")
    attributes = {"source_system": "zabbix", **intent["source"], "zabbix_ack_sync_intent": intent, "other": "preserved"}
    with connect_role(pg_lab, "alerta_app") as conn:
        conn.execute("INSERT INTO alerts(id,resource,event,status,customer,attributes,history) VALUES(%s,'synthetic-worker','stable-trigger','open',%s,%s,ARRAY[]::history[])",
            (intent["alert_id"], intent["customer"], Jsonb(attributes)))
        conn.execute("UPDATE alerts SET status='ack',history=ARRAY[ROW(%s,'stable-trigger',NULL,'ack',NULL,%s,'ack',clock_timestamp() AT TIME ZONE 'UTC',%s,NULL)::history],update_time=clock_timestamp() AT TIME ZONE 'UTC' WHERE id=%s",
            (str(uuid.uuid4()), "synthetic note " + sign_intent(intent, "lab", KEY), intent["actor"]["login"], intent["alert_id"]))
    return intent


@pytest.mark.asyncio
async def test_T1_full_signed_capture_worker_delivery_projection(pg_lab):
    intent = seed(pg_lab, "81401")
    settings = Settings(external_writes_enabled=True, cohorts=(COHORT,), config_revision="worker-path-v1",
                        deployment_id="isolated-worker-test", database_name=pg_lab["database_name"])
    db = RoleSelectedLabPostgres(pg_lab["admin_dsn"])
    client = SourceClient()
    worker = Worker(settings, db, {"lab": KEY}, registry=Registry(), client_factory=lambda _: client)
    assert await worker.check_health()
    for _ in range(200):
        if (await db.call("inspect", {"action_id": intent["action_id"]})):
            break
        assert await worker.verify_once()
    assert await worker.delivery_once()
    assert client.writes == ["81401"]
    rows = await db.call("inspect", {"action_id": intent["action_id"]})
    assert rows[0]["state"] == "ok"
    assert rows[0]["completion_evidence"] == "write_response"
    with connect_role(pg_lab, "alerta_app") as conn:
        attrs, status, head = conn.execute("SELECT attributes,status,(history[1]).type FROM alerts WHERE id=%s", (intent["alert_id"],)).fetchone()
        assert attrs["zabbix_ack_sync"] == "ok"
        assert attrs["zabbix_event_id"] == "81401" and attrs["other"] == "preserved"
        assert status == "ack" and head == "ack"


@pytest.mark.asyncio
async def test_T29_sql_unknown_outcome_reconciles_without_second_write(pg_lab):
    intent = seed(pg_lab, "81402")
    db = RoleSelectedLabPostgres(pg_lab["admin_dsn"])
    settings = Settings(external_writes_enabled=True, cohorts=(COHORT,))
    client = SourceClient(lose_response=True)
    worker = Worker(settings, db, {"lab": KEY}, registry=Registry(), client_factory=lambda _: client)
    for _ in range(200):
        if (await db.call("inspect", {"action_id": intent["action_id"]})):
            break
        assert await worker.verify_once()
    assert await worker.delivery_once()
    row = (await db.call("inspect", {"action_id": intent["action_id"]}))[0]
    assert row["uncertain"]
    recon = Reconciler(settings, db, Registry(), worker_id=worker.worker_id, client_factory=lambda _: client)
    assert await recon.reconcile_action(row)
    row = (await db.call("inspect", {"action_id": intent["action_id"]}))[0]
    assert row["state"] == "ok" and not row["uncertain"]
    assert row["completion_evidence"] == "reference_observed"
    assert client.writes == ["81402"]


@pytest.mark.asyncio
async def test_T31_explicit_unknown_settlement_requires_evidence_then_separate_retry(pg_lab):
    intent = seed(pg_lab, "81403")
    db = RoleSelectedLabPostgres(pg_lab["admin_dsn"])
    class NoEffectFirst(SourceClient):
        def __init__(self):
            super().__init__()
            self.first = True
        async def acknowledge(self, event_id, **kwargs):
            if self.first:
                self.first = False
                # Synthetic fixture confirms the first transport failed before commit;
                # worker cannot infer that, hence uncertain until operator review.
                raise RPCError("outcome_unknown", transient=True, uncertain=True)
            await super().acknowledge(event_id, **kwargs)
    client = NoEffectFirst()
    worker = Worker(Settings(external_writes_enabled=True, cohorts=(COHORT,)), db, {"lab": KEY},
                    registry=Registry(), client_factory=lambda _: client)
    for _ in range(200):
        if await db.call("inspect", {"action_id": intent["action_id"]}):
            break
        assert await worker.verify_once()
    assert await worker.delivery_once()
    row = (await db.call("inspect", {"action_id": intent["action_id"]}))[0]
    assert row["uncertain"]
    audit = {"action_id": intent["action_id"], "actor_ref": "lab-operator", "change_ref": "LAB-SETTLE"}
    with pytest.raises(psycopg.Error):
        await db.operator("settle_unknown", audit)
    await db.operator("settle_unknown", audit | {"disposition": "no-effect-confirmed",
        "evidence_ref": "synthetic-fixture-proves-no-remote-effect", "confirmation": "REMOTE_ATTEMPT_FENCED_AND_SETTLED"})
    row = (await db.call("inspect", {"action_id": intent["action_id"]}))[0]
    assert not row["uncertain"] and row["state"] == "failed"
    assert not await worker.delivery_once()  # Settlement does not send or queue automatically.
    await db.operator("retry", audit)
    assert await worker.delivery_once()
    assert client.writes == ["81403"]
    row = (await db.call("inspect", {"action_id": intent["action_id"]}))[0]
    assert row["state"] == "ok" and row["attempts"] == 2


@pytest.mark.asyncio
async def test_T9_expired_never_sent_effect_allows_later_explicit_action(pg_lab):
    intent = seed(pg_lab, "81404")
    db = RoleSelectedLabPostgres(pg_lab["admin_dsn"])
    worker = Worker(Settings(external_writes_enabled=True, cohorts=(COHORT,)), db, {"lab": KEY},
                    registry=Registry(), client_factory=lambda _: SourceClient())
    for _ in range(200):
        if await db.call("inspect", {"action_id": intent["action_id"]}):
            break
        assert await worker.verify_once()
    # Lab-only clock advancement of the authoritative execution deadline; no
    # production owner mutation and no claim of native four-hour elapsed testing.
    with connect_role(pg_lab, "zabalack_owner") as conn:
        conn.execute("UPDATE zabalack.actions SET execute_before=clock_timestamp()-interval '1 second' WHERE action_id=%s", (intent["action_id"],))
    assert not await worker.delivery_once()
    assert (await db.call("inspect", {"action_id": intent["action_id"]}))[0]["state"] == "expired"
    later = seed(pg_lab, "81404")
    for _ in range(200):
        if await db.call("inspect", {"action_id": later["action_id"]}):
            break
        assert await worker.verify_once()
    row = (await db.call("inspect", {"action_id": later["action_id"]}))[0]
    assert row["state"] == "authorised", "later explicit human action must not coalesce behind expired effect"
    assert await worker.delivery_once()
