"""Executable screenshot transcript: real Alerta/SQL, synthetic actor, mock source.

The existing persisted_api fixture uses Flask's test client, a synthetic JWT,
an injected health probe and SET ROLE. This is not installed-service evidence.
Only an explicit capture run exports a small allowlist of observed values.
"""
import json
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
import pytest

from test_postgres_http import persisted_api, COHORT, KEY  # noqa: F401
from zabalack.config import Settings
from zabalack.db import Postgres
from zabalack.worker import Worker


class RoleSelectedDB(Postgres):
    """Same explicit role selection as the WASM reference worker fixture."""
    async def _call(self, function, operation, payload):
        role, entry = {
            "worker": ("zabalack_worker", "zabalack.worker_call"),
            "operator": ("zabalack_operator", "zabalack.operator_call"),
        }[function]
        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as conn:
            await conn.execute("SET ROLE " + role)
            async with conn.transaction():
                assert (await (await conn.execute("SELECT current_user")).fetchone())[0] == role
                await conn.execute("SET LOCAL statement_timeout='3000ms'")
                return (await (await conn.execute("SELECT " + entry + "(%s,%s)",
                                                 (operation, Jsonb(payload)))).fetchone())[0]


class MockSource:
    """No network implementation; delivery can only append to an in-memory list."""
    def __init__(self):
        self.reads = []
        self.writes = []

    async def event_get(self, event_id, cohort, **kwargs):
        self.reads.append(event_id)
        return [{"eventid": event_id, "objectid": "910002", "source": "0", "object": "0",
                 "value": "1", "clock": "1700000000", "acknowledged": "0", "r_eventid": "0",
                 "hosts": [{"hostid": "910003"}]}]

    async def acknowledge(self, event_id, **kwargs):
        self.writes.append(event_id)


class MockRegistry:
    def resolve(self, instance):
        assert instance == "http-lab"
        return object()


@pytest.mark.postgres
@pytest.mark.alerta
@pytest.mark.asyncio
async def test_T1_T36_docs_walkthrough(persisted_api, pg_lab):
    lab = persisted_api
    db = RoleSelectedDB(pg_lab["admin_dsn"])
    audit = {"actor_ref": "synthetic-docs-operator", "change_ref": "LAB-DOCS-ONLY"}
    await db.operator("configure_installation", audit | {
        "deployment_id": "synthetic-docs", "config_revision": "http-r1"})
    await db.operator("configure_cohort", audit | {"cohort": COHORT})
    await db.operator("set_external_writes", audit | {
        "enabled": True, "confirmation": "ENABLE_EXTERNAL_WRITES", "gates_reviewed": True})
    await db.operator("resume", audit | {"instance": "http-lab"})
    identity = lab.new_alert(event_id="910011")
    request = {"action": "ack", "text": "Investigating this synthetic lab alert"}
    response = lab.client.put(f"/alert/{identity}/action", headers=lab.headers, json=request)
    assert response.status_code == 200
    with lab.pgdb("alerta_app") as conn:
        status = conn.execute("SELECT status FROM alerts WHERE id=%s", (identity,)).fetchone()[0]
    with lab.pgdb("zabalack_owner") as conn:
        captured = conn.execute("SELECT count(*) FROM zabalack.captures WHERE alert_id=%s",
                                (identity,)).fetchone()[0]
    assert status == "ack" and captured == 1
    client = MockSource()
    settings = Settings(external_writes_enabled=True, cohorts=(COHORT,), config_revision="http-r1",
                        deployment_id="synthetic-docs", database_name=pg_lab["database_name"])
    worker = Worker(settings, db, {"lab": KEY}, registry=MockRegistry(), client_factory=lambda _: client)
    assert await worker.check_health()
    for _ in range(200):
        if any(row["alert_id"] == identity for row in await db.call("inspect", {})):
            break
        assert await worker.verify_once()
    rows = []
    for _ in range(200):
        assert await worker.delivery_once()
        rows = [row for row in await db.call("inspect", {"state": "ok"}) if row["alert_id"] == identity]
        if rows:
            break
    assert len(rows) == 1
    row = rows[0]
    assert row["completion_evidence"] == "write_response" and row["attempts"] == 1
    assert client.reads.count("910011") == client.writes.count("910011") == 1
    with lab.pgdb("alerta_app") as conn:
        projection = conn.execute("SELECT attributes->>'zabbix_ack_sync' FROM alerts WHERE id=%s",
                                  (identity,)).fetchone()[0]
    assert projection == "ok"
    repeat = lab.client.put(f"/alert/{identity}/action", headers=lab.headers, json={"action": "ack"})
    assert repeat.status_code == 409
    if os.environ.get("ZABALACK_DOCS_CAPTURE") == "YES_SYNTHETIC_TRANSCRIPT":
        # Fail closed: no external DSN is accepted by the documentation recorder.
        assert pg_lab["backend"] == "postgres18_wasm"
        result = {
            "alert_id": identity, "request": request, "http_status": response.status_code,
            "alert_status": status, "capture_count": captured,
            "repeat_http_status": repeat.status_code,
            "ledger": {key: row[key] for key in ("state", "completion_evidence", "attempts")},
            "projection": projection, "mock_pre_read_event_ids": [event for event in client.reads if event == "910011"],
            "mock_acknowledged_event_ids": [event for event in client.writes if event == "910011"],
        }
        target = Path(__file__).resolve().parents[2] / "build/docs/observed.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2) + "\n")
