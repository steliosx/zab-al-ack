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


@pytest.fixture
def lab(pg_lab):
    with connect_role(pg_lab, "zabalack_owner") as owner:
        owner.execute("""INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,
                      host_ids,group_ids,enabled) VALUES(%s,%s,%s,'retire-r1','1','["980003"]','["980004"]',true)
                      ON CONFLICT(customer,instance,trigger_id) DO UPDATE SET enabled=true""",
                      (CUSTOMER, INSTANCE, TRIGGER))
    return pg_lab


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
    alert_id, _, payload = stage(lab, issued=datetime.now(timezone.utc))
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
    alert_id, event_id, payload = stage(lab)
    action_id = new_action(lab, alert_id, event_id, payload)
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("DELETE FROM zabalack.alert_staging WHERE alert_id=%s", (alert_id,))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({MARKER: "not-a-uuid"}), alert_id))
    assert project(lab, action_id) == "projection_generation_unknown"


def test_T41_neither_authority_present_refuses_rather_than_permits(lab):
    alert_id, event_id, payload = stage(lab)
    action_id = new_action(lab, alert_id, event_id, payload)
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("DELETE FROM zabalack.alert_staging WHERE alert_id=%s", (alert_id,))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes-%s-%s WHERE id=%s", (MARKER, INTENT, alert_id))
    assert project(lab, action_id) == "projection_generation_unknown"
    assert attributes_of(lab, alert_id)["zabbix_ack_sync"] == "pending", "nothing was projected"
