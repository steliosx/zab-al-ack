"""T40 — reader-facing confidentiality of the staged intent (amendment A5).

The signer copies the acknowledging operator's authenticated identity, including
their complete authorised customer list, into
`alert.attributes['zabbix_ack_sync_intent']`. Alerta returns `attributes` in
ordinary alert responses, so in a `CUSTOMER_VIEWS` deployment a reader entitled
to one customer's alert can read the operator's memberships in customers that
reader has no authority over.

Scope split. This module covers single-alert response modes across the capture
lifecycle, using explicitly mocked storage: no database trigger, no capture
record and no cleanup worker runs here. The collection response and the
authenticated search path need the real query builder and are covered against
real PostgreSQL in `test_postgres_search_confidentiality.py`. Nothing in either
module establishes T41 (safe retirement of stored intents) or any G1-G8
deployment evidence.

The reader and the engineer hold overlapping but non-identical scopes: the
engineer serves lab-a and lab-b, the reader lab-a and lab-c. Neither contains
the other, which is the general form of the invariant — the concern is lab-b.

Every case also asserts that the read left the internals intact. A sanitiser
that mutated the shared attribute dictionary would satisfy the confidentiality
assertions while destroying the capture input, the operator's own attributes and
the committed proof, so those are checked explicitly rather than assumed.
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
ENGINEER_LOGIN = "multi-customer-engineer"
ENGINEER_CUSTOMERS = ["lab-a", "lab-b"]
READER_CUSTOMERS = ["lab-a", "lab-c"]
UNDISCLOSED = "lab-b"
# An operator-owned attribute unrelated to this integration. A read must not
# disturb it, and neither must any future sanitiser.
UNRELATED_ATTRIBUTE = "runbook_url"
UNRELATED_VALUE = "https://runbook.invalid/search-lab"

SOURCE = dict(source_system="zabbix", source_contract_version="1", zabbix_instance="lab-zbx",
              zabbix_event_id="42", zabbix_trigger_id="12", zabbix_cause_event_id=None,
              zabbix_recovery_event_id=None)
COHORT = dict(customer="lab-a", zabbix_instance="lab-zbx", zabbix_trigger_id="12", host_ids=["22"],
              group_ids=["32"], revision="r1", policy_revision="1", enabled=True,
              one_to_one=True, comment_enabled=True, allow_admin=False)


def _token(app, username, customers, scope):
    now = datetime.now(timezone.utc)
    return "Bearer " + jwt.encode(
        dict(iss="synthetic", typ="Bearer", sub="synthetic-" + username, aud="synthetic-api",
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
    attributes = deepcopy(SOURCE)
    attributes[UNRELATED_ATTRIBUTE] = UNRELATED_VALUE
    with app.app_context():
        alert = Alert("synthetic-resource", "stable-family", id=ALERT_ID, customer="lab-a", severity="major",
                      status="open", attributes=attributes,
                      history=[History(ALERT_ID, "stable-family", status="open")])
    memory = {"alert": alert, "history_writes": 0, "fail_save": False}

    def find(cls, identity, customers=None):
        if identity != ALERT_ID or (customers and memory["alert"].customer not in customers):
            return None
        return memory["alert"]

    def update_attributes(self, attributes):
        # Reference Alerta commits this in process_action() before signing is
        # attempted, so the staged dictionary reaches storage even when the
        # acknowledgement itself later fails.
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

    yield SimpleNamespace(
        client=app.test_client(), memory=memory,
        engineer={"Authorization": _token(app, ENGINEER_LOGIN, ENGINEER_CUSTOMERS, "write:alerts")},
        reader={"Authorization": _token(app, "single-customer-reader", READER_CUSTOMERS, "read:alerts")})


# --- lifecycle states -------------------------------------------------------

def acknowledge(lab, expect=200):
    response = lab.client.put(f"/alert/{ALERT_ID}/action", json={"action": "ack", "text": "synthetic note"},
                              headers=lab.engineer)
    assert response.status_code == expect, response.get_json()
    return response


def before_processing(lab):
    acknowledge(lab)
    assert lab.memory["alert"].attributes["zabbix_ack_sync"] == "pending"


def after_processing(lab):
    acknowledge(lab)
    action_id = lab.memory["alert"].attributes["zabbix_ack_sync_action_id"]
    # project() merges the reserved namespace with `||`; it removes nothing.
    lab.memory["alert"].attributes.update({
        "zabbix_ack_sync": "ok", "zabbix_ack_sync_reason": "acknowledged",
        "zabbix_ack_sync_action_id": action_id, "zabbix_ack_sync_generation": 1})


def worker_unavailable(lab):
    acknowledge(lab)
    # No verification and no projection: the state a stopped or backlogged
    # worker leaves behind, for as long as it stays stopped.
    assert lab.memory["alert"].attributes["zabbix_ack_sync_reason"] == "awaiting_capture"


def failed_acknowledgement(lab):
    lab.memory["fail_save"] = True
    acknowledge(lab, expect=500)
    # The acknowledgement did not commit, but process_action() already committed
    # the staged attributes, so the intent outlives the failed request.
    assert lab.memory["alert"].status == "open"
    assert lab.memory["history_writes"] == 0


LIFECYCLE = {"before_processing": before_processing, "after_processing": after_processing,
             "worker_unavailable": worker_unavailable, "failed_acknowledgement": failed_acknowledgement}


# --- assertions -------------------------------------------------------------

def read_alert(lab, mode):
    suffix = "?callback=synthetic_cb" if mode == "jsonp" else ""
    response = lab.client.get(f"/alert/{ALERT_ID}{suffix}", headers=lab.reader)
    assert response.status_code == 200
    if mode == "jsonp":
        # `jsonp` wraps the body as application/javascript inside the view
        # decorator, before after_request runs, so an is_json-keyed filter
        # never engages for this response mode.
        assert response.mimetype == "application/javascript"
        assert response.get_data(as_text=True).startswith("synthetic_cb(")
    return response


def assert_reader_learns_nothing(response):
    """The reserved key and the undisclosed membership stay out of the body.

    The acknowledging login is deliberately not asserted against the whole body.
    `Alert.serialize` includes committed history, whose entries carry `user`, and
    that attribution is Alerta's own audit behaviour under the separate history
    layer of §12 — A1 keeps it in successful history responses. A5 governs the
    staged intent, so the login is asserted against `attributes` instead, where
    the intent is its only carrier.
    """
    body = response.get_data(as_text=True)
    assert INTENT_ATTRIBUTE not in body, "reader response carries the staged intent"
    assert UNDISCLOSED not in body, f"reader response discloses membership in {UNDISCLOSED}"


def assert_attributes_carry_no_identity(response):
    attributes = response.get_json()["alert"]["attributes"]
    assert attributes.get(INTENT_ATTRIBUTE) is None
    rendered = repr(sorted(attributes.items()))
    assert ENGINEER_LOGIN not in rendered, "alert attributes disclose the acknowledging identity"
    assert UNDISCLOSED not in rendered, f"alert attributes disclose membership in {UNDISCLOSED}"


def assert_internals_preserved(lab, state):
    """A read must not damage capture input, operator attributes or the proof."""
    attributes = lab.memory["alert"].attributes
    intent = attributes.get(INTENT_ATTRIBUTE)
    assert intent is not None, "the read removed the stored intent the trigger needs"
    assert intent["actor"]["login"] == ENGINEER_LOGIN
    assert intent["actor"]["customers"] == sorted(ENGINEER_CUSTOMERS)
    assert attributes[UNRELATED_ATTRIBUTE] == UNRELATED_VALUE, "the read disturbed an unrelated attribute"
    assert attributes["zabbix_instance"] == SOURCE["zabbix_instance"], "the read disturbed source identity"
    if state != "failed_acknowledgement":
        assert "[zabalack:" in lab.memory["alert"].history[0].text, "the read disturbed the committed proof"


@pytest.mark.parametrize("state", sorted(LIFECYCLE))
@pytest.mark.parametrize("mode", ["json", "jsonp"])
def test_T40_reader_does_not_receive_intent(reader_lab, state, mode):
    LIFECYCLE[state](reader_lab)
    response = read_alert(reader_lab, mode)
    if mode == "json":
        assert_attributes_carry_no_identity(response)
    assert_reader_learns_nothing(response)
    assert_internals_preserved(reader_lab, state)


@pytest.mark.parametrize("mode", ["json", "jsonp"])
def test_T40_acknowledging_engineer_read_also_excludes_the_intent(reader_lab, mode):
    """The intent is restricted staging data, not a projection for any reader."""
    before_processing(reader_lab)
    suffix = "?callback=synthetic_cb" if mode == "jsonp" else ""
    response = reader_lab.client.get(f"/alert/{ALERT_ID}{suffix}", headers=reader_lab.engineer)
    assert response.status_code == 200
    assert INTENT_ATTRIBUTE not in response.get_data(as_text=True)
    assert_internals_preserved(reader_lab, "before_processing")


def test_T40_repeated_reads_do_not_degrade_capture_input(reader_lab):
    """Serving many readers must not consume or mutate the stored intent."""
    before_processing(reader_lab)
    first = deepcopy(reader_lab.memory["alert"].attributes[INTENT_ATTRIBUTE])
    for _ in range(3):
        assert_reader_learns_nothing(read_alert(reader_lab, "json"))
        assert_reader_learns_nothing(read_alert(reader_lab, "jsonp"))
    assert reader_lab.memory["alert"].attributes[INTENT_ATTRIBUTE] == first
