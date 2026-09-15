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


def fire_ack(lab, alert_id, payload, *, marker=True, proof=None):
    """Commit the acknowledgement history entry, firing `zabalack_capture`.

    `proof` overrides the marker text: a malformed one still fires the trigger
    (the reserved prefix is present) and captures as `marker_malformed`.
    """
    from zabalack.crypto import sign_intent
    if proof is None:
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


def claim_through_worker_call(lab, payload):
    """Claim this capture through `worker_call('claim_verification')`, as `zabalack_worker`.

    Captures other cases left due are postponed, not touched, and this one is
    made due now (a held capture is due a minute after its hold; the lab does
    not wait), so the claim — which takes the oldest (every fourth turn, the
    newest) due row across the outbox — can only return this one.
    """
    with connect_role(lab, "zabalack_owner") as owner:
        capture_id = owner.execute("SELECT capture_id FROM zabalack.captures WHERE alert_id=%s AND intent=%s "
                                   "ORDER BY captured_at DESC LIMIT 1", (payload["alert_id"], Jsonb(payload))).fetchone()[0]
        owner.execute("UPDATE zabalack.outbox SET due_at=clock_timestamp()+interval '1 hour' WHERE capture_id<>%s "
                      "AND state IN ('captured_unverified','verifier_hold') AND due_at<=clock_timestamp()", (capture_id,))
        owner.execute("UPDATE zabalack.outbox SET due_at=least(due_at,clock_timestamp()) WHERE capture_id=%s", (capture_id,))
    # A fresh verifier identity per call: one holds at most four live leases.
    lease = {"worker_id": "fence-verifier-" + uuid.uuid4().hex[:8]}
    with connect_role(lab, "zabalack_worker") as worker:
        claimed = worker.execute("SELECT zabalack.worker_call('claim_verification',%s)",
                                 (Jsonb(lease),)).fetchone()[0]
    assert claimed is not None and claimed["capture_id"] == str(capture_id) and claimed["intent"] == payload, \
        "precondition: this capture was claimed"
    return lease, claimed


def verify_through_worker_call(lab, payload):
    """Verify this capture through `worker_call`, the worker's entry point.

    `claim_verification` then `verify_complete`, as `zabalack_worker`, with the
    capture's own intent. A hand-built action row would pass with the real
    verification path broken.
    """
    lease, claimed = claim_through_worker_call(lab, payload)
    with connect_role(lab, "zabalack_worker") as worker:
        return worker.execute("SELECT zabalack.worker_call('verify_complete',%s)", (Jsonb(
            lease | {"capture_id": claimed["capture_id"], "lease_generation": claimed["lease_generation"],
                     "intent": payload, "actor_ref": payload["actor"]["login"]}),)).fetchone()[0]


def settle_through_worker_call(lab, payload, operation, reason):
    """`hold_capture` or `reject_capture` on this capture, through `worker_call`."""
    lease, claimed = claim_through_worker_call(lab, payload)
    with connect_role(lab, "zabalack_worker") as worker:
        return worker.execute("SELECT zabalack.worker_call(%s,%s)", (operation, Jsonb(
            lease | {"capture_id": claimed["capture_id"], "lease_generation": claimed["lease_generation"],
                     "reason": reason}),)).fetchone()[0]


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


def test_T41_a_bounded_pass_spends_its_batch_only_on_rows_that_are_due(lab):
    """A mixed table: more stagings still inside their window than one batch
    holds, and rows that are due behind them. The batch is chosen from the rows
    that are due, so a staging inside its window takes no place in it and the
    due rows are retired at the batch's rate whatever precedes them in heap
    order; the batch still bounds what a pass examines, and repeated passes
    converge. Rows other modules left inside their windows are ignored the
    same way, so the case does not depend on what they left on the table."""
    sweep(lab)   # rows other modules left due are retired first; rows inside their windows stay
    fresh = [stage(lab, issued=datetime.now(timezone.utc))[0] for _ in range(3)]
    due = [stage(lab)[0] for _ in range(4)]
    first = sweep(lab, limit=2)
    assert first["examined"] == 2 and first["retired"] == 2, first
    assert all(INTENT in attributes_of(lab, alert_id) for alert_id in fresh), "a row inside its window takes no place"
    passes = [first]
    while any(INTENT in attributes_of(lab, alert_id) for alert_id in due):
        passes.append(sweep(lab, limit=2))
        assert passes[-1]["retired"] >= 1, "every pass with due rows left makes progress"
        assert len(passes) <= 8, passes
    assert all(INTENT in attributes_of(lab, alert_id) for alert_id in fresh)
    # Nothing of this case is due any more. A pass may still find a row another
    # module staged that came due meanwhile; what it must not do is spend a
    # place on a row it then leaves inside its window.
    after = sweep(lab, limit=2)
    assert after["examined"] == after["retired"], "every row a pass examines is one it retires"
    assert all(INTENT in attributes_of(lab, alert_id) for alert_id in fresh)


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


def test_T41_a_pass_counts_what_still_carries_the_key_when_the_drain_asks(lab):
    """The standalone drain asks (`report_remaining`): once a pass leaves
    nothing due behind it, the reply carries the rows still carrying the key —
    a staging inside its window and grace, left to a later pass by design — so
    a cleanup is complete only at zero. The worker's periodic call does not
    ask and gets no count. The count is compared with an independent one,
    since rows other modules left inside their windows count too."""
    sweep(lab)   # rows other modules left due are retired first
    fresh_id, event_id, fresh = stage(lab, issued=datetime.now(timezone.utc))
    due_id, _, _ = stage(lab)

    def pass_through_worker_call(payload):
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("UPDATE zabalack.installation SET retirement_due_at=NULL,retirement_interval_seconds=900 "
                          "WHERE singleton")
        with connect_role(lab, "zabalack_worker") as worker:
            return worker.execute("SELECT zabalack.worker_call('retire_intents',%s)", (Jsonb(payload),)).fetchone()[0]

    def carrying_the_key():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT count(*) FROM public.alerts WHERE attributes ? %s", (INTENT,)).fetchone()[0]

    def slot_due_in():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT extract(epoch FROM retirement_due_at-clock_timestamp()) "
                                 "FROM zabalack.installation WHERE singleton").fetchone()[0]

    asked = pass_through_worker_call({"limit": 1000, "grace_seconds": 0, "report_remaining": True})
    assert asked["retired"] >= 1 and "continues" not in asked, asked
    assert INTENT not in attributes_of(lab, due_id) and INTENT in attributes_of(lab, fresh_id)
    assert asked["remaining"] == carrying_the_key() >= 1, asked
    # The retry named is the latest any counted row can be due — its own
    # window's end plus the grace, read from the rows — and the slot is brought
    # forward to no later than it, not left at the interval, so the retry the
    # rollback documents is not refused as not due and finds those rows due.
    # A bound from the clock alone would name a later time than the rows need.
    with connect_role(lab, "zabalack_owner") as owner:
        latest_due = owner.execute("SELECT max((attributes->%s->>'capture_expires_at')::timestamptz) "
                                   "FROM public.alerts WHERE attributes ? %s", (INTENT, INTENT)).fetchone()[0]
        named = owner.execute("SELECT %s::timestamptz", (asked["retry_at"],)).fetchone()[0]
        slot = owner.execute("SELECT retirement_due_at FROM zabalack.installation WHERE singleton").fetchone()[0]
    assert named == latest_due and slot <= named, (named, latest_due, slot)
    assert 0 < slot_due_in() <= 60, slot_due_in()
    unasked = pass_through_worker_call({"limit": 1000, "grace_seconds": 0})
    assert "remaining" not in unasked and "retry_at" not in unasked, unasked
    assert slot_due_in() > 600, "the periodic call keeps its interval"
    # Its window closed, the same row is retired and no longer counted.
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: intent_for(fresh_id, event_id, action_id=fresh["action_id"],
                                                issued=datetime.now(timezone.utc) - timedelta(minutes=10))}), fresh_id))
    closed = pass_through_worker_call({"limit": 1000, "grace_seconds": 0, "report_remaining": True})
    assert closed["retired"] >= 1 and INTENT not in attributes_of(lab, fresh_id)
    assert closed["remaining"] == carrying_the_key(), closed


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
        assert worker_sweep() == {"examined": 0, "retired": 0, "deferred": 0, "skipped": "not_due"}
    with connect_role(lab, "zabalack_owner") as owner:
        due = owner.execute("SELECT retirement_due_at>clock_timestamp() FROM zabalack.installation "
                            "WHERE singleton").fetchone()[0]
    assert due is True, "the claim moved the slot forward by the configured interval"
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET retirement_due_at=clock_timestamp()"
                      "-interval '1 second' WHERE singleton")
    assert "skipped" not in worker_sweep(), "the slot reopens once the interval has elapsed"


