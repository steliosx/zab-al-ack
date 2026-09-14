"""T40 — the staged intent must not be searchable before the acknowledgement
commits, nor after one that never committed (amendment A5).

Reference Alerta commits staged plugin attributes in `process_action()`, through
an independent `_updateone()` commit, before `from_action()` fires the signing
hook and writes history. Between those two commits the intent is committed and
visible to every other session. Retiring it inside the acknowledgement
transaction — however atomic — therefore cannot close the disclosure: the
interval exists before that transaction reaches its history write, and a request
that fails or is abandoned leaves the staged value behind indefinitely.

The design must protect that interval, either by restricting what reader queries
may inspect or by never committing readable plaintext there. Completing T41
cleanup does not by itself establish T40.

Native only. Demonstrating the interval requires a second concurrent session
observing a row another session has committed but not finished with. PGlite
multiplexes sessions onto a single backend and cannot show it, so these cases
are skipped rather than reported under the WASM layer.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from flask import g
from alerta.app import db, plugins
from alerta.models.alert import Alert

from zabalack.config import Settings
from zabalack.normalizer import ZabbixNormaliser
from zabalack.plugins import AckSigningPlugin, create_app

pytestmark = [pytest.mark.postgres, pytest.mark.alerta]

KEY = b"synthetic-interval-lab-only-32bytes-key!"
ALERT_CUSTOMER = "interval-lab-a"
ENGINEER_CUSTOMERS = [ALERT_CUSTOMER, "interval-lab-b"]
READER_CUSTOMERS = [ALERT_CUSTOMER, "interval-lab-c"]
UNDISCLOSED_CUSTOMER = "interval-lab-b"
ENGINEER_LOGIN = "interval-multi-customer-engineer"
INTENT_ATTRIBUTE = "zabbix_ack_sync_intent"
BARRIER_TIMEOUT = 30

SOURCE = dict(source_system="zabbix", source_contract_version="1", zabbix_instance="interval-lab",
              zabbix_event_id="940001", zabbix_trigger_id="940002", zabbix_cause_event_id=None,
              zabbix_recovery_event_id=None)
COHORT = dict(customer=ALERT_CUSTOMER, zabbix_instance="interval-lab", zabbix_trigger_id="940002",
              host_ids=["940003"], group_ids=["940004"], revision="interval-r1", policy_revision="1",
              enabled=True, one_to_one=True, comment_enabled=True, allow_admin=False)

_event_seq = iter(range(950001, 959999))


def _token(app, username, customers, scope):
    now = datetime.now(timezone.utc)
    return "Bearer " + jwt.encode(
        dict(iss="synthetic", typ="Bearer", sub="synthetic-" + username, aud="interval-lab-api",
             exp=now + timedelta(minutes=10), nbf=now - timedelta(seconds=1), iat=now,
             preferred_username=username, customers=customers, scope=scope),
        app.config["SECRET_KEY"], algorithm="HS256")


@pytest.fixture
def interval_api(pg_lab, pgdb, monkeypatch):
    if pg_lab["backend"] != "native_postgres18":
        pytest.skip("NOT_RUN_NATIVE: the pre-signing interval needs two concurrent sessions; "
                    "PGlite multiplexes onto one backend and cannot establish it")
    app = create_app(dict(DATABASE_URL=pg_lab["admin_dsn"], DATABASE_NAME=None,
                          DATABASE_SCHEMA="public", PLUGINS=[], TESTING=True,
                          AUTH_REQUIRED=True, CUSTOMER_VIEWS=True, OAUTH2_CLIENT_ID="interval-lab-api",
                          SECRET_KEY="synthetic-interval-lab-jwt-only-32by", PLUGINS_RAISE_ON_ERROR=False,
                          AUDIT_TRAIL=[]))
    original_connect = db.connect
    roles_seen: list[str] = []

    def runtime_connect():
        # Every HTTP session — the acknowledging one and the reader's — runs as
        # the application role, not the lab administrator, so the interval is
        # observed under the deployed identity rather than a privileged session.
        connection = original_connect()
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE alerta_app")
            cursor.execute("SELECT current_user")
            roles_seen.append(cursor.fetchone()[0])
        connection.commit()
        return connection

    monkeypatch.setattr(db, "connect", runtime_connect)
    settings = Settings(enabled=True, config_revision="interval-r1", signing_kid="lab", cohorts=(COHORT,),
                        ingestion_mappings={"interval-ingest": {"customer": ALERT_CUSTOMER,
                                                                "instance": "interval-lab"}})
    signer = AckSigningPlugin(settings=settings, keys={"lab": KEY}, probe=SimpleNamespace(check=lambda: {}))
    monkeypatch.setattr(plugins, "plugins", OrderedDict(normalizer=ZabbixNormaliser(settings=settings), signer=signer))
    monkeypatch.setattr(plugins, "rules", None)
    monkeypatch.setattr(plugins, "config", app.config)

    def enrol(enabled=True):
        with pgdb("zabalack_owner") as owner:
            owner.execute("""INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,
                          policy_version,host_ids,group_ids,enabled)
                          VALUES(%s,'interval-lab','940002','interval-r1','1','["940003"]','["940004"]',%s)
                          ON CONFLICT(customer,instance,trigger_id)
                          DO UPDATE SET enabled=%s,revision='interval-r1'""",
                          (ALERT_CUSTOMER, enabled, enabled))

    enrol()

    def new_alert(event_id):
        with app.test_request_context("/synthetic-lab-setup"):
            g.login = "interval-ingest"
            source = deepcopy(SOURCE)
            source["zabbix_event_id"] = str(event_id)
            alert = Alert("synthetic-" + str(uuid4()), "interval-trigger-family", attributes=source,
                          customer=ALERT_CUSTOMER, severity="major", status="open")
            return alert.create().id

    yield SimpleNamespace(app=app, client=app.test_client(), new_alert=new_alert, pgdb=pgdb, enrol=enrol,
                          roles_seen=roles_seen,
                          engineer=_token(app, ENGINEER_LOGIN, ENGINEER_CUSTOMERS, "write:alerts"),
                          reader=_token(app, "interval-single-customer-reader", READER_CUSTOMERS, "read:alerts"))


def probe(lab, term, event_id):
    """Attempt to inspect one alert's intent from a reader session.

    Returns the status code: the guard refuses the selector with a stable 400,
    so a 200 with any count means the interval was queryable.
    """
    query = f"attributes.{INTENT_ATTRIBUTE}:{term} AND attributes.zabbix_event_id:{event_id}"
    response = lab.client.get("/alerts/count", query_string={"q": query},
                              headers={"Authorization": lab.reader})
    return response.status_code


def acknowledge(lab, identity, client=None):
    return (client or lab.client).put(f"/alert/{identity}/action",
                                      headers={"Authorization": lab.engineer},
                                      json={"action": "ack", "text": "synthetic note"})


def test_T40_staged_intent_is_not_searchable_during_the_pre_signing_interval(interval_api, monkeypatch):
    """Pause between the early attribute commit and the history write, then probe.

    `process_action()` has committed the staged attributes by the time
    `from_action()` is entered, and that commit is independent of the request, so
    the reader session below observes a fully committed row.
    """
    lab = interval_api
    event_id = str(next(_event_seq))
    identity = lab.new_alert(event_id)

    staged = threading.Event()
    release = threading.Event()
    original = Alert.from_action

    def paused(self, action, text="", timeout=None):
        staged.set()
        assert release.wait(timeout=BARRIER_TIMEOUT), "reader probe did not complete"
        return original(self, action, text, timeout)

    monkeypatch.setattr(Alert, "from_action", paused)
    outcome = {}

    def acknowledging():
        try:
            with lab.app.test_client() as client:
                outcome["response"] = acknowledge(lab, identity, client=client)
        except BaseException as error:  # surfaced below; a silent thread death would hide it
            outcome["error"] = error

    worker = threading.Thread(target=acknowledging, daemon=True)
    worker.start()
    try:
        assert staged.wait(timeout=BARRIER_TIMEOUT), "acknowledgement never reached the interval"
        refused = lab.client.get("/alerts/count", headers={"Authorization": lab.reader}, query_string={
            "q": f"attributes.{INTENT_ATTRIBUTE}:{UNDISCLOSED_CUSTOMER} AND attributes.zabbix_event_id:{event_id}"})
        # Permitted data stays visible during the pause, so the refusal below is
        # the guard acting rather than the alert being unreachable.
        visible = lab.client.get("/alerts/count", headers={"Authorization": lab.reader},
                                 query_string={"q": f"attributes.zabbix_event_id:{event_id}"})
    finally:
        release.set()
        worker.join(timeout=BARRIER_TIMEOUT)

    assert not worker.is_alive(), "the acknowledging thread did not terminate"
    assert "error" not in outcome, outcome.get("error")
    assert outcome["response"].status_code == 200, outcome["response"].get_json()
    assert visible.status_code == 200 and visible.get_json()["total"] == 1
    assert refused.status_code == 400, "the staged intent was queryable before the acknowledgement committed"
    assert set(lab.roles_seen) == {"alerta_app"}, lab.roles_seen


def test_T40_staged_intent_is_not_searchable_after_a_failed_acknowledgement(interval_api, monkeypatch):
    """A request that never commits its history still leaves the staged attributes."""
    lab = interval_api
    event_id = str(next(_event_seq))
    identity = lab.new_alert(event_id)

    def refuse(self, action, text="", timeout=None):
        raise RuntimeError("synthetic failure after the staged attribute commit")

    monkeypatch.setattr(Alert, "from_action", refuse)
    response = acknowledge(lab, identity)
    assert response.status_code == 500
    with lab.pgdb("alerta_owner") as owner:
        status = owner.execute("SELECT status FROM alerts WHERE id=%s", (identity,)).fetchone()[0]
    assert status == "open", "precondition: the acknowledgement did not commit"
    assert probe(lab, UNDISCLOSED_CUSTOMER, event_id) == 400


def test_T40_staged_intent_is_not_searchable_when_capture_never_occurs(interval_api):
    """With no enabled database cohort the trigger fires and captures nothing."""
    lab = interval_api
    lab.enrol(enabled=False)
    event_id = str(next(_event_seq))
    identity = lab.new_alert(event_id)
    assert acknowledge(lab, identity).status_code == 200
    with lab.pgdb("zabalack_owner") as owner:
        captures = owner.execute("SELECT count(*) FROM zabalack.captures WHERE alert_id=%s",
                                 (identity,)).fetchone()[0]
    assert captures == 0, "precondition: capture_ack returned at its eligibility check"
    assert probe(lab, UNDISCLOSED_CUSTOMER, event_id) == 400
