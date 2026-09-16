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


# --- A5 stage two (T41): the generation fence through the real entry point ---
# Each case below drives the real `PUT /alert/<id>/action`, `PUT /alert/<id>/status`
# and `PUT /alert/<id>/attributes` handlers with a customer-scoped write:alerts
# JWT, verifies through `worker_call('verify_complete')` as zabalack_worker, and
# projects as zabalack_worker. Nothing is hand-inserted.

from psycopg.types.json import Jsonb  # noqa: E402


def fence_rows(lab, alert_id):
    with lab.pgdb("zabalack_owner") as owner:
        return owner.execute("SELECT action_id::text,recorded_by FROM zabalack.alert_generations "
                             "WHERE alert_id=%s ORDER BY seq", (alert_id,)).fetchall()


def newest_generation(lab, alert_id):
    with lab.pgdb("zabalack_owner") as owner:
        value = owner.execute("SELECT zabalack.newest_generation(%s)", (alert_id,)).fetchone()[0]
    return str(value) if value is not None else None


def alert_row(lab, alert_id):
    with lab.pgdb("alerta_app") as app:
        return app.execute("SELECT status,attributes,(history[1]).text FROM alerts WHERE id=%s", (alert_id,)).fetchone()


def verify_newest_capture(lab, alert_id):
    """Claim and verify this alert's newest capture through `worker_call`.

    Other tests in this module leave captures unverified; they are postponed,
    not touched, so the claim below can only return this alert's capture.
    """
    rows = capture_rows(lab, alert_id)
    assert rows, "precondition: a capture exists"
    capture_id, intent = rows[-1][0], rows[-1][5]
    with lab.pgdb("zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.outbox SET due_at=clock_timestamp()+interval '1 hour' "
                      "WHERE capture_id<>%s AND due_at<=clock_timestamp()", (capture_id,))
    # A fresh worker identity per call: a verifier holds at most four live
    # leases, and this module verifies more than that inside one lease lifetime.
    lease = {"worker_id": "http-verifier-" + uuid4().hex[:8]}
    with lab.pgdb("zabalack_worker") as worker:
        claimed = worker.execute("SELECT zabalack.worker_call('claim_verification',%s)", (Jsonb(lease),)).fetchone()[0]
        assert claimed is not None and claimed["capture_id"] == str(capture_id), claimed
        return worker.execute("SELECT zabalack.worker_call('verify_complete',%s)", (Jsonb(
            lease | {"capture_id": claimed["capture_id"], "lease_generation": claimed["lease_generation"],
                     "intent": intent, "actor_ref": "http-actor"}),)).fetchone()[0]


def project(lab, action_id):
    with lab.pgdb("zabalack_worker") as worker:
        return worker.execute("SELECT zabalack.project(%s)", (action_id,)).fetchone()[0]["reason"]


def acknowledge(lab, identity, *, expect=200):
    response = lab.client.put(f"/alert/{identity}/action", headers=lab.headers,
                              json={"action": "ack", "text": "synthetic note"})
    assert response.status_code == expect, response.get_json()
    assert "[zabalack:" not in response.get_data(as_text=True)
    return response


def reopen(lab, identity):
    """Reopen the alert so it can be acknowledged again, as the source would leave it.

    Alerta's attribute write drops every key whose value is None, so after one
    acknowledgement the row no longer carries the nullable source-contract keys
    (`zabbix_cause_event_id`, `zabbix_recovery_event_id`) and the plugin would
    skip a second acknowledgement with `legacy_missing_mapping`. In production
    the next source update re-normalises them through `pre_receive`; here the
    row is restored to that ingestion shape directly.
    """
    response = lab.client.put(f"/alert/{identity}/status", headers=lab.headers, json={"status": "open"})
    assert response.status_code == 200, response.get_json()
    restore_source_nulls(lab, identity)


def restore_source_nulls(lab, identity):
    """Restore the nullable source-contract keys to their ingestion shape.

    Every route that lets a plugin return the alert — an action, a status
    change — ends in Alerta's attribute write, which drops keys whose value is
    None; the plugin then skips the next acknowledgement with
    `legacy_missing_mapping`. In production the next source update
    re-normalises them through `pre_receive`.
    """
    with lab.pgdb("alerta_app") as app:
        app.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                    (Jsonb({"zabbix_cause_event_id": None, "zabbix_recovery_event_id": None}), identity))


def put_attributes(lab, identity, attributes):
    response = lab.client.put(f"/alert/{identity}/attributes", headers=lab.headers,
                              json={"attributes": attributes})
    assert response.status_code == 200, response.get_json()


