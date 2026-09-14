"""T41 — retire the staged intent from `public.alerts` without weakening the
generation guard that depended on it (amendment A5, stage two).

Retirement cannot be placed in `project()` alone. `claim_verification` selects
only `state IN ('captured_unverified','verifier_hold')`, and `project()` is
reachable only with a verified `action_id`, so most of A5's lifecycle table
never reaches it: staging that failed at `status_change`, a cohort absent from
`zabalack.cohorts`, capture-side evidence failure, `capture_bypass`,
`reject_capture`, `verifier_hold` with the key unavailable, and a worker stopped
or backlogged. Each of those is a separate case below, and the sweep is asserted
to retire the attribute in each **without** verification or delivery having
succeeded.

The generation guard is the second half. Between a new staging and its capture,
`take_action` has popped `zabbix_ack_sync_local_at` and no newer `actions` row
exists, so neither the `local_at` test nor the `capture_order` test can see the
newer generation — that was what the retired intent's `action_id` provided. The
replacement is `zabalack.alert_staging`, which lives where the Alerta API cannot
reach it, and which takes no expiry: `execution_expires_at` bounds delivery,
while `projection_due_at` has no equivalent deadline.

Execution layer. Under the reference runner these execute on PostgreSQL 18.3 in
PGlite (WASM), supplemental single-backend evidence. The concurrent race between
a sweep and an in-flight projection needs two sessions and lives in
`tests/alerta/test_native_generation_race.py`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from psycopg.types.json import Jsonb
import pytest

from conftest import connect_role

pytestmark = pytest.mark.postgres

CUSTOMER = "retire-lab-a"
INSTANCE = "retire-lab"
TRIGGER = "980002"
UNDISCLOSED = "retire-lab-b"
INTENT = "zabbix_ack_sync_intent"
MARKER = "zabbix_ack_sync_staged_action_id"
UNTIL = "zabbix_ack_sync_staged_until"
_event = iter(range(981001, 989999))


def stamp(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def intent_for(alert_id, event_id, *, issued=None, action_id=None):
    """A payload shaped exactly like the one `take_action` stages."""
    issued = issued or datetime.now(timezone.utc)
    return {
        "version": 1, "action_id": action_id or str(uuid.uuid4()), "alert_id": alert_id,
        "customer": CUSTOMER,
        "source": {"zabbix_instance": INSTANCE, "zabbix_event_id": event_id, "zabbix_trigger_id": TRIGGER},
        "actor": {"login": "retire-engineer", "customers": [CUSTOMER, UNDISCLOSED],
                  "admin": False, "auth_type": "user"},
        "route": f"/alert/{alert_id}/action", "action": "ack", "cohort_revision": "retire-r1",
        "policy_revision": "1", "issued_at": stamp(issued),
        "capture_expires_at": stamp(issued + timedelta(seconds=60)),
        "execution_expires_at": stamp(issued + timedelta(hours=4)),
    }


def enrol(pg_lab):
    with connect_role(pg_lab, "zabalack_owner") as owner:
        owner.execute("""INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,
                      host_ids,group_ids,enabled) VALUES(%s,%s,%s,'retire-r1','1','["980003"]','["980004"]',true)
                      ON CONFLICT(customer,instance,trigger_id) DO UPDATE SET enabled=true""",
                      (CUSTOMER, INSTANCE, TRIGGER))


def cleanup(pg_lab):
    """Leave the lab as this module found it.

    `tests/postgres/assertions.sql` counts whole tables, so a module that leaves
    captures behind breaks a later one. Correctness must not depend on which
    order the modules happen to run in.
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
        owner.execute("""DELETE FROM zabalack.alert_staging WHERE alert_id IN
                      (SELECT id FROM public.alerts WHERE customer=%s)""", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.cohorts WHERE customer=%s", (CUSTOMER,))
        owner.execute("DELETE FROM zabalack.actor_identity WHERE login='retire-engineer'")
    with connect_role(pg_lab, "alerta_owner") as owner:
        owner.execute("DELETE FROM alerts WHERE customer=%s", (CUSTOMER,))


@pytest.fixture
def lab(pg_lab):
    enrol(pg_lab)
    yield pg_lab
    cleanup(pg_lab)


def stage(lab, *, issued=None, action_id=None, staged=True):
    """Create an alert carrying a staged intent, exactly as the plugin leaves it.

    This models the committed state after `process_action()`, which Alerta
    commits before `from_action()` signs — so it is reached on every lifecycle
    path, including the ones that never produce a capture.

    `issued` defaults into the past so the staged intent is already past its
    `capture_expires_at`; a freshly staged one is deliberately not yet eligible,
    which `test_T41_a_staging_still_inside_its_window_is_not_swept` asserts.
    """
    issued = issued or datetime.now(timezone.utc) - timedelta(minutes=10)
    alert_id, event_id = str(uuid.uuid4()), str(next(_event))
    payload = intent_for(alert_id, event_id, issued=issued, action_id=action_id)
    attributes = {"source_system": "zabbix", "zabbix_instance": INSTANCE,
                  "zabbix_event_id": event_id, "zabbix_trigger_id": TRIGGER,
                  "runbook_url": "https://runbook.invalid/retire-lab",
                  "zabbix_ack_sync": "pending", "zabbix_ack_sync_reason": "awaiting_capture",
                  "zabbix_ack_sync_action_id": payload["action_id"]}
    if staged:
        attributes[INTENT] = payload
        attributes[MARKER] = payload["action_id"]
        attributes[UNTIL] = payload["capture_expires_at"]
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("INSERT INTO alerts(id,resource,event,status,customer,service,attributes,history) "
                     "VALUES(%s,'retire-resource','retire-event','open',%s,ARRAY['retire-service'],%s,"
                     "ARRAY[]::history[])", (alert_id, CUSTOMER, Jsonb(attributes)))
    return alert_id, event_id, payload


def attributes_of(lab, alert_id):
    with connect_role(lab, "alerta_owner") as owner:
        return owner.execute("SELECT attributes FROM alerts WHERE id=%s", (alert_id,)).fetchone()[0]


def ledger(lab, alert_id):
    with connect_role(lab, "zabalack_owner") as owner:
        return owner.execute("SELECT staged_action_id,staged_until,observed_by FROM zabalack.alert_staging "
                             "WHERE alert_id=%s", (alert_id,)).fetchone()


def sweep(lab, *, grace=0, limit=1000):
    with connect_role(lab, "zabalack_owner") as owner:
        return owner.execute("SELECT zabalack.retire_intents(%s,%s)", (limit, grace)).fetchone()[0]


def fire_ack(lab, alert_id, payload, *, marker=True):
    """Commit the acknowledgement history entry, firing `zabalack_capture`."""
    from zabalack.crypto import sign_intent
    proof = sign_intent(payload, "lab", b"synthetic-retire-lab-only-32bytes-key!!!") if marker else "no marker"
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET status='ack',history=ARRAY[ROW(%s,'retire-event',NULL,'ack',NULL,%s,"
                     "'ack',clock_timestamp() AT TIME ZONE 'UTC',%s,NULL)::history],"
                     "update_time=clock_timestamp() AT TIME ZONE 'UTC' WHERE id=%s",
                     (str(uuid.uuid4()), "synthetic note " + proof, payload["actor"]["login"], alert_id))


def capture_of(lab, alert_id):
    with connect_role(lab, "zabalack_owner") as owner:
        return owner.execute("SELECT capture_id,intent,state,diagnostic_reason FROM zabalack.captures "
                             "WHERE alert_id=%s", (alert_id,)).fetchone()


# --- the sweep retires, and preserves what it must ---------------------------

def test_T41_sweep_retires_the_intent_and_keeps_the_generation_marker(lab):
    alert_id, _, payload = stage(lab)
    assert INTENT in attributes_of(lab, alert_id), "precondition: the intent is committed"
    assert sweep(lab)["retired"] >= 1
    after = attributes_of(lab, alert_id)
    assert INTENT not in after, "the staged intent must not survive the sweep"
    assert UNTIL not in after, "staged_until governed cleanup eligibility and goes with the intent"
    assert after[MARKER] == payload["action_id"], "the generation marker is retained for the alert's lifetime"


def test_T41_sweep_leaves_unrelated_attributes_and_source_identity_intact(lab):
    alert_id, event_id, _ = stage(lab)
    sweep(lab)
    after = attributes_of(lab, alert_id)
    assert after["runbook_url"] == "https://runbook.invalid/retire-lab"
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


def test_T41_sweep_is_bounded(lab):
    """The limit bounds how many rows a pass examines, not how many it retires.

    Rows still inside their window are examined and left alone, so a pass can
    retire fewer than it examined. Repeated passes converge because each one
    removes the intents it did retire.
    """
    for _ in range(4):
        stage(lab)
    bounded = sweep(lab, limit=2)
    assert bounded["examined"] == 2, "a pass must not examine more rows than its limit"
    assert bounded["retired"] <= bounded["examined"]
    total = 0
    for _ in range(8):
        total += sweep(lab, limit=2)["retired"]
    with connect_role(lab, "alerta_owner") as owner:
        remaining = owner.execute("SELECT count(*) FROM alerts WHERE attributes ? %s AND "
                                  "(attributes->>%s)::timestamptz<clock_timestamp()",
                                  (INTENT, UNTIL)).fetchone()[0]
    assert remaining == 0, "repeated bounded passes converge"


# --- the generation guard ----------------------------------------------------

def new_action(lab, alert_id, event_id, payload, *, order=None):
    """Insert the action row `verify_complete` would have produced."""
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


def project(lab, action_id):
    with connect_role(lab, "zabalack_worker") as worker:
        return worker.execute("SELECT zabalack.project(%s)", (action_id,)).fetchone()[0]["reason"]


def test_T41_projection_succeeds_for_the_current_generation(lab):
    """A positive control: the guard must not refuse everything."""
    alert_id, event_id, payload = stage(lab)
    action_id = new_action(lab, alert_id, event_id, payload)
    assert project(lab, action_id) == "projected"
    after = attributes_of(lab, alert_id)
    assert after["zabbix_ack_sync"] == "ok" and after["zabbix_ack_sync_action_id"] == action_id
    assert INTENT not in after, "projection retires the intent on the path that reaches it"
    assert after[MARKER] == action_id, "the marker survives a successful projection"


def test_T41_an_older_projection_is_refused_after_a_newer_staging_is_swept(lab):
    """The decisive ordering, at SQL level.

    A is staged and becomes an action. B is then staged on the same alert and
    never produces an action row — the request failed before its history write.
    The sweep retires B's intent, which is exactly where the old guard's evidence
    used to live. A must still be refused, with no expiry involved.
    """
    alert_id, event_id, first = stage(lab)
    action_id = new_action(lab, alert_id, event_id, first)
    second = intent_for(alert_id, event_id, issued=datetime.now(timezone.utc) - timedelta(minutes=5))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: second, MARKER: second["action_id"],
                             UNTIL: second["capture_expires_at"]}), alert_id))
    assert sweep(lab)["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id), "precondition: B's intent is gone"
    assert ledger(lab, alert_id)[0] == uuid.UUID(second["action_id"]), "the ledger holds B"
    assert project(lab, action_id) == "projection_action_generation_mismatch"
    assert attributes_of(lab, alert_id)["zabbix_ack_sync"] == "pending", "A must not overwrite B's state"


def test_T41_the_guard_does_not_expire(lab):
    """`execution_expires_at` bounds delivery; the projection queue has no deadline."""
    long_ago = datetime.now(timezone.utc) - timedelta(days=30)
    alert_id, event_id, first = stage(lab, issued=long_ago)
    action_id = new_action(lab, alert_id, event_id, first)
    second = intent_for(alert_id, event_id, issued=long_ago + timedelta(minutes=1))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: second, MARKER: second["action_id"],
                             UNTIL: second["capture_expires_at"]}), alert_id))
    sweep(lab)
    assert project(lab, action_id) == "projection_action_generation_mismatch", \
        "a projection resuming long after every delivery deadline is still refused"


