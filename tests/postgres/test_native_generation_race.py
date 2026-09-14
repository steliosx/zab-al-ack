"""T41 — the decisive generation race, on native PostgreSQL (amendment A5).

Stage A, then stage B on the same alert. B never produces an action row: its
request failed before the history write, so the only record of it is the staged
attribute the sweep is about to remove. A's projection then resumes long after
every delivery deadline has passed — `execution_expires_at` bounds delivery,
while `projection_due_at` carries no equivalent deadline, so a projection queued
before a prolonged outage can resume arbitrarily later. A must not overwrite B's
state.

Native only. The point of these cases is two sessions contending for the same
committed row: one sweeping, one projecting, in both orders. PGlite multiplexes
sessions onto a single backend and cannot establish it, so they are skipped
rather than reported under the WASM layer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import uuid

from psycopg.types.json import Jsonb
import pytest

from conftest import connect_role
from test_intent_retirement import (CUSTOMER, INSTANCE, INTENT, MARKER, TRIGGER, UNTIL,
                                    action_from_real_capture, attributes_of, cleanup, enrol,
                                    intent_for, ledger, new_action, stage, stage_over, sweep)

pytestmark = pytest.mark.postgres

BARRIER_TIMEOUT = 30


@pytest.fixture
def native(pg_lab):
    if pg_lab["backend"] != "native_postgres18":
        pytest.skip("NOT_RUN_NATIVE: the sweep/projection race needs two concurrent sessions; "
                    "PGlite multiplexes onto one backend and cannot establish it")
    enrol(pg_lab)
    yield pg_lab
    cleanup(pg_lab)


def two_generations(lab):
    """A becomes an action; B is staged afterwards and never reaches a capture.

    A is issued more than four hours ago, so its delivery lifetime has long
    expired by the time its projection resumes.
    """
    long_ago = datetime.now(timezone.utc) - timedelta(hours=6)
    alert_id, event_id, first = stage(lab, issued=long_ago)
    action_id = new_action(lab, alert_id, event_id, first)
    second = intent_for(alert_id, event_id, issued=long_ago + timedelta(minutes=1))
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: second, MARKER: second["action_id"],
                             UNTIL: second["capture_expires_at"]}), alert_id))
    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT count(*) FROM zabalack.actions WHERE action_id=%s",
                             (second["action_id"],)).fetchone()[0] == 0, \
            "precondition: B never produced an action row"
    return alert_id, action_id, first, second


def run_concurrently(first_step, second_step):
    """Start `first_step`, let it take its locks, then run `second_step`.

    Thread exceptions are captured rather than left to die silently, and both
    threads are joined so a hung lock fails the case instead of passing it.
    """
    started, release, outcome = threading.Event(), threading.Event(), {}

    def leader():
        try:
            started.set()
            outcome["first"] = first_step()
        except BaseException as error:
            outcome["first_error"] = error
        finally:
            release.set()

    worker = threading.Thread(target=leader, daemon=True)
    worker.start()
    assert started.wait(timeout=BARRIER_TIMEOUT), "the leading session never started"
    try:
        outcome["second"] = second_step()
    except BaseException as error:  # pragma: no cover - surfaced by the assertions below
        outcome["second_error"] = error
    assert release.wait(timeout=BARRIER_TIMEOUT), "the leading session did not finish"
    worker.join(timeout=BARRIER_TIMEOUT)
    assert not worker.is_alive(), "the leading session did not terminate"
    assert "first_error" not in outcome, outcome.get("first_error")
    assert "second_error" not in outcome, outcome.get("second_error")
    return outcome


def project_in_own_session(lab, action_id):
    def step():
        with connect_role(lab, "zabalack_worker") as worker:
            return worker.execute("SELECT zabalack.project(%s)", (action_id,)).fetchone()[0]["reason"]
    return step


def sweep_in_own_session(lab):
    def step():
        return sweep(lab, grace=0)
    return step


@pytest.mark.parametrize("order", ["sweep_first", "projection_first"])
def test_T41_native_an_older_projection_never_overwrites_a_newer_staging(native, order):
    """The decisive case, run in both lock orders.

    Whichever session reaches the row first, the outcome must be the same: the
    ledger holds B, and A's projection is refused. `retire_intents` takes the
    alert row with SKIP LOCKED and `project()` takes it with a plain FOR UPDATE,
    so the two defer rather than deadlock, and neither ordering can produce a
    projected A.
    """
    lab = native
    alert_id, action_id, first, second = two_generations(lab)
    steps = (sweep_in_own_session(lab), project_in_own_session(lab, action_id))
    if order == "projection_first":
        steps = (project_in_own_session(lab, action_id), sweep_in_own_session(lab))
    run_concurrently(*steps)
    # The sweep may have skipped a locked row; a second pass settles it either way.
    sweep(lab, grace=0)
    after = attributes_of(lab, alert_id)
    assert INTENT not in after, "B's intent must not survive"
    assert ledger(lab, alert_id)[0] == uuid.UUID(second["action_id"]), "the ledger holds B, not A"
    assert after["zabbix_ack_sync"] == "pending", "A must not have overwritten B's state"
    assert after["zabbix_ack_sync_action_id"] == first["action_id"], "no projection wrote A's outcome"
    with connect_role(lab, "zabalack_worker") as worker:
        reason = worker.execute("SELECT zabalack.project(%s)", (action_id,)).fetchone()[0]["reason"]
    assert reason == "projection_action_generation_mismatch", \
        "A is still refused once the race has settled, six hours past its delivery deadline"


def test_T41_native_a_stale_attribute_dictionary_replayed_during_the_race(native):
    """The replay `db.update_attributes` makes possible, under contention.

    The sweep observes B first, which is the defended case: once anything has
    recorded the generation, a client holding the dictionary from before B's
    staging can rewrite the row wholesale and restore A's marker, but cannot move
    the authority. `zabalack.alert_staging` is in the `zabalack` schema, which
    `alerta_app` has no privilege on.

    The undefended window — a replay landing before *any* observation of B — is
    asserted separately and deliberately in
    `test_intent_retirement.test_T41_a_replay_predating_every_observation_of_a_staging_is_a_known_window`.
    """
    lab = native
    alert_id, action_id, first, second = two_generations(lab)
    assert sweep(lab, grace=0)["retired"] >= 1, "precondition: the sweep observed B"
    assert ledger(lab, alert_id)[0] == uuid.UUID(second["action_id"])
    stale = attributes_of(lab, alert_id)
    stale.pop(INTENT, None)
    stale[MARKER] = first["action_id"]

    def replay():
        with connect_role(lab, "alerta_app") as conn:
            conn.execute("UPDATE alerts SET attributes=%s WHERE id=%s", (Jsonb(stale), alert_id))
        return "replayed"

    outcome = run_concurrently(replay, project_in_own_session(lab, action_id))
    assert attributes_of(lab, alert_id)[MARKER] == first["action_id"], "precondition: the replay landed"
    assert ledger(lab, alert_id)[0] == uuid.UUID(second["action_id"]), \
        "the replay must not move the authoritative generation backwards"
    assert outcome["second"] == "projection_action_generation_mismatch"
    with connect_role(lab, "zabalack_worker") as worker:
        reason = worker.execute("SELECT zabalack.project(%s)", (action_id,)).fetchone()[0]["reason"]
    assert reason == "projection_action_generation_mismatch", "and still refused once the race has settled"
    assert attributes_of(lab, alert_id)["zabbix_ack_sync"] == "pending"


def test_T41_native_only_one_sweep_runs_at_a_time(native):
    """The scan is unindexed, so concurrent sweeps must not multiply it.

    `public.alerts` carries no index for the staged-intent predicate and
    `zabalack_owner` cannot create one, so each pass is a sequential scan. One
    sweep per worker per cycle would mean every worker scanning the whole table.
    An advisory transaction lock lets exactly one proceed; the rest return
    immediately and wait for the next cycle.

    Native only: observing the contention needs a second session while the first
    holds an open transaction, which PGlite's single backend cannot provide.
    """
    lab = native
    stage(lab)
    with connect_role(lab, "zabalack_owner") as holder:
        holder.execute("BEGIN")
        first = holder.execute("SELECT zabalack.retire_intents(1000,0)").fetchone()[0]
        assert "skipped" not in first, "the lock holder does the work"
        with connect_role(lab, "zabalack_owner") as other:
            second = other.execute("SELECT zabalack.retire_intents(1000,0)").fetchone()[0]
        assert second == {"examined": 0, "noted": 0, "retired": 0, "skipped": "sweep_in_progress"}
        holder.execute("COMMIT")
    assert "skipped" not in sweep(lab), "the lock is released with its transaction"


def test_T41_native_an_older_projection_is_refused_before_the_newer_staging_is_captured(native):
    """The uncaptured-newer-staging window, on native PostgreSQL.

    A is seeded through a real `capture_ack()`, so the ledger already holds A.
    B is then staged and its request never reaches its history write, so nothing
    has captured it and no sweep has observed it — the alert row's marker is the
    only record that B exists. A's delayed projection must refuse and must leave
    B's intent in place.

    The PGlite module covers the same sequence; this runs it under the deployed
    roles with a real backend per session, alongside the concurrent orderings.
    """
    lab = native
    alert_id, event_id, first = stage(lab, issued=datetime.now(timezone.utc) - timedelta(hours=6))
    action_id = action_from_real_capture(lab, alert_id, event_id, first)
    newer = stage_over(lab, alert_id, event_id)

    with connect_role(lab, "zabalack_owner") as owner:
        assert owner.execute("SELECT count(*) FROM zabalack.actions WHERE action_id=%s",
                             (newer["action_id"],)).fetchone()[0] == 0, "precondition: B has no action row"
        assert owner.execute("SELECT staged_action_id FROM zabalack.alert_staging WHERE alert_id=%s",
                             (alert_id,)).fetchone()[0] == uuid.UUID(first["action_id"]), \
            "precondition: the ledger still holds A"

    with connect_role(lab, "zabalack_worker") as worker:
        reason = worker.execute("SELECT zabalack.project(%s)", (action_id,)).fetchone()[0]["reason"]
    assert reason == "projection_action_generation_mismatch"
    after = attributes_of(lab, alert_id)
    assert after[INTENT]["action_id"] == newer["action_id"], "B's intent must survive"
    assert after["zabbix_ack_sync"] == "pending", "A must not overwrite B's state"