def test_T41_a_full_batch_keeps_the_slot_due_until_the_backlog_drains(lab):
    """More eligible intents than one batch. A batch that reached its limit
    leaves the slot due, so the next call continues at the fast cadence; only
    a batch below the limit closes the slot for the interval. Without this a
    burst waited out one interval per batch, and a sustained rate above the
    batch size per interval grew the backlog without bound."""
    alerts = [stage(lab)[0] for _ in range(3)]
    limit = 2   # rows other modules left inside their windows take no place in the batch
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET retirement_due_at=NULL,retirement_interval_seconds=900 "
                      "WHERE singleton")

    def worker_sweep():
        with connect_role(lab, "zabalack_worker") as worker:
            return worker.execute("SELECT zabalack.worker_call('retire_intents',%s)",
                                  (Jsonb({"limit": limit, "grace_seconds": 0}),)).fetchone()[0]

    passes = [worker_sweep()]
    assert passes[0]["examined"] == limit and passes[0].get("continues") is True, passes[0]
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT retirement_due_at<=clock_timestamp() FROM zabalack.installation "
                             "WHERE singleton").fetchone()[0] is True, "a full batch leaves the slot due"
    while passes[-1].get("continues"):
        passes.append(worker_sweep())
        assert len(passes) <= 8, passes
    assert "skipped" not in passes[-1] and passes[-1]["examined"] < limit
    assert all(INTENT not in attributes_of(lab, alert_id) for alert_id in alerts), "the backlog drained"
    assert worker_sweep() == {"examined": 0, "retired": 0, "deferred": 0, "skipped": "not_due"}, \
        "a batch below the limit closes the slot for the interval"


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


@pytest.mark.parametrize("grant,revoke,what", [
    ("GRANT EXECUTE ON FUNCTION zabalack.newest_generation(text) TO PUBLIC",
     "REVOKE EXECUTE ON FUNCTION zabalack.newest_generation(text) FROM PUBLIC", "a PUBLIC-executable function"),
    ("GRANT SELECT ON zabalack.alert_generations TO zabalack_worker",
     "REVOKE SELECT ON zabalack.alert_generations FROM zabalack_worker", "a grantee on the fence"),
    ("GRANT EXECUTE ON FUNCTION zabalack.stage_generation(text,uuid,jsonb) TO zabalack_operator",
     "REVOKE EXECUTE ON FUNCTION zabalack.stage_generation(text,uuid,jsonb) FROM zabalack_operator",
     "a second grantee on the staging entry point"),
    ("REVOKE EXECUTE ON FUNCTION zabalack.stage_generation(text,uuid,jsonb) FROM alerta_app",
     "GRANT EXECUTE ON FUNCTION zabalack.stage_generation(text,uuid,jsonb) TO alerta_app",
     "no grantee at all on the staging entry point: every acknowledgement would fail behind a passing probe"),
    ("REVOKE USAGE ON SCHEMA zabalack FROM alerta_app",
     "GRANT USAGE ON SCHEMA zabalack TO alerta_app",
     "the recorded role without USAGE on the schema: its qualified call would fail at schema access behind a passing probe"),
])
def test_T41_a_seal_over_a_public_function_or_a_fence_grantee_stays_unhealthy(lab, grant, revoke, what):
    """`catalog_fingerprints()` hashes ACLs, so a stray grant flips `functions_ok`
    through the digests alone — until someone seals over it. The two conjuncts
    008 adds refuse that tree even under a matching seal, and `13_reseal.sql`
    refuses to produce such a seal at all. Deleting either conjunct makes the
    sealed-over case report healthy."""
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute(grant)
    try:
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "false", \
                "the digests alone catch a stray grant"
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
        with connect_role(lab, "zabalack_owner") as owner:
            # Lab-only: seal over the stray grant, as a principal holding
            # seal_installation() could. The digests now agree with it.
            owner.execute("SELECT zabalack.seal_installation()")
            assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "false", \
                what + " is unhealthy under a matching seal"
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(revoke)
            owner.execute(reseal)
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


def test_T41_a_member_of_the_recorded_application_role_is_unhealthy(lab):
    """A membership is not an ACL, and outside the four zabalack roles it is not
    hashed either, so nothing the manifest seals moves with it.

    Granting the recorded application role to another role makes that role an
    executor of `stage_generation()` — it inherits the privilege, or, `NOINHERIT`
    as every role this lab creates is, reaches it with `SET ROLE` — while
    `aclexplode()` still reports exactly one non-owner grantee. The member here
    is `zabalack_installer`, outside the set whose memberships
    `catalog_fingerprints()` hashes, so both sealed digests still match, exactly
    as they would for any login an operator grants the role to; under `NOINHERIT`
    not even `has_function_privilege()` resolves the privilege, so the
    membership itself is the only record of the second credential.
    `capture_health()` reports `functions_ok` false for it and `13_reseal.sql`
    refuses to seal it; the grant script refuses to record a role that already
    has members, having no `ADMIN OPTION` to revoke one with.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    grant = (ROOT / "sql/install/12_application_grant.sql").read_text()

    def direct_grantees():
        with connect_role(lab, "zabalack_owner") as owner:
            return [row[0] for row in owner.execute(
                "SELECT pg_get_userbyid(e.grantee) FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) e "
                "WHERE p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure "
                "AND e.grantee<>p.proowner ORDER BY 1").fetchall()]

    def assumes_the_role(role):
        """`SET ROLE alerta_app` is open to it: it executes whatever that role can."""
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT pg_has_role(%s,'alerta_app','MEMBER')", (role,)).fetchone()[0]

    def resolves_execute(role):
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT has_function_privilege(%s,"
                                 "'zabalack.stage_generation(text,uuid,jsonb)','EXECUTE')", (role,)).fetchone()[0]

    with connect_role(lab, "zabalack_owner") as owner:
        sealed = owner.execute("SELECT functions_md5,structures_md5 FROM zabalack.readiness_manifest "
                               "WHERE singleton").fetchone()
    assert direct_grantees() == ["alerta_app"] and assumes_the_role("zabalack_installer") is False, "precondition"

    # Lab-only: the membership is granted by the role's administrator. It is
    # not zabalack_owner's to grant — nor, for the same reason, to revoke,
    # which is why 12_application_grant.sql refuses rather than revoking it.
    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute("GRANT alerta_app TO zabalack_installer")
    try:
        assert assumes_the_role("zabalack_installer") is True, "the member can assume the recorded role"
        assert resolves_execute("zabalack_installer") is False, \
            "and a NOINHERIT member does not even resolve the privilege: only the membership shows it"
        assert direct_grantees() == ["alerta_app"], "the ACL still names one grantee"
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT functions_md5,structures_md5 FROM zabalack.readiness_manifest "
                                 "WHERE singleton").fetchone() == sealed, \
                "both digests still match: this membership is neither an ACL nor a hashed one"
            assert owner.execute("SELECT (zabalack.catalog_fingerprints())->>'structures'="
                                 "structures_md5 FROM zabalack.readiness_manifest "
                                 "WHERE singleton").fetchone()[0] is True, \
                "the tree still fingerprints to what was sealed"
            assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "false", \
                "a member of the recorded role is unhealthy under a matching seal"
        # A failed script leaves its transaction aborted: one per connection.
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="revoke those memberships first"):
                owner.execute(grant.replace(':"alert_app_role"', '"alerta_app"'))
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute("REVOKE alerta_app FROM zabalack_installer")
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"
        # The refusal was the membership alone: the same grant runs once it is gone.
        owner.execute(grant.replace(':"alert_app_role"', '"alerta_app"'))
        owner.execute(reseal)
    with connect_role(lab, "zabalack_owner") as owner:
        assert direct_grantees() == ["alerta_app"]
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


def test_T41_an_application_role_that_can_assume_a_zabalack_role_is_unhealthy(lab):
    """The membership that matters in the other direction: what the application
    role can act as.

    `zabalack_worker` carries `EXECUTE` on `worker_call(text,jsonb)`, whose
    operations include `verify_complete`, `hold_capture` and `reject_capture`.
    An application role granted that role reaches them — inheriting, or with
    `SET ROLE` — and marks captures verified without the Python verifier's
    signature check, while every ACL in the schema, and so both sealed digests,
    stay exactly as they were: `catalog_fingerprints()` hashes memberships only
    where the *member* is one of the four zabalack roles, and here the member is
    the application role. `capture_health()` refuses it, `13_reseal.sql` refuses
    to seal it, and the grant script refuses to record such a role.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    grant = (ROOT / "sql/install/12_application_grant.sql").read_text()

    def reads(query, *params):
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute(query, params).fetchone()[0]

    sealed = reads("SELECT (functions_md5,structures_md5)::text FROM zabalack.readiness_manifest WHERE singleton")
    assert reads("SELECT pg_has_role('alerta_app','zabalack_worker','MEMBER')") is False, "precondition"

    # Lab-only: granting a zabalack role is the role administrator's act, not
    # zabalack_owner's — which is why the scripts refuse rather than revoke.
    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute("GRANT zabalack_worker TO alerta_app")
    try:
        assert reads("SELECT pg_has_role('alerta_app','zabalack_worker','MEMBER')") is True, \
            "the application role can act as the worker"
        assert reads("SELECT has_function_privilege('alerta_app',"
                     "'zabalack.worker_call(text,jsonb)','EXECUTE')") is False, \
            "and NOINHERIT hides it from the privilege lookup: only the membership shows it"
        assert reads("SELECT (functions_md5,structures_md5)::text FROM zabalack.readiness_manifest "
                     "WHERE singleton") == sealed, "no ACL moved, so neither sealed digest moved"
        assert reads("SELECT (zabalack.capture_health())->>'functions_ok'") == "false", \
            "an application role that can assume a zabalack role is unhealthy under a matching seal"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="name a separate application role"):
                owner.execute(grant.replace(':"alert_app_role"', '"alerta_app"'))
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute("REVOKE zabalack_worker FROM alerta_app")
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute(grant.replace(':"alert_app_role"', '"alerta_app"'))
        owner.execute(reseal)
    assert reads("SELECT (zabalack.capture_health())->>'functions_ok'") == "true"


