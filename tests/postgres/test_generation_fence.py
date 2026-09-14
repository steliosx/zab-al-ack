"""T41 — retire the staged intent from `public.alerts`, and replace the guard that
read it with a generation fence the Alerta API cannot reach (amendment A5,
stage two).

The fence is `zabalack.alert_generations`: append-only, ordered by an identity
sequence the database assigns, written by `zabalack.stage_generation()` in the
same statement that writes the staged attributes. Nothing updates or deletes a
fence row. A generation recorded at staging outranks any recorded by observation
(capture, migration), so a capture arriving late can never promote an older
acknowledgement above a newer staging. `project()` refuses unless its action is
the newest generation.

Retirement cannot be placed in `project()` alone. `claim_verification` selects
only `state IN ('captured_unverified','verifier_hold')`, and `project()` is
reachable only with a verified `action_id`, so most of A5's lifecycle table never
reaches it. Each row is a separate case below, and the sweep is asserted to
retire the attribute in every one of them **without** verification or delivery
having succeeded.

Execution layer. Under the reference runner these execute on PostgreSQL 18.3 in
PGlite (WASM), supplemental single-backend evidence. Cases needing two sessions —
lock orders, concurrent stagings, a populated-database upgrade — live in
`test_native_generation_fence.py`. Cases reached through the real HTTP entry
point live in `tests/alerta/test_postgres_http.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import uuid

from psycopg.types.json import Jsonb
import psycopg.errors
import pytest

from conftest import connect_role

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[2]
CUSTOMER = "fence-lab-a"
INSTANCE = "fence-lab"
TRIGGER = "990002"
UNDISCLOSED = "fence-lab-b"
INTENT = "zabbix_ack_sync_intent"
ACTION_KEY = "zabbix_ack_sync_action_id"
KEY = b"synthetic-fence-lab-only-32bytes-key!!!!"
_event = iter(range(991001, 999999))


def stamp(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def intent_for(alert_id, event_id, *, issued=None, action_id=None):
    """A payload shaped exactly like the one `take_action` stages."""
    issued = issued or datetime.now(timezone.utc)
    return {
        "version": 1, "action_id": action_id or str(uuid.uuid4()), "alert_id": alert_id,
        "customer": CUSTOMER,
        "source": {"zabbix_instance": INSTANCE, "zabbix_event_id": event_id, "zabbix_trigger_id": TRIGGER},
        "actor": {"login": "fence-engineer", "customers": [CUSTOMER, UNDISCLOSED],
                  "admin": False, "auth_type": "user"},
        "route": f"/alert/{alert_id}/action", "action": "ack", "cohort_revision": "fence-r1",
        "policy_revision": "1", "issued_at": stamp(issued),
        "capture_expires_at": stamp(issued + timedelta(seconds=60)),
        "execution_expires_at": stamp(issued + timedelta(hours=4)),
    }


def enrol(pg_lab):
    with connect_role(pg_lab, "zabalack_owner") as owner:
        owner.execute("""INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,
                      host_ids,group_ids,enabled) VALUES(%s,%s,%s,'fence-r1','1','["990003"]','["990004"]',true)
                      ON CONFLICT(customer,instance,trigger_id) DO UPDATE SET enabled=true""",
                      (CUSTOMER, INSTANCE, TRIGGER))


def cleanup(pg_lab):
    """Leave the lab as this module found it.

    `tests/postgres/assertions.sql` counts whole tables, so a module that leaves
    captures behind breaks a later one. Correctness must not depend on which
    order the modules happen to run in. The fence rows are removed here by the
    lab, as its owner; nothing in the shipped SQL deletes one.
    """
    with connect_role(pg_lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET capture_disabled=false WHERE singleton")
        owner.execute("""DELETE FROM zabalack.verification_leases WHERE capture_id IN
                      (SELECT capture_id FROM zabalack.captures WHERE customer=%s)""", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.actions WHERE customer=%s", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.effects WHERE instance=%s", (INSTANCE,))
        owner.execute("""DELETE FROM zabalack.outbox WHERE capture_id IN
                      (SELECT capture_id FROM zabalack.captures WHERE customer=%s)""", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.captures WHERE customer=%s", (CUSTOMER,))
        owner.execute("""DELETE FROM zabalack.alert_generations WHERE alert_id IN
                      (SELECT id FROM public.alerts WHERE customer=%s)""", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.cohorts WHERE customer=%s", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.actor_identity WHERE login='fence-engineer'")
    with connect_role(pg_lab, "alerta_owner") as owner:
        owner.execute("DELETE FROM alerts WHERE customer=%s", (CUSTOMER,))


@pytest.fixture
def lab(pg_lab):
    enrol(pg_lab)
    yield pg_lab
    cleanup(pg_lab)


def stage(lab, *, issued=None, action_id=None, staged=True, extra=None):
    """Create an alert and stage it through the protected operation.

    `zabalack.stage_generation()` is called as `alerta_app` — the one role
    granted it — exactly as the signer does inside the request. `issued`
    defaults into the past so the staged intent is already past its
    `capture_expires_at`; a freshly staged one is deliberately not yet eligible
    for retirement, which `test_T41_a_staging_still_inside_its_window_is_not_swept`
    asserts.
    """
    issued = issued or datetime.now(timezone.utc) - timedelta(minutes=10)
    alert_id, event_id = str(uuid.uuid4()), str(next(_event))
    payload = intent_for(alert_id, event_id, issued=issued, action_id=action_id)
    attributes = {"source_system": "zabbix", "zabbix_instance": INSTANCE,
                  "zabbix_event_id": event_id, "zabbix_trigger_id": TRIGGER,
                  "runbook_url": "https://runbook.invalid/fence-lab", **(extra or {})}
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("INSERT INTO alerts(id,resource,event,status,customer,service,attributes,history) "
                     "VALUES(%s,'fence-resource','fence-event','open',%s,ARRAY['fence-service'],%s,"
                     "ARRAY[]::history[])", (alert_id, CUSTOMER, Jsonb(attributes)))
        if staged:
            conn.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                         (alert_id, payload["action_id"], Jsonb(payload)))
    return alert_id, event_id, payload


def stage_over(lab, alert_id, event_id, *, issued=None):
    """A newer staging on an existing alert, through the protected operation."""
    payload = intent_for(alert_id, event_id, issued=issued)
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                     (alert_id, payload["action_id"], Jsonb(payload)))
    return payload


def stage_pre_fence(lab, alert_id, payload):
    """Stage as the plugin did before the fence existed: the attribute alone.

    This is also what a forged or replayed attribute write looks like, since
    `PUT /alert/<id>/attributes` runs no plugin and replaces the whole object.
    """
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=(attributes-'zabbix_ack_sync_local_at')||%s WHERE id=%s",
                     (Jsonb({INTENT: payload, "zabbix_ack_sync": "pending",
                             "zabbix_ack_sync_reason": "awaiting_capture", ACTION_KEY: payload["action_id"]}),
                      alert_id))


def attributes_of(lab, alert_id):
    with connect_role(lab, "alerta_owner") as owner:
        return owner.execute("SELECT attributes FROM alerts WHERE id=%s", (alert_id,)).fetchone()[0]


def fence(lab, alert_id):
    with connect_role(lab, "zabalack_owner") as owner:
        return owner.execute("SELECT action_id::text,recorded_by,seq FROM zabalack.alert_generations "
                             "WHERE alert_id=%s ORDER BY seq", (alert_id,)).fetchall()


def newest(lab, alert_id):
    with connect_role(lab, "zabalack_owner") as owner:
        value = owner.execute("SELECT zabalack.newest_generation(%s)", (alert_id,)).fetchone()[0]
    return str(value) if value is not None else None


def sweep(lab, *, grace=0, limit=1000):
    with connect_role(lab, "zabalack_owner") as owner:
        return owner.execute("SELECT zabalack.retire_intents(%s,%s)", (limit, grace)).fetchone()[0]


def fire_ack(lab, alert_id, payload, *, marker=True):
    """Commit the acknowledgement history entry, firing `zabalack_capture`."""
    from zabalack.crypto import sign_intent
    proof = sign_intent(payload, "lab", KEY) if marker else "no marker"
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET status='ack',history=ARRAY[ROW(%s,'fence-event',NULL,'ack',NULL,%s,"
                     "'ack',clock_timestamp() AT TIME ZONE 'UTC',%s,NULL)::history],"
                     "update_time=clock_timestamp() AT TIME ZONE 'UTC' WHERE id=%s",
                     (str(uuid.uuid4()), "synthetic note " + proof, payload["actor"]["login"], alert_id))


def capture_of(lab, alert_id):
    with connect_role(lab, "zabalack_owner") as owner:
        return owner.execute("SELECT capture_id,intent,state,diagnostic_reason FROM zabalack.captures "
                             "WHERE alert_id=%s ORDER BY captured_at DESC LIMIT 1", (alert_id,)).fetchone()


def project(lab, action_id):
    with connect_role(lab, "zabalack_worker") as worker:
        return worker.execute("SELECT zabalack.project(%s)", (action_id,)).fetchone()[0]["reason"]


def verify_through_worker_call(lab, payload):
    """Verify the newest capture through `worker_call`, the worker's entry point.

    `claim_verification` then `verify_complete`, as `zabalack_worker`, with the
    capture's own intent. A hand-built action row would pass with the real
    verification path broken.
    """
    lease = {"worker_id": "fence-verifier"}
    with connect_role(lab, "zabalack_worker") as worker:
        claimed = worker.execute("SELECT zabalack.worker_call('claim_verification',%s)",
                                 (Jsonb(lease),)).fetchone()[0]
        assert claimed is not None and claimed["intent"] == payload, "precondition: this capture was claimed"
        return worker.execute("SELECT zabalack.worker_call('verify_complete',%s)", (Jsonb(
            lease | {"capture_id": claimed["capture_id"], "lease_generation": claimed["lease_generation"],
                     "intent": payload, "actor_ref": payload["actor"]["login"]}),)).fetchone()[0]


def new_action(lab, alert_id, event_id, payload, *, order=None):
    """Insert the action row `verify_complete` would have produced.

    Used only where verification is not the subject and its lifetime rule would
    get in the way — an action issued days ago cannot be verified now. Cases
    about verification itself go through `verify_through_worker_call`.
    """
    action_id = payload["action_id"]
    with connect_role(lab, "zabalack_owner") as owner:
        capture_id = owner.execute(
            "INSERT INTO zabalack.captures(alert_id,customer,snapshot,intent,history_type,history_user,"
            "history_update_time,resulting_status,state) VALUES(%s,%s,%s,%s,'ack',%s,clock_timestamp(),'ack',"
            "'captured_unverified') RETURNING capture_id",
            (alert_id, CUSTOMER, Jsonb(payload["source"]), Jsonb(payload),
             payload["actor"]["login"])).fetchone()[0]
        effect_id = owner.execute(
            "INSERT INTO zabalack.effects(instance,event_id,generation,primary_action_id,latest_action_time) "
            "VALUES(%s,%s,(SELECT coalesce(max(generation),0)+1 FROM zabalack.effects WHERE instance=%s "
            "AND event_id=%s),%s,%s) RETURNING effect_id",
            (INSTANCE, event_id, INSTANCE, event_id, action_id, payload["issued_at"])).fetchone()[0]
        owner.execute("INSERT INTO zabalack.actor_identity(actor_ref,login) VALUES(%s,%s) "
                      "ON CONFLICT(actor_ref) DO NOTHING", (payload["actor"]["login"], payload["actor"]["login"]))
        owner.execute(
            "INSERT INTO zabalack.actions(action_id,capture_id,capture_order,effect_id,alert_id,customer,"
            "instance,event_id,trigger_id,actor_ref,cohort_revision,policy_version,issued_at,execute_before,"
            "state) VALUES(%s,%s,coalesce(%s,(SELECT capture_order FROM zabalack.captures WHERE capture_id=%s)),"
            "%s,%s,%s,%s,%s,%s,%s,%s,'1',%s,%s,'ok')",
            (action_id, capture_id, order, capture_id, effect_id, alert_id, CUSTOMER, INSTANCE, event_id,
             TRIGGER, payload["actor"]["login"], payload["cohort_revision"], payload["issued_at"],
             payload["execution_expires_at"]))
    return action_id


# --- the protected staging operation -----------------------------------------

def test_T41_staging_records_the_generation_and_the_reserved_keys_in_one_statement(lab):
    alert_id, _, payload = stage(lab, extra={"zabbix_ack_sync_local_at": "2026-01-01T00:00:00Z"})
    rows = fence(lab, alert_id)
    assert [(row[0], row[1]) for row in rows] == [(payload["action_id"], "staging")]
    after = attributes_of(lab, alert_id)
    assert after[INTENT] == payload and after[ACTION_KEY] == payload["action_id"]
    assert after["zabbix_ack_sync"] == "pending" and after["zabbix_ack_sync_reason"] == "awaiting_capture"
    assert "zabbix_ack_sync_local_at" not in after, "the local-only marker is retired by a new staging"
    assert after["runbook_url"] == "https://runbook.invalid/fence-lab", "unrelated attributes survive"
    with connect_role(lab, "zabalack_owner") as owner:
        audited = owner.execute("SELECT details->>'action_id' FROM zabalack.audit WHERE operation='stage_generation' "
                                "AND object_ref=%s", (alert_id,)).fetchall()
    assert audited == [(payload["action_id"],)]


@pytest.mark.parametrize("case", ["action_id", "alert_id", "customer", "shape"])
def test_T41_a_staging_that_does_not_bind_is_refused_and_stages_nothing(lab, case):
    alert_id, event_id, payload = stage(lab, staged=False)
    before = attributes_of(lab, alert_id)
    bad = dict(payload)
    if case == "action_id":
        bad["action_id"] = str(uuid.uuid4())
    elif case == "alert_id":
        bad["alert_id"] = str(uuid.uuid4())
    elif case == "customer":
        bad["customer"] = UNDISCLOSED
    else:
        bad = ["not", "an", "object"]
    with connect_role(lab, "alerta_app") as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("SELECT zabalack.stage_generation(%s,%s,%s)", (alert_id, payload["action_id"], Jsonb(bad)))
    assert fence(lab, alert_id) == [], "a refused staging records no generation"
    assert attributes_of(lab, alert_id) == before, "a refused staging writes nothing"


def test_T41_a_staging_for_a_missing_alert_is_refused(lab):
    payload = intent_for(str(uuid.uuid4()), "990000")
    with connect_role(lab, "alerta_app") as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="staging target missing"):
            conn.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                         (payload["alert_id"], payload["action_id"], Jsonb(payload)))
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT count(*) FROM zabalack.alert_generations WHERE action_id=%s",
                             (payload["action_id"],)).fetchone()[0] == 0


def test_T41_a_replayed_action_id_is_refused_not_restaged(lab):
    """The signer issues a fresh id per staging; an existing one is a replay."""
    alert_id, _, payload = stage(lab)
    assert sweep(lab)["retired"] >= 1 and INTENT not in attributes_of(lab, alert_id)
    with connect_role(lab, "alerta_app") as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="generation already recorded"):
            conn.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                         (alert_id, payload["action_id"], Jsonb(payload)))
    assert INTENT not in attributes_of(lab, alert_id), "a replay must not restore the retired intent"
    assert len(fence(lab, alert_id)) == 1


def test_T41_only_the_application_role_may_stage_and_it_may_do_nothing_else(lab):
    alert_id, _, payload = stage(lab, staged=False)
    for role in ("zabalack_worker", "zabalack_probe", "zabalack_operator", "alerta_owner"):
        with connect_role(lab, role) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                             (alert_id, payload["action_id"], Jsonb(payload)))
    assert fence(lab, alert_id) == []
    with connect_role(lab, "alerta_app") as app:
        for statement in ("SELECT count(*) FROM zabalack.alert_generations",
                          "INSERT INTO zabalack.alert_generations(alert_id,action_id,recorded_by) "
                          "VALUES('x',gen_random_uuid(),'staging')",
                          "DELETE FROM zabalack.alert_generations",
                          "SELECT zabalack.newest_generation('x')",
                          "SELECT zabalack.project(gen_random_uuid())",
                          "SELECT zabalack.worker_call('doctor','{}')",
                          "SELECT zabalack.retire_intents(1,0)",
                          "SELECT zabalack.claim_retirement_slot()",
                          "SELECT zabalack.capture_health()"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(statement)
        app.execute("SELECT zabalack.stage_generation(%s,%s,%s)", (alert_id, payload["action_id"], Jsonb(payload)))
    assert [row[1] for row in fence(lab, alert_id)] == ["staging"]


def test_T41_the_staging_entry_point_is_hardened_and_nothing_in_the_schema_is_public(lab):
    """The grant of USAGE on the schema to the application role rests on this."""
    with connect_role(lab, "zabalack_owner") as owner:
        definer, config = owner.execute(
            "SELECT prosecdef,proconfig FROM pg_proc WHERE oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure"
        ).fetchone()
        assert definer is True
        assert any(item.startswith("search_path=") and "pg_catalog" in item and "zabalack" in item
                   for item in (config or [])), config
        public_executable = owner.execute(
            "SELECT p.proname FROM pg_proc p WHERE p.pronamespace='zabalack'::regnamespace "
            "AND (p.proacl IS NULL OR EXISTS(SELECT 1 FROM aclexplode(p.proacl) e WHERE e.grantee=0))").fetchall()
        assert public_executable == []
        grantees = owner.execute(
            "SELECT e.grantee::regrole::text FROM pg_class c CROSS JOIN LATERAL "
            "aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) e "
            "WHERE c.oid='zabalack.alert_generations'::regclass AND e.grantee<>c.relowner").fetchall()
        assert grantees == []
    with connect_role(lab, "zabalack_probe") as probe:
        health = probe.execute("SELECT zabalack.capture_health()").fetchone()[0]
    assert health["functions_ok"] is True and health["zabalack_schema_version"] == 4


# --- the sweep retires, and preserves what it must ---------------------------

def test_T41_sweep_retires_the_intent_and_keeps_the_reader_visible_action_id(lab):
    alert_id, _, payload = stage(lab)
    assert INTENT in attributes_of(lab, alert_id), "precondition: the intent is committed"
    assert sweep(lab)["retired"] >= 1
    after = attributes_of(lab, alert_id)
    assert INTENT not in after, "the staged intent must not survive the sweep"
    assert after[ACTION_KEY] == payload["action_id"]


def test_T41_sweep_leaves_unrelated_attributes_and_source_identity_intact(lab):
    alert_id, event_id, _ = stage(lab)
    sweep(lab)
    after = attributes_of(lab, alert_id)
    assert after["runbook_url"] == "https://runbook.invalid/fence-lab"
    assert after["source_system"] == "zabbix"
    assert (after["zabbix_instance"], after["zabbix_event_id"], after["zabbix_trigger_id"]) == \
        (INSTANCE, event_id, TRIGGER)
    assert after["zabbix_ack_sync"] == "pending"


def test_T41_sweep_preserves_restricted_capture_evidence_byte_for_byte(lab):
    """`zabalack.captures.intent` is signed evidence and is never swept."""
    alert_id, _, payload = stage(lab)
    fire_ack(lab, alert_id, payload)
    before = capture_of(lab, alert_id)
    assert before is not None and before[1] == payload, "precondition: capture recorded the intent"
    sweep(lab)
    assert capture_of(lab, alert_id)[1] == before[1], "restricted capture evidence must survive retirement"
    assert INTENT not in attributes_of(lab, alert_id)


def test_T41_the_sweep_never_touches_the_fence(lab):
    alert_id, event_id, _ = stage(lab)
    stage_over(lab, alert_id, event_id, issued=datetime.now(timezone.utc) - timedelta(minutes=5))
    before = fence(lab, alert_id)
    assert len(before) == 2
    for _ in range(3):
        sweep(lab)
    assert fence(lab, alert_id) == before


# --- lifecycle coverage, one case per row of A5's exposure table -------------
# Each state is reached without verification or delivery ever succeeding, and
# the sweep must retire the attribute in every one of them.

def test_T41_row1_staging_that_failed_before_the_history_write(lab):
    alert_id, _, _ = stage(lab)
    assert capture_of(lab, alert_id) is None, "precondition: no capture; the trigger never fired"
    assert sweep(lab)["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id)


def test_T41_row2_cohort_enabled_in_configuration_but_absent_from_the_database(lab):
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.cohorts SET enabled=false WHERE customer=%s", (CUSTOMER,))
    alert_id, _, payload = stage(lab)
    fire_ack(lab, alert_id, payload)
    assert capture_of(lab, alert_id) is None, "precondition: capture_ack returned at its eligibility check"
    assert sweep(lab)["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id)


def test_T41_row3_capture_side_evidence_failure(lab):
    alert_id, _, payload = stage(lab)
    fire_ack(lab, alert_id, payload, marker=False)
    capture = capture_of(lab, alert_id)
    assert capture is None or capture[3] is not None, "precondition: no clean capture"
    assert sweep(lab)["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id)


def test_T41_row4_capture_bypass(lab):
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET capture_disabled=true WHERE singleton")
    try:
        alert_id, _, payload = stage(lab)
        fire_ack(lab, alert_id, payload)
        assert capture_of(lab, alert_id)[3] == "skipped_capture_bypass", "precondition: bypassed"
        assert sweep(lab)["retired"] >= 1
        assert INTENT not in attributes_of(lab, alert_id)
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("UPDATE zabalack.installation SET capture_disabled=false WHERE singleton")


def test_T41_row5_worker_side_reject_capture(lab):
    alert_id, _, payload = stage(lab)
    fire_ack(lab, alert_id, payload)
    capture_id = capture_of(lab, alert_id)[0]
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.captures SET state='failed',diagnostic_reason='failed_intent_invalid' "
                      "WHERE capture_id=%s", (capture_id,))
        owner.execute("UPDATE zabalack.outbox SET state='failed' WHERE capture_id=%s", (capture_id,))
        assert owner.execute("SELECT count(*) FROM zabalack.actions WHERE alert_id=%s",
                             (alert_id,)).fetchone()[0] == 0, "precondition: no action row exists"
    assert sweep(lab)["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id)


def test_T41_row6_verifier_hold_with_the_verification_key_unavailable(lab):
    """A hold is re-claimable and unbounded, so retirement cannot wait on it."""
    alert_id, _, payload = stage(lab)
    fire_ack(lab, alert_id, payload)
    capture_id = capture_of(lab, alert_id)[0]
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.captures SET state='verifier_hold',"
                      "diagnostic_reason='verifier_key_unavailable' WHERE capture_id=%s", (capture_id,))
        owner.execute("UPDATE zabalack.outbox SET state='verifier_hold' WHERE capture_id=%s", (capture_id,))
    assert sweep(lab)["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id)
    with connect_role(lab, "zabalack_owner") as owner:
        held = owner.execute("SELECT state,intent FROM zabalack.captures WHERE capture_id=%s",
                             (capture_id,)).fetchone()
    assert held[0] == "verifier_hold" and held[1] == payload, \
        "the held capture stays claimable with its intent intact once keys return"


def test_T41_row7_worker_stopped_or_backlogged_with_the_capture_unclaimed(lab):
    alert_id, _, payload = stage(lab)
    fire_ack(lab, alert_id, payload)
    capture_id = capture_of(lab, alert_id)[0]
    with connect_role(lab, "zabalack_owner") as owner:
        unclaimed = owner.execute("SELECT count(*) FROM zabalack.verification_leases WHERE capture_id=%s",
                                  (capture_id,)).fetchone()[0]
    assert unclaimed == 0, "precondition: nothing has claimed this capture"
    assert sweep(lab)["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id)
    assert capture_of(lab, alert_id)[1] == payload, "a backlogged capture stays processable afterwards"


def test_T41_row8_verify_complete_reaches_projection(lab):
    """The one row that does reach `project()`; retirement there is additional."""
    alert_id, _, payload = stage(lab)
    fire_ack(lab, alert_id, payload)
    assert capture_of(lab, alert_id)[3] is None, "precondition: a clean capture"
    assert sweep(lab)["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id)


def test_T41_a_staging_still_inside_its_window_is_not_swept(lab):
    """Eligibility is `capture_expires_at` plus a grace margin, and nothing else.

    `capture_ack()` performs no lifetime check of its own — it records a capture
    however late, and `verify_complete` is what rejects an out-of-window
    `captured_at`. Sweeping at the instant of expiry would therefore race a
    capture still in flight and turn a verification-time rejection into
    `intent_missing`. The margin removes the race.
    """
    fresh, _, _ = stage(lab, issued=datetime.now(timezone.utc))
    assert INTENT in attributes_of(lab, fresh)
    sweep(lab, grace=0)
    assert INTENT in attributes_of(lab, fresh), "a staging inside its window must survive"
    just_expired, _, _ = stage(lab, issued=datetime.now(timezone.utc) - timedelta(seconds=90))
    sweep(lab, grace=300)
    assert INTENT in attributes_of(lab, just_expired), "the grace margin defers a capture still in flight"
    sweep(lab, grace=0)
    assert INTENT not in attributes_of(lab, just_expired)


def test_T41_an_unvalidatable_intent_is_retired_now_not_deferred(lab):
    """The attribute is API-writable; a forged window cannot exempt itself."""
    alert_id, _, payload = stage(lab, staged=False)
    forged = dict(payload, issued_at=payload["issued_at"], capture_expires_at="infinity")
    stage_pre_fence(lab, alert_id, forged)
    assert sweep(lab, grace=300)["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id)
    assert fence(lab, alert_id) == [], "the sweep records no generation for what it retires"


def test_T41_sweep_is_bounded(lab):
    """The limit bounds how many rows a pass examines, not how many it retires.

    The scan has no ORDER BY, so a row still inside its window that precedes
    this module's rows in heap order is examined on every pass and would starve
    a limit-2 pass until its window closes. That is the sweep's real behaviour,
    bounded by the window plus grace; the test must not depend on what other
    modules left on the table, so the limit is widened by their live rows.
    """
    with connect_role(lab, "alerta_owner") as owner:
        foreign = owner.execute("SELECT count(*) FROM alerts WHERE customer<>%s AND attributes ? %s",
                                (CUSTOMER, INTENT)).fetchone()[0]
    for _ in range(4):
        stage(lab)
    limit = 2 + foreign
    bounded = sweep(lab, limit=limit)
    assert bounded["examined"] == limit, "a pass must not examine more rows than its limit"
    assert bounded["retired"] <= bounded["examined"]
    for _ in range(8):
        sweep(lab, limit=limit)
    with connect_role(lab, "alerta_owner") as owner:
        remaining = owner.execute(
            "SELECT count(*) FROM alerts WHERE customer=%s AND attributes ? %s AND "
            "(attributes->%s->>'capture_expires_at')::timestamptz<clock_timestamp()",
            (CUSTOMER, INTENT, INTENT)).fetchone()[0]
    assert remaining == 0, "repeated bounded passes converge"


# --- the generation guard ----------------------------------------------------

def test_T41_projection_succeeds_for_the_current_generation(lab):
    """A positive control, through the real verification path."""
    alert_id, _, payload = stage(lab, issued=datetime.now(timezone.utc))
    fire_ack(lab, alert_id, payload)
    verified = verify_through_worker_call(lab, payload)
    assert verified["action_id"] == payload["action_id"]
    assert project(lab, payload["action_id"]) == "projected"
    after = attributes_of(lab, alert_id)
    assert after["zabbix_ack_sync"] in ("ok", "pending") and after[ACTION_KEY] == payload["action_id"]
    assert INTENT not in after, "projection retires the intent on the path that reaches it"
    assert [(row[0], row[1]) for row in fence(lab, alert_id)] == [(payload["action_id"], "staging")]


def test_T41_an_older_projection_is_refused_after_a_newer_staging_is_swept(lab):
    """The decisive ordering, at SQL level.

    A is verified. B is staged afterwards and never reaches a capture; the sweep
    removes B's intent. A's projection then runs with nothing on the row to say B
    ever existed — and must still be refused, because the fence says so.
    """
    alert_id, event_id, first = stage(lab, issued=datetime.now(timezone.utc))
    fire_ack(lab, alert_id, first)
    verify_through_worker_call(lab, first)
    second = stage_over(lab, alert_id, event_id, issued=datetime.now(timezone.utc) - timedelta(minutes=5))
    assert sweep(lab)["retired"] >= 1 and INTENT not in attributes_of(lab, alert_id)
    assert project(lab, first["action_id"]) == "projection_action_generation_mismatch"
    assert newest(lab, alert_id) == second["action_id"]
    after = attributes_of(lab, alert_id)
    assert after["zabbix_ack_sync"] == "pending" and after[ACTION_KEY] == second["action_id"], \
        "A's projection wrote nothing over B's state"


def test_T41_the_guard_does_not_expire(lab):
    """`projection_due_at` has no deadline; a month-old projection must still be refused."""
    long_ago = datetime.now(timezone.utc) - timedelta(days=30)
    alert_id, event_id, first = stage(lab, issued=long_ago)
    action_id = new_action(lab, alert_id, event_id, first)
    stage_over(lab, alert_id, event_id, issued=long_ago + timedelta(minutes=1))
    sweep(lab)
    assert project(lab, action_id) == "projection_action_generation_mismatch"


def test_T41_the_fence_only_appends(lab):
    alert_id, event_id, first = stage(lab)
    second = stage_over(lab, alert_id, event_id)
    before = fence(lab, alert_id)
    assert [row[0] for row in before] == [first["action_id"], second["action_id"]]
    assert before[0][2] < before[1][2], "the database assigns the order"
    sweep(lab)
    action_id = new_action(lab, alert_id, event_id, second)
    assert project(lab, action_id) == "projected"
    assert fence(lab, alert_id) == before, "neither the sweep nor projection changes the fence"


def test_T41_a_capture_records_its_generation_in_the_same_transaction(lab):
    """The fallback for stagings the protected operation never saw."""
    alert_id, _, payload = stage(lab, staged=False)
    stage_pre_fence(lab, alert_id, payload)
    assert fence(lab, alert_id) == [], "precondition: the attribute alone records nothing"
    fire_ack(lab, alert_id, payload)
    assert [(row[0], row[1]) for row in fence(lab, alert_id)] == [(payload["action_id"], "capture")]


def test_T41_a_late_capture_never_outranks_a_generation_recorded_at_staging(lab):
    """The rule the fallback must obey: observation never promotes over staging.

    B is staged through the protected operation. An older acknowledgement A',
    staged before the fence existed, is then captured — later than B, so with a
    higher sequence. A' still cannot become the newest generation, and its
    verified action is refused projection.
    """
    alert_id, event_id, newer = stage(lab, issued=datetime.now(timezone.utc))
    older = intent_for(alert_id, event_id, issued=datetime.now(timezone.utc) - timedelta(seconds=20))
    stage_pre_fence(lab, alert_id, older)
    fire_ack(lab, alert_id, older)
    rows = fence(lab, alert_id)
    assert [(row[0], row[1]) for row in rows] == [(newer["action_id"], "staging"), (older["action_id"], "capture")]
    assert rows[1][2] > rows[0][2], "precondition: the late capture has the higher sequence"
    assert newest(lab, alert_id) == newer["action_id"]
    verify_through_worker_call(lab, older)
    assert project(lab, older["action_id"]) == "projection_action_generation_mismatch"


def test_T41_erasing_the_reserved_keys_cannot_permit_an_older_projection(lab):
    alert_id, event_id, first = stage(lab, issued=datetime.now(timezone.utc))
    fire_ack(lab, alert_id, first)
    verify_through_worker_call(lab, first)
    assert project(lab, first["action_id"]) == "projected"
    second = stage_over(lab, alert_id, event_id)
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes-%s-%s WHERE id=%s", (INTENT, ACTION_KEY, alert_id))
    assert project(lab, first["action_id"]) == "projection_action_generation_mismatch"
    assert newest(lab, alert_id) == second["action_id"]


def test_T41_a_stale_attribute_dictionary_cannot_restore_an_older_generation(lab):
    """The whole-object replace `PUT /alert/<id>/attributes` performs."""
    alert_id, event_id, first = stage(lab, issued=datetime.now(timezone.utc))
    fire_ack(lab, alert_id, first)
    verify_through_worker_call(lab, first)
    assert project(lab, first["action_id"]) == "projected"
    stale = attributes_of(lab, alert_id)
    second = stage_over(lab, alert_id, event_id)
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=%s WHERE id=%s", (Jsonb(stale), alert_id))
    assert attributes_of(lab, alert_id)[ACTION_KEY] == first["action_id"], "precondition: the row says A"
    assert project(lab, first["action_id"]) == "projection_action_generation_mismatch"
    assert newest(lab, alert_id) == second["action_id"]


def test_T41_a_forged_intent_written_around_the_plugin_records_no_generation(lab):
    alert_id, event_id, current = stage(lab, issued=datetime.now(timezone.utc))
    fire_ack(lab, alert_id, current)
    verify_through_worker_call(lab, current)
    forged = intent_for(alert_id, event_id)
    stage_pre_fence(lab, alert_id, forged)
    assert [row[0] for row in fence(lab, alert_id)] == [current["action_id"]]
    assert newest(lab, alert_id) == current["action_id"]
    assert project(lab, current["action_id"]) == "projected", "legitimate current work still succeeds"


def test_T41_no_generation_recorded_refuses_rather_than_permits(lab):
    """Migration records one for every alert with an action or an intent; a row
    with none is one whose evidence never existed, and it is refused."""
    alert_id, event_id, payload = stage(lab)
    action_id = new_action(lab, alert_id, event_id, payload)
    with connect_role(lab, "zabalack_owner") as owner:
        # Lab-only: nothing shipped deletes a fence row.
        owner.execute("DELETE FROM zabalack.alert_generations WHERE alert_id=%s", (alert_id,))
    assert project(lab, action_id) == "projection_generation_unknown"


def test_T41_a_projection_retires_the_intent_and_keeps_the_action_key(lab):
    alert_id, _, payload = stage(lab, issued=datetime.now(timezone.utc))
    fire_ack(lab, alert_id, payload)
    verify_through_worker_call(lab, payload)
    assert project(lab, payload["action_id"]) == "projected"
    after = attributes_of(lab, alert_id)
    assert INTENT not in after and after[ACTION_KEY] == payload["action_id"]
    assert after["zabbix_ack_sync_generation"] is not None


# --- coordination ---------------------------------------------------------------

def test_T41_the_worker_reaches_retirement_through_worker_call(lab):
    alert_id, _, _ = stage(lab)
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET retirement_due_at=NULL WHERE singleton")
    with connect_role(lab, "zabalack_worker") as worker:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            worker.execute("SELECT zabalack.retire_intents(1000,0)")
        result = worker.execute("SELECT zabalack.worker_call('retire_intents',%s)",
                                (Jsonb({"limit": 1000, "grace_seconds": 0}),)).fetchone()[0]
    assert result["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id)


def test_T41_the_retirement_cadence_is_deployment_state_not_a_worker_clock(lab):
    """One scan per interval across the deployment, not one per worker."""
    stage(lab)
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET retirement_due_at=NULL,"
                      "retirement_interval_seconds=900 WHERE singleton")

    def worker_sweep():
        with connect_role(lab, "zabalack_worker") as worker:
            return worker.execute("SELECT zabalack.worker_call('retire_intents',%s)",
                                  (Jsonb({"limit": 1000, "grace_seconds": 0}),)).fetchone()[0]

    first = worker_sweep()
    assert "skipped" not in first, "the first caller in an interval does the work"
    for _ in range(3):
        assert worker_sweep() == {"examined": 0, "retired": 0, "skipped": "not_due"}
    with connect_role(lab, "zabalack_owner") as owner:
        due = owner.execute("SELECT retirement_due_at>clock_timestamp() FROM zabalack.installation "
                            "WHERE singleton").fetchone()[0]
    assert due is True, "the claim moved the slot forward by the configured interval"
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET retirement_due_at=clock_timestamp()"
                      "-interval '1 second' WHERE singleton")
    assert "skipped" not in worker_sweep(), "the slot reopens once the interval has elapsed"


# --- installation ---------------------------------------------------------------

def test_T41_the_readiness_manifest_must_be_resealed_after_the_migration(lab):
    """`functions_md5` and `structures_md5` move with 008 and the grant; a
    deployment that applies them without re-sealing reports `functions_ok`
    false and its workers refuse to run — the intended failure."""
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    with connect_role(lab, "zabalack_owner") as owner:
        original = owner.execute("SELECT functions_md5 FROM zabalack.readiness_manifest").fetchone()[0]
        owner.execute("UPDATE zabalack.readiness_manifest SET functions_md5='pre-008-seal' WHERE singleton")
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "false", \
            "a stale seal must report unhealthy rather than pass"
        owner.execute(reseal)
        assert owner.execute("SELECT functions_md5 FROM zabalack.readiness_manifest").fetchone()[0] == original
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


def test_T41_the_reseal_refuses_a_tree_that_is_not_at_the_expected_schema(lab):
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    with connect_role(lab, "zabalack_owner") as owner:
        installed = owner.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0]
        owner.execute("UPDATE zabalack.installation SET schema_version=%s WHERE singleton", (installed - 1,))
    try:
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="expected zabalack schema version"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("UPDATE zabalack.installation SET schema_version=%s WHERE singleton", (installed,))
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0] == installed


def test_T41_the_reseal_and_the_grant_run_only_as_the_schema_owner(lab):
    for script in ("sql/install/12_application_grant.sql", "sql/install/13_reseal.sql"):
        text = (ROOT / script).read_text().replace(':"alert_app_role"', '"alerta_app"')
        with connect_role(lab, "zabalack_worker") as worker:
            with pytest.raises(Exception, match="run as zabalack_owner|permission denied"):
                worker.execute(text)


def test_T41_the_application_grant_is_exactly_one_function(lab):
    with connect_role(lab, "zabalack_owner") as owner:
        granted = owner.execute(
            "SELECT p.proname FROM pg_proc p WHERE p.pronamespace='zabalack'::regnamespace "
            "AND has_function_privilege('alerta_app',p.oid,'EXECUTE') ORDER BY 1").fetchall()
        tables = owner.execute(
            "SELECT c.relname FROM pg_class c WHERE c.relnamespace='zabalack'::regnamespace AND c.relkind='r' "
            "AND (has_table_privilege('alerta_app',c.oid,'SELECT') OR has_table_privilege('alerta_app',c.oid,'INSERT') "
            "OR has_table_privilege('alerta_app',c.oid,'UPDATE') OR has_table_privilege('alerta_app',c.oid,'DELETE'))"
        ).fetchall()
    assert granted == [("stage_generation",)]
    assert tables == []


def test_T41_published_migrations_keep_their_registered_digests(lab):
    """A shipped migration is immutable; a correction is a new file."""
    manifest = json.loads((ROOT / "docs/sql_manifest.json").read_text())
    registered = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    migrations = sorted(path for path in registered if path.startswith("sql/migrations/"))
    assert "sql/migrations/008_generation_fence.sql" in migrations
    for path in migrations:
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == registered[path], \
            f"{path} no longer matches its registered digest; ship a forward migration instead"
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0] == 4