def test_T41_ledger_advances_only(lab):
    """A replayed older generation cannot move the ledger backwards."""
    alert_id, event_id, first = stage(lab)
    sweep(lab)
    newer = intent_for(alert_id, event_id, issued=datetime.now(timezone.utc) - timedelta(minutes=2))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: newer, MARKER: newer["action_id"],
                             UNTIL: newer["capture_expires_at"]}), alert_id))
    sweep(lab)
    assert ledger(lab, alert_id)[0] == uuid.UUID(newer["action_id"])
    with connect_role(lab, "zabalack_owner") as owner:
        advanced = owner.execute("SELECT zabalack.note_staging(%s,%s,%s,'replay')",
                                 (alert_id, first["action_id"], first["capture_expires_at"])).fetchone()[0]
    assert advanced is False, "an older generation must be refused by the ledger"
    assert ledger(lab, alert_id)[0] == uuid.UUID(newer["action_id"])


def test_T41_capture_notes_the_ledger_in_the_same_transaction(lab):
    """The ledger does not depend on a sweep having run."""
    # A few seconds back, so the window capture_ack() hands note_staging is
    # inside its clock_timestamp() + 60s bound rather than exactly on it.
    alert_id, _, payload = stage(lab, issued=datetime.now(timezone.utc) - timedelta(seconds=5))
    assert ledger(lab, alert_id) is None
    fire_ack(lab, alert_id, payload)
    row = ledger(lab, alert_id)
    assert row is not None and row[0] == uuid.UUID(payload["action_id"]) and row[2] == "capture"