def test_T41_the_application_role_executes_nothing_else_in_the_schema(lab):
    """A stray grant on another zabalack function, sealed over.

    `catalog_fingerprints()` hashes every function's ACL, so a direct
    `GRANT EXECUTE ON zabalack.worker_call(text,jsonb) TO alerta_app` moves the
    digest and reads unhealthy — until someone re-seals, which every upgrade
    does. The seal then agrees with an application role that can call
    `claim_verification` and `verify_complete`, marking captures verified with
    no signature check on the way. Health asserts the role's effective rights in
    the schema, not the entry point's ACL alone, so a sealed-over grant stays
    unhealthy; `13_reseal.sql` refuses to seal it, and the grant script refuses
    to record such a role.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    grant = (ROOT / "sql/install/12_application_grant.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("GRANT EXECUTE ON FUNCTION zabalack.worker_call(text,jsonb) TO alerta_app")
    try:
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT has_function_privilege('alerta_app',"
                                 "'zabalack.worker_call(text,jsonb)','EXECUTE')").fetchone()[0] is True, \
                "the application role can now reach the worker's operations"
            assert health() == "false", "the digests alone catch the stray grant"
            # Lab-only: seal over it, as every upgrade's re-seal would.
            owner.execute("SELECT zabalack.seal_installation()")
        assert health() == "false", "a sealed-over grant on another function stays unhealthy"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="executes|execute"):
                owner.execute(grant.replace(':"alert_app_role"', '"alerta_app"'))
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("REVOKE EXECUTE ON FUNCTION zabalack.worker_call(text,jsonb) FROM alerta_app")
            owner.execute(reseal)
    assert health() == "true"


@pytest.mark.parametrize("give,take,what", [
    ("GRANT EXECUTE ON FUNCTION zabalack.worker_call(text,jsonb) TO zabalack_operator",
     "REVOKE EXECUTE ON FUNCTION zabalack.worker_call(text,jsonb) FROM zabalack_operator",
     "the worker's entry point, which claims verification leases"),
    ("GRANT EXECUTE ON FUNCTION zabalack.project(uuid) TO zabalack_operator",
     "REVOKE EXECUTE ON FUNCTION zabalack.project(uuid) FROM zabalack_operator",
     "projection, which writes the acknowledgement through"),
    ("GRANT EXECUTE ON FUNCTION zabalack.operator_call(text,jsonb) TO zabalack_worker",
     "REVOKE EXECUTE ON FUNCTION zabalack.operator_call(text,jsonb) FROM zabalack_worker",
     "the controls: external writes, resume, cohorts, capture bypass"),
    ("GRANT EXECUTE ON FUNCTION zabalack.newest_generation(text) TO zabalack_operator",
     "REVOKE EXECUTE ON FUNCTION zabalack.newest_generation(text) FROM zabalack_operator",
     "an owner-only function, which the map grants to no one"),
    ("GRANT SELECT ON zabalack.captures TO zabalack_operator",
     "REVOKE SELECT ON zabalack.captures FROM zabalack_operator",
     "a relation, which the migrations grant to no one at all"),
    ("GRANT UPDATE ON SEQUENCE zabalack.alert_generations_seq_seq TO zabalack_operator",
     "REVOKE UPDATE ON SEQUENCE zabalack.alert_generations_seq_seq FROM zabalack_operator",
     "the fence's identity sequence, where UPDATE is setval() and a sequence "
     "moved backwards gives a later staging a lower seq than an earlier one"),
])
def test_T41_only_the_installations_own_grants_survive_a_reseal(lab, give, take, what):
    """The grant map of the whole schema, asserted after the seal.

    Every ACL here is hashed, so a stray grant reads unhealthy through the
    digests — and the re-seal every upgrade performs then agrees with it. Three
    review rounds found that same shape one function at a time
    (`worker_call()`, then `operator_call()`), so what is asserted is the map:
    the non-owner grantee of every `zabalack` function is the one the
    installation makes, and no relation here admits a grantee at all.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute(give)
    try:
        assert health() == "false", "the digests alone catch the stray grant"
        with connect_role(lab, "zabalack_owner") as owner:
            # Lab-only: seal over it, as every upgrade's re-seal would.
            owner.execute("SELECT zabalack.seal_installation()")
        assert health() == "false", "a grant on " + what + " is unhealthy under a matching seal"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(take)
            owner.execute(reseal)
    assert health() == "true"


@pytest.mark.parametrize("role,reaches", [("zabalack_worker", "zabalack.worker_call(text,jsonb)"),
                                          ("zabalack_operator", "zabalack.operator_call(text,jsonb)")])
