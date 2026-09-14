"""T40 — the staged intent must not be reachable through authenticated search.

Removing the intent from response bodies does not close the confidentiality
invariant. Alerta's PostgreSQL query parser compiles `attributes.<key>:<term>`
into `"attributes"::jsonb ->> '<key>' ILIKE '%<term>%'`, and `->>` on a JSON
object yields that object's whole serialised text. A reader entitled to an alert
can therefore probe whether an arbitrary string occurs anywhere inside the intent
attached to it — including customers that reader has no authority over — and read
the answer from the authenticated `/alerts/count` or `/alerts` endpoints, without
the intent ever appearing in a response body.

These cases run against real PostgreSQL through the pinned Alerta application, so
they exercise the actual parser and the actual customer scoping. The reader and
the acknowledging engineer hold overlapping but non-identical scopes: neither is
a subset of the other, which is the general form of the A5 invariant.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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

KEY = b"synthetic-search-lab-only-32bytes-key!!!"
ALERT_CUSTOMER = "search-lab-a"
# The engineer also serves search-lab-b; the reader serves search-lab-c instead.
# Neither scope contains the other, and search-lab-b is the undisclosed one.
ENGINEER_CUSTOMERS = [ALERT_CUSTOMER, "search-lab-b"]
READER_CUSTOMERS = [ALERT_CUSTOMER, "search-lab-c"]
UNDISCLOSED_CUSTOMER = "search-lab-b"
ABSENT_CUSTOMER = "search-lab-zzz"
ENGINEER_LOGIN = "search-multi-customer-engineer"
INTENT_ATTRIBUTE = "zabbix_ack_sync_intent"

SOURCE = dict(source_system="zabbix", source_contract_version="1", zabbix_instance="search-lab",
              zabbix_event_id="920001", zabbix_trigger_id="920002", zabbix_cause_event_id=None,
              zabbix_recovery_event_id=None)
COHORT = dict(customer=ALERT_CUSTOMER, zabbix_instance="search-lab", zabbix_trigger_id="920002",
              host_ids=["920003"], group_ids=["920004"], revision="search-r1", policy_revision="1",
              enabled=True, one_to_one=True, comment_enabled=True, allow_admin=False)


def _token(app, username, customers, scope):
    now = datetime.now(timezone.utc)
    return "Bearer " + jwt.encode(
        dict(iss="synthetic", typ="Bearer", sub="synthetic-" + username, aud="search-lab-api",
             exp=now + timedelta(minutes=10), nbf=now - timedelta(seconds=1), iat=now,
             preferred_username=username, customers=customers, scope=scope),
        app.config["SECRET_KEY"], algorithm="HS256")


@pytest.fixture
def search_api(pg_lab, pgdb, monkeypatch):
    if pg_lab["backend"] == "postgres18_wasm":
        with pgdb("alerta_owner") as owner:
            assert owner.execute("SELECT current_user").fetchone()[0] == "alerta_owner"
    app = create_app(dict(DATABASE_URL=pg_lab["admin_dsn"], DATABASE_NAME=None,
                          DATABASE_SCHEMA="public", PLUGINS=[], TESTING=True,
                          AUTH_REQUIRED=True, CUSTOMER_VIEWS=True, OAUTH2_CLIENT_ID="search-lab-api",
                          SECRET_KEY="synthetic-search-lab-jwt-only-32byte", PLUGINS_RAISE_ON_ERROR=False,
                          AUDIT_TRAIL=[]))
    original_connect = db.connect

    def runtime_connect():
        connection = original_connect()
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE alerta_app")
        connection.commit()
        return connection

    monkeypatch.setattr(db, "connect", runtime_connect)
    settings = Settings(enabled=True, config_revision="search-r1", signing_kid="lab", cohorts=(COHORT,),
                        ingestion_mappings={"search-ingest": {"customer": ALERT_CUSTOMER, "instance": "search-lab"}})
    signer = AckSigningPlugin(settings=settings, keys={"lab": KEY}, probe=SimpleNamespace(check=lambda: {}))
    monkeypatch.setattr(plugins, "plugins", OrderedDict(normalizer=ZabbixNormaliser(settings=settings), signer=signer))
    monkeypatch.setattr(plugins, "rules", None)
    monkeypatch.setattr(plugins, "config", app.config)
    with pgdb("zabalack_owner") as owner:
        owner.execute("""INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,
                      host_ids,group_ids,enabled) VALUES(%s,'search-lab','920002','search-r1','1',
                      '["920003"]','["920004"]',true) ON CONFLICT(customer,instance,trigger_id)
                      DO UPDATE SET enabled=true,revision='search-r1'""", (ALERT_CUSTOMER,))

    def new_alert():
        with app.test_request_context("/synthetic-lab-setup"):
            g.login = "search-ingest"
            alert = Alert("synthetic-" + str(uuid4()), "search-trigger-family", attributes=deepcopy(SOURCE),
                          customer=ALERT_CUSTOMER, severity="major", status="open")
            return alert.create().id

    yield SimpleNamespace(
        client=app.test_client(), new_alert=new_alert, pgdb=pgdb,
        engineer=_token(app, ENGINEER_LOGIN, ENGINEER_CUSTOMERS, "write:alerts"),
        reader=_token(app, "search-single-customer-reader", READER_CUSTOMERS, "read:alerts"))


def acknowledge(lab):
    identity = lab.new_alert()
    response = lab.client.put(f"/alert/{identity}/action", headers={"Authorization": lab.engineer},
                              json={"action": "ack", "text": "synthetic note"})
    assert response.status_code == 200, response.get_json()
    return identity


def stored_intent(lab, identity):
    with lab.pgdb("alerta_owner") as owner:
        row = owner.execute("SELECT attributes->%s FROM alerts WHERE id=%s",
                            (INTENT_ATTRIBUTE, identity)).fetchone()
    return row[0] if row else None


def probe_count(lab, term):
    """Ask the count endpoint whether `term` occurs anywhere inside the intent."""
    response = lab.client.get(f"/alerts/count?q=attributes.{INTENT_ATTRIBUTE}:{term}",
                              headers={"Authorization": lab.reader})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["total"]


def test_T40_absent_term_probe_is_negative_control(search_api):
    """A term that is in no intent returns zero, so a positive probe means disclosure."""
    lab = search_api
    identity = acknowledge(lab)
    assert stored_intent(lab, identity) is not None, "precondition: an intent was staged"
    assert probe_count(lab, ABSENT_CUSTOMER) == 0


def test_T40_reader_search_cannot_probe_undisclosed_customer(search_api):
    lab = search_api
    acknowledge(lab)
    # The reader has no authority over search-lab-b and must not be able to
    # confirm the acknowledging engineer serves it.
    assert probe_count(lab, UNDISCLOSED_CUSTOMER) == 0


def test_T40_reader_search_cannot_probe_acknowledging_identity(search_api):
    lab = search_api
    acknowledge(lab)
    # The same oracle discloses who acknowledged, independently of membership.
    assert probe_count(lab, ENGINEER_LOGIN) == 0


def test_T40_reader_alerts_query_cannot_probe_undisclosed_customer(search_api):
    lab = search_api
    acknowledge(lab)
    response = lab.client.get(f"/alerts?q=attributes.{INTENT_ATTRIBUTE}:{UNDISCLOSED_CUSTOMER}",
                              headers={"Authorization": lab.reader})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["total"] == 0