# --- preservation: the marker cannot be erased or rolled back ----------------

def test_T41_erasing_the_marker_cannot_permit_an_older_projection(lab):
    """`PUT /alert/<id>/attributes` can delete the attribute; the ledger decides.

    `db.update_attributes` drops keys whose value is null and writes the whole
    object back, so a `write:alerts` holder can remove the marker. The ledger is
    in the `zabalack` schema, on which `alerta_app` holds no privilege.
    """
    alert_id, event_id, first = stage(lab)
    action_id = new_action(lab, alert_id, event_id, first)
    second = intent_for(alert_id, event_id, issued=datetime.now(timezone.utc) - timedelta(minutes=5))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: second, MARKER: second["action_id"],
                             UNTIL: second["capture_expires_at"]}), alert_id))
    sweep(lab)
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes-%s WHERE id=%s", (MARKER, alert_id))
    assert MARKER not in attributes_of(lab, alert_id), "precondition: the marker was erased"
    assert project(lab, action_id) == "projection_action_generation_mismatch"


def test_T41_a_stale_attribute_dictionary_cannot_restore_an_older_generation(lab):
    """The replay `update_attributes` makes possible, at the storage layer.

    Any client holding an `Alert` loaded before a newer staging rewrites the row
    wholesale on its next attribute update, reinstating the older marker. The
    ledger is what keeps that from re-authorising the older projection.
    """
    alert_id, event_id, first = stage(lab)
    action_id = new_action(lab, alert_id, event_id, first)
    stale = attributes_of(lab, alert_id)
    second = intent_for(alert_id, event_id, issued=datetime.now(timezone.utc) - timedelta(minutes=5))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: second, MARKER: second["action_id"],
                             UNTIL: second["capture_expires_at"]}), alert_id))
    sweep(lab)
    with connect_role(lab, "alerta_app") as conn:
        # The whole-object replace db.update_attributes performs.
        conn.execute("UPDATE alerts SET attributes=%s WHERE id=%s", (Jsonb(stale), alert_id))
    assert attributes_of(lab, alert_id)[MARKER] == first["action_id"], "precondition: the replay landed"
    assert project(lab, action_id) == "projection_action_generation_mismatch", \
        "the replayed older marker must not re-authorise the older projection"


def test_T41_a_malformed_marker_counts_as_mismatched_never_as_absent(lab):
    """Present-but-unparseable is a mismatch, not a missing marker.

    This originally asserted `projection_generation_unknown`, which contradicted
    the rule in its own name: `unknown` is the verdict for a row carrying no
    generation evidence at all, and a corrupted marker is evidence that
    something wrote a generation here. Both verdicts refuse, so neither let a
    projection through, but the reason must say which case it is.
    """
    alert_id, event_id, payload = stage(lab)
    action_id = new_action(lab, alert_id, event_id, payload)
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("DELETE FROM zabalack.alert_staging WHERE alert_id=%s", (alert_id,))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({MARKER: "not-a-uuid"}), alert_id))
    assert project(lab, action_id) == "projection_action_generation_mismatch"