def test_T41_real_action_records_the_generation_at_staging(persisted_api):
    lab = persisted_api
    identity = lab.new_alert()
    acknowledge(lab, identity)
    (action_id, recorded_by), = fence_rows(lab, identity)
    assert recorded_by == "staging", "the protected operation, not the capture fallback, recorded it"
    status, attributes, _ = alert_row(lab, identity)
    assert status == "ack" and attributes["zabbix_ack_sync_action_id"] == action_id
    assert capture_rows(lab, identity)[0][5]["action_id"] == action_id
    with lab.pgdb("zabalack_owner") as owner:
        assert owner.execute("SELECT count(*) FROM zabalack.audit WHERE operation='stage_generation' "
                             "AND object_ref=%s", (identity,)).fetchone()[0] == 1


def test_T41_real_action_is_refused_when_the_generation_cannot_be_recorded(persisted_api):
    """No staging exists without its generation: the request fails closed."""
    lab = persisted_api
    identity = lab.new_alert()
    with lab.pgdb("zabalack_owner") as owner:
        owner.execute(sql.SQL("ALTER TABLE zabalack.alert_generations ADD CONSTRAINT http_fence_fault "
                              "CHECK(alert_id<>{})").format(sql.Literal(identity)))
    try:
        response = acknowledge(lab, identity, expect=503)
        assert response.get_json().get("message") == "zabalack_generation_unavailable"
        assert fence_rows(lab, identity) == []
        status, attributes, _ = alert_row(lab, identity)
        assert status == "open" and "zabbix_ack_sync_intent" not in attributes
        assert attributes.get("zabbix_ack_sync") is None, "nothing was staged"
        assert capture_rows(lab, identity) == []
        unrelated = lab.new_alert()
        acknowledge(lab, unrelated)
        assert [row[1] for row in fence_rows(lab, unrelated)] == ["staging"], "the fault is scoped to one alert"
    finally:
        with lab.pgdb("zabalack_owner") as owner:
            owner.execute("ALTER TABLE zabalack.alert_generations DROP CONSTRAINT http_fence_fault")


def test_T41_the_generation_and_the_staging_commit_together(persisted_api):
    """A failure of the staging write rolls the generation back with it.

    The fault sits on the alert row's attributes, after the fence insert inside
    the same function has succeeded. If the two were separate transactions the
    fence row would survive; it must not.
    """
    lab = persisted_api
    identity = lab.new_alert()
    with lab.pgdb("alerta_owner") as owner:
        owner.execute(sql.SQL("ALTER TABLE alerts ADD CONSTRAINT http_staging_fault "
                              "CHECK(NOT(id={} AND attributes ? 'zabbix_ack_sync_intent')) NOT VALID")
                      .format(sql.Literal(identity)))
    try:
        acknowledge(lab, identity, expect=503)
        assert fence_rows(lab, identity) == [], "the fence insert rolled back with the staging write"
        status, attributes, _ = alert_row(lab, identity)
        assert status == "open" and "zabbix_ack_sync_intent" not in attributes
    finally:
        with lab.pgdb("alerta_owner") as owner:
            owner.execute("ALTER TABLE alerts DROP CONSTRAINT http_staging_fault")
    acknowledge(lab, identity)
    assert [row[1] for row in fence_rows(lab, identity)] == ["staging"]
    assert "zabbix_ack_sync_intent" in alert_row(lab, identity)[1]


def act(lab, identity, action, *, expect=200):
    response = lab.client.put(f"/alert/{identity}/action", headers=lab.headers, json={"action": action})
    assert response.status_code == expect, response.get_json()
    return response


@pytest.mark.parametrize("state", ["shelved", "closed", "expired"])
def test_T41_an_acknowledgement_alerta_refuses_records_no_generation(persisted_api, state):
    """Alerta answers 409 for `ack` on a shelved, closed or expired alert — from
    from_action(), after the plugin has run and after process_action() has
    committed. Nothing may have been staged by then: no fence row, no reserved
    key, no capture. Without the plugin's own transition check the protected
    operation commits a staging-tier generation nothing can ever capture."""
    lab = persisted_api
    identity = lab.new_alert()
    act(lab, identity, {"shelved": "shelve", "closed": "close", "expired": "expired"}[state])
    restore_source_nulls(lab, identity)   # so the acknowledgement reaches the transition check, not a skip
    assert alert_row(lab, identity)[0] == state, "precondition"
    acknowledge(lab, identity, expect=409)
    assert fence_rows(lab, identity) == [] and newest_generation(lab, identity) is None
    status, attributes, _ = alert_row(lab, identity)
    assert status == state and not any(key.startswith("zabbix_ack_sync") for key in attributes)
    assert capture_rows(lab, identity) == []


