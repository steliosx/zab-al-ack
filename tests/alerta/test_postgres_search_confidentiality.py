"""T40 — reader-facing confidentiality on every surface that needs the real
query builder: the collection response, `/alerts/history`, and authenticated
search (amendment A5).

Removing the intent from single-alert response bodies does not close the
invariant. Two further surfaces reach the same stored value.

`/alerts/history` serialises through `RichHistory`, a separate class from
`Alert`. Its rows come from `SELECT … attributes … FROM alerts, unnest(history…)`,
so each entry carries the alert row's *current* attributes; filtering
`Alert.serialize` alone leaves the intent readable there, including via JSONP.

Alerta's PostgreSQL query parser compiles `attributes.<key>:<term>` into
`"attributes"::jsonb ->> '<key>' ILIKE '%<term>%'`, and `->>` on a JSON object
yields that object's whole serialised text. A reader entitled to an alert can
therefore probe whether an arbitrary string occurs anywhere inside the intent
attached to it and read the answer from `/alerts/count` or `/alerts`, without the
intent appearing in any body.

Controls. Every probe is scoped to its own alert by conjoining the source event
id, so counts cannot accumulate across cases. A probe for a term in no intent is
the negative control. A probe on a *permitted* projection field is the positive
control: disabling attribute search wholesale would satisfy every zero assertion
without protecting anything, and must fail that case instead. The precondition
for a meaningful run is that restricted capture evidence exists in
`zabalack.captures`, not that the alert attribute still holds the intent — the
latter would reject a correct stage-two retirement.

Execution layer. Under the reference runner these execute on PostgreSQL 18.3 in
PGlite (WASM), which is supplemental single-backend evidence. Native execution is
a separate layer and is not established here. The pre-signing interval needs two
concurrent sessions, which PGlite cannot provide, and lives in
`test_native_staging_interval.py`.
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
ABSENT_TERM = "search-lab-zzz"
ENGINEER_LOGIN = "search-multi-customer-engineer"
INTENT_ATTRIBUTE = "zabbix_ack_sync_intent"
UNRELATED_ATTRIBUTE = "runbook_url"
UNRELATED_VALUE = "https://runbook.invalid/search-lab"

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

    def new_alert(event_id):
        with app.test_request_context("/synthetic-lab-setup"):
            g.login = "search-ingest"
            source = deepcopy(SOURCE)
            source["zabbix_event_id"] = str(event_id)
            source[UNRELATED_ATTRIBUTE] = UNRELATED_VALUE
            alert = Alert("synthetic-" + str(uuid4()), "search-trigger-family", attributes=source,
                          customer=ALERT_CUSTOMER, severity="major", status="open")
            return alert.create().id

    yield SimpleNamespace(
        client=app.test_client(), new_alert=new_alert, pgdb=pgdb,
        engineer=_token(app, ENGINEER_LOGIN, ENGINEER_CUSTOMERS, "write:alerts"),
        reader=_token(app, "search-single-customer-reader", READER_CUSTOMERS, "read:alerts"))


# --- helpers ----------------------------------------------------------------

_event_seq = iter(range(930001, 939999))


def acknowledge(lab):
    """Acknowledge a fresh alert and return its identity and unique event id."""
    event_id = str(next(_event_seq))
    identity = lab.new_alert(event_id)
    response = lab.client.put(f"/alert/{identity}/action", headers={"Authorization": lab.engineer},
                              json={"action": "ack", "text": "synthetic note"})
    assert response.status_code == 200, response.get_json()
    return identity, event_id


def assert_capture_evidence(lab, identity):
    """Restricted capture evidence exists, so the run is meaningful.

    This replaces any precondition on the alert attribute still holding the
    intent: stage two retires that attribute, and a correct retirement must not
    make these cases vacuous.
    """
    with lab.pgdb("zabalack_owner") as owner:
        row = owner.execute("SELECT intent FROM zabalack.captures WHERE alert_id=%s",
                            (identity,)).fetchone()
    assert row is not None and row[0] is not None, "precondition: restricted capture evidence recorded"
    assert row[0]["actor"]["customers"] == sorted(ENGINEER_CUSTOMERS)


def probe(lab, field, term, event_id, endpoint="/alerts/count"):
    """Scope a probe to exactly one alert so counts cannot accumulate."""
    query = f"attributes.{field}:{term} AND attributes.zabbix_event_id:{event_id}"
    response = lab.client.get(endpoint, query_string={"q": query},
                              headers={"Authorization": lab.reader})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["total"]


# --- search path ------------------------------------------------------------

def test_T40_absent_term_probe_is_negative_control(search_api):
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    assert probe(lab, INTENT_ATTRIBUTE, ABSENT_TERM, event_id) == 0


def test_T40_permitted_projection_field_remains_searchable(search_api):
    """Positive control: disabling attribute search wholesale must not pass T40.

    `zabbix_ack_sync` is a permitted projection field. If a mitigation made every
    attribute query return nothing, the zero assertions below would all succeed
    while breaking legitimate operator search, so this case must stay green.
    """
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    assert probe(lab, "zabbix_ack_sync", "pending", event_id) == 1


def test_T40_reader_search_cannot_probe_undisclosed_customer(search_api):
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    assert probe(lab, INTENT_ATTRIBUTE, UNDISCLOSED_CUSTOMER, event_id) == 0


def test_T40_reader_search_cannot_probe_acknowledging_identity(search_api):
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    assert probe(lab, INTENT_ATTRIBUTE, ENGINEER_LOGIN, event_id) == 0


def test_T40_reader_alerts_query_cannot_probe_undisclosed_customer(search_api):
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    assert probe(lab, INTENT_ATTRIBUTE, UNDISCLOSED_CUSTOMER, event_id, endpoint="/alerts") == 0


# --- collection and history responses ---------------------------------------

def collection(lab, event_id, mode):
    # `show-history` defaults to false upstream, so the collection omits history
    # unless asked; it is requested here to prove the committed proof survives.
    params = {"q": f"attributes.zabbix_event_id:{event_id}", "show-history": "true"}
    if mode == "jsonp":
        params["callback"] = "synthetic_cb"
    response = lab.client.get("/alerts", query_string=params, headers={"Authorization": lab.reader})
    assert response.status_code == 200
    if mode == "jsonp":
        assert response.mimetype == "application/javascript"
        assert response.get_data(as_text=True).startswith("synthetic_cb(")
    return response


@pytest.mark.parametrize("mode", ["json", "jsonp"])
def test_T40_collection_response_excludes_the_intent(search_api, mode):
    """An ordinary collection request that returns the alert, not a zero-count probe."""
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    response = collection(lab, event_id, mode)
    body = response.get_data(as_text=True)
    assert INTENT_ATTRIBUTE not in body, "collection response carries the staged intent"
    assert UNDISCLOSED_CUSTOMER not in body, "collection response discloses the undisclosed membership"
    if mode == "json":
        alerts = response.get_json()["alerts"]
        assert len(alerts) == 1, "precondition: the collection returned the target alert"
        attributes = alerts[0]["attributes"]
        assert attributes.get(INTENT_ATTRIBUTE) is None
        # Permitted fields and the committed proof survive the exclusion.
        assert attributes["zabbix_event_id"] == event_id
        assert attributes["zabbix_instance"] == SOURCE["zabbix_instance"]
        assert attributes[UNRELATED_ATTRIBUTE] == UNRELATED_VALUE
        assert attributes["zabbix_ack_sync"] == "pending"
        acked = [entry for entry in alerts[0]["history"] if entry.get("type") == "ack"]
        assert acked and "[zabalack:" in acked[0]["text"], "the committed proof was stripped"


@pytest.mark.parametrize("mode", ["json", "jsonp"])
def test_T40_alerts_history_endpoint_excludes_the_intent(search_api, mode):
    """`/alerts/history` serialises through RichHistory, a class Alert's patch misses."""
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    params = {"q": f"attributes.zabbix_event_id:{event_id}"}
    if mode == "jsonp":
        params["callback"] = "synthetic_cb"
    response = lab.client.get("/alerts/history", query_string=params,
                              headers={"Authorization": lab.reader})
    assert response.status_code == 200
    if mode == "jsonp":
        assert response.mimetype == "application/javascript"
    body = response.get_data(as_text=True)
    assert INTENT_ATTRIBUTE not in body, "history response carries the staged intent"
    assert UNDISCLOSED_CUSTOMER not in body, "history response discloses the undisclosed membership"
    if mode == "json":
        entries = response.get_json()["history"]
        acked = [entry for entry in entries if entry.get("type") == "ack"]
        assert acked, "precondition: the acknowledgement entry is present"
        # A1 retains the committed proof, and §12's history layer permits the
        # acknowledging attribution; only the staged intent is excluded.
        assert "[zabalack:" in acked[0]["text"]
        assert acked[0]["user"] == ENGINEER_LOGIN
        assert acked[0]["attributes"].get(INTENT_ATTRIBUTE) is None
        assert acked[0]["attributes"][UNRELATED_ATTRIBUTE] == UNRELATED_VALUE


def test_T40_backing_state_is_unchanged_by_reads(search_api):
    """Reads must not mutate the stored row the capture path depends on."""
    lab = search_api
    identity, event_id = acknowledge(lab)
    collection(lab, event_id, "json")
    lab.client.get(f"/alert/{identity}", headers={"Authorization": lab.reader})
    with lab.pgdb("alerta_owner") as owner:
        attributes = owner.execute("SELECT attributes FROM alerts WHERE id=%s", (identity,)).fetchone()[0]
    assert attributes[UNRELATED_ATTRIBUTE] == UNRELATED_VALUE
    assert attributes["zabbix_instance"] == SOURCE["zabbix_instance"]
    assert_capture_evidence(lab, identity)