def test_T41_neither_authority_present_refuses_rather_than_permits(lab):
    alert_id, event_id, payload = stage(lab)
    action_id = new_action(lab, alert_id, event_id, payload)
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("DELETE FROM zabalack.alert_staging WHERE alert_id=%s", (alert_id,))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes-%s-%s WHERE id=%s", (MARKER, INTENT, alert_id))
    assert project(lab, action_id) == "projection_generation_unknown"
    assert attributes_of(lab, alert_id)["zabbix_ack_sync"] == "pending", "nothing was projected"


def test_T41_the_worker_reaches_retirement_through_worker_call(lab):
    """The sweep runs through the same coordination boundary as every other
    worker operation, so `zabalack_worker` needs no direct grant on it."""
    alert_id, _, _ = stage(lab)
    with connect_role(lab, "zabalack_worker") as worker:
        result = worker.execute("SELECT zabalack.worker_call('retire_intents',%s)",
                                (Jsonb({"limit": 1000, "grace_seconds": 0}),)).fetchone()[0]
    assert result["retired"] >= 1
    assert INTENT not in attributes_of(lab, alert_id)
    with connect_role(lab, "zabalack_worker") as worker:
        with pytest.raises(Exception, match="permission denied"):
            worker.execute("SELECT zabalack.retire_intents(10,0)")


def test_T41_an_ingestion_update_cannot_erase_the_marker(lab):
    """`strip_reserved` removes reserved keys from the inbound payload, and
    Alerta merges with `attributes || new`, so the stored marker survives."""
    from zabalack.normalizer import strip_reserved
    alert_id, event_id, payload = stage(lab)
    sweep(lab)
    inbound = {"source_system": "zabbix", "zabbix_instance": INSTANCE, "zabbix_event_id": event_id,
               "zabbix_trigger_id": TRIGGER, MARKER: str(uuid.uuid4()), INTENT: {"forged": True},
               "operator_note": "kept"}
    stripped = strip_reserved(inbound)
    assert MARKER not in stripped and INTENT not in stripped, "reserved keys never reach storage from ingestion"
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s", (Jsonb(stripped), alert_id))
    after = attributes_of(lab, alert_id)
    assert after[MARKER] == payload["action_id"], "ingestion must not replace the generation marker"
    assert after["operator_note"] == "kept", "ordinary inbound attributes still apply"
    assert INTENT not in after


def test_T41_project_skip_retains_the_marker(lab):
    """A skip is not a newer staging; it drops the intent and its cleanup clock."""
    from types import SimpleNamespace
    from zabalack.config import Settings
    from zabalack.normalizer import project_skip
    alert = SimpleNamespace(attributes={INTENT: {"action_id": "a"}, MARKER: "generation-1",
                                        UNTIL: "2026-01-01T00:00:00.000000Z", "runbook_url": "kept"})
    project_skip(alert, "skipped_disabled", Settings(config_revision="r1"))
    assert INTENT not in alert.attributes, "the intent goes"
    assert UNTIL not in alert.attributes, "so does the timestamp that governed its cleanup"
    assert alert.attributes[MARKER] == "generation-1", "the generation marker stays"
    assert alert.attributes["runbook_url"] == "kept"
    assert alert.attributes["zabbix_ack_sync"] == "skipped"


def test_T41_a_replay_predating_every_observation_of_a_staging_is_a_known_window(lab):
    """The boundary of this design, asserted rather than assumed.

    The ledger can only hold a generation something observed. `capture_ack()`
    records one synchronously with the capture, and the sweep records one when it
    visits the row — but a staging that is overwritten by a stale dictionary
    *before* either happens leaves no trace anywhere, because the only record it
    ever had was the attribute the replay just discarded.

    The rule still holds on its own terms: the marker the row carries is A, and
    A's projection matches it. Closing this window needs the staged generation
    recorded synchronously with the staging commit, which is a second trigger on
    `public.alerts` and a change to the installation procedure. Until then the
    exposure is bounded by the sweep interval, not indefinite, and it is recorded
    in A5 rather than described as closed.
    """
    alert_id, event_id, first = stage(lab)
    action_id = new_action(lab, alert_id, event_id, first)
    sweep(lab)
    stale = attributes_of(lab, alert_id)
    second = intent_for(alert_id, event_id, issued=datetime.now(timezone.utc) - timedelta(minutes=5))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: second, MARKER: second["action_id"],
                             UNTIL: second["capture_expires_at"]}), alert_id))
        # Replayed before any sweep or capture has seen the newer staging.
        conn.execute("UPDATE alerts SET attributes=%s WHERE id=%s", (Jsonb(stale), alert_id))
    assert ledger(lab, alert_id)[0] == uuid.UUID(first["action_id"]), \
        "nothing ever observed the newer generation, so the ledger cannot hold it"
    assert project(lab, action_id) == "projected", \
        "the marker and the ledger agree on A, so A projects; this is the window, stated"