def test_T41_an_acknowledgement_that_does_not_transition_records_no_generation(persisted_api):
    """From `assign` Alerta keeps the status and answers 200 (rule ALL-*): no
    signed status write follows. The plugin stages nothing, records the skip,
    and the alert acknowledges normally once it is open again."""
    lab = persisted_api
    identity = lab.new_alert()
    response = lab.client.put(f"/alert/{identity}/status", headers=lab.headers, json={"status": "assign"})
    assert response.status_code == 200, response.get_json()
    restore_source_nulls(lab, identity)
    acknowledge(lab, identity)
    assert fence_rows(lab, identity) == []
    status, attributes, _ = alert_row(lab, identity)
    assert status == "assign"
    assert attributes["zabbix_ack_sync"] == "skipped"
    assert attributes["zabbix_ack_sync_reason"] == "skipped_no_ack_transition"
    assert "zabbix_ack_sync_intent" not in attributes and "zabbix_ack_sync_action_id" not in attributes
    assert capture_rows(lab, identity) == []
    reopen(lab, identity)
    acknowledge(lab, identity)
    assert [row[1] for row in fence_rows(lab, identity)] == ["staging"]
    assert alert_row(lab, identity)[0] == "ack"


def stage_unsigned(lab, identity):
    """A second acknowledgement whose staging commits and whose signing fails.

    T19's fault: the capture insert fails, so Alerta rolls the history write
    back; the staging, committed earlier by the protected operation and again by
    Alerta's own attribute write, stays. This is the "staged, never signed" row
    of A5's lifecycle table, reached through the real entry point.
    """
    reopen(lab, identity)
    with lab.pgdb("zabalack_owner") as owner:
        owner.execute(sql.SQL("ALTER TABLE zabalack.captures ADD CONSTRAINT http_sign_fault CHECK(alert_id<>{}) NOT VALID")
                      .format(sql.Literal(identity)))
    try:
        response = lab.client.put(f"/alert/{identity}/action", headers=lab.headers, json={"action": "ack"})
        assert response.status_code >= 400
    finally:
        with lab.pgdb("zabalack_owner") as owner:
            owner.execute("ALTER TABLE zabalack.captures DROP CONSTRAINT http_sign_fault")
    status, attributes, _ = alert_row(lab, identity)
    assert status == "open" and "zabbix_ack_sync_intent" in attributes
    return attributes["zabbix_ack_sync_action_id"]


def test_T41_an_older_projection_never_overwrites_a_newer_staging_through_the_api(persisted_api):
    """Your case 1. B staged and uncaptured; B's keys deleted through the API;
    separately, A's whole dictionary replayed through the API. A stays refused."""
    lab = persisted_api
    identity = lab.new_alert()
    acknowledge(lab, identity)
    first = verify_newest_capture(lab, identity)
    assert project(lab, first["action_id"]) == "projected"
    stale = alert_row(lab, identity)[1]
    # Verified but not delivered: the projected display state is `pending` with A's id.
    assert stale["zabbix_ack_sync"] == "pending" and stale["zabbix_ack_sync_action_id"] == first["action_id"]
    second = stage_unsigned(lab, identity)
    assert [row[1] for row in fence_rows(lab, identity)] == ["staging", "staging"]
    assert newest_generation(lab, identity) == second
    # Forgery one: delete B's keys.
    put_attributes(lab, identity, {"zabbix_ack_sync_action_id": None, "zabbix_ack_sync_intent": None})
    assert "zabbix_ack_sync_action_id" not in alert_row(lab, identity)[1]
    assert project(lab, first["action_id"]) == "projection_action_generation_mismatch"
    # Forgery two: replay A's dictionary wholesale.
    put_attributes(lab, identity, stale)
    assert alert_row(lab, identity)[1]["zabbix_ack_sync_action_id"] == first["action_id"], "precondition: the row says A"
    assert project(lab, first["action_id"]) == "projection_action_generation_mismatch"
    assert newest_generation(lab, identity) == second
    with lab.pgdb("zabalack_owner") as owner:
        assert owner.execute("SELECT projection_reason FROM zabalack.actions WHERE action_id=%s",
                             (first["action_id"],)).fetchone()[0] == "projection_action_generation_mismatch"


