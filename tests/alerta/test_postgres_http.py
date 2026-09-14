"""Actual pinned Alerta HTTP and Psycopg2 persistence against PostgreSQL18.

Native mode uses the disposable shared fixture. ACK_TEST_WASM=1 runs the same
sequential code through the official PGlite wire adapter and must be reported as
WASM supplemental evidence, never native concurrency/authentication evidence.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from flask import g
from psycopg import sql
from alerta.app import db, plugins
from alerta.models.alert import Alert

from zabalack.config import Settings
from zabalack.crypto import verify_capture
from zabalack.normalizer import ZabbixNormaliser
from zabalack.plugins import AckSigningPlugin, create_app

pytestmark = [pytest.mark.postgres, pytest.mark.alerta]
KEY = b"synthetic-integration-test-only-32bytes!!"
SOURCE = dict(source_system="zabbix", source_contract_version="1", zabbix_instance="http-lab",
              zabbix_event_id="910001", zabbix_trigger_id="910002", zabbix_cause_event_id=None,
              zabbix_recovery_event_id=None)
COHORT = dict(customer="http-lab-customer", zabbix_instance="http-lab", zabbix_trigger_id="910002",
              host_ids=["910003"], group_ids=["910004"], revision="http-r1", policy_revision="1",
              enabled=True, one_to_one=True, comment_enabled=True, allow_admin=False)


@pytest.fixture
def persisted_api(pg_lab, pgdb, monkeypatch):
    # The upstream factory reruns its own Alerta schema DDL on startup. This is
    # isolated fixture setup using lab administrator authority, not evidence of
    # migration/trigger rights. Every tested application connection below is
    # explicitly SET ROLE alerta_app; other role boundaries have their own tests.
    if pg_lab["backend"] == "postgres18_wasm":
        # PGlite multiplexes sessions onto one backend. Explicitly select the
        # Alerta schema owner before its startup DDL; session-role isolation is
        # NOT evidence from this adapter. Runtime queries still assert their role.
        with pgdb("alerta_owner") as owner:
            assert owner.execute("SELECT current_user").fetchone()[0] == "alerta_owner"
    app = create_app(dict(DATABASE_URL=pg_lab["admin_dsn"], DATABASE_NAME=None,
                          DATABASE_SCHEMA="public", PLUGINS=[], TESTING=True,
                          AUTH_REQUIRED=True, CUSTOMER_VIEWS=True, OAUTH2_CLIENT_ID="http-lab-api",
                          SECRET_KEY="synthetic-http-lab-jwt-only-32bytes!", PLUGINS_RAISE_ON_ERROR=False,
                          AUDIT_TRAIL=[]))
    original_connect = db.connect

    def runtime_connect():
        connection = original_connect()
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE alerta_app")
            cursor.execute("SELECT current_user")
            assert cursor.fetchone()[0] == "alerta_app"
        connection.commit()
        return connection

    monkeypatch.setattr(db, "connect", runtime_connect)
    settings = Settings(enabled=True, config_revision="http-r1", signing_kid="lab", cohorts=(COHORT,),
                        ingestion_mappings={"http-ingest": {"customer": "http-lab-customer", "instance": "http-lab"}})
    # This injected probe separates actual HTTP+persistence tests from dedicated
    # health transport/identity tests. No healthy-pool claim derives from it.
    signer = AckSigningPlugin(settings=settings, keys={"lab": KEY}, probe=SimpleNamespace(check=lambda: {}))
    monkeypatch.setattr(plugins, "plugins", OrderedDict(normalizer=ZabbixNormaliser(settings=settings), signer=signer))
    monkeypatch.setattr(plugins, "rules", None)
    monkeypatch.setattr(plugins, "config", app.config)
    with pgdb("zabalack_owner") as owner:
        owner.execute("""INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,
                      host_ids,group_ids,enabled) VALUES('http-lab-customer','http-lab','910002','http-r1','1',
                      '["910003"]','["910004"]',true) ON CONFLICT(customer,instance,trigger_id)
                      DO UPDATE SET enabled=true,revision='http-r1'""")
    now = datetime.now(timezone.utc)
    token = jwt.encode(dict(iss="synthetic", typ="Bearer", sub="http-human-id", aud="http-lab-api",
                            exp=now + timedelta(minutes=10), nbf=now - timedelta(seconds=1), iat=now,
                            preferred_username="http-human", customers=["http-lab-customer"], scope="write:alerts"),
                       app.config["SECRET_KEY"], algorithm="HS256")

    def new_alert(*, event_id=None):
        with app.test_request_context("/synthetic-lab-setup"):
            g.login = "http-ingest"
            source = deepcopy(SOURCE)
            if event_id is not None:
                source["zabbix_event_id"] = str(event_id)
            alert = Alert("synthetic-" + str(uuid4()), "stable-trigger-family", attributes=source,
                          customer="http-lab-customer", severity="major", status="open")
            return alert.create().id

    yield SimpleNamespace(app=app, client=app.test_client(), headers={"Authorization": "Bearer " + token},
                          new_alert=new_alert, pgdb=pgdb, signer=signer)


def capture_rows(lab, alert_id):
    with lab.pgdb("zabalack_owner") as owner:
        return owner.execute("""SELECT capture_id,captured_at,alert_id,customer,snapshot,intent,marker,
                     history_type,history_user,history_update_time,resulting_status FROM zabalack.captures
                     WHERE alert_id=%s ORDER BY captured_at""", (alert_id,)).fetchall()


def test_T1_T36_real_HTTP_capture_then_409(persisted_api):
    lab = persisted_api
    identity = lab.new_alert()
    response = lab.client.put(f"/alert/{identity}/action", headers=lab.headers,
                              json={"action": "ack", "text": "synthetic note"})
    assert response.status_code == 200, response.get_json()
    rows = capture_rows(lab, identity)
    assert len(rows) == 1
    columns = ("capture_id", "captured_at", "alert_id", "customer", "snapshot", "intent", "marker",
               "history_type", "history_user", "history_update_time", "resulting_status")
    capture = dict(zip(columns, rows[0]))
    capture["source"] = capture.pop("snapshot")
    intent = verify_capture(capture, {"lab": KEY})
    assert intent["source"]["zabbix_event_id"] == "910001"
    assert intent["actor"]["login"] == "http-human"
    again = lab.client.put(f"/alert/{identity}/action", headers=lab.headers, json={"action": "ack"})
    assert again.status_code == 409, again.get_json()
    assert len(capture_rows(lab, identity)) == 1
    with lab.pgdb("alerta_app") as app:
        history = app.execute("SELECT (history[1]).type,(history[1]).text,status FROM alerts WHERE id=%s", (identity,)).fetchone()
    assert history[0] == "ack" and history[2] == "ack" and "[zabalack:" in history[1]


def test_T5_T6_real_status_and_attribute_changes_do_not_capture(persisted_api):
    lab = persisted_api
    identity = lab.new_alert()
    response = lab.client.put(f"/alert/{identity}/status", headers=lab.headers, json={"status": "ack"})
    assert response.status_code == 200, response.get_json()
    assert capture_rows(lab, identity) == []
    with lab.pgdb("alerta_app") as app:
        app.execute("UPDATE alerts SET attributes=attributes||'{\"zabbix_ack_sync\":\"pending\"}' WHERE id=%s", (identity,))
    assert capture_rows(lab, identity) == []


def test_T19_T22_real_capture_failure_rolls_back_ack_only(persisted_api):
    lab = persisted_api
    identity = lab.new_alert()
    # Fault injection is lab-only zabalack-owner policy, scoped to exactly this one
    # synthetic alert. CHECK failure occurs after the trigger attempts insert.
    with lab.pgdb("zabalack_owner") as owner:
        owner.execute(sql.SQL("ALTER TABLE zabalack.captures ADD CONSTRAINT http_fault CHECK(alert_id<>{})").format(sql.Literal(identity)))
    try:
        response = lab.client.put(f"/alert/{identity}/action", headers=lab.headers, json={"action": "ack"})
        assert response.status_code >= 400
        assert "[zabalack:" not in response.get_data(as_text=True)
        with lab.pgdb("alerta_app") as app:
            row = app.execute("SELECT status,(history[1]).type FROM alerts WHERE id=%s", (identity,)).fetchone()
        assert row[0] == "open" and row[1] == "new"
        assert capture_rows(lab, identity) == []
        # Unrelated source ingestion still writes. This is an actual upstream
        # model INSERT while the capture fault constraint remains installed.
        unrelated = lab.new_alert()
        assert unrelated != identity
    finally:
        with lab.pgdb("zabalack_owner") as owner:
            owner.execute("ALTER TABLE zabalack.captures DROP CONSTRAINT http_fault")