def test_T41_the_readiness_manifest_must_be_resealed_after_the_migration(lab):
    """008 moves the catalog, so a manifest sealed before it no longer matches.

    `capture_health()` compares the live fingerprints against the sealed ones, so
    a deployment that applies the migration without re-sealing reports
    `functions_ok` false and its workers refuse to run — which is the intended
    failure, not a silent upgrade.
    """
    from pathlib import Path
    reseal = Path(__file__).resolve().parents[2] / "sql/install/12_reseal.sql"
    with connect_role(lab, "zabalack_owner") as owner:
        original = owner.execute("SELECT functions_md5 FROM zabalack.readiness_manifest").fetchone()[0]
        owner.execute("UPDATE zabalack.readiness_manifest SET functions_md5='pre-008-seal' WHERE singleton")
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "false", \
            "a stale seal must report unhealthy rather than pass"
        owner.execute(reseal.read_text())
        assert owner.execute("SELECT functions_md5 FROM zabalack.readiness_manifest").fetchone()[0] == original
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == "true"


def test_T41_the_reseal_refuses_a_tree_that_is_not_at_the_expected_schema(lab):
    """It seals what it was written for, or nothing."""
    from pathlib import Path
    reseal = (Path(__file__).resolve().parents[2] / "sql/install/12_reseal.sql").read_text()
    with connect_role(lab, "zabalack_owner") as owner:
        installed = owner.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0]
        owner.execute("UPDATE zabalack.installation SET schema_version=%s WHERE singleton",
                      (installed - 1,))
    try:
        with connect_role(lab, "zabalack_owner") as owner:
            with pytest.raises(Exception, match="expected zabalack schema version"):
                owner.execute(reseal)
    finally:
        # The refusal aborts its own transaction, so the restore needs a session
        # that is not already in a failed one. Restoring the version this lab
        # actually had, rather than a literal, keeps the next migration from
        # leaving the lab one version behind for every later module.
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("UPDATE zabalack.installation SET schema_version=%s WHERE singleton",
                          (installed,))
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0] == installed


# --- the staged attribute is API-writable, so nothing from it is trusted -----

def forge(lab, alert_id, payload, **overrides):
    """Write a forged intent the way a `write:alerts` client can.

    `PUT /alert/<id>/attributes` runs no plugin hook, so `strip_reserved()` never
    sees it and any JSON reaches the column.
    """
    forged = dict(payload, action_id=str(uuid.uuid4()), **overrides)
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: forged}), alert_id))
    return forged


@pytest.mark.parametrize("overrides,why", [
    ({"capture_expires_at": "infinity"}, "unbounded window"),
    ({"capture_expires_at": stamp(datetime.now(timezone.utc) + timedelta(days=3650))}, "far-future window"),
    ({"issued_at": stamp(datetime.now(timezone.utc) + timedelta(hours=1)),
      "capture_expires_at": stamp(datetime.now(timezone.utc) + timedelta(hours=1, seconds=60))},
     "future-dated staging"),
    ({"capture_expires_at": stamp(datetime.now(timezone.utc) - timedelta(minutes=10) + timedelta(hours=2))},
     "window wider than the signer's 60 seconds"),
])
def test_T41_a_forged_staged_window_cannot_pin_the_ledger(lab, overrides, why):
    """A forged window must neither become the authority nor exempt itself.

    Before this was bounded, a client could write `capture_expires_at: infinity`
    and the sweep promoted it: `note_staging` advances only on a greater
    timestamp, so no legitimate finite generation could ever replace it, and
    every later projection was refused as a generation mismatch — permanently,
    from one unauthenticated attribute write. The forged intent also exempted
    itself from retirement, because its window never expired.
    """
    alert_id, event_id, honest = stage(lab)
    sweep(lab)
    before = ledger(lab, alert_id)
    assert before[0] == uuid.UUID(honest["action_id"]), "precondition: the honest generation is recorded"

    forge(lab, alert_id, honest, **overrides)
    result = sweep(lab)
    assert result["noted"] == 0, f"a {why} must not advance the ledger"
    assert ledger(lab, alert_id)[0] == before[0], "the authority is unchanged"
    assert INTENT not in attributes_of(lab, alert_id), \
        f"a {why} is unvalidatable plaintext and must still be retired"

    # Issued a few seconds back so its window lands comfortably inside
    # note_staging's clock_timestamp() + 60s bound. At exactly `now` the value
    # sits on the bound and any skew between the Python and database clocks
    # decides the result — which is what made this case fail roughly one run in
    # twenty. The bound itself is a security check and is not widened for a test.
    later = intent_for(alert_id, event_id,
                       issued=datetime.now(timezone.utc) - timedelta(seconds=5))
    with connect_role(lab, "zabalack_owner") as owner:
        advanced = owner.execute("SELECT zabalack.note_staging(%s,%s,%s,'later')",
                                 (alert_id, later["action_id"], later["capture_expires_at"])).fetchone()[0]
    assert advanced is True, "a legitimate later generation must still be able to advance"


def test_T41_note_staging_refuses_an_unbounded_window_directly(lab):
    """Defence in depth: the bound holds at the ledger, not only at its caller."""
    alert_id, _, payload = stage(lab)
    with connect_role(lab, "zabalack_owner") as owner:
        for value in ("infinity", stamp(datetime.now(timezone.utc) + timedelta(days=1))):
            assert owner.execute("SELECT zabalack.note_staging(%s,%s,%s,'probe')",
                                 (alert_id, payload["action_id"], value)).fetchone()[0] is False, value
        assert owner.execute("SELECT count(*) FROM zabalack.alert_staging WHERE alert_id=%s",
                             (alert_id,)).fetchone()[0] == 0


