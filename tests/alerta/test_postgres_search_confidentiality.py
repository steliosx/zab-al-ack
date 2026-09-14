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
yields that object's whole serialised text. A reader entitled to an alert could
therefore probe whether an arbitrary string occurs anywhere inside the intent and
read the answer from a count, without the intent appearing in any body. The same
reach exists through `_exists_`, the `_.key` alias, sub-expressions, `q.df`,
direct filters, sorting, and `group-by=attributes`, which makes the aggregate
endpoints return the whole attributes object as a plain dictionary.

The guard refuses those selectors with a **stable 400**, identical whether or not
a matching alert or intent exists, so the refusal is not itself an oracle. These
cases therefore assert a refusal, not a zero count: a zero count would be
satisfied by an empty database.

Controls. A permitted keyed attribute search, a permitted sort and a permitted
grouping must keep working, so a mitigation that disabled attribute querying
wholesale fails rather than passing everything. Refusals for a present and an
absent term are compared to each other, which is what demonstrates the absence of
an oracle. Every query is scoped to its own alert by conjoining the source event
id. The precondition for a meaningful run is that restricted capture evidence
exists in `zabalack.captures`, not that the alert attribute still holds the
intent — the latter would reject a correct stage-two retirement.

Execution layer. Under the reference runner these execute on PostgreSQL 18.3 in
PGlite (WASM), which is supplemental single-backend evidence. Native execution is
a separate layer and is not established here. The pre-signing interval needs two
concurrent sessions, which PGlite cannot provide, and lives in
`test_native_staging_interval.py`.
"""
import ast
from collections import OrderedDict
from copy import deepcopy
import json
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
            # `get_topn_count` reads FROM alerts, UNNEST(service) svc, which drops
            # any alert whose service array is empty; without one the aggregate
            # cases would pass vacuously on an empty result.
            alert = Alert("synthetic-" + str(uuid4()), "search-trigger-family", attributes=source,
                          customer=ALERT_CUSTOMER, severity="major", status="open",
                          service=["search-lab-service"])
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


def unwrap(response, mode):
    """Parse either response mode into a dictionary.

    The `jsonp` decorator builds its body as `str(response.data)`, so the JSON
    arrives inside a Python bytes literal wrapped in the callback.
    """
    text = response.get_data(as_text=True)
    if mode != "jsonp":
        return json.loads(text)
    inner = text[text.index("(") + 1:text.rindex(")")]
    if inner[:2] in ("b'", 'b"'):
        return json.loads(ast.literal_eval(inner).decode())
    return json.loads(inner)


def scoped(event_id, selector=None):
    base = f"attributes.zabbix_event_id:{event_id}"
    return f"{selector} AND {base}" if selector else base


def request(lab, endpoint="/alerts/count", mode="json", **params):
    if mode == "jsonp":
        params["callback"] = "synthetic_cb"
    return lab.client.get(endpoint, query_string=params, headers={"Authorization": lab.reader})


def assert_refused(response):
    """A restricted selector is refused identically however it is phrased.

    The comparable part is the status and the message; Alerta stamps a fresh
    `requestId` on every error body, which is not part of the contract.
    """
    assert response.status_code == 400, response.get_data(as_text=True)
    body = json.loads(response.get_data(as_text=True))
    return response.status_code, body.get("status"), body.get("message")


def permitted_total(lab, endpoint="/alerts/count", mode="json", **params):
    response = request(lab, endpoint=endpoint, mode=mode, **params)
    assert response.status_code == 200, response.get_data(as_text=True)
    return unwrap(response, mode)["total"]


# --- the guard refuses every route to the restricted key --------------------

def test_T40_restricted_selector_refusal_does_not_depend_on_the_term(search_api):
    """The refusal must be identical for a present and an absent term.

    This is what shows the 400 is not itself an oracle: if a restricted selector
    were refused only when it matched, the status code would carry the answer.
    """
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    present = request(lab, q=scoped(event_id, f"attributes.{INTENT_ATTRIBUTE}:{UNDISCLOSED_CUSTOMER}"))
    absent = request(lab, q=scoped(event_id, f"attributes.{INTENT_ATTRIBUTE}:{ABSENT_TERM}"))
    assert assert_refused(present) == assert_refused(absent)


@pytest.mark.parametrize("selector", [
    pytest.param(f"attributes.{INTENT_ATTRIBUTE}:{UNDISCLOSED_CUSTOMER}", id="keyed-term"),
    pytest.param(f"attributes.{INTENT_ATTRIBUTE}:{ENGINEER_LOGIN}", id="keyed-identity"),
    pytest.param(f"_.{INTENT_ATTRIBUTE}:{UNDISCLOSED_CUSTOMER}", id="underscore-alias"),
    pytest.param(f'attributes.{INTENT_ATTRIBUTE}:"{UNDISCLOSED_CUSTOMER}"', id="phrase"),
    pytest.param(f"_exists_:{INTENT_ATTRIBUTE}", id="exists"),
    pytest.param(f"attributes.{INTENT_ATTRIBUTE}:({UNDISCLOSED_CUSTOMER} OR {ABSENT_TERM})", id="subquery"),
])
@pytest.mark.parametrize("endpoint", ["/alerts/count", "/alerts", "/alerts/history"])
def test_T40_restricted_query_selectors_are_refused(search_api, selector, endpoint):
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    assert_refused(request(lab, endpoint=endpoint, q=scoped(event_id, selector)))


def test_T40_whole_attributes_default_field_is_refused(search_api):
    """`q.df=attributes` turns a bare term into a whole-column search."""
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    response = lab.client.get("/alerts/count", query_string={"q": UNDISCLOSED_CUSTOMER, "q.df": "attributes"},
                              headers={"Authorization": lab.reader})
    assert_refused(response)


def test_T40_restricted_direct_filter_is_refused(search_api):
    """`attributes` is absent from EXCLUDE_FROM_QUERY, so this reaches `@>`."""
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    response = lab.client.get("/alerts/count",
                              query_string={f"attributes.{INTENT_ATTRIBUTE}": UNDISCLOSED_CUSTOMER},
                              headers={"Authorization": lab.reader})
    assert_refused(response)


@pytest.mark.parametrize("sort_by", [f"attributes.{INTENT_ATTRIBUTE}", f"-attributes.{INTENT_ATTRIBUTE}",
                                     "attributes"])
def test_T40_restricted_sort_is_refused(search_api, sort_by):
    lab = search_api
    acknowledge(lab)
    assert_refused(request(lab, endpoint="/alerts", **{"sort-by": sort_by}))


@pytest.mark.parametrize("endpoint", ["/alerts/topn/count", "/alerts/topn/flapping", "/alerts/topn/standing"])
def test_T40_whole_attributes_grouping_is_refused(search_api, endpoint):
    """`group-by=attributes` makes the aggregates select the whole object.

    `get_topn_count` interpolates the grouping expression into both SELECT and
    GROUP BY and returns it under that key, so the attributes dictionary reaches
    the reader as a plain value, bypassing both patched serialisers.
    """
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    assert_refused(request(lab, endpoint=endpoint, **{"group-by": "attributes"}))


def test_T40_crafted_default_field_cannot_restore_the_disclosure(search_api):
    """`q.df` is substituted *inside* the quotes of `"__default_field__"`.

    `QueryParser.parse` replaces the unquoted token, so a crafted default field
    becomes a raw expression — here a concatenation naming `attributes::text`
    with no quotes at all, which a quoted-spelling check would not see. The
    default field is validated against supported names before compilation.
    """
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    crafted = 'text" || attributes::text || "text'
    response = lab.client.get("/alerts/count", query_string={"q": UNDISCLOSED_CUSTOMER, "q.df": crafted},
                              headers={"Authorization": lab.reader})
    assert_refused(response)


@pytest.mark.parametrize("default_field", ["attributes", "attributes.zabbix_ack_sync",
                                           "history", "not_a_field", ""])
def test_T40_unsupported_default_fields_are_refused(search_api, default_field):
    lab = search_api
    acknowledge(lab)
    assert_refused(lab.client.get("/alerts/count",
                                  query_string={"q": ABSENT_TERM, "q.df": default_field},
                                  headers={"Authorization": lab.reader}))


@pytest.mark.parametrize("sort_by", [
    pytest.param(f"attributes.{INTENT_ATTRIBUTE}.ignored", id="extra-component"),
    pytest.param(f"-attributes.{INTENT_ATTRIBUTE}.ignored", id="extra-component-reversed"),
    pytest.param(f"attributes.{INTENT_ATTRIBUTE}.a.b", id="several-extra-components"),
])
def test_T40_sort_with_extra_components_is_refused(search_api, sort_by):
    """Upstream keeps only `split('.')[1]` and discards the rest.

    `attributes.<restricted>.ignored` would otherwise sort by the restricted key
    through a spelling the guard did not recognise, so unsupported selector forms
    are refused rather than normalised.
    """
    lab = search_api
    acknowledge(lab)
    assert_refused(request(lab, endpoint="/alerts", **{"sort-by": sort_by}))


@pytest.mark.parametrize("field", ["attributes", "attributes!"])
def test_T40_whole_object_direct_filter_is_refused(search_api, field):
    """`VALID_PARAMS['attributes']` is filterable, so a bare selector reaches SQL."""
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    assert_refused(lab.client.get("/alerts/count", query_string={field: "{}"},
                                  headers={"Authorization": lab.reader}))


@pytest.mark.parametrize("field", [f"attributes.{INTENT_ATTRIBUTE}.ignored"])
def test_T40_direct_filter_with_extra_components_is_refused(search_api, field):
    lab = search_api
    acknowledge(lab)
    assert_refused(lab.client.get("/alerts/count", query_string={field: UNDISCLOSED_CUSTOMER},
                                  headers={"Authorization": lab.reader}))


@pytest.mark.parametrize("group_by", [
    pytest.param("ATTRIBUTES", id="upper-case"),
    pytest.param("Attributes", id="mixed-case"),
    pytest.param('"attributes"', id="quoted"),
    pytest.param('"ATTRIBUTES"', id="quoted-upper"),
    pytest.param(" attributes ", id="padded"),
])
def test_T40_grouping_column_variants_are_refused(search_api, group_by):
    """PostgreSQL folds an unquoted identifier and honours a quoted one.

    `ATTRIBUTES`, `Attributes` and `"attributes"` all name the same column, so
    comparing the raw spelling would refuse only one of them. The grouping token
    is interpolated into SELECT and GROUP BY with no upstream validation, so the
    guard also refuses anything that is not a plain identifier.
    """
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    assert_refused(request(lab, endpoint="/alerts/topn/count", **{"group-by": group_by}))


@pytest.mark.parametrize("sort_by", [
    pytest.param(f"ATTRIBUTES.{INTENT_ATTRIBUTE}", id="upper-case-column"),
    pytest.param(f"Attributes.{INTENT_ATTRIBUTE}", id="mixed-case-column"),
    pytest.param("attributes.x'||attributes::text||'y", id="quote-in-key"),
    pytest.param('attributes.x"y', id="double-quote-in-key"),
    pytest.param("attributes.x y", id="space-in-key"),
])
def test_T40_sort_key_variants_are_refused(search_api, sort_by):
    """`sort_by_columns` interpolates the key raw into `attributes->'{key}'`.

    A quote in the key escapes the literal, so any key that is not a plain name
    is unsupported syntax regardless of whether it names the restricted key.
    """
    lab = search_api
    acknowledge(lab)
    assert_refused(request(lab, endpoint="/alerts", **{"sort-by": sort_by}))


@pytest.mark.parametrize("field", [f"ATTRIBUTES.{INTENT_ATTRIBUTE}", f"Attributes.{INTENT_ATTRIBUTE}",
                                   "ATTRIBUTES"])
def test_T40_direct_filter_column_variants_are_refused(search_api, field):
    lab = search_api
    acknowledge(lab)
    assert_refused(lab.client.get("/alerts/count", query_string={field: UNDISCLOSED_CUSTOMER},
                                  headers={"Authorization": lab.reader}))


# --- positive controls: legitimate attribute querying still works -----------

def test_T40_permitted_projection_field_remains_searchable(search_api):
    """A mitigation that disabled attribute search wholesale must fail here."""
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    assert permitted_total(lab, q=scoped(event_id, "attributes.zabbix_ack_sync:pending")) == 1


def test_T40_supported_default_field_still_works(search_api):
    """Validating `q.df` must not break the legitimate default-field feature."""
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    response = lab.client.get("/alerts/count",
                              query_string={"q": ABSENT_TERM, "q.df": "text"},
                              headers={"Authorization": lab.reader})
    assert response.status_code == 200, response.get_data(as_text=True)


def test_T40_permitted_sort_and_grouping_still_work(search_api):
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    sorted_response = request(lab, endpoint="/alerts", q=scoped(event_id),
                              **{"sort-by": "attributes.zabbix_ack_sync"})
    assert sorted_response.status_code == 200, sorted_response.get_data(as_text=True)
    grouped = request(lab, endpoint="/alerts/topn/count", q=scoped(event_id), **{"group-by": "event"})
    assert grouped.status_code == 200, grouped.get_data(as_text=True)
    # The UNNEST(service) join keeps the fixture alert only because it carries a
    # service; an empty result here would make the aggregate cases vacuous.
    assert unwrap(grouped, "json")["total"] == 1


# --- collection and history responses ---------------------------------------

def collection(lab, event_id, mode):
    # `show-history` defaults to false upstream, so the collection omits history
    # unless asked; it is requested here to prove the committed proof survives.
    params = {"q": scoped(event_id), "show-history": "true"}
    if mode == "jsonp":
        params["callback"] = "synthetic_cb"
    response = lab.client.get("/alerts", query_string=params, headers={"Authorization": lab.reader})
    assert response.status_code == 200
    if mode == "jsonp":
        assert response.mimetype == "application/javascript"
        assert response.get_data(as_text=True).startswith("synthetic_cb(")
    return response


def assert_alert_payload(alert, event_id):
    """The intent is gone; everything the reader is entitled to survives."""
    attributes = alert["attributes"]
    assert attributes.get(INTENT_ATTRIBUTE) is None
    assert attributes["zabbix_event_id"] == event_id
    assert attributes["zabbix_instance"] == SOURCE["zabbix_instance"]
    assert attributes[UNRELATED_ATTRIBUTE] == UNRELATED_VALUE
    assert attributes["zabbix_ack_sync"] == "pending"
    acked = [entry for entry in alert["history"] if entry.get("type") == "ack"]
    assert acked and "[zabalack:" in acked[0]["text"], "the committed proof was stripped"


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
    # Parsed in both modes: a nonempty result and the permitted fields are
    # asserted for JSONP too, not only for JSON.
    alerts = unwrap(response, mode)["alerts"]
    assert len(alerts) == 1, "precondition: the collection returned the target alert"
    assert_alert_payload(alerts[0], event_id)


@pytest.mark.parametrize("mode", ["json", "jsonp"])
def test_T40_alerts_history_endpoint_excludes_the_intent(search_api, mode):
    """`/alerts/history` serialises through RichHistory, a class Alert's patch misses."""
    lab = search_api
    identity, event_id = acknowledge(lab)
    assert_capture_evidence(lab, identity)
    params = {"q": scoped(event_id)}
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
    entries = unwrap(response, mode)["history"]
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
