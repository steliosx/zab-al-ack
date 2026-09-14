"""Installed Alerta HTTP/auth/alarm integration with explicitly mocked storage.

Valid synthetic JWTs exercise the actual permission decorator. No database or
tenant-permission deployment claims derive from this in-process store.
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


ALERT_ID = "81000000-0000-4000-8000-000000000001"
SOURCE = dict(source_system="zabbix", source_contract_version="1", zabbix_instance="lab-zbx",
              zabbix_event_id="41", zabbix_trigger_id="11", zabbix_cause_event_id=None,
              zabbix_recovery_event_id=None)
COHORT = dict(customer="lab-a", zabbix_instance="lab-zbx", zabbix_trigger_id="11", host_ids=["21"],
              group_ids=["31"], revision="r1", policy_revision="1", enabled=True,
              one_to_one=True, comment_enabled=True, allow_admin=False)


@pytest.fixture
def http_lab(monkeypatch):
    app = Flask("reference-http-mocked-storage")
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
    # Mocked storage: the protected staging operation is recorded, not executed.
    signer = AckSigningPlugin(settings=settings, probe=SimpleNamespace(check=lambda: {}),
                              keys={"lab": b"synthetic-hmac-lab-only-32bytes!!!"},
                              stager=lambda alert_id, intent: 1)
    monkeypatch.setattr(plugins, "plugins", OrderedDict(signer=signer))
    monkeypatch.setattr(plugins, "rules", None)
    monkeypatch.setattr(plugins, "config", app.config)
    with app.app_context():
        alert = Alert("synthetic-resource", "stable-family", id=ALERT_ID, customer="lab-a", severity="major",
                      status="open", attributes=deepcopy(SOURCE), history=[History(ALERT_ID, "stable-family", status="open")])
    memory = {"alert": alert, "history_writes": 0, "fail_save": False, "staged": []}

    def find(cls, identity, customers=None):
        if identity != ALERT_ID or (customers and memory["alert"].customer not in customers):
            return None
        return memory["alert"]

    def update_attributes(self, attributes):
        memory["staged"].append(deepcopy(attributes))
        return deepcopy(attributes)

    def save(**kwargs):
        if memory["fail_save"]:
            # Simulates PostgreSQL DETAIL, exercising the real HTTP exception
            # handler and privacy adapter. It is not a PG transaction test.
            raise RuntimeError("synthetic failed row DETAIL: " + kwargs["history"][0].text)
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
    now = datetime.now(timezone.utc)
    token = jwt.encode(dict(iss="synthetic", typ="Bearer", sub="synthetic-human-id", aud="synthetic-api",
                            exp=now + timedelta(minutes=5), nbf=now - timedelta(seconds=1), iat=now,
                            preferred_username="synthetic-human", customers=["lab-a"], scope="write:alerts"),
                       app.config["SECRET_KEY"], algorithm="HS256")
    yield app.test_client(), memory, {"Authorization": "Bearer " + token}


def test_T1_T36_actual_HTTP_200_then_409_no_new_action(http_lab):
    client, memory, headers = http_lab
    first = client.put(f"/alert/{ALERT_ID}/action", json={"action": "ack", "text": "synthetic note"}, headers=headers)
    assert first.status_code == 200, first.get_json()
    assert memory["history_writes"] == 1
    assert memory["alert"].history[0].change_type == "ack"
    assert "[zabalack:" in memory["alert"].history[0].text
    identity = deepcopy(memory["alert"].attributes["zabbix_ack_sync_intent"])
    again = client.put(f"/alert/{ALERT_ID}/action", json={"action": "ack"}, headers=headers)
    assert again.status_code == 409, again.get_json()
    assert memory["history_writes"] == 1
    assert memory["alert"].attributes["zabbix_ack_sync_intent"] == identity


def test_T19_actual_failed_save_HTTP_response_contains_no_proof(http_lab):
    client, memory, headers = http_lab
    memory["fail_save"] = True
    response = client.put(f"/alert/{ALERT_ID}/action", json={"action": "ack"}, headers=headers)
    assert response.status_code == 500
    assert memory["alert"].status == "open"
    assert memory["history_writes"] == 0
    assert "[zabalack:" not in response.get_data(as_text=True)
    assert "[zabalack:" not in repr(memory["staged"])


def test_T24_actual_HTTP_auth_rejects_invalid_JWT(http_lab):
    client, memory, headers = http_lab
    response = client.put(f"/alert/{ALERT_ID}/action", json={"action": "ack"},
                          headers={"Authorization": "Bearer synthetic-invalid"})
    assert response.status_code == 401
    assert memory["history_writes"] == 0