def test_T41_a_member_of_the_worker_role_reaches_the_verification_path(lab, role, reaches):
    """And the membership route into it, which no ACL and no digest records.

    The member here is `alerta_app_next`, outside the four roles whose
    memberships `catalog_fingerprints()` hashes — a member inside that set moves
    the structures digest and would be caught by the seal comparison alone,
    proving nothing about this conjunct. `NOINHERIT` keeps
    `has_function_privilege()` from resolving the role's function for it while
    `SET ROLE` is open to it, so neither the grantee map nor the digests see the
    second credential. Both roles the map grants to are covered: the worker's
    verification path and the operator's controls.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def reads(query):
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute(query).fetchone()[0]

    def health():
        return reads("SELECT (zabalack.capture_health())->>'functions_ok'")

    sealed = reads("SELECT (functions_md5,structures_md5)::text FROM zabalack.readiness_manifest WHERE singleton")
    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute(f"GRANT {role} TO alerta_app_next")
    try:
        assert reads(f"SELECT pg_has_role('alerta_app_next','{role}','MEMBER')") is True, \
            "the member can assume the role"
        assert reads(f"SELECT has_function_privilege('alerta_app_next','{reaches}','EXECUTE')") is False, \
            "NOINHERIT hides it from the privilege lookup: only the membership shows it"
        assert reads("SELECT (functions_md5,structures_md5)::text FROM zabalack.readiness_manifest "
                     "WHERE singleton") == sealed, "no ACL moved, so neither sealed digest moved"
        assert reads("SELECT (zabalack.catalog_fingerprints())->>'structures'=structures_md5 "
                     "FROM zabalack.readiness_manifest WHERE singleton") is True, \
            "the tree still fingerprints to what was sealed"
        assert health() == "false", f"a member of {role} is unhealthy under a matching seal"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute(f"REVOKE {role} FROM alerta_app_next")
    assert health() == "true"


@pytest.mark.parametrize("give,take,what", [
    ("ALTER FUNCTION zabalack.worker_call(text,jsonb) OWNER TO zabalack_worker",
     "ALTER FUNCTION zabalack.worker_call(text,jsonb) OWNER TO zabalack_owner",
     "a SECURITY DEFINER function reassigned, so its body runs as another role"),
    ("ALTER TABLE zabalack.alert_generations OWNER TO zabalack_operator",
     "ALTER TABLE zabalack.alert_generations OWNER TO zabalack_owner",
     "the fence table reassigned, which hands its new owner every right on it"),
])
def test_T41_everything_here_stays_owned_by_the_schema_owner(lab, give, take, what):
    """Ownership is the other half of the grant map.

    The grantee rules exclude the owner by definition — an owner needs no ACL
    entry — so reassigning an object moves it out of their reach entirely, and a
    `SECURITY DEFINER` function reassigned runs its body as the new role.
    `proowner`, `relowner` and `nspowner` are all hashed, so the digests catch
    it; this is what still refuses it after the re-seal an upgrade performs.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    # Lab-only: reassigning ownership is the superuser's act, not the owner's.
    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute(give)
    try:
        assert health() == "false", "the digests alone catch the reassignment"
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute("SELECT zabalack.seal_installation()")
        assert health() == "false", what + " is unhealthy under a matching seal"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute(take)
            admin.execute("REVOKE ALL ON FUNCTION zabalack.worker_call(text,jsonb) FROM PUBLIC")
            admin.execute("GRANT EXECUTE ON FUNCTION zabalack.worker_call(text,jsonb) TO zabalack_worker")
            admin.execute("REVOKE ALL ON zabalack.alert_generations FROM PUBLIC")
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)
    assert health() == "true"


@pytest.mark.parametrize("give,take,what", [
    ("GRANT zabalack_owner TO zabalack_worker",
     "REVOKE zabalack_owner FROM zabalack_worker",
     "a runtime login that can assume the owner, reaching every owner-only function"),
    ("GRANT zabalack_operator TO zabalack_probe",
     "REVOKE zabalack_operator FROM zabalack_probe",
     "the probe able to assume the operator and its controls"),
    ("ALTER ROLE zabalack_operator SUPERUSER",
     "ALTER ROLE zabalack_operator NOSUPERUSER",
     "a runtime role made SUPERUSER, which walks past every rule here"),
    ("ALTER ROLE zabalack_worker BYPASSRLS",
     "ALTER ROLE zabalack_worker NOBYPASSRLS",
     "BYPASSRLS on a runtime login"),
    ("ALTER ROLE zabalack_owner LOGIN",
     "ALTER ROLE zabalack_owner NOLOGIN",
     "the owner made directly connectable, which the digests do not hash at all"),
    ("ALTER ROLE zabalack_worker INHERIT",
     "ALTER ROLE zabalack_worker NOINHERIT",
     "a runtime role made INHERIT, against what 00_roles.sql declares"),
])
def test_T41_the_roles_keep_their_declared_shape(lab, give, take, what):
    """Roles themselves, after the seal: memberships among the four, and attributes.

    A role attribute is neither an ACL nor an ownership, so every rule about
    grants and owners passes while `SUPERUSER` or `BYPASSRLS` on a runtime login
    walks past all of them; and `zabalack_owner` granted to a runtime role hands
    that login every owner-only function through `SET ROLE` — the installer
    exception is for recorded principals outside these four, not the four
    themselves.
    `catalog_fingerprints()` hashes five attributes and these memberships, so the
    digests catch most of it until a re-seal agrees; `LOGIN` on the owner it does
    not hash at all, and only this assertion sees it.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    # Lab-only: role attributes and memberships are the superuser's to change.
    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute(give)
        # Seal over it at once, as every upgrade's re-seal would: the point is
        # what still refuses it afterwards.
        admin.execute("SELECT zabalack.seal_installation()")
    try:
        assert health() == "false", what + " is unhealthy under a matching seal"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute(take)
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)
    assert health() == "true"


@pytest.mark.parametrize("give,take,what", [
    ("REVOKE USAGE ON SCHEMA zabalack FROM zabalack_worker",
     "GRANT USAGE ON SCHEMA zabalack TO zabalack_worker",
     "the worker's USAGE revoked, which stops worker_call() at schema access"),
    ("REVOKE USAGE ON SCHEMA zabalack FROM zabalack_probe",
     "GRANT USAGE ON SCHEMA zabalack TO zabalack_probe",
     "the probe's USAGE revoked, which stops capture_health() at schema access"),
    ("REVOKE USAGE ON SCHEMA zabalack FROM zabalack_operator",
     "GRANT USAGE ON SCHEMA zabalack TO zabalack_operator",
     "the operator's USAGE revoked, which stops every control at schema access"),
    ("GRANT USAGE ON SCHEMA zabalack TO alerta_app_next",
     "REVOKE USAGE ON SCHEMA zabalack FROM alerta_app_next",
     "a role the installation never granted holding USAGE on the schema"),
    ("GRANT CREATE ON SCHEMA zabalack TO alerta_app_next",
     "REVOKE CREATE ON SCHEMA zabalack FROM alerta_app_next",
     "CREATE on the schema, which puts foreign objects where the manifest looks"),
    ("GRANT USAGE ON SCHEMA zabalack TO PUBLIC",
     "REVOKE USAGE ON SCHEMA zabalack FROM PUBLIC",
     "USAGE granted to PUBLIC"),
])
def test_T41_the_schema_keeps_its_own_grant_map(lab, give, take, what):
    """The schema ACL is the gate in front of every other rule, and until now the
    only thing asserted about it was its owner.

    Revoke `USAGE` on `zabalack` from `zabalack_worker` and every function grant
    below still reads exactly right while `zabalack.worker_call()` fails at
    schema access: verification and delivery stop behind a probe reporting
    healthy. `nspacl` is hashed, so the digests catch it — and the re-seal every
    upgrade performs then agrees with it, which is why the map is asserted here
    as well. It is exact: `USAGE` and no other privilege, to the three runtime
    roles, the alerts owner and the recorded application role, and to nobody
    else.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute(give)
        # Seal over it at once, as every upgrade's re-seal would.
        owner.execute("SELECT zabalack.seal_installation()")
    try:
        assert health() == "false", what + " is unhealthy under a matching seal"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(take)
            owner.execute(reseal)
    assert health() == "true"