def test_T41_a_forged_intent_leaves_no_generation_marker_behind(lab):
    """The restored marker must never carry an unvalidated action id."""
    alert_id, _, honest = stage(lab)
    sweep(lab)
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes-%s WHERE id=%s", (MARKER, alert_id))
    forged = forge(lab, alert_id, honest, capture_expires_at="infinity")
    sweep(lab)
    after = attributes_of(lab, alert_id)
    assert after.get(MARKER) != forged["action_id"], "a forged generation must not become the marker"
    assert INTENT not in after


def action_from_real_capture(lab, alert_id, event_id, payload):
    """Seed an action the way the worker does, from a capture the trigger made.

    `new_action` inserts its own capture row and so never runs `capture_ack()`.
    This path fires the real acknowledgement first, so the ledger is seeded
    synchronously by the trigger exactly as it is in a deployment.
    """
    fire_ack(lab, alert_id, payload)
    capture = capture_of(lab, alert_id)
    assert capture is not None and capture[3] is None, "precondition: a clean capture"
    assert ledger(lab, alert_id)[0] == uuid.UUID(payload["action_id"]), \
        "precondition: capture_ack seeded the ledger with this generation"
    with connect_role(lab, "zabalack_owner") as owner:
        effect_id = owner.execute(
            "INSERT INTO zabalack.effects(instance,event_id,generation,primary_action_id,latest_action_time) "
            "VALUES(%s,%s,(SELECT coalesce(max(generation),0)+1 FROM zabalack.effects WHERE instance=%s "
            "AND event_id=%s),%s,%s) RETURNING effect_id",
            (INSTANCE, event_id, INSTANCE, event_id, payload["action_id"],
             payload["issued_at"])).fetchone()[0]
        owner.execute("INSERT INTO zabalack.actor_identity(actor_ref,login) VALUES(%s,%s) "
                      "ON CONFLICT(actor_ref) DO NOTHING",
                      (payload["actor"]["login"], payload["actor"]["login"]))
        owner.execute(
            "INSERT INTO zabalack.actions(action_id,capture_id,capture_order,effect_id,alert_id,customer,"
            "instance,event_id,trigger_id,actor_ref,cohort_revision,policy_version,issued_at,execute_before,"
            "state) VALUES(%s,%s,(SELECT capture_order FROM zabalack.captures WHERE capture_id=%s),"
            "%s,%s,%s,%s,%s,%s,%s,%s,'1',%s,%s,'ok')",
            (payload["action_id"], capture[0], capture[0], effect_id, alert_id, CUSTOMER, INSTANCE,
             event_id, TRIGGER, payload["actor"]["login"], payload["cohort_revision"],
             payload["issued_at"], payload["execution_expires_at"]))
    return payload["action_id"]


def stage_over(lab, alert_id, event_id, *, issued=None):
    """Commit a newer staging on an existing alert, exactly as `take_action` does."""
    newer = intent_for(alert_id, event_id, issued=issued or datetime.now(timezone.utc))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: newer, MARKER: newer["action_id"],
                             UNTIL: newer["capture_expires_at"]}), alert_id))
    return newer


def test_T41_an_older_projection_is_refused_before_the_newer_staging_is_captured(lab):
    """The window the guard exists for, with the ledger still holding the older
    generation.

    A is captured, so `capture_ack()` has already seeded the ledger with A. B is
    then staged and its request has not reached its history write, so nothing has
    captured it and no sweep has seen it: the alert row's marker is the only
    record of B. If the marker is consulted only when the ledger is absent, A
    projects here — overwriting B's state and deleting B's intent — which is
    precisely the disclosure-adjacent overwrite T41's guard is meant to prevent.
    """
    alert_id, event_id, first = stage(lab, issued=datetime.now(timezone.utc) - timedelta(hours=6))
    action_id = action_from_real_capture(lab, alert_id, event_id, first)
    newer = stage_over(lab, alert_id, event_id)

    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT count(*) FROM zabalack.actions WHERE action_id=%s",
                             (newer["action_id"],)).fetchone()[0] == 0, "precondition: B has no action row"
        assert owner.execute("SELECT staged_action_id FROM zabalack.alert_staging WHERE alert_id=%s",
                             (alert_id,)).fetchone()[0] == uuid.UUID(first["action_id"]), \
            "precondition: nothing has advanced the ledger to B yet"
    assert attributes_of(lab, alert_id)[MARKER] == newer["action_id"], \
        "precondition: the alert row is the only record of B"

    assert project(lab, action_id) == "projection_action_generation_mismatch"
    after = attributes_of(lab, alert_id)
    assert after[INTENT]["action_id"] == newer["action_id"], "B's intent must survive A's refusal"
    assert after["zabbix_ack_sync"] == "pending", "A must not overwrite B's state"


def test_T41_a_malformed_marker_is_mismatched_even_with_a_ledger_entry(lab):
    """Present-but-unparseable is not the same as absent, whatever the ledger says."""
    alert_id, event_id, payload = stage(lab)
    action_id = action_from_real_capture(lab, alert_id, event_id, payload)
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({MARKER: "not-a-uuid"}), alert_id))
    assert project(lab, action_id) == "projection_action_generation_mismatch"


