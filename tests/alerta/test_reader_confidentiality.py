"""T40 — reader-facing confidentiality of the staged intent (amendment A5).

The signer copies the acknowledging operator's authenticated identity, including
their complete authorised customer list, into
`alert.attributes['zabbix_ack_sync_intent']`. Alerta returns `attributes` in
ordinary alert responses, so in a `CUSTOMER_VIEWS` deployment a reader entitled
to one customer's alert can read the operator's memberships in customers that
reader has no authority over.

These cases assert the response boundary only. They use explicitly mocked
storage: no database trigger, no capture record and no cleanup worker runs here,
so nothing in this module establishes T41 (safe retirement of stored intents) or
any G1-G8 deployment evidence. Lifecycle states are simulated at the attribute
level, which is what a reader-facing assertion needs.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from flask import Flask, g
from alerta import settings as defaults
from alerta.app import alarm_model, db, hooks, plugins
from alerta.exceptions import ExceptionHandlers
from alerta.models.alert import Alert
from alerta.models.enums import Scope
from alerta.models.history import History
from alerta.models.metrics import Timer
from alerta.views import api

from zabalack.config import Settings
from zabalack.plugins import AckSigningPlugin, configure_app


ALERT_ID = "81000000-0000-4000-8000-000000000002"
INTENT_ATTRIBUTE = "zabbix_ack_sync_intent"
# The acknowledging engineer serves three customers; the alert belongs to one.
ENGINEER_CUSTOMERS = ["lab-a", "lab-b", "lab-c"]
# Customers the reader has no authority over and must never learn from a read.
UNDISCLOSED = ["lab-b", "lab-c"]
SOURCE = dict(source_system="zabbix", source_contract_version="1", zabbix_instance="lab-zbx",
              zabbix_event_id="42", zabbix_trigger_id="12", zabbix_cause_event_id=None,
              zabbix_recovery_event_id=None)
COHORT = dict(customer="lab-a", zabbix_instance="lab-zbx", zabbix_trigger_id="12", host_ids=["22"],
              group_ids=["32"], revision="r1", policy_revision="1", enabled=True,
              one_to_one=True, comment_enabled=True, allow_admin=False)


def _token(app, username, customers, scope):
    now = datetime.now(timezone.utc)
    return jwt.encode(dict(iss="synthetic", typ="Bearer", sub="synthetic-" + username, aud="synthetic-api",
                           exp=now + timedelta(minutes=5), nbf=now - timedelta(seconds=1), iat=now,
                           preferred_username=username, customers=customers, scope=scope),
                      app.config["SECRET_KEY"], algorithm="HS256")


@pytest.fixture
def reader_lab(monkeypatch):
    app = Flask("reference-reader-confidentiality")
    app.config.from_object(defaults)
    app.config.update(TESTING=True, AUTH_REQUIRED=True, CUSTOMER_VIEWS=True, OAUTH2_CLIENT_ID="synthetic-api",
                      SECRET_KEY="synthetic-lab-signing-secret-only", PLUGINS_RAISE_ON_ERROR=False)
    alarm_model.init_app(app)
    Scope.init_app(app)
    hooks.init_app(app)
    ExceptionHandlers().register(app)
    app.register_blueprint(api)
    configure_app(app)

    @app.before_request
    def lab_request_id():
        g.request_id = "synthetic-request"

    settings = Settings(enabled=True, config_revision="lab-r1", signing_kid="lab", cohorts=(COHORT,))
    signer = AckSigningPlugin(settings=settings, probe=SimpleNamespace(check=lambda: {}),
                              keys={"lab": b"synthetic-hmac-lab-only-32bytes!!!"})
    monkeypatch.setattr(plugins, "plugins", OrderedDict(signer=signer))
    monkeypatch.setattr(plugins, "rules", None)
    monkeypatch.setattr(plugins, "config", app.config)
    with app.app_context():
        alert = Alert("synthetic-resource", "stable-family", id=ALERT_ID, customer="lab-a", severity="major",
                      status="open", attributes=deepcopy(SOURCE),
                      history=[History(ALERT_ID, "stable-family", status="open")])
    memory = {"alert": alert, "history_writes": 0, "fail_save": False}

    def find(cls, identity, customers=None):
        if identity != ALERT_ID or (customers and memory["alert"].customer not in customers):
            return None
        return memory["alert"]

    def update_attributes(self, attributes):
        # Reference Alerta commits this in process_action() before signing is
        # attempted; the staged dictionary therefore reaches storage even when
        # the acknowledgement itself later fails.
        self.attributes = deepcopy(attributes)
        return deepcopy(attributes)

    def save(**kwargs):
        if memory["fail_save"]:
            raise RuntimeError("synthetic failed row DETAIL")
        memory["history_writes"] += 1
        alert.status = kwargs["status"]
        alert.history = kwargs["history"] + alert.history
        return alert

    monkeypatch.setattr(Alert, "find_by_id", classmethod(find))
    monkeypatch.setattr(Alert, "update_attributes", update_attributes)
    monkeypatch.setattr(Alert, "update_tags", lambda self, tags: True)
    monkeypatch.setattr(Alert, "tag", lambda self, tags: True)
    monkeypatch.setattr(Alert, "_get_hist_info", lambda self, action: (self.status, None, "open", 3600))
    monkeypatch.setattr(Alert, "from_db", classmethod(lambda cls, value: value))
    monkeypatch.setattr(db, "set_alert", save)
    monkeypatch.setattr(db, "update_timer", lambda timer: timer)
    monkeypatch.setattr(Timer, "from_db", classmethod(lambda cls, timer: timer))

    engineer = {"Authorization": "Bearer " + _token(app, "multi-customer-engineer", ENGINEER_CUSTOMERS, "write:alerts")}
    reader = {"Authorization": "Bearer " + _token(app, "single-customer-reader", ["lab-a"], "read:alerts")}
    yield SimpleNamespace(client=app.test_client(), memory=memory, engineer=engineer, reader=reader)


def acknowledge(lab):
    return lab.client.put(f"/alert/{ALERT_ID}/action", json={"action": "ack", "text": "synthetic note"},
                          headers=lab.engineer)


def assert_reader_learns_nothing(response):
    """The reserved intent key and every undisclosed customer stay out of the body."""
    body = response.get_data(as_text=True)
    assert INTENT_ATTRIBUTE not in body, "reader response carries the staged intent"
    for customer in UNDISCLOSED:
        assert customer not in body, f"reader response discloses membership in {customer}"


def test_T40_reader_does_not_receive_intent_before_worker_processing(reader_lab):
    assert acknowledge(reader_lab).status_code == 200
    assert reader_lab.memory["alert"].attributes["zabbix_ack_sync"] == "pending"
    response = reader_lab.client.get(f"/alert/{ALERT_ID}", headers=reader_lab.reader)
    assert response.status_code == 200
    assert response.get_json()["alert"]["attributes"].get(INTENT_ATTRIBUTE) is None
    assert_reader_learns_nothing(response)


def test_T40_reader_does_not_receive_intent_after_worker_processing(reader_lab):
    assert acknowledge(reader_lab).status_code == 200
    action_id = reader_lab.memory["alert"].attributes["zabbix_ack_sync_action_id"]
    # Projection merges the reserved namespace with `||`; it removes nothing.
    reader_lab.memory["alert"].attributes.update({
        "zabbix_ack_sync": "ok", "zabbix_ack_sync_reason": "acknowledged",
        "zabbix_ack_sync_action_id": action_id, "zabbix_ack_sync_generation": 1})
    response = reader_lab.client.get(f"/alert/{ALERT_ID}", headers=reader_lab.reader)
    assert response.status_code == 200
    assert response.get_json()["alert"]["attributes"]["zabbix_ack_sync"] == "ok"
    assert response.get_json()["alert"]["attributes"].get(INTENT_ATTRIBUTE) is None
    assert_reader_learns_nothing(response)


def test_T40_reader_does_not_receive_intent_when_worker_unavailable(reader_lab):
    assert acknowledge(reader_lab).status_code == 200
    # No verification, no projection: the alert stays at the staged pending state
    # indefinitely, which is the state a stopped or backlogged worker leaves.
    assert reader_lab.memory["alert"].attributes["zabbix_ack_sync_reason"] == "awaiting_capture"
    response = reader_lab.client.get(f"/alert/{ALERT_ID}", headers=reader_lab.reader)
    assert response.status_code == 200
    assert response.get_json()["alert"]["attributes"].get(INTENT_ATTRIBUTE) is None
    assert_reader_learns_nothing(response)


def test_T40_reader_does_not_receive_intent_after_failed_acknowledgement(reader_lab):
    reader_lab.memory["fail_save"] = True
    assert acknowledge(reader_lab).status_code == 500
    # The acknowledgement did not commit, but process_action() already committed
    # the staged attributes, so the intent outlives the failed request.
    assert reader_lab.memory["alert"].status == "open"
    assert reader_lab.memory["history_writes"] == 0
    response = reader_lab.client.get(f"/alert/{ALERT_ID}", headers=reader_lab.reader)
    assert response.status_code == 200
    assert response.get_json()["alert"]["attributes"].get(INTENT_ATTRIBUTE) is None
    assert_reader_learns_nothing(response)


def test_T40_jsonp_response_mode_does_not_disclose_intent(reader_lab):
    assert acknowledge(reader_lab).status_code == 200
    # `jsonp` wraps the body as application/javascript inside the view decorator,
    # before after_request runs, so an is_json-keyed filter never engages.
    response = reader_lab.client.get(f"/alert/{ALERT_ID}?callback=synthetic_cb", headers=reader_lab.reader)
    assert response.status_code == 200
    assert response.mimetype == "application/javascript"
    assert response.get_data(as_text=True).startswith("synthetic_cb(")
    assert_reader_learns_nothing(response)