@pytest.mark.parametrize("give,take,what", [
    ("GRANT zabalack_owner TO alerta_app_next",
     "REVOKE zabalack_owner FROM alerta_app_next",
     "an outside login granted the owner role, reaching worker_call() with SET ROLE"),
    ("GRANT zabalack_worker TO zabalack_installer",
     "REVOKE zabalack_worker FROM zabalack_installer",
     "an outside login granted the worker role and its verification path"),
    ("GRANT zabalack_probe TO zabalack_installer",
     "REVOKE zabalack_probe FROM zabalack_installer",
     "an outside login granted the probe role"),
    ("GRANT zabalack_operator TO zabalack_installer",
     "REVOKE zabalack_operator FROM zabalack_installer",
     "an outside login granted the operator role and its controls"),
])
def test_T41_only_a_recorded_installer_may_hold_a_zabalack_role(lab, give, take, what):
    """The membership rule entire: any member, any of the four roles.

    Each earlier round closed one corner of this — members of the worker and the
    operator, then the four among themselves — and each left the same shape
    open, because the member had to be one of a named set. Here it is any role
    at all. A `NOINHERIT` member resolves no privilege through any ACL, so every
    `has_*_privilege()` assertion reads clean while `SET ROLE` hands it the role
    whole: `alerta_app_next`, an ordinary login, granted `zabalack_owner` calls
    `zabalack.worker_call()` and records a capture as verified with no signature
    check. `catalog_fingerprints()` hashes a membership only where the *member*
    is itself one of the four, so an outside one moves no digest: nothing but
    this assertion sees it, sealed or not.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    # Lab-only: role memberships are the superuser's to grant.
    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute(give)
        admin.execute("SELECT zabalack.seal_installation()")
    try:
        assert health() == "false", what + " is unhealthy under a matching seal"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute(take)
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)
    assert health() == "true"


@pytest.mark.parametrize("function,role", [
    ("zabalack.worker_call(text,jsonb)", "zabalack_worker"),
    ("zabalack.project(uuid)", "zabalack_worker"),
    ("zabalack.operator_call(text,jsonb)", "zabalack_operator"),
    ("zabalack.capture_health()", "zabalack_probe"),
    ("zabalack.capture_health()", "zabalack_operator"),
    ("zabalack.capture_ack()", "alerta_owner"),
    ("zabalack.seal_installation()", "alerta_owner"),
])
def test_T41_every_grant_the_installation_makes_is_still_there(lab, function, role):
    """The grant map read the other way round.

    `test_T41_only_the_installations_own_grants_survive_a_reseal` refuses an ACL
    entry that should not be there; a *missing* one it cannot see. Revoke
    `EXECUTE` on `operator_call()` from `zabalack_operator` and there is no
    unexpected row to find, while every control — pause, resume, cohorts,
    external writes — fails with `permission denied`; the migrations do not
    re-make a grant they already made, so the re-seal records the grantless ACL
    as the baseline and the probe reports healthy over a deployment whose
    operator can do nothing. The map is two-sided now, as it already was for
    `stage_generation()`.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("REVOKE EXECUTE ON FUNCTION %s FROM %s" % (function, role))
        owner.execute("SELECT zabalack.seal_installation()")
    try:
        assert health() == "false", "%s without its grant to %s is unhealthy" % (function, role)
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("GRANT EXECUTE ON FUNCTION %s TO %s" % (function, role))
            owner.execute(reseal)
    assert health() == "true"


def test_T41_a_role_that_reaches_a_recorded_installer_is_refused_too(lab):
    """Reach, not the edge.

    A recorded installer may act as `zabalack_owner`; a role that can act as the
    *installer* can therefore act as the owner too, through the two-edge chain
    `outside login → installer → zabalack_owner`, which a rule reading direct
    `pg_auth_members` edges with `roleid` among the four never sees. `SET ROLE`
    follows the chain and `pg_has_role(..., 'MEMBER')` does as well, so the rule
    is stated as reach: only the roles the installation names may act as any of
    the four, and anything that reaches one of those roles must be named too.
    """
    installer = (ROOT / "sql/install/09_installer_roles.sql").read_text()
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute("GRANT zabalack_owner TO zabalack_installer")
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute(installer.replace(":'installer_roles'", "'zabalack_installer'"))
        owner.execute(reseal)
    try:
        assert health() == "true", "precondition: the recorded installer is allowed"
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute("GRANT zabalack_installer TO alerta_app_next")
            admin.execute("SELECT zabalack.seal_installation()")
        try:
            with connect_role(lab, "zabalack_owner") as owner:
                assert owner.execute(
                    "SELECT pg_has_role('alerta_app_next','zabalack_owner','MEMBER')").fetchone()[0] is True
            assert health() == "false", "a role that reaches the installer reaches the owner"
            with connect_role(lab, "zabalack_owner") as owner:
                with pytest.raises(Exception, match="before sealing"):
                    owner.execute(reseal)
            # The record is not a way in either: naming the chained login is what
            # the script refuses, since it is no installer of this installation.
            with connect_role(lab, "zabalack_owner") as owner:
                with pytest.raises(Exception, match="not members of zabalack_owner"):
                    owner.execute(installer.replace(
                        ":'installer_roles'", "'zabalack_installer,alerta_app_next'"))
        finally:
            with connect_role(lab, "zabalack_owner") as admin:
                admin.execute("RESET ROLE")
                admin.execute("REVOKE zabalack_installer FROM alerta_app_next")
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute("REVOKE zabalack_owner FROM zabalack_installer")
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(installer.replace(":'installer_roles'", "''"))
            owner.execute(reseal)
    assert health() == "true"


@pytest.mark.parametrize("grantee,refusal", [
    ("alerta_app", "other than the staging entry point"),
    ("zabalack_probe", "a grantee the installation does not make"),
])
def test_T41_a_procedure_is_executable_too(lab, grantee, refusal):
    """`prokind` was a filter on the map, exactly as `relkind` was on the relations.

    A `SECURITY DEFINER` procedure runs as its owner just as a function does, so
    one added to this schema and granted to a role reaches whatever
    `zabalack_owner` reaches — `worker_call()` among it, which records a capture
    as verified with no signature check. `catalog_fingerprints()` hashed only
    `prokind='f'` and every assertion carried the same filter, so such an object
    moved no digest and no rule saw it: healthy before the seal and after it.
    Both cover every routine now, so the application role's effective rights and
    the schema's grant map each catch it, depending on who holds the grant.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("""CREATE PROCEDURE zabalack.drifted() LANGUAGE plpgsql SECURITY DEFINER
                      SET search_path=pg_catalog,zabalack AS $$
                      BEGIN PERFORM zabalack.worker_call('doctor','{}'::jsonb); END $$""")
        owner.execute("REVOKE ALL ON PROCEDURE zabalack.drifted() FROM PUBLIC")
        owner.execute("GRANT EXECUTE ON PROCEDURE zabalack.drifted() TO %s" % grantee)
        owner.execute("SELECT zabalack.seal_installation()")
    try:
        # It is a procedure, not a function: the filter that hid it.
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute(
                "SELECT prokind FROM pg_proc WHERE oid='zabalack.drifted'::regproc").fetchone()[0] == "p"
        assert health() == "false", "a granted procedure here is unhealthy under a matching seal"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match=refusal):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("DROP PROCEDURE zabalack.drifted()")
            owner.execute(reseal)
    assert health() == "true"


def test_T41_the_grant_script_refuses_an_application_role_that_reaches_a_procedure(lab):
    """The map is the standing assertion; the grant script is the door.

    `12_application_grant.sql` has no whole-schema map of its own — it asks what
    the role it is about to record can reach — so the `prokind` filter was the
    only thing between it and recording a role that executes a `SECURITY
    DEFINER` procedure here. That is the one place the filter mattered on its
    own.
    """
    grant = (ROOT / "sql/install/12_application_grant.sql").read_text()
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("""CREATE PROCEDURE zabalack.drifted() LANGUAGE plpgsql SECURITY DEFINER
                      SET search_path=pg_catalog,zabalack AS $$
                      BEGIN PERFORM zabalack.worker_call('doctor','{}'::jsonb); END $$""")
        owner.execute("REVOKE ALL ON PROCEDURE zabalack.drifted() FROM PUBLIC")
        owner.execute("GRANT EXECUTE ON PROCEDURE zabalack.drifted() TO alerta_app_next")
    try:
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="executes other zabalack function"):
                owner.execute(grant.replace(':"alert_app_role"', '"alerta_app_next"'))
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT application_role FROM zabalack.installation "
                                 "WHERE singleton").fetchone()[0] == "alerta_app", "a refused grant records nothing"
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("DROP PROCEDURE zabalack.drifted()")
            owner.execute(reseal)
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


def test_T41_the_alerts_owner_keeps_its_usage_on_the_schema(lab):
    """The positive side of the schema map reached the runtime roles and the
    recorded application role, and stopped there.

    Wherever the deployment splits the application from the alerts owner — which
    `docs/deployment.md` prefers — that owner is a fourth role holding `USAGE`,
    and 005 grants it `capture_ack()` for the trigger and `seal_installation()`
    for the installer. Revoke its `USAGE` and both fail at schema access, and so
    does `10_trigger.sql`, which installs and repairs the trigger; the negative
    rule sees nothing, since nothing unexpected was added.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT pg_get_userbyid(relowner) FROM pg_class "
                             "WHERE oid='public.alerts'::regclass").fetchone()[0] == "alerta_owner"
        assert owner.execute("SELECT application_role FROM zabalack.installation "
                             "WHERE singleton").fetchone()[0] == "alerta_app", "precondition: split roles"
        owner.execute("REVOKE USAGE ON SCHEMA zabalack FROM alerta_owner")
        owner.execute("SELECT zabalack.seal_installation()")
    try:
        assert health() == "false"
        with connect_role(lab, "alerta_owner") as owner:
            with pytest.raises(Exception, match="permission denied for schema zabalack"):
                owner.execute("SELECT zabalack.seal_installation()")
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="alerts owner has lost USAGE"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("GRANT USAGE ON SCHEMA zabalack TO alerta_owner")
            owner.execute(reseal)
    assert health() == "true"