def test_T41_published_migrations_keep_their_registered_digests(lab):
    """A recorded migration's bytes must never change.

    `sql/migrate.py` refuses with `Migration drift` when a migration already in
    `zabalack.schema_migrations` no longer hashes to its recorded digest, and a
    database that applied the earlier bytes would be stranded on the superseded
    definitions with no way forward. Corrections ship as a new migration, so this
    asserts the published digests of every migration the manifest registers
    against the files on disk.
    """
    import hashlib
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "docs/sql_manifest.json").read_text())
    registered = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    for path in sorted((root / "sql/migrations").glob("*.sql")):
        key = path.relative_to(root).as_posix()
        assert key in registered, f"{key} is not registered in the SQL manifest"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == registered[key], \
            f"{key} no longer matches its registered digest; ship a forward migration instead"


def test_T41_the_retirement_cadence_is_deployment_state_not_a_worker_clock(lab):
    """One scan per interval across the deployment, not one per worker.

    The advisory lock inside `retire_intents` is released when its `worker_call`
    transaction ends, so it excludes an overlapping sweep but not a repeated one.
    A process-local timer does not help either: every worker starts with its own,
    so N workers each run the unindexed scan once per interval. The slot is
    claimed atomically in the installation row instead.
    """
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
        # A second worker, or the same one on its next cycle, must not rescan.
        assert worker_sweep() == {"examined": 0, "noted": 0, "retired": 0, "skipped": "not_due"}

    with connect_role(lab, "zabalack_owner") as owner:
        due = owner.execute("SELECT retirement_due_at>clock_timestamp() FROM zabalack.installation "
                            "WHERE singleton").fetchone()[0]
    assert due is True, "the claim moved the slot forward by the configured interval"

    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET retirement_due_at=clock_timestamp()"
                      "-interval '1 second' WHERE singleton")
    assert "skipped" not in worker_sweep(), "the slot reopens once the interval has elapsed"


def test_T41_the_repair_clears_a_ledger_poisoned_before_the_bounds_existed(lab):
    """Bounding what may enter the ledger does not repair what is already in it.

    008's backfill passes the staged attribute's `capture_expires_at` straight
    into `note_staging` with no bounds, and that attribute is API-writable, so a
    database that applied 008 can hold `staged_until = 'infinity'`. Advances are
    monotonic, so no legitimate finite generation can ever replace such a row and
    every later projection for that alert is refused permanently. Reproduced
    before the repair existed: a valid later generation returned false.
    """
    alert_id, event_id, payload = stage(lab)
    forged = str(uuid.uuid4())
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("INSERT INTO zabalack.alert_staging(alert_id,staged_action_id,staged_until,observed_by)"
                      " VALUES(%s,%s,'infinity','migration') ON CONFLICT(alert_id) DO UPDATE"
                      " SET staged_action_id=excluded.staged_action_id,staged_until='infinity'",
                      (alert_id, forged))
    # Issued a few seconds back so its window lands comfortably inside
    # note_staging's clock_timestamp() + 60s bound. At exactly `now` the value
    # sits on the bound and any skew between the Python and database clocks
    # decides the result — which is what made this case fail roughly one run in
    # twenty. The bound itself is a security check and is not widened for a test.
    later = intent_for(alert_id, event_id,
                       issued=datetime.now(timezone.utc) - timedelta(seconds=5))
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT zabalack.note_staging(%s,%s,%s,'later')",
                             (alert_id, later["action_id"], later["capture_expires_at"])).fetchone()[0] is False, \
            "precondition: the poisoned row blocks every finite generation"
        result = owner.execute("SELECT zabalack.repair_staging_ledger()").fetchone()[0]
    assert result["removed"] >= 1, "the repair must remove the unbounded entry"

    with connect_role(lab, "zabalack_owner") as owner:
        row = owner.execute("SELECT staged_action_id,isfinite(staged_until) FROM zabalack.alert_staging"
                            " WHERE alert_id=%s", (alert_id,)).fetchone()
    if row is not None:
        assert row[0] != uuid.UUID(forged), "the forged generation must not survive the repair"
        assert row[1] is True, "no non-finite window may remain"

    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT zabalack.note_staging(%s,%s,%s,'later')",
                             (alert_id, later["action_id"], later["capture_expires_at"])).fetchone()[0] is True, \
            "a legitimate generation must be able to advance again"


def test_T41_the_repair_is_idempotent_and_keeps_authenticated_entries(lab):
    """Running it twice changes nothing, and an authenticated row must survive.

    "Sound" means corroborated by evidence the signer produced: an action row, or
    a capture whose intent `capture_ack()` validated. An entry derived only from
    the API-writable staged attribute is deliberately not preserved here — see
    the forged-row case below for why.
    """
    alert_id, event_id, payload = stage(lab)
    action_from_real_capture(lab, alert_id, event_id, payload)
    before = ledger(lab, alert_id)
    assert before is not None, "precondition: a verified entry exists"
    with connect_role(lab, "zabalack_owner") as owner:
        first = owner.execute("SELECT zabalack.repair_staging_ledger()").fetchone()[0]
        second = owner.execute("SELECT zabalack.repair_staging_ledger()").fetchone()[0]
    assert first["removed"] == 0 and second["removed"] == 0, "a sound ledger needs no repair"
    assert ledger(lab, alert_id)[0] == before[0], "a valid entry must survive untouched"


