"""F1 source binding at ingestion (migration 015), against the real SQL.

Covers the design's SQL evidence list (docs/DESIGN_0.2.0_F1_SOURCE_BINDING.md section 5)
that a single session can show: the bind outcomes and poison, the selection rule,
the database-assigned order, the exact microsecond round trip, the grant map, the
capture check, the coexistence cases agreed with the Voalack reviewer, and the
order-integrity health check. Native multi-session concurrency and the deployment
lab cases are separate evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import psycopg
from psycopg.types.json import Jsonb
import pytest

from conftest import connect_role
from zabalack.crypto import build_intent, sign_intent
from zabalack_ingestion import epoch_now, ingest

CUSTOMER = "Synthetic Binding"
INSTANCE = "binding-zbx"
TRIGGER = "61100"
KEY = b"synthetic-binding-key-32-bytes!!"


@pytest.fixture
def lab(pg_lab):
    with connect_role(pg_lab, "zabalack_owner") as owner:
        owner.execute("""INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,
                      host_ids,group_ids,enabled) VALUES(%s,%s,%s,'binding-r1','1','["61101"]','["61102"]',true)
                      ON CONFLICT(customer,instance,trigger_id) DO UPDATE SET enabled=true""",
                      (CUSTOMER, INSTANCE, TRIGGER))
    yield pg_lab
    with connect_role(pg_lab, "zabalack_owner") as owner:
        owner.execute("""DELETE FROM zabalack.outbox WHERE capture_id IN
                      (SELECT capture_id FROM zabalack.captures WHERE customer=%s)""", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.captures WHERE customer=%s", (CUSTOMER,))
        owner.execute("""DELETE FROM zabalack.alert_generations WHERE alert_id IN
                      (SELECT id FROM public.alerts WHERE customer=%s)""", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.binding_events WHERE alert_id IN (SELECT alert_id FROM zabalack.source_bindings WHERE customer=%s)", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.source_bindings WHERE customer=%s", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.cohorts WHERE customer=%s", (CUSTOMER,))
    with connect_role(pg_lab, "alerta_owner") as owner:
        owner.execute("DELETE FROM alerts WHERE customer=%s", (CUSTOMER,))


def source(event_id="61200", *, instance=INSTANCE, trigger=TRIGGER):
    return {"zabbix_instance": instance, "zabbix_event_id": event_id, "zabbix_trigger_id": trigger}


def new_alert(lab, event_id="61200", *, source_system="zabbix", trigger=TRIGGER,
              resource="binding-resource", event="binding-event"):
    alert_id = str(uuid.uuid4())
    attributes = {"source_system": source_system, **source(event_id, trigger=trigger)}
    with connect_role(lab, "alerta_app") as app:
        app.execute("INSERT INTO alerts(id,resource,event,status,customer,attributes,history) "
                    "VALUES(%s,%s,%s,'open',%s,%s,ARRAY[]::history[])",
                    (alert_id, resource, event, CUSTOMER, Jsonb(attributes)))
    return alert_id


def bind(lab, alert_id, src, *, receive_id=None, receive_time=None, customer=CUSTOMER):
    with connect_role(lab, "alerta_app") as app:
        return app.execute("SELECT zabalack.bind_source(%s,%s,%s,%s,%s,%s,%s)",
                           (alert_id, receive_id or str(uuid.uuid4()), receive_time or epoch_now(), customer,
                            src["zabbix_instance"], src["zabbix_event_id"], src["zabbix_trigger_id"])).fetchone()[0]


def read(lab, alert_id, receive_id, receive_time):
    with connect_role(lab, "alerta_app") as app:
        return app.execute("SELECT zabalack.read_binding(%s,%s,%s)", (alert_id, receive_id, receive_time)).fetchone()[0]


def epoch_of(lab, alert_id):
    with connect_role(lab, "alerta_owner") as owner:
        return owner.execute("SELECT last_receive_id,last_receive_time FROM alerts WHERE id=%s", (alert_id,)).fetchone()


def acknowledge(lab, alert_id, src, *, with_intent=True):
    """Stage an intent for `src` on the row and commit a marked acknowledgement, firing the trigger."""
    with connect_role(lab, "alerta_owner") as owner:
        now = owner.execute("SELECT clock_timestamp()").fetchone()[0]
    intent = build_intent(alert_id, CUSTOMER, src, {"login": "binding-engineer", "customers": [CUSTOMER],
                          "admin": False, "auth_type": "user"}, "binding-r1", "1", now=now)
    with connect_role(lab, "alerta_app") as app:
        if with_intent:
            app.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                        (Jsonb({"zabbix_ack_sync_intent": intent}), alert_id))
        app.execute("UPDATE alerts SET status='ack',history=ARRAY[ROW(%s,event,NULL,'ack',NULL,%s,'ack',"
                    "clock_timestamp() AT TIME ZONE 'UTC',%s,NULL)::history] WHERE id=%s",
                    (str(uuid.uuid4()), "note " + sign_intent(intent, "lab", KEY), "binding-engineer", alert_id))
    with connect_role(lab, "zabalack_owner") as owner:
        rows = owner.execute("SELECT c.state,c.diagnostic_reason,o.state FROM zabalack.captures c JOIN zabalack.outbox o "
                             "USING(capture_id) WHERE c.alert_id=%s ORDER BY c.capture_order", (alert_id,)).fetchall()
        generations = owner.execute("SELECT count(*) FROM zabalack.alert_generations WHERE alert_id=%s",
                                    (alert_id,)).fetchone()[0]
    return rows, generations


# --- bind outcomes, poison and the database-assigned order ---------------------------------

def test_F1_bind_outcomes_idempotence_and_poison(lab):
    alert_id = new_alert(lab)
    receive_id, receive_time = str(uuid.uuid4()), epoch_now()
    assert bind(lab, alert_id, source(), receive_id=receive_id, receive_time=receive_time) == "bound"
    assert bind(lab, alert_id, source(), receive_id=receive_id, receive_time=receive_time) == "idempotent_repeat"
    assert read(lab, alert_id, receive_id, receive_time)["outcome"] == "usable"
    # Same epoch, different source: the original values stay and the epoch is poisoned.
    assert bind(lab, alert_id, source("61299"), receive_id=receive_id, receive_time=receive_time) == "conflict_poisoned"
    assert read(lab, alert_id, receive_id, receive_time) == {"outcome": "source_binding_conflict"}
    # A same-value repeat on a poisoned epoch never makes it usable again.
    assert bind(lab, alert_id, source(), receive_id=receive_id, receive_time=receive_time) == "idempotent_repeat"
    assert read(lab, alert_id, receive_id, receive_time) == {"outcome": "source_binding_conflict"}
    with connect_role(lab, "zabalack_owner") as owner:
        row = owner.execute("SELECT zabbix_event_id,state FROM zabalack.source_bindings WHERE alert_id=%s", (alert_id,)).fetchone()
        reasons = [r[0] for r in owner.execute("SELECT reason FROM zabalack.binding_events WHERE alert_id=%s "
                                               "ORDER BY event_id", (alert_id,)).fetchall()]
    assert row == ("61200", "poisoned")
    assert reasons == ["bound", "idempotent_repeat", "conflict_poisoned", "idempotent_repeat"]


def test_F1_the_latest_recorded_binding_is_the_only_usable_one(lab):
    alert_id = new_alert(lab)
    first = (str(uuid.uuid4()), epoch_now())
    second = (str(uuid.uuid4()), first[1] - timedelta(seconds=10))   # an application clock that stepped back
    bind(lab, alert_id, source("61201"), receive_id=first[0], receive_time=first[1])
    bind(lab, alert_id, source("61202"), receive_id=second[0], receive_time=second[1])
    # Ranked by the order of recording, not by the receive time.
    assert read(lab, alert_id, *first) == {"outcome": "source_binding_not_latest"}
    assert read(lab, alert_id, *second)["zabbix_event_id"] == "61202"
    # A later poisoned binding outranks an earlier bound one: the earlier is no longer usable.
    third = (str(uuid.uuid4()), epoch_now())
    bind(lab, alert_id, source("61203"), receive_id=third[0], receive_time=third[1])
    bind(lab, alert_id, source("61204"), receive_id=third[0], receive_time=third[1])
    assert read(lab, alert_id, *second) == {"outcome": "source_binding_not_latest"}
    assert read(lab, alert_id, *third) == {"outcome": "source_binding_conflict"}
    # An epoch nobody bound is missing, whatever the alert's attributes say.
    assert read(lab, alert_id, str(uuid.uuid4()), epoch_now()) == {"outcome": "source_binding_missing"}
    assert read(lab, alert_id, None, None) == {"outcome": "source_binding_missing"}


def test_F1_bind_seq_is_assigned_by_the_database_only(lab):
    alert_id = new_alert(lab)
    bind(lab, alert_id, source())
    with connect_role(lab, "zabalack_owner") as owner:
        with pytest.raises(psycopg.Error):
            with owner.transaction():
                owner.execute("INSERT INTO zabalack.source_bindings(alert_id,last_receive_id,last_receive_time,customer,"
                              "zabbix_instance,zabbix_event_id,zabbix_trigger_id,state,bind_seq,bound_at) "
                              "VALUES(%s,'x',now()::timestamp,%s,%s,'1',%s,'bound',999999,now())",
                              (alert_id, CUSTOMER, INSTANCE, TRIGGER))
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT zabalack.binding_order_intact()").fetchone()[0] is True


def test_F1_the_epoch_round_trips_at_microsecond_precision(lab):
    alert_id = new_alert(lab)
    stamp = datetime(2026, 11, 1, 18, 0, 0, 123457)
    receive_id, receive_time, outcome = (None, None, None)
    with connect_role(lab, "alerta_app") as app:
        receive_id, receive_time, outcome = ingest(app, alert_id, CUSTOMER, source(), receive_time=stamp)
    assert outcome == "bound"
    assert epoch_of(lab, alert_id) == (receive_id, stamp)
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT last_receive_time FROM zabalack.source_bindings WHERE alert_id=%s",
                             (alert_id,)).fetchone()[0] == stamp
    assert read(lab, alert_id, receive_id, stamp)["outcome"] == "usable"
    assert read(lab, alert_id, receive_id, stamp + timedelta(microseconds=1))["outcome"] == "source_binding_missing"


def test_F1_bind_source_reads_nothing_in_public_alerts(lab):
    """It binds an epoch for an alert id that does not exist: no read of public.alerts is involved."""
    assert bind(lab, str(uuid.uuid4()), source()) == "bound"
    with connect_role(lab, "zabalack_owner") as owner:
        for routine in ("bind_source(text,text,timestamp without time zone,text,text,text,text)",
                        "read_binding(text,text,timestamp without time zone)",
                        "select_binding(text,text,timestamp without time zone)"):
            body = owner.execute("SELECT pg_get_functiondef(%s::regprocedure)", ("zabalack." + routine,)).fetchone()[0]
            assert "alerts" not in body.replace("source_bindings", "").replace("binding_events", ""), routine


@pytest.mark.parametrize("role", ["zabalack_worker", "zabalack_probe", "zabalack_operator"])
def test_F1_only_the_application_role_reaches_the_binding_entry_points(lab, role):
    with connect_role(lab, role) as conn:
        for call in ("SELECT zabalack.bind_source('a','b',now()::timestamp,'c','d','1','2')",
                     "SELECT zabalack.read_binding('a','b',now()::timestamp)",
                     "SELECT zabalack.select_binding('a','b',now()::timestamp)",
                     "SELECT * FROM zabalack.source_bindings", "SELECT * FROM zabalack.binding_events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.transaction():
                    conn.execute(call)
    with connect_role(lab, "alerta_app") as app:
        for call in ("SELECT zabalack.select_binding('a','b',now()::timestamp)", "SELECT * FROM zabalack.source_bindings",
                     "INSERT INTO zabalack.binding_events(alert_id,last_receive_id,last_receive_time,reason,recorded_at) "
                     "VALUES('a','b',now()::timestamp,'bound',now())"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with app.transaction():
                    app.execute(call)


# --- the capture check ---------------------------------------------------------------------

def test_F1_a_capture_for_the_bound_source_is_accepted(lab):
    alert_id = new_alert(lab)
    with connect_role(lab, "alerta_app") as app:
        ingest(app, alert_id, CUSTOMER, source())
    rows, _ = acknowledge(lab, alert_id, source())
    assert rows == [("captured_unverified", None, "captured_unverified")]


def test_F1_an_unbound_alert_is_refused_at_capture(lab):
    alert_id = new_alert(lab)
    rows, generations = acknowledge(lab, alert_id, source())
    assert rows == [("failed", "source_binding_missing", "failed")]
    assert generations == 0


def test_F1_an_attribute_edit_cannot_select_a_source(lab):
    """F1 itself: bind 61200, then edit the attributes to 61299 and acknowledge for 61299."""
    alert_id = new_alert(lab)
    with connect_role(lab, "alerta_app") as app:
        ingest(app, alert_id, CUSTOMER, source())
        app.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                    (Jsonb(source("61299")), alert_id))
    rows, generations = acknowledge(lab, alert_id, source("61299"))
    assert rows == [("failed", "source_binding_mismatch", "failed")]
    assert generations == 0


def test_F1_poisoned_and_superseded_epochs_are_refused_at_capture(lab):
    poisoned = new_alert(lab)
    with connect_role(lab, "alerta_app") as app:
        receive_id, receive_time, _ = ingest(app, poisoned, CUSTOMER, source())
    bind(lab, poisoned, source("61298"), receive_id=receive_id, receive_time=receive_time)
    assert acknowledge(lab, poisoned, source())[0] == [("failed", "source_binding_conflict", "failed")]
    superseded = new_alert(lab)
    with connect_role(lab, "alerta_app") as app:
        ingest(app, superseded, CUSTOMER, source())
    bind(lab, superseded, source("61297"))              # a later binding for a different epoch
    assert acknowledge(lab, superseded, source())[0] == [("failed", "source_binding_not_latest", "failed")]


# --- coexistence (Voalack review, 1 November 2026) ------------------------------------------

def test_F1_an_alert_outside_zabalack_eligibility_writes_no_row(lab):
    """Not source_system=zabbix, or no enabled cohort: untouched, nothing recorded (zero capture
    rows, zero generations)."""
    foreign = new_alert(lab, source_system="voalack")
    assert acknowledge(lab, foreign, source()) == ([], 0)
    uncohorted = new_alert(lab, trigger="61999")
    assert acknowledge(lab, uncohorted, source(trigger="61999")) == ([], 0)


# Voalack's alert class (Astra review 61c86918): with these the alert matches both selectors.
VOALACK_CLASS = {"event": "VeeamOneAlarm", "resource": "veeamone:synthetic"}


def assert_never_claimed(lab, alert_id):
    """claim_verification, as zabalack_worker, returns no claim for this alert's failed capture.

    Every other due capture is postponed and this one is made due now, so the claim (which takes
    the oldest, or every fourth turn the newest, due row) could only return this one if it were
    admissible at all."""
    with connect_role(lab, "zabalack_owner") as owner:
        capture_id = owner.execute("SELECT capture_id FROM zabalack.captures WHERE alert_id=%s",
                                   (alert_id,)).fetchone()[0]
        owner.execute("UPDATE zabalack.outbox SET due_at=clock_timestamp()+interval '1 hour' WHERE capture_id<>%s "
                      "AND state IN ('captured_unverified','verifier_hold') AND due_at<=clock_timestamp()", (capture_id,))
        owner.execute("UPDATE zabalack.outbox SET due_at=least(due_at,clock_timestamp()) WHERE capture_id=%s",
                      (capture_id,))
    for turn in range(4):   # covers the every-fourth-turn newest-first pick
        with connect_role(lab, "zabalack_worker") as worker:
            claimed = worker.execute("SELECT zabalack.worker_call('claim_verification',%s)", (Jsonb(
                {"worker_id": "binding-verifier-" + uuid.uuid4().hex[:8]}),)).fetchone()[0]
        assert claimed is None, (turn, claimed)


def test_F1_an_eligible_marked_alert_without_a_binding_fails_closed_locally(lab):
    """An alert that matches Zabalack's selector and Voalack's class (event VeeamOneAlarm,
    resource veeamone:synthetic) and carries a [zabalack: acknowledgement, with no Zabalack
    binding: one failed local record, zero generations, and claim_verification never returns it.
    Voalack's classifier is not run here; the overlap is in the alert's shape only."""
    alert_id = new_alert(lab, **VOALACK_CLASS)
    rows, generations = acknowledge(lab, alert_id, source())
    assert rows == [("failed", "source_binding_missing", "failed")] and generations == 0
    assert_never_claimed(lab, alert_id)
    no_intent = new_alert(lab, **VOALACK_CLASS)
    rows, generations = acknowledge(lab, no_intent, source(), with_intent=False)
    assert rows == [("failed", "intent_missing", "failed")] and generations == 0
    assert_never_claimed(lab, no_intent)


# --- health ----------------------------------------------------------------------------------

def test_F1_a_cached_binding_generator_reads_unhealthy(lab):
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT zabalack.binding_order_intact()").fetchone()[0] is True
        with owner.transaction(force_rollback=True):
            generator = owner.execute("SELECT pg_get_serial_sequence('zabalack.source_bindings','bind_seq')").fetchone()[0]
            owner.execute(f"ALTER SEQUENCE {generator} CACHE 20")
            assert owner.execute("SELECT zabalack.binding_order_intact()").fetchone()[0] is False
            assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "false"


@pytest.mark.parametrize("change", [
    "REVOKE EXECUTE ON FUNCTION zabalack.bind_source(text,text,timestamp without time zone,text,text,text,text) FROM alerta_app",
    "REVOKE EXECUTE ON FUNCTION zabalack.read_binding(text,text,timestamp without time zone) FROM alerta_app",
    "GRANT EXECUTE ON FUNCTION zabalack.read_binding(text,text,timestamp without time zone) TO zabalack_worker",
    "GRANT EXECUTE ON FUNCTION zabalack.select_binding(text,text,timestamp without time zone) TO alerta_app",
], ids=["bind-grant-missing", "read-grant-missing", "worker-reads-bindings", "app-reaches-internal-helper"])
def test_F1_the_binding_grant_map_is_asserted_both_ways(lab, change):
    """A missing or extra binding grant makes capture_health() read unhealthy immediately, in the
    same transaction. Nothing here re-seals, so no claim is made about the state after a reseal."""
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"
        with owner.transaction(force_rollback=True):
            owner.execute(change)
            assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "false"