@pytest.mark.parametrize("role,target", [
    ("zabalack_worker", "alerta_owner"),
    ("zabalack_probe", "alerta_owner"),
    ("zabalack_operator", "alerta_app"),
    ("zabalack_owner", "alerta_app_next"),
])
def test_T41_the_four_are_members_of_nothing_themselves(lab, role, target):
    """Every other membership rule here reads inward — who can act as one of the
    four. This one reads outward: what one of them can act as.

    `GRANT alerta_owner TO zabalack_worker` and the worker does `SET ROLE
    alerta_owner` and writes `public.alerts` directly, past the projection
    boundary the whole design rests on — while
    `has_table_privilege('zabalack_worker','public.alerts','UPDATE')`, asserted
    since 005, stays `false` throughout: it resolves *inherited* privilege, and a
    `NOINHERIT` role inherits none of what `SET ROLE` still hands it. That blind
    spot is the one this PR has met in every membership round. The membership is
    hashed, the member being one of the four, so the digests catch it until the
    re-seal agrees with it; this assertion is what holds afterwards.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute("GRANT %s TO %s" % (target, role))
        admin.execute("SELECT zabalack.seal_installation()")
    try:
        with connect_role(lab, "zabalack_owner") as owner:
            # The role reaches it with SET ROLE and no privilege lookup shows it.
            assert owner.execute("SELECT pg_has_role(%s,%s,'MEMBER')", (role, target)).fetchone()[0] is True
            assert owner.execute("SELECT pg_has_role(%s,%s,'USAGE')", (role, target)).fetchone()[0] is False
        assert health() == "false", "%s able to assume %s is unhealthy under a matching seal" % (role, target)
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute("REVOKE %s FROM %s" % (target, role))
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)
    assert health() == "true"


def test_T41_the_worker_reaches_the_alerts_owner_through_set_role_alone(lab):
    """The exposure itself, once, rather than only the rule that refuses it.

    A `NOINHERIT` runtime role granted the alerts owner writes the table the
    projection boundary exists to protect, and every `has_*_privilege()`
    assertion in this installation reads clean while it does.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    alert_id = str(uuid.uuid4())
    with connect_role(lab, "alerta_app") as app:
        app.execute("INSERT INTO alerts(id,resource,event,status,customer,service,attributes,history) "
                    "VALUES(%s,'outbound','outbound','open',%s,ARRAY['s'],'{}'::jsonb,ARRAY[]::history[])",
                    (alert_id, CUSTOMER))
    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute("GRANT alerta_owner TO zabalack_worker")
    try:
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute(
                "SELECT has_table_privilege('zabalack_worker','public.alerts','UPDATE')").fetchone()[0] is False
        with connect_role(lab, "zabalack_worker") as worker:
            worker.execute("SET ROLE alerta_owner")
            assert worker.execute("SELECT current_user").fetchone()[0] == "alerta_owner"
            written = worker.execute(
                "UPDATE alerts SET attributes=attributes||'{\"forged\":\"1\"}'::jsonb WHERE id=%s",
                (alert_id,)).rowcount
        assert written == 1, "the worker wrote public.alerts through SET ROLE"
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "false"
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute("REVOKE alerta_owner FROM zabalack_worker")
        with connect_role(lab, "alerta_owner") as owner:
            owner.execute("DELETE FROM alerts WHERE id=%s", (alert_id,))
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)


@pytest.mark.parametrize("give,take,what", [
    ("REVOKE UPDATE(attributes) ON public.alerts FROM zabalack_owner",
     "GRANT UPDATE(attributes) ON public.alerts TO zabalack_owner",
     "the one column the owner writes"),
    ("REVOKE SELECT(customer) ON public.alerts FROM zabalack_owner",
     "GRANT SELECT(customer) ON public.alerts TO zabalack_owner",
     "a column the owner reads"),
    ("REVOKE SELECT(id,customer,status,attributes,update_time) ON public.alerts FROM zabalack_owner",
     "GRANT SELECT(id,customer,status,attributes,update_time) ON public.alerts TO zabalack_owner",
     "every column the owner reads"),
    ("GRANT UPDATE(text) ON public.alerts TO zabalack_owner",
     "REVOKE UPDATE(text) ON public.alerts FROM zabalack_owner",
     "a column the owner must not write"),
    ("GRANT SELECT(raw_data) ON public.alerts TO zabalack_owner",
     "REVOKE SELECT(raw_data) ON public.alerts FROM zabalack_owner",
     "a column the owner must not read"),
    ("GRANT DELETE ON public.alerts TO zabalack_owner",
     "REVOKE DELETE ON public.alerts FROM zabalack_owner",
     "DELETE on the alerts table"),
])
def test_T41_the_owner_keeps_exactly_its_grants_on_public_alerts(lab, give, take, what):
    """The owner's access to `public.alerts`, read both ways.

    Every rule here about that table was a prohibition — no `UPDATE` on `status`,
    `history`, `severity` or `customer`, no `TRIGGER` — and a prohibition cannot
    see a grant that is *missing*. Revoke `UPDATE(attributes)` and
    `stage_generation()`, `SECURITY DEFINER` and running as this owner, fails
    with `permission denied for table alerts`: every eligible acknowledgement is
    refused `zabalack_generation_unavailable`, and retirement and projection lose
    their access too. `public.alerts` is outside the schema
    `catalog_fingerprints()` hashes, so no digest moves either — `functions_ok`
    reads `true` before any re-seal as well as after one, and this assertion is
    the only thing that sees it. The owner reads five columns and writes one.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    # The alerts owner grants and revokes on its own table.
    with connect_role(lab, "alerta_owner") as owner:
        owner.execute(give)
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("SELECT zabalack.seal_installation()")
    try:
        assert health() == "false", what + " is unhealthy under a matching seal"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "alerta_owner") as owner:
            owner.execute(take)
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)
    assert health() == "true"


def test_T41_without_its_update_grant_the_owner_cannot_stage_at_all(lab):
    """The consequence itself, once: the entry point fails and the API refuses.

    `stage_generation()` runs as `zabalack_owner`, so the owner's `UPDATE`
    on `attributes` is what every acknowledgement rests on — and the
    installation reported healthy without it.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    alert_id, action_id = str(uuid.uuid4()), str(uuid.uuid4())
    with connect_role(lab, "alerta_owner") as owner:
        owner.execute("REVOKE UPDATE(attributes) ON public.alerts FROM zabalack_owner")
    try:
        with connect_role(lab, "alerta_app") as app:
            app.execute("INSERT INTO alerts(id,resource,event,status,customer,service,attributes,history) "
                        "VALUES(%s,'owner-grant','owner-grant','open',%s,ARRAY['s'],'{}'::jsonb,ARRAY[]::history[])",
                        (alert_id, CUSTOMER))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                            (alert_id, action_id, Jsonb({"action_id": action_id, "alert_id": alert_id})))
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT count(*) FROM zabalack.alert_generations WHERE alert_id=%s",
                                 (alert_id,)).fetchone()[0] == 0, "nothing staged, nothing recorded"
    finally:
        with connect_role(lab, "alerta_owner") as owner:
            owner.execute("GRANT UPDATE(attributes) ON public.alerts TO zabalack_owner")
            owner.execute("DELETE FROM alerts WHERE id=%s", (alert_id,))
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