def test_T41_the_repair_removes_a_forged_row_whose_window_looks_plausible(lab):
    """Bounds alone do not identify a forged generation.

    A row written through the API with a finite `capture_expires_at` inside the
    60-second window passes every range check, so on a database that applied 008
    it survived the earlier repair even though its `staged_action_id` corresponds
    to no action and no capture. `project()` then treated that UUID as the
    authority and refused the legitimate action indefinitely. Reproduced against
    the earlier repair: `{"removed": 0}` with the row still present.

    The repair therefore keeps only what authenticated evidence corroborates.
    """
    alert_id, event_id, payload = stage(lab)
    forged = str(uuid.uuid4())
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("INSERT INTO zabalack.alert_staging(alert_id,staged_action_id,staged_until,observed_by)"
                      " VALUES(%s,%s,%s,'migration') ON CONFLICT(alert_id) DO UPDATE"
                      " SET staged_action_id=excluded.staged_action_id,staged_until=excluded.staged_until",
                      (alert_id, forged, stamp(datetime.now(timezone.utc) + timedelta(seconds=30))))
        assert owner.execute("SELECT count(*) FROM zabalack.actions WHERE action_id=%s",
                             (forged,)).fetchone()[0] == 0, "precondition: no action corroborates it"
        result = owner.execute("SELECT zabalack.repair_staging_ledger()").fetchone()[0]
    assert result["removed"] >= 1, "a plausible-looking but uncorroborated generation must not survive"
    row = ledger(lab, alert_id)
    assert row is None or str(row[0]) != forged


def test_T41_the_repair_does_not_readmit_from_the_writable_attribute(lab):
    """The repair must not reinstate what it just removed from a forgeable source.

    The staged intent attribute is the same API-writable input the repair exists
    to undo, so rebuilding from it would restore the forged generation on the
    next line.
    """
    alert_id, event_id, payload = stage(lab)
    forged_intent = dict(payload, action_id=str(uuid.uuid4()))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: forged_intent}), alert_id))
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("SELECT zabalack.repair_staging_ledger()")
    row = ledger(lab, alert_id)
    assert row is None or str(row[0]) != forged_intent["action_id"], \
        "the repair must not read the generation back out of the alert attribute"


def test_T41_a_verified_generation_survives_the_repair(lab):
    """Only a verified generation corroborates itself.

    An action row exists only after `verify_complete`, which is where the
    signature is checked, so that is the corroboration the repair accepts.
    """
    alert_id, event_id, payload = stage(lab)
    action_from_real_capture(lab, alert_id, event_id, payload)
    before = ledger(lab, alert_id)
    assert before is not None and before[0] == uuid.UUID(payload["action_id"])
    with connect_role(lab, "zabalack_owner") as owner:
        result = owner.execute("SELECT zabalack.repair_staging_ledger()").fetchone()[0]
    assert result["removed"] == 0, "a verified generation must survive"
    assert ledger(lab, alert_id)[0] == before[0]


def test_T41_an_unverified_capture_does_not_corroborate_a_generation(lab):
    """`capture_ack()` never checks the MAC, so a capture is not authentication.

    It regex-matches the marker's structure and binds it to the committed history
    entry; the repository states the boundary outright — "SQL does not verify
    cryptography; Python tests prove the mandatory verify-before-promote trust
    boundary." A forged intent captured with a fabricated proof therefore
    produces a capture row that would corroborate its own forged generation, and
    the poisoned ledger entry would survive the repair and keep refusing
    legitimate projections.
    """
    alert_id, event_id, payload = stage(lab)
    fire_ack(lab, alert_id, payload)
    assert capture_of(lab, alert_id) is not None, "precondition: a capture row exists"
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT count(*) FROM zabalack.actions WHERE alert_id=%s",
                             (alert_id,)).fetchone()[0] == 0, "precondition: never verified"
        assert owner.execute("SELECT verified_action_id FROM zabalack.captures WHERE alert_id=%s",
                             (alert_id,)).fetchone()[0] is None
        result = owner.execute("SELECT zabalack.repair_staging_ledger()").fetchone()[0]
    assert result["removed"] >= 1, "an unverified capture must not corroborate its own generation"
    assert ledger(lab, alert_id) is None


def test_T41_the_repair_installs_no_fallback_generation(lab):
    """Deleting must not downgrade an alert to an older action.

    Where the ledger held an uncaptured newer generation B, removing B and
    restoring older A left both authorities agreeing on A once a whole-object
    replay dropped B's marker, so `project(A)` overwrote B's pending state —
    worse than leaving the entry alone. The repair deletes and never restores:
    the alert falls to its marker, and a missing marker refuses.
    """
    alert_id, event_id, first = stage(lab, issued=datetime.now(timezone.utc) - timedelta(hours=6))
    action_id = action_from_real_capture(lab, alert_id, event_id, first)
    newer = stage_over(lab, alert_id, event_id)
    sweep(lab)
    assert ledger(lab, alert_id)[0] == uuid.UUID(newer["action_id"]), "precondition: the ledger observed B"

    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("SELECT zabalack.repair_staging_ledger()")
        restored = owner.execute("SELECT staged_action_id FROM zabalack.alert_staging WHERE alert_id=%s",
                                 (alert_id,)).fetchone()
    assert restored is None or restored[0] != uuid.UUID(first["action_id"]), \
        "the older action must not be installed as the authority"

    # The replay that drops B's marker, which is what made the fallback unsafe.
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes-%s-%s WHERE id=%s", (MARKER, INTENT, alert_id))
    assert project(lab, action_id) in {"projection_generation_unknown",
                                       "projection_action_generation_mismatch"}, \
        "with no authority left, the older projection must refuse rather than proceed"
    assert attributes_of(lab, alert_id)["zabbix_ack_sync"] == "pending", "B's state must survive"