def test_T41_a_swept_staging_keeps_its_protection_against_a_reverified_replay(persisted_api):
    """Your case 2. B observed and retired by the sweep; A's capture replayed and
    verified again as a replay, after B's retirement. B's protection holds
    throughout."""
    lab = persisted_api
    identity = lab.new_alert()
    acknowledge(lab, identity)
    first = verify_newest_capture(lab, identity)
    assert project(lab, first["action_id"]) == "projected"
    signed_text = alert_row(lab, identity)[2]
    assert "[zabalack:" in signed_text
    # A's proof replayed as a fresh history entry, captured now — inside A's
    # capture window, which `verify_complete` measures from `captured_at` — and
    # verified only at the end, after B has been staged and retired: the
    # delayed verification of a replay. (Read the intent before opening the
    # writing connection: PGlite multiplexes sessions onto one backend, so a
    # nested connection's SET ROLE leaks.)
    first_intent = capture_rows(lab, identity)[0][5]
    with lab.pgdb("alerta_app") as app:
        app.execute("UPDATE alerts SET status='ack',history=ARRAY[ROW(%s,'stable-trigger-family',NULL,'ack',NULL,%s,"
                    "'ack',clock_timestamp() AT TIME ZONE 'UTC','http-human',NULL)::history]||history,"
                    "attributes=attributes||%s WHERE id=%s",
                    (str(uuid4()), signed_text, Jsonb({"zabbix_ack_sync_intent": first_intent}), identity))
    assert len(capture_rows(lab, identity)) == 2, "precondition: the replay was captured"
    second = stage_unsigned(lab, identity)
    assert [row[1] for row in fence_rows(lab, identity)] == ["staging", "staging"]
    # B was staged seconds ago, so its capture window is still open and the sweep
    # would defer it. Only the attribute's own timestamps decide when it is
    # retired — the attribute is API-writable, and this is what a client can do
    # to it — while the fence row is what protects B; move the window into the past.
    with lab.pgdb("alerta_app") as app:
        app.execute("UPDATE alerts SET attributes=jsonb_set(jsonb_set(attributes,'{zabbix_ack_sync_intent,issued_at}',"
                    "to_jsonb((clock_timestamp()-interval '10 minutes')::text)),'{zabbix_ack_sync_intent,capture_expires_at}',"
                    "to_jsonb((clock_timestamp()-interval '9 minutes')::text)) WHERE id=%s", (identity,))
    with lab.pgdb("zabalack_owner") as owner:
        assert owner.execute("SELECT zabalack.retire_intents(1000,0)").fetchone()[0]["retired"] >= 1
    assert "zabbix_ack_sync_intent" not in alert_row(lab, identity)[1]
    assert project(lab, first["action_id"]) == "projection_action_generation_mismatch"
    replay = verify_newest_capture(lab, identity)
    assert replay.get("replay") is True and replay["action_id"] == first["action_id"]
    assert [row[1] for row in fence_rows(lab, identity)] == ["staging", "staging"], "a replay records nothing new"
    assert newest_generation(lab, identity) == second
    assert project(lab, first["action_id"]) == "projection_action_generation_mismatch"


def test_T41_forged_attributes_establish_no_generation_and_legitimate_work_still_projects(persisted_api):
    """Your case 3. Forged markers and intents written through the API leave the
    fence untouched; the current action projects; a later legitimate
    acknowledgement on the same alert becomes the newest generation."""
    lab = persisted_api
    identity = lab.new_alert()
    acknowledge(lab, identity)
    first = verify_newest_capture(lab, identity)
    forged_id = str(uuid4())
    forged_intent = dict(capture_rows(lab, identity)[0][5], action_id=forged_id)
    put_attributes(lab, identity, {"zabbix_ack_sync_action_id": forged_id, "zabbix_ack_sync_intent": forged_intent,
                                   "zabbix_ack_sync_forged": "client-supplied"})
    assert alert_row(lab, identity)[1]["zabbix_ack_sync_action_id"] == forged_id, "precondition: stored verbatim"
    assert [row[0] for row in fence_rows(lab, identity)] == [first["action_id"]]
    assert newest_generation(lab, identity) == first["action_id"]
    assert project(lab, first["action_id"]) == "projected"
    attributes = alert_row(lab, identity)[1]
    assert attributes["zabbix_ack_sync_action_id"] == first["action_id"] and "zabbix_ack_sync_intent" not in attributes
    reopen(lab, identity)
    acknowledge(lab, identity)
    third = verify_newest_capture(lab, identity)
    assert third["action_id"] != first["action_id"]
    assert [row[1] for row in fence_rows(lab, identity)] == ["staging", "staging"]
    assert newest_generation(lab, identity) == third["action_id"]
    assert project(lab, third["action_id"]) == "projected"
    assert project(lab, first["action_id"]) == "projection_action_generation_mismatch"