@pytest.mark.parametrize("role", ["zabalack_worker", "zabalack_probe", "zabalack_operator"])
def test_T41_the_runtime_roles_can_still_open_a_session(lab, role):
    """`CONNECT` is the last line of `00_roles.sql` nothing asserted.

    It is not an ACL this schema hashes and no other rule looks at it, so
    revoking it is silent in a way the owner's `USAGE` on `public` is not:
    `capture_health()` answers normally through the probe's existing session
    while the worker never connects again, and verification and delivery stop
    behind a probe that passes and a seal that agrees with it.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    with connect_role(lab, "zabalack_owner") as owner:
        database = owner.execute("SELECT current_database()").fetchone()[0]
    # Lab-only: database-level grants are the superuser's act. PUBLIC holds
    # CONNECT by default, so the role-level revoke alone changes nothing.
    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute('REVOKE CONNECT ON DATABASE "%s" FROM %s, PUBLIC' % (database, role))
        admin.execute("SELECT zabalack.seal_installation()")
    try:
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT has_database_privilege(%s,%s,'CONNECT')",
                                 (role, database)).fetchone()[0] is False
        assert health() == "false", "%s cannot connect, so the installation is not healthy" % role
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="before sealing"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute('GRANT CONNECT ON DATABASE "%s" TO %s, PUBLIC' % (database, role))
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)
    assert health() == "true"


def test_T41_without_public_usage_the_owner_fails_closed_and_the_reseal_says_why(lab):
    """The owner's `USAGE` on schema `public` fails *loudly*, and the refusal names it.

    `stage_generation()`, `project()` and `retire_intents()` are `SECURITY
    DEFINER` and run as `zabalack_owner`, reaching `public.alerts` through that
    grant — but so do `capture_health()` and `13_reseal.sql` itself, so revoking
    it seals nothing and reports nothing healthy: both raise `permission denied
    for schema public`, and the probe's caller treats an exception from
    `capture_health()` as a failure. This case pins that fail-closed behaviour,
    and that the re-seal now names the missing grant instead of leaving a bare
    permission error to be diagnosed.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute("REVOKE USAGE ON SCHEMA public FROM zabalack_owner")
        admin.execute("REVOKE USAGE ON SCHEMA public FROM PUBLIC")
    try:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            assert admin.execute(
                "SELECT has_schema_privilege('zabalack_owner','public','USAGE')").fetchone()[0] is False
        # Health does not answer "unhealthy" here — it raises, which the probe
        # treats as a failure. Nothing reports healthy and nothing seals.
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                owner.execute("SELECT zabalack.capture_health()")
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="lost USAGE on schema public"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute("GRANT USAGE ON SCHEMA public TO zabalack_owner")
            admin.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


def test_T41_the_grant_script_refuses_an_application_role_that_cannot_log_in(lab):
    """Alerta connects *as* the application role, and no member of it is allowed.

    So a role that cannot open a session leaves the deployment with no credential
    able to stage at all — and the script revokes the former grantee before it
    records the new one, so naming such a role would take the working credential
    away and put nothing in its place, behind grants that read exactly right.
    The script is one transaction: refusing rolls the revoke back with it, which
    this asserts rather than assumes.
    """
    grant = (ROOT / "sql/install/12_application_grant.sql").read_text()
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT rolcanlogin FROM pg_roles "
                             "WHERE rolname='alerta_app_nologin'").fetchone()[0] is False
        with pytest.raises(Exception, match="cannot open a session"):
            owner.execute(grant.replace(':"alert_app_role"', '"alerta_app_nologin"'))
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT application_role FROM zabalack.installation "
                             "WHERE singleton").fetchone()[0] == "alerta_app"
        # The working credential is untouched: the revoke rolled back too.
        assert owner.execute("SELECT has_function_privilege('alerta_app',"
                             "'zabalack.stage_generation(text,uuid,jsonb)','EXECUTE')").fetchone()[0] is True
        assert owner.execute("SELECT has_schema_privilege('alerta_app','zabalack','USAGE')").fetchone()[0] is True
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


@pytest.mark.parametrize("give,take,what", [
    ("ALTER ROLE alerta_app NOLOGIN", "ALTER ROLE alerta_app LOGIN",
     "the recorded role made NOLOGIN"),
    ("ALTER ROLE alerta_app CONNECTION LIMIT 0", "ALTER ROLE alerta_app CONNECTION LIMIT -1",
     "the recorded role given a connection limit of zero"),
])
def test_T41_the_recorded_application_role_can_still_open_a_session(lab, give, take, what):
    """...and it must keep being able to, after the install as well as during it.

    None of `rolcanlogin`, `rolconnlimit` or `CONNECT` is an ACL this schema
    hashes, so altering the recorded role moves no digest and every grant below
    still reads exactly right, while every eligible acknowledgement is refused
    `zabalack_generation_unavailable`. `rolvaliduntil` is deliberately not
    asserted: it binds password authentication only, and a certificate-
    authenticated role outlives it.
    """
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    # Lab-only: role attributes are the superuser's to change.
    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute(give)
        admin.execute("SELECT zabalack.seal_installation()")
    try:
        assert health() == "false", what + " is unhealthy under a matching seal"
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="cannot open a session"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute(take)
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)
    assert health() == "true"


def test_T41_a_recorded_installer_holds_the_owner_role_and_nothing_else(lab):
    """The exemption the record makes, and its limit.

    Migrations run as `zabalack_owner`, so a deployment has principals that can
    assume it; `sql/install/09_installer_roles.sql` names them. A named one may
    hold `zabalack_owner` — and only that. Recorded or not, a runtime role is
    never its to hold: the record exempts a principal from the owner rule, not
    from the boundary the other three draw.
    """
    installer = (ROOT / "sql/install/09_installer_roles.sql").read_text()
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def health():
        with connect_role(lab, "zabalack_owner") as owner:
            return owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0]

    with connect_role(lab, "zabalack_owner") as admin:
        admin.execute("RESET ROLE")
        admin.execute("GRANT zabalack_owner TO zabalack_installer")
    try:
        assert health() == "false", "an unrecorded owner member is unhealthy"
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(installer.replace(":'installer_roles'", "'zabalack_installer'"))
            assert owner.execute("SELECT installer_roles FROM zabalack.installation WHERE singleton"
                                 ).fetchone()[0] == ["zabalack_installer"]
            owner.execute(reseal)
        assert health() == "true", "the recorded installer may hold the owner role"
        # ...and only it. The record is no licence over the runtime roles.
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute("GRANT zabalack_worker TO zabalack_installer")
            admin.execute("SELECT zabalack.seal_installation()")
        try:
            assert health() == "false", "a recorded installer holding the worker role is still refused"
            with connect_role(lab, "zabalack_owner") as owner:
                with pytest.raises(Exception, match="before sealing"):
                    owner.execute(reseal)
        finally:
            with connect_role(lab, "zabalack_owner") as admin:
                admin.execute("RESET ROLE")
                admin.execute("REVOKE zabalack_worker FROM zabalack_installer")
    finally:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute("REVOKE zabalack_owner FROM zabalack_installer")
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(installer.replace(":'installer_roles'", "''"))
            owner.execute(reseal)
    assert health() == "true"


def test_T41_an_installation_that_never_recorded_its_installers_is_not_sealed(lab):
    """The column starts NULL, and NULL is not an empty allowlist.

    An upgrade that skips the record would otherwise carry the old exemption
    forward silently. `capture_health()` refuses it and `13_reseal.sql` names the
    script.
    """
    installer = (ROOT / "sql/install/09_installer_roles.sql").read_text()
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET installer_roles=NULL WHERE singleton")
        owner.execute("SELECT zabalack.seal_installation()")
    try:
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "false"
            with pytest.raises(Exception, match="09_installer_roles.sql"):
                owner.execute(reseal)
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(installer.replace(":'installer_roles'", "''"))
            owner.execute(reseal)
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


@pytest.mark.parametrize("named,setup,teardown,refusal", [
    ("no_such_installer", None, None, "do not exist"),
    ("zabalack_worker", None, None, "zabalack role was named as an installer"),
    ("alerta_app_next", None, None, "not members of zabalack_owner"),
    ("", "GRANT zabalack_owner TO zabalack_installer",
     "REVOKE zabalack_owner FROM zabalack_installer",
     "outside the recorded installer topology"),
])
def test_T41_the_installer_record_refuses_a_topology_it_was_not_shown(lab, named, setup, teardown, refusal):
    """The record has to equal the catalog, in both directions.

    A name that is not a role, or is one of the four, or holds nothing, records
    an exemption for something the catalog does not show; and a role that does
    hold `zabalack_owner` and was left out is exactly the exposure this closes.
    `zabalack_owner` holds no `ADMIN OPTION` over those memberships and cannot
    revoke one, as `12_application_grant.sql` cannot revoke a membership of the
    application role: the script refuses, names them, and leaves the recorded
    list as it found it.
    """
    installer = (ROOT / "sql/install/09_installer_roles.sql").read_text()
    with connect_role(lab, "zabalack_owner") as owner:
        before = owner.execute("SELECT installer_roles FROM zabalack.installation WHERE singleton").fetchone()[0]
    if setup:
        with connect_role(lab, "zabalack_owner") as admin:
            admin.execute("RESET ROLE")
            admin.execute(setup)
    try:
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match=refusal):
                owner.execute(installer.replace(":'installer_roles'", "'%s'" % named))
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT installer_roles FROM zabalack.installation WHERE singleton"
                                 ).fetchone()[0] == before, "a refused record changes nothing"
    finally:
        if teardown:
            with connect_role(lab, "zabalack_owner") as admin:
                admin.execute("RESET ROLE")
                admin.execute(teardown)
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


def test_T41_the_installer_record_runs_only_as_the_schema_owner(lab):
    installer = (ROOT / "sql/install/09_installer_roles.sql").read_text().replace(":'installer_roles'", "''")
    with connect_role(lab, "zabalack_worker") as worker:
        with pytest.raises(Exception, match="run as zabalack_owner|permission denied"):
            worker.execute(installer)


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


def test_T41_changing_the_application_role_revokes_the_former_one(lab):
    """The grant script is re-run for a new application role. The former role
    loses the staging entry point in the same transaction, the new one gains it,
    the recorded role follows, and once re-sealed the tree is healthy. Without
    the revoke, a role change left two credentials able to stage — and the
    probe, which only refused PUBLIC, reported that healthy."""
    grant = (ROOT / "sql/install/12_application_grant.sql").read_text()
    reseal = (ROOT / "sql/install/13_reseal.sql").read_text()

    def grantee_of():
        with connect_role(lab, "zabalack_owner") as owner:
            executors = owner.execute(
                "SELECT pg_get_userbyid(e.grantee) FROM pg_proc p CROSS JOIN LATERAL aclexplode(p.proacl) e "
                "WHERE p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure AND e.grantee<>p.proowner "
                "ORDER BY 1").fetchall()
            recorded = owner.execute("SELECT application_role FROM zabalack.installation WHERE singleton").fetchone()[0]
        return [row[0] for row in executors], recorded

    assert grantee_of() == (["alerta_app"], "alerta_app"), "precondition: the lab granted the application role"
    try:
        # The lab's second application login, which is what the rules require of
        # one: no members, no zabalack role it is or can assume, and nothing
        # else in the schema it can execute or read.
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(grant.replace(':"alert_app_role"', '"alerta_app_next"'))
        assert grantee_of() == (["alerta_app_next"], "alerta_app_next"), "the former grantee lost the entry point"
        with connect_role(lab, "alerta_app") as app:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute("SELECT zabalack.stage_generation(%s,%s,%s)", (str(uuid.uuid4()), str(uuid.uuid4()), Jsonb({})))
        # The schema USAGE the script granted goes with the entry point. The
        # schema's grant map names the *recorded* application role, so a former
        # role's USAGE left behind reads unhealthy and refuses the next seal.
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT has_schema_privilege('alerta_app','zabalack','USAGE')").fetchone()[0] is False
            owner.execute(reseal)
            assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"
    finally:
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(grant.replace(':"alert_app_role"', '"alerta_app"'))
            owner.execute(reseal)
    assert grantee_of() == (["alerta_app"], "alerta_app")
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


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


# --- regressions from review -------------------------------------------------

def test_T41_one_ordering_two_concurrent_acknowledgements_do_not_wedge_the_alert(lab):
    """The fence is the one ordering; 002's capture_order comparison is gone.

    A is staged, then B. B is captured and verified first, A afterwards, as two
    overlapping requests can leave them. Under two independent orderings B is
    refused for the newer capture (A) and A for the newer staging (B) — both
    forever. Under one, the later staging projects and the earlier is refused.
    """
    alert_id, event_id, first = stage(lab, issued=datetime.now(timezone.utc))
    second = stage_over(lab, alert_id, event_id)
    fire_ack(lab, alert_id, second)
    verify_through_worker_call(lab, second)
    stage_pre_fence(lab, alert_id, first)   # A's request lands its (already recorded) intent last
    fire_ack(lab, alert_id, first)
    verify_through_worker_call(lab, first)
    with connect_role(lab, "zabalack_owner") as owner:
        orders = dict(owner.execute("SELECT action_id::text,capture_order FROM zabalack.actions WHERE alert_id=%s",
                                    (alert_id,)).fetchall())
    assert orders[first["action_id"]] > orders[second["action_id"]], "precondition: A captured after B"
    assert project(lab, second["action_id"]) == "projected"
    assert project(lab, first["action_id"]) == "projection_action_generation_mismatch"
    assert attributes_of(lab, alert_id)[ACTION_KEY] == second["action_id"]


@pytest.mark.parametrize("value", ["2026-01-01T00:00:00+99:00", "not a time", "2026-13-45T00:00:00Z", "infinity"])
def test_T41_an_unparseable_api_writable_window_is_retired_not_a_sweep_abort(lab, value):
    """One forged timestamp must never abort the sweep for every other row."""
    victim, _, _ = stage(lab)
    alert_id, _, payload = stage(lab, staged=False)
    stage_pre_fence(lab, alert_id, dict(payload, capture_expires_at=value))
    result = sweep(lab, grace=300)
    assert result["examined"] >= 2 and result["retired"] >= 2
    assert INTENT not in attributes_of(lab, alert_id) and INTENT not in attributes_of(lab, victim)


@pytest.mark.parametrize("value", ["2026-01-01T00:00:00+99:00", "not a time"])
def test_T41_an_unparseable_local_marker_refuses_rather_than_raising(lab, value):
    alert_id, event_id, payload = stage(lab, issued=datetime.now(timezone.utc))
    fire_ack(lab, alert_id, payload)
    verify_through_worker_call(lab, payload)
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({"zabbix_ack_sync_local_at": value}), alert_id))
    assert project(lab, payload["action_id"]) == "projection_local_generation_mismatch"


def test_T41_the_capture_fallback_records_only_an_intent_bound_to_its_own_row(lab):
    other, other_event, _ = stage(lab, staged=False)
    alert_id, _, _ = stage(lab, staged=False)
    # An intent that is internally consistent for the other alert, written on
    # this row: the signer binds route to alert_id, so it must be built that way.
    foreign = intent_for(other, other_event)
    stage_pre_fence(lab, alert_id, foreign)
    fire_ack(lab, alert_id, foreign)
    capture = capture_of(lab, alert_id)
    assert capture is not None and capture[3] == "binding_mismatch", "precondition: the capture failed binding"
    assert fence(lab, alert_id) == [] and fence(lab, other) == [], "an unbound intent records no generation anywhere"



def test_T41_the_capture_fallback_records_nothing_for_a_capture_that_did_not_bind(lab):
    """An intent on its own row whose marker names another action, then a
    marker without a well-formed proof: neither can ever be verified into an
    action, so neither records a generation. Recorded, one would be the newest
    generation on an alert that holds no staging-recorded row — forever, since
    nothing removes a fence row — and refuse the older action's re-projection.
    A capture that binds still records, pending verification of its proof."""
    alert_id, _, payload = stage(lab, staged=False)
    stage_pre_fence(lab, alert_id, payload)
    fire_ack(lab, alert_id, dict(payload, action_id=str(uuid.uuid4())))
    assert capture_of(lab, alert_id)[3] == "binding_mismatch", "precondition: the marker named another action"
    assert fence(lab, alert_id) == []
    fire_ack(lab, alert_id, payload, proof="[zabalack:v1 action=not-a-proof]")
    assert capture_of(lab, alert_id)[3] == "marker_malformed", "precondition: the marker was malformed"
    assert fence(lab, alert_id) == []
    fire_ack(lab, alert_id, payload)
    assert capture_of(lab, alert_id)[3] is None, "precondition: the capture bound"
    assert [(row[0], row[1]) for row in fence(lab, alert_id)] == [(payload["action_id"], "capture")]
