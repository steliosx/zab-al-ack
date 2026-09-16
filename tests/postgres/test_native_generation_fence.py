"""T41 — the generation fence under two sessions, on native PostgreSQL (A5).

Native only. Each case here needs two sessions contending for the same
committed row or lock — a sweep against a projection in both orders, two
stagings of one alert, a staging against a projection in both orders — or a
second database to upgrade. PGlite multiplexes sessions onto a single backend
and cannot establish any of them, so they are skipped rather than reported
under the WASM layer.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
from pathlib import Path
import shutil
import threading
import time
import uuid

import psycopg
from psycopg import conninfo, sql
from psycopg.types.json import Jsonb
import pytest

from conftest import connect_role
from test_generation_fence import (ACTION_KEY, INTENT, attributes_of, cleanup, enrol, fence,
                                   fire_ack, intent_for, new_action, newest, project, stage,
                                   settle_through_worker_call, stage_over, stage_pre_fence, sweep,
                                   verify_through_worker_call)

pytestmark = pytest.mark.postgres

ROOT = Path(__file__).resolve().parents[2]
BARRIER_TIMEOUT = 30


@pytest.fixture
def native(pg_lab):
    if pg_lab["backend"] != "native_postgres18":
        pytest.skip("NOT_RUN_NATIVE: these cases need two concurrent sessions or a second database; "
                    "PGlite multiplexes onto one backend and cannot establish either")
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
    second = stage_over(lab, alert_id, event_id, issued=long_ago + timedelta(minutes=1))
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
        return project(lab, action_id)
    return step


def sweep_in_own_session(lab):
    def step():
        return sweep(lab, grace=0)
    return step


class HeldTransaction:
    """A session that runs one statement and holds its transaction open until released."""

    def __init__(self, lab, role, statement, params=()):
        self.lab, self.role, self.statement, self.params = lab, role, statement, params
        self.holding, self.release, self.result, self.error = threading.Event(), threading.Event(), None, None
        self.conn = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            with psycopg.connect(self.lab["admin_dsn"]) as conn:
                self.conn = conn
                conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(self.role)))
                self.result = conn.execute(self.statement, self.params).fetchone()
                self.holding.set()
                assert self.release.wait(timeout=BARRIER_TIMEOUT), "held transaction never released"
                conn.commit()
        except BaseException as error:  # pragma: no cover - surfaced by the assertions below
            self.error = error
            self.holding.set()

    def __enter__(self):
        self.thread.start()
        assert self.holding.wait(timeout=BARRIER_TIMEOUT), "the holding session never took its locks"
        assert self.error is None, self.error
        return self

    def probe(self, statement, params=()):
        """One more statement in the held transaction, while the holder waits.

        psycopg serialises use of one connection across threads, and the holding
        thread is blocked on an event, not on the connection.
        """
        return self.conn.execute(statement, params).fetchone()

    def run(self, statement, params=None):
        """One more statement in the held transaction that returns no rows."""
        self.conn.execute(statement, params)

    def __exit__(self, *exc):
        self.release.set()
        self.thread.join(timeout=BARRIER_TIMEOUT)
        assert not self.thread.is_alive(), "the holding session did not commit"
        assert self.error is None, self.error


@pytest.mark.parametrize("order", ["sweep_first", "projection_first"])
def test_T41_native_an_older_projection_never_overwrites_a_newer_staging(native, order):
    """Stage A, then B; retire B's intent without a B action row; resume A's
    projection six hours later. The order is enforced, not hoped for: the
    leading session holds its transaction open until the other has been seen
    to wait, or to skip. The sweep takes the row with SKIP LOCKED and never the
    generation lock; project() takes the generation lock and then the row. In
    both orders A cannot overwrite B's state."""
    alert_id, action_id, first, second = two_generations(native)
    results = []
    if order == "sweep_first":
        # The sweep holds B's row; A's projection queues behind it and then sees B.
        with HeldTransaction(native, "zabalack_owner", "SELECT zabalack.retire_intents(1000,0)") as held:
            assert held.result[0]["retired"] >= 1, "precondition: the sweep retired B's intent"
            blocked = threading.Thread(target=lambda: results.append(project(native, action_id)), daemon=True)
            blocked.start()
            blocked.join(timeout=2)
            assert blocked.is_alive(), "the projection must wait for the row the sweep holds"
        blocked.join(timeout=BARRIER_TIMEOUT)
        assert not blocked.is_alive() and results == ["projection_action_generation_mismatch"]
    else:
        # The projection holds the generation lock and the row; the sweep skips
        # the row rather than waiting on it, and retires it on its next pass.
        with HeldTransaction(native, "zabalack_worker", "SELECT zabalack.project(%s)", (action_id,)) as held:
            assert INTENT in attributes_of(native, alert_id), "precondition: B's intent is on the row"
            assert sweep(native, grace=0)["deferred"] >= 1, "the held row is counted as deferred, not omitted"
            assert INTENT in attributes_of(native, alert_id), "SKIP LOCKED leaves the held row to the next pass"
        assert held.result[0]["reason"] == "projection_action_generation_mismatch"
        assert sweep(native, grace=0)["retired"] >= 1
    after = attributes_of(native, alert_id)
    assert INTENT not in after, "B's intent is retired by the sweep"
    assert after[ACTION_KEY] == second["action_id"] and after["zabbix_ack_sync"] == "pending"
    assert newest(native, alert_id) == second["action_id"]
    with connect_role(native, "zabalack_owner") as owner:
        assert owner.execute("SELECT projection_reason FROM zabalack.actions WHERE action_id=%s",
                             (action_id,)).fetchone()[0] == "projection_action_generation_mismatch"


def test_T41_native_a_stale_attribute_dictionary_replayed_during_the_race(native):
    """A client holding A's dictionary rewrites the row wholesale while B's
    intent is being retired and A's projection resumes. The row then carries A;
    the fence still carries B, and A stays refused."""
    alert_id, action_id, first, second = two_generations(native)
    stale = dict(attributes_of(native, alert_id))
    stale[INTENT], stale[ACTION_KEY] = first, first["action_id"]

    def replay_then_project():
        with connect_role(native, "alerta_app") as conn:
            conn.execute("UPDATE alerts SET attributes=%s WHERE id=%s", (Jsonb(stale), alert_id))
        return project(native, action_id)

    outcome = run_concurrently(sweep_in_own_session(native), replay_then_project)
    assert outcome["second"] == "projection_action_generation_mismatch"
    assert newest(native, alert_id) == second["action_id"]
    assert attributes_of(native, alert_id).get("zabbix_ack_sync") != "ok"


def test_T41_native_two_stagings_of_one_alert_serialise_on_the_generation_lock(native):
    """The second staging blocks until the first commits and receives the higher
    sequence: the database assigns the order, and the newest generation is the
    one whose staging committed last."""
    alert_id, event_id, first = stage(native)
    second = intent_for(alert_id, event_id)
    third = intent_for(alert_id, event_id)
    with HeldTransaction(native, "alerta_app", "SELECT zabalack.stage_generation(%s,%s,%s)",
                         (alert_id, second["action_id"], Jsonb(second))) as held:
        def contend():
            with connect_role(native, "alerta_app") as conn:
                return conn.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                                    (alert_id, third["action_id"], Jsonb(third))).fetchone()[0]
        blocked = threading.Thread(target=lambda: results.append(contend()), daemon=True)
        results = []
        blocked.start()
        blocked.join(timeout=2)
        assert blocked.is_alive(), "the second staging must wait for the first to commit"
    blocked.join(timeout=BARRIER_TIMEOUT)
    assert not blocked.is_alive() and results, "the second staging completed once the first committed"
    assert results[0] > held.result[0]
    assert [row[0] for row in fence(native, alert_id)] == [first["action_id"], second["action_id"], third["action_id"]]
    assert newest(native, alert_id) == third["action_id"]


GENERATION_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended('zabalack.generation:'||%s,0))"
TRY_GENERATION_LOCK = "SELECT pg_try_advisory_xact_lock(hashtextextended('zabalack.generation:'||%s,0))"
ROW_LOCK = "SELECT id FROM public.alerts WHERE id=%s FOR UPDATE"
ROW_LOCK_NOWAIT = "SELECT id FROM public.alerts WHERE id=%s FOR UPDATE NOWAIT"


@pytest.mark.parametrize("contender", ["projection", "staging"])
@pytest.mark.parametrize("held", ["row", "generation"])
def test_T41_native_staging_and_projection_take_the_generation_lock_then_the_row(native, contender, held):
    """Both take the generation lock first and the alert row second, so they
    can only queue, never deadlock. Each is made to wait on one lock held
    alone, and probed from the holding session: waiting on the row, it must
    already hold the generation lock (the holder's try-lock fails); waiting on
    the generation lock, it must hold no row lock (the holder's NOWAIT row
    lock succeeds). Either function taking the row first fails the case."""
    alert_id, event_id, first = stage(native, issued=datetime.now(timezone.utc))
    fire_ack(native, alert_id, first)
    verify_through_worker_call(native, first)
    second = intent_for(alert_id, event_id)
    results = []
    if contender == "projection":
        def contend():
            results.append(project(native, first["action_id"]))
    else:
        def contend():
            with connect_role(native, "alerta_app") as conn:
                results.append(conn.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                                            (alert_id, second["action_id"], Jsonb(second))).fetchone()[0])
    with HeldTransaction(native, "alerta_app", ROW_LOCK if held == "row" else GENERATION_LOCK, (alert_id,)) as holder:
        blocked = threading.Thread(target=contend, daemon=True)
        blocked.start()
        blocked.join(timeout=2)
        assert blocked.is_alive(), f"the {contender} must wait for the held {held} lock"
        if held == "row":
            assert holder.probe(TRY_GENERATION_LOCK, (alert_id,))[0] is False, \
                f"the {contender} waits on the row already holding the generation lock"
        else:
            try:
                holder.probe(ROW_LOCK_NOWAIT, (alert_id,))
            except psycopg.errors.LockNotAvailable:
                pytest.fail(f"the {contender} took the row before the generation lock")
    blocked.join(timeout=BARRIER_TIMEOUT)
    assert not blocked.is_alive() and results, f"the {contender} completed once the holder committed"
    if contender == "projection":
        assert results == ["projected"], "A was current: the holder changed no generation"
        assert newest(native, alert_id) == first["action_id"]
    else:
        assert results[0] > 0 and newest(native, alert_id) == second["action_id"]
        assert [row[0] for row in fence(native, alert_id)] == [first["action_id"], second["action_id"]]


def test_T41_native_only_one_sweep_runs_at_a_time(native):
    stage(native)
    with HeldTransaction(native, "zabalack_owner",
                         "SELECT pg_advisory_xact_lock(hashtextextended('zabalack.retire_intents',0))"):
        assert sweep(native, grace=0) == {"examined": 0, "retired": 0, "deferred": 0, "skipped": "sweep_in_progress"}
    assert sweep(native, grace=0)["retired"] >= 1


def test_T41_native_a_deferred_row_keeps_the_slot_due(native):
    """A row another session holds is skipped by the sweep, not omitted: the
    pass reports it deferred and the slot stays due, so the next cycle retires
    it instead of the next interval. Without that, an under-limit pass closed
    the slot for the whole interval over the row it never reached."""
    alert_id, _, _ = stage(native)
    with connect_role(native, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET retirement_due_at=NULL,retirement_interval_seconds=900 "
                      "WHERE singleton")

    def worker_sweep():
        with connect_role(native, "zabalack_worker") as worker:
            return worker.execute("SELECT zabalack.worker_call('retire_intents',%s)",
                                  (Jsonb({"limit": 1000, "grace_seconds": 0}),)).fetchone()[0]

    with HeldTransaction(native, "alerta_app", ROW_LOCK, (alert_id,)):
        held = worker_sweep()
        assert "skipped" not in held and held["deferred"] >= 1 and held.get("continues") is True, held
        assert INTENT in attributes_of(native, alert_id), "the held row was left to the next pass"
        with connect_role(native, "zabalack_owner") as owner:
            assert owner.execute("SELECT retirement_due_at<=clock_timestamp() FROM zabalack.installation "
                                 "WHERE singleton").fetchone()[0] is True, "a deferred row leaves the slot due"
    released = worker_sweep()
    assert "skipped" not in released and released["retired"] >= 1 and released["deferred"] == 0, released
    assert INTENT not in attributes_of(native, alert_id)
    assert worker_sweep() == {"examined": 0, "retired": 0, "deferred": 0, "skipped": "not_due"}


def test_T41_native_a_held_row_does_not_hold_back_the_rows_behind_it(native):
    """A mixed batch on a live table: one row another session holds, rows still
    inside their windows, and more due rows than one batch. While the hold
    lasts, every pass retires due rows — the held row costs a pass at most its
    own place, the rows inside their windows cost none — until only the held
    row is left, reported deferred with the slot kept due; released, the next
    pass retires it. Before this the batch was chosen before expiry or locks
    were looked at, and held or fresh rows at the front of the heap could keep
    the due rows behind them out of every pass."""
    sweep(native, grace=0)   # rows other modules left due are retired first
    held_id, _, _ = stage(native)
    fresh = [stage(native, issued=datetime.now(timezone.utc))[0] for _ in range(3)]
    due = [stage(native)[0] for _ in range(5)]
    with HeldTransaction(native, "alerta_app", ROW_LOCK, (held_id,)):
        passes = []
        while any(INTENT in attributes_of(native, alert_id) for alert_id in due):
            passes.append(sweep(native, grace=0, limit=2))
            assert passes[-1]["retired"] >= 1, "one held row against a batch of two: every pass retires the other place"
            # Five due rows at one per pass beside the held row is the worst case;
            # the slack is for a row another module staged that came due meanwhile.
            assert len(passes) <= 8, passes
        assert INTENT in attributes_of(native, held_id) and all(INTENT in attributes_of(native, a) for a in fresh)
        last = sweep(native, grace=0, limit=2)
        assert last["deferred"] >= 1, last
        assert last["examined"] == last["retired"], "every row the pass examines is one it retires"
        assert INTENT in attributes_of(native, held_id)
    assert sweep(native, grace=0, limit=2)["retired"] >= 1
    assert INTENT not in attributes_of(native, held_id)
    assert all(INTENT in attributes_of(native, alert_id) for alert_id in fresh)


def wait_until_waiting_on(lab, relation):
    """Block until some session is waiting for a lock on `relation`."""
    deadline = time.monotonic() + BARRIER_TIMEOUT
    with psycopg.connect(lab["admin_dsn"], autocommit=True) as admin:
        while time.monotonic() < deadline:
            if admin.execute("SELECT count(*) FROM pg_locks WHERE relation=%s::regclass AND NOT granted",
                             (relation,)).fetchone()[0]:
                return
            time.sleep(0.02)
    raise AssertionError(f"no session came to wait on {relation}")


def test_T41_native_a_staging_committed_during_the_sweep_is_left_alone(native):
    """The concurrent acknowledgement race. A sweep begins while a row's previous
    intent is due; before its scan reaches the row, an acknowledgement stages a
    new intent there and commits. Reaching the row, the sweep must test it
    against the clock and leave the new staging inside its window. Against the
    time saved when the sweep began, the new intent read as issued in the
    future — unvalidatable — and was retired at once, and the acknowledgement's
    capture then found no intent on the row.

    The lab holds the sweep at its scan with an ACCESS EXCLUSIVE lock on the
    table, taken by the staging session and released by its commit; the sweep
    is the shipped function, unchanged, and the staging goes through the
    protected operation. The sweep is seen waiting before the intent is issued,
    so its start precedes the staging by construction."""
    alert_id, event_id, previous = stage(native)   # its previous intent is due
    outcome = {}

    def sweep_in_flight():
        outcome["sweep"] = sweep(native, grace=0)

    sweeper = threading.Thread(target=sweep_in_flight, daemon=True)
    with HeldTransaction(native, "alerta_app", "SELECT 1") as staging:
        staging.run("LOCK TABLE public.alerts IN ACCESS EXCLUSIVE MODE")
        sweeper.start()
        wait_until_waiting_on(native, "public.alerts")
        fresh = intent_for(alert_id, event_id)   # issued now: after the sweep began
        staging.run("SELECT zabalack.stage_generation(%s,%s,%s)", (alert_id, fresh["action_id"], Jsonb(fresh)))
    sweeper.join(timeout=BARRIER_TIMEOUT)
    assert not sweeper.is_alive(), "the sweep did not finish once the staging committed"
    assert "skipped" not in outcome["sweep"], outcome["sweep"]
    after = attributes_of(native, alert_id)
    assert after.get(INTENT) == fresh, "a staging committed while the sweep was in flight is left inside its window"
    assert after[ACTION_KEY] == fresh["action_id"]
    assert [row[0] for row in fence(native, alert_id)] == [previous["action_id"], fresh["action_id"]]
    # Once its window has closed, the same row is retired like any other.
    with connect_role(native, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                     (Jsonb({INTENT: intent_for(alert_id, event_id, issued=datetime.now(timezone.utc)
                                                - timedelta(minutes=10), action_id=fresh["action_id"])}),
                      alert_id))
    assert sweep(native, grace=0)["retired"] >= 1
    assert INTENT not in attributes_of(native, alert_id)


def test_T41_native_the_retry_bound_covers_a_staging_committed_during_the_pass(native):
    """A pass asked to count is held at its scan while an acknowledgement stages
    a new intent on a row and commits; the count sees that row, and the retry
    the pass schedules must be no earlier than the moment that row can be due
    — its window runs from its own issue, after the pass began. A bound taken
    from the pass's entry scheduled the retry before that, and the retry found
    the row still inside its window and pushed the next attempt out again."""
    alert_id, event_id, _ = stage(native)   # its previous intent is due
    with connect_role(native, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET retirement_due_at=NULL,retirement_interval_seconds=900 "
                      "WHERE singleton")
    outcome = {}

    def pass_in_flight():
        with connect_role(native, "zabalack_worker") as worker:
            outcome["pass"] = worker.execute(
                "SELECT zabalack.worker_call('retire_intents',%s)",
                (Jsonb({"limit": 1000, "grace_seconds": 0, "report_remaining": True}),)).fetchone()[0]

    sweeper = threading.Thread(target=pass_in_flight, daemon=True)
    with HeldTransaction(native, "alerta_app", "SELECT 1") as staging:
        staging.run("LOCK TABLE public.alerts IN ACCESS EXCLUSIVE MODE")
        sweeper.start()
        wait_until_waiting_on(native, "public.alerts")
        fresh = intent_for(alert_id, event_id)   # issued now: after the pass began
        staging.run("SELECT zabalack.stage_generation(%s,%s,%s)", (alert_id, fresh["action_id"], Jsonb(fresh)))
    sweeper.join(timeout=BARRIER_TIMEOUT)
    assert not sweeper.is_alive(), "the pass did not finish once the staging committed"
    result = outcome["pass"]
    assert "skipped" not in result and result["remaining"] >= 1 and result.get("retry_at"), result
    assert attributes_of(native, alert_id).get(INTENT) == fresh, "the staging counted is the one left inside its window"
    with connect_role(native, "zabalack_owner") as owner:
        covers = owner.execute("SELECT retirement_due_at>=%s::timestamptz AND %s::timestamptz>=%s::timestamptz "
                               "FROM zabalack.installation WHERE singleton",
                               (fresh["capture_expires_at"], result["retry_at"], fresh["capture_expires_at"])).fetchone()[0]
    assert covers is True, "the retry named, and the slot, are no earlier than the counted staging can be due"


def test_T41_native_held_candidates_do_not_cost_the_pass_its_progress(native):
    """Three due rows, a batch of two: the two that come first in heap order —
    the order the scan chooses in — are held by another session, the third is
    free. The batch's places are the held rows, and a pass must still retire
    the free row while both holds last. Before this the batch was the first
    rows that were due, held or not; every pass chose the same two held rows
    again, and the free row behind them waited for the holds to lift,
    `deferred` keeping the slot due to no effect. Heap order is read, not
    assumed: reused line pointers put a later row ahead of an earlier one."""
    sweep(native, grace=0)   # rows other modules left due are retired first
    rows = [stage(native)[0] for _ in range(3)]
    with connect_role(native, "alerta_owner") as owner:
        ordered = [row[0] for row in owner.execute(
            "SELECT id FROM public.alerts WHERE id=ANY(%s) ORDER BY ctid", (rows,)).fetchall()]
    held, free_id = ordered[:2], ordered[2]
    with HeldTransaction(native, "alerta_app", ROW_LOCK, (held[0],)), \
         HeldTransaction(native, "alerta_app", ROW_LOCK, (held[1],)):
        first = sweep(native, grace=0, limit=2)
        assert first["deferred"] == 2, first
        assert first["retired"] >= 1, "the free row is retired while both holds last"
        assert INTENT not in attributes_of(native, free_id)
        assert all(INTENT in attributes_of(native, alert_id) for alert_id in held)
        again = sweep(native, grace=0, limit=2)
        assert again["deferred"] == 2 and again["examined"] == again["retired"], again
        assert all(INTENT in attributes_of(native, alert_id) for alert_id in held)
    released = sweep(native, grace=0, limit=2)
    assert released["retired"] >= 2 and released["deferred"] == 0, released
    assert all(INTENT not in attributes_of(native, alert_id) for alert_id in held)


def test_T41_native_the_cadence_claim_does_not_block_a_losing_worker(native):
    """A worker that loses the claim returns at once; it never waits out
    `statement_timeout` and is never marked unhealthy for losing."""
    with connect_role(native, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.installation SET retirement_due_at=NULL WHERE singleton")
    with HeldTransaction(native, "zabalack_owner",
                         "SELECT pg_advisory_xact_lock(hashtextextended('zabalack.retire_intents',0))"):
        started = datetime.now(timezone.utc)
        with connect_role(native, "zabalack_worker") as worker:
            worker.execute("SET statement_timeout='3000ms'")
            result = worker.execute("SELECT zabalack.worker_call('retire_intents',%s)",
                                    (Jsonb({"limit": 10, "grace_seconds": 0}),)).fetchone()[0]
        assert result.get("skipped") == "not_due"
        assert (datetime.now(timezone.utc) - started).total_seconds() < 2


def _migrator():
    spec = importlib.util.spec_from_file_location("zabalack_upgrade_migrator", ROOT / "sql/migrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def schema3_database(native, tmp_path):
    """A fresh database at schema 3 through the shipped runner, dropped afterwards.

    Roles are cluster-wide; the schema-level part of the lab bootstrap is
    provisioned here, then `001`–`007` through `sql/migrate.py` pointed at a
    copy of the tree without 008 *or anything after it*, then the trigger as the
    alerts owner. Excluding 008 alone would hand a schema-3 database every later
    migration, each of which is written for the tree 008 leaves behind.
    """
    admin_dsn = native["admin_dsn"]
    name = "zabalack_upgrade_" + uuid.uuid4().hex[:8]
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    lab = {"admin_dsn": conninfo.make_conninfo(admin_dsn, dbname=name), "backend": "native_postgres18"}
    migrator = _migrator()
    real_root = migrator.ROOT
    try:
        with psycopg.connect(lab["admin_dsn"], autocommit=True) as db:
            db.execute("GRANT USAGE,CREATE ON SCHEMA public TO alerta_owner")
            db.execute("CREATE SCHEMA zabalack AUTHORIZATION zabalack_owner")
            db.execute("REVOKE ALL ON SCHEMA zabalack FROM PUBLIC")
            db.execute("GRANT USAGE ON SCHEMA zabalack TO zabalack_worker,zabalack_probe,zabalack_operator,alerta_owner")
            db.execute("GRANT USAGE ON SCHEMA public TO zabalack_owner,alerta_app")
            db.execute("SET ROLE alerta_owner")
            db.execute(Path(native["source"]).read_text())
            db.execute("RESET ROLE")
            db.execute((ROOT / "tests/postgres/bootstrap_after_schema.sql").read_text())
            staged_root = tmp_path / "sql"
            (staged_root / "migrations").mkdir(parents=True)
            for path in sorted((ROOT / "sql/migrations").glob("*.sql")):
                if int(path.name[:3]) < 8:
                    shutil.copy(path, staged_root / "migrations" / path.name)
            migrator.ROOT = staged_root
            db.execute("SET ROLE zabalack_owner")
            migrator.migrate(db)
            db.execute("RESET ROLE")
            db.execute("SET ROLE alerta_owner")
            db.execute((ROOT / "sql/install/10_trigger.sql").read_text())
            db.execute("RESET ROLE")
            assert db.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0] == 3
        migrator.ROOT = real_root
        enrol(lab)
        yield lab, migrator
    finally:
        migrator.ROOT = real_root
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def upgrade(lab, migrator):
    """008 onward, the installer record, the application grant and the re-seal,
    through the real entry points and in the runbook's order. The runner applies
    every migration the schema-3 tree was missing, in one pass, which is how a
    real upgrade arrives.

    The scratch database's migrations run as the superuser with `SET ROLE`, so no
    role holds `zabalack_owner` and the recorded list is empty — still a positive
    act: the column starts `NULL` and `13_reseal.sql` refuses to seal until it is
    set.
    """
    with psycopg.connect(lab["admin_dsn"], autocommit=True) as db:
        db.execute("SET ROLE zabalack_owner")
        migrator.migrate(db)
        db.execute((ROOT / "sql/install/09_installer_roles.sql").read_text().replace(":'installer_roles'", "''"))
        db.execute((ROOT / "sql/install/12_application_grant.sql").read_text().replace(':"alert_app_role"', '"alerta_app"'))
        db.execute((ROOT / "sql/install/13_reseal.sql").read_text())
        db.execute("RESET ROLE")
        assert db.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0] == 4


def remove_live_intent(lab, alert_id):
    """What a whole-object attribute replace, or a pre-fence repair, leaves behind."""
    with connect_role(lab, "alerta_app") as conn:
        conn.execute("UPDATE alerts SET attributes=attributes-%s WHERE id=%s", (INTENT, alert_id))


def migration_state(lab):
    """Committed installation, routines and pending work, without volatile health timestamps."""
    with connect_role(lab, "zabalack_owner") as owner:
        state = {table: owner.execute(sql.SQL(
            "SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text"
        ).format(sql.Identifier("zabalack", table))).fetchall() for table in (
            "installation", "schema_migrations", "readiness_manifest", "captures", "outbox",
            "verification_leases", "actions", "effects")}
        state["routines"] = owner.execute(
            "SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid) FROM pg_proc p "
            "WHERE p.pronamespace='zabalack'::regnamespace ORDER BY 1").fetchall()
    return state


def upgrade_health(lab, *, healthy):
    """Read the real probe entry point as its caller, not as the migration owner."""
    with connect_role(lab, "zabalack_probe") as probe:
        health = probe.execute("SELECT zabalack.capture_health()").fetchone()[0]
    assert health["trigger_ok"] is True, "the existing capture trigger remains attached and unchanged"
    assert health["functions_ok"] is healthy, health
    return health


def test_T41_native_a_failed_migration_loop_rolls_back_and_recovers_forward(native, tmp_path):
    """A failure after 008/009 rolls back the entire migrate() call, not only its last file.

    The injected migration exists only in a private copy. It proves the backfill
    and both ledger entries were reached, changes queued work, then raises. The
    original tree must subsequently upgrade the same populated database intact.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        alert_id, event_id, _ = stage(lab, staged=False)
        first = intent_for(alert_id, event_id)
        stage_pre_fence(lab, alert_id, first)
        fire_ack(lab, alert_id, first)
        verify_through_worker_call(lab, first)
        second = intent_for(alert_id, event_id)
        stage_pre_fence(lab, alert_id, second)
        fire_ack(lab, alert_id, second)
        before = migration_state(lab)
        attributes_before = attributes_of(lab, alert_id)
        assert before["actions"] and len(before["captures"]) == 2, "populated upgrade precondition"
        assert {row[0]["state"] for row in before["outbox"]} == {"verified", "captured_unverified"}
        assert upgrade_health(lab, healthy=True)["zabalack_schema_version"] == 3

        fault_root = tmp_path / "failing-sql"
        shutil.copytree(ROOT / "sql/migrations", fault_root / "migrations")
        failure = fault_root / "migrations/010_synthetic_upgrade_failure.sql"
        failure.write_text("""DO $$ BEGIN
 IF (SELECT schema_version FROM zabalack.installation WHERE singleton)<>4
  OR (SELECT count(*) FROM zabalack.schema_migrations WHERE version IN
      ('008_generation_fence.sql','009_abandoned_attempt_sweep.sql'))<>2 THEN
  RAISE EXCEPTION 'injection did not reach both prior migrations';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM zabalack.alert_generations g JOIN zabalack.captures c
      ON g.action_id=(c.intent->>'action_id')::uuid JOIN zabalack.outbox o USING(capture_id)
      WHERE g.recorded_by='migration' AND o.state='captured_unverified') THEN
  RAISE EXCEPTION 'injection did not observe the pending capture backfill';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM zabalack.installation WHERE singleton
      AND routine_bodies_md5=(SELECT zabalack.catalog_fingerprints()->>'routines')) THEN
  RAISE EXCEPTION 'injection did not observe the migrated routine pin';
 END IF;
END $$;
UPDATE zabalack.outbox SET state='failed',reason='synthetic_upgrade_failure'
 WHERE state='captured_unverified';
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM zabalack.outbox WHERE reason='synthetic_upgrade_failure') THEN
  RAISE EXCEPTION 'injection did not change pending work';
 END IF;
 RAISE EXCEPTION 'injected failure after migrations 008 and 009';
END $$;
""", encoding="utf-8")
        real_root = migrator.ROOT
        try:
            migrator.ROOT = fault_root
            with connect_role(lab, "zabalack_owner") as owner:
                with pytest.raises(psycopg.errors.RaiseException, match="injected failure after migrations 008 and 009"):
                    migrator.migrate(owner)
        finally:
            migrator.ROOT = real_root

        assert migration_state(lab) == before, "failed upgrade changed committed schema, routines or captured work"
        assert attributes_of(lab, alert_id) == attributes_before
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT to_regclass('zabalack.alert_generations'),"
                                 "to_regprocedure('zabalack.stage_generation(text,uuid,jsonb)')").fetchone() == (None, None)
        assert upgrade_health(lab, healthy=True)["zabalack_schema_version"] == 3

        upgrade(lab, migrator)
        assert upgrade_health(lab, healthy=True)["zabalack_schema_version"] == 4
        after = migration_state(lab)
        for table in ("captures", "outbox", "verification_leases", "actions", "effects"):
            assert after[table] == before[table], f"forward recovery rewrote retained {table}"
        with connect_role(lab, "zabalack_owner") as owner:
            recorded = dict(owner.execute("SELECT version,sha256 FROM zabalack.schema_migrations").fetchall())
        assert recorded == {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in (ROOT / "sql/migrations").glob("*.sql")}
        assert [row[0] for row in fence(lab, alert_id)] == [first["action_id"], second["action_id"]]
        verify_through_worker_call(lab, second)
        assert project(lab, second["action_id"]) == "projected", "retained pending work remains usable"


def test_T41_native_an_incomplete_installation_refuses_health_until_forward_recovery(native, tmp_path):
    """migrate, 09, 12 and 13 commit separately; only the completed sequence is healthy.

    Both role records begin NULL. The first reseal refusal names the application
    grant (12), which is checked before the installer record (09); the operator
    still completes the documented order rather than treating that message as
    proof that the other step is done.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        alert_id, event_id, _ = stage(lab, staged=False)
        payload = intent_for(alert_id, event_id)
        before = migration_state(lab)
        reseal = (ROOT / "sql/install/13_reseal.sql").read_text()
        with connect_role(lab, "zabalack_owner") as owner:
            migrator.migrate(owner)
            assert owner.execute("SELECT installer_roles,application_role FROM zabalack.installation "
                                 "WHERE singleton").fetchone() == (None, None)

        for installers_recorded in (False, True):
            if installers_recorded:
                with connect_role(lab, "zabalack_owner") as owner:
                    owner.execute((ROOT / "sql/install/09_installer_roles.sql").read_text().replace(
                        ":'installer_roles'", "''"))
                    assert owner.execute("SELECT installer_roles,application_role FROM zabalack.installation "
                                         "WHERE singleton").fetchone() == ([], None)
            assert upgrade_health(lab, healthy=False)["zabalack_schema_version"] == 4
            with connect_role(lab, "alerta_app") as app:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    app.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                                (alert_id, payload["action_id"], Jsonb(payload)))
            with connect_role(lab, "zabalack_owner") as owner:
                with pytest.raises(psycopg.errors.RaiseException) as refused:
                    owner.execute(reseal)
            assert "12_application_grant.sql" in str(refused.value) and "before sealing" in str(refused.value)
            assert migration_state(lab)["readiness_manifest"] == before["readiness_manifest"], \
                "a refused seal must leave the previous manifest intact"

        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute((ROOT / "sql/install/12_application_grant.sql").read_text().replace(
                ':"alert_app_role"', '"alerta_app"'))
            assert owner.execute("SELECT installer_roles,application_role FROM zabalack.installation "
                                 "WHERE singleton").fetchone() == ([], "alerta_app")
            assert owner.execute("SELECT has_schema_privilege('alerta_app','zabalack','USAGE'),"
                                 "has_schema_privilege('alerta_app','zabalack','CREATE')").fetchone() == (True, False)
            assert owner.execute(
                "SELECT DISTINCT p.oid::regprocedure::text FROM pg_proc p CROSS JOIN pg_roles r "
                "WHERE p.pronamespace='zabalack'::regnamespace AND pg_has_role('alerta_app',r.oid,'MEMBER') "
                "AND has_function_privilege(r.oid,p.oid,'EXECUTE') ORDER BY 1").fetchall() == [
                    ("zabalack.stage_generation(text,uuid,jsonb)",)]
            assert owner.execute(
                "SELECT c.relname FROM pg_class c CROSS JOIN pg_roles r "
                "WHERE c.relnamespace='zabalack'::regnamespace AND c.relkind IN ('r','p','v','m') "
                "AND pg_has_role('alerta_app',r.oid,'MEMBER') "
                "AND has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"
            ).fetchall() == []
        upgrade_health(lab, healthy=False)
        assert migration_state(lab)["readiness_manifest"] == before["readiness_manifest"]
        with connect_role(lab, "alerta_app") as app:
            for statement in ("SELECT zabalack.worker_call('claim_verification','{}')",
                              "SELECT count(*) FROM zabalack.alert_generations"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    app.execute(statement)
        with connect_role(lab, "zabalack_worker") as worker:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                worker.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                               (alert_id, payload["action_id"], Jsonb(payload)))
        assert fence(lab, alert_id) == [], "refused callers recorded no staging"

        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute(reseal)
        upgrade_health(lab, healthy=True)
        with connect_role(lab, "alerta_app") as app:
            app.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                        (alert_id, payload["action_id"], Jsonb(payload)))
        assert newest(lab, alert_id) == payload["action_id"]
        fire_ack(lab, alert_id, payload)
        verify_through_worker_call(lab, payload)
        assert project(lab, payload["action_id"]) == "projected"


def test_T41_native_a_populated_database_upgrades_through_the_migration_runner(native, tmp_path):
    """A schema-3 database with work in flight, upgraded by `sql/migrate.py`.

    Populated as the pre-fence plugin left it: A acknowledged, verified through
    the schema-3 `worker_call` and projected; B staged afterwards as an attribute
    alone, never signed. After 008, the grant and the re-seal: the fence holds
    A then B at the migration tier, A's projection is refused, B captures,
    verifies and projects, and the first staging through the protected
    operation outranks both. The readiness manifest reports healthy.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        # Work in flight at schema 3.
        alert_id, event_id, first = stage(lab, staged=False, extra={"zabbix_ack_sync_local_at": "2026-01-01T00:00:00Z"})
        first = intent_for(alert_id, event_id)
        stage_pre_fence(lab, alert_id, first)
        fire_ack(lab, alert_id, first)
        verify_through_worker_call(lab, first)
        assert project(lab, first["action_id"]) == "projected"
        second = intent_for(alert_id, event_id)
        stage_pre_fence(lab, alert_id, second)
        untouched, _, _ = stage(lab, staged=False)
        # A live row whose intent is malformed — no action_id — must not abort the
        # upgrade; the sweep retires it later and it records no generation.
        malformed, _, broken = stage(lab, staged=False)
        with connect_role(lab, "alerta_app") as conn:
            conn.execute("UPDATE alerts SET attributes=attributes||%s WHERE id=%s",
                         (Jsonb({INTENT: {key: value for key, value in broken.items() if key != "action_id"}}), malformed))
        upgrade(lab, migrator)
        assert [(row[0], row[1]) for row in fence(lab, alert_id)] == \
            [(first["action_id"], "migration"), (second["action_id"], "migration")]
        assert fence(lab, untouched) == [], "a row that was never staged acquires its generation at its first staging"
        assert fence(lab, malformed) == [], "a malformed intent is skipped by the backfill, not fatal to it"
        with connect_role(lab, "zabalack_probe") as probe:
            health = probe.execute("SELECT zabalack.capture_health()").fetchone()[0]
        assert health["functions_ok"] is True and health["trigger_ok"] is True and health["zabalack_schema_version"] == 4
        assert project(lab, first["action_id"]) == "projection_action_generation_mismatch"
        fire_ack(lab, alert_id, second)
        verify_through_worker_call(lab, second)
        assert project(lab, second["action_id"]) == "projected"
        third = stage_over(lab, alert_id, event_id)
        assert newest(lab, alert_id) == third["action_id"]
        assert [row[1] for row in fence(lab, alert_id)] == ["migration", "migration", "staging"]
        assert project(lab, second["action_id"]) == "projection_action_generation_mismatch"


def test_T41_native_a_populated_upgrade_carries_captures_awaiting_verification(native, tmp_path):
    """Acknowledgements captured before the upgrade and still awaiting
    verification — B unverified, C on verifier hold — whose live intents left
    the row before 008 ran. Neither has an action row nor a live intent for the
    backfill to see. They are recorded from their captures, in capture order:
    C is the newest generation the moment the fence exists, A (verified,
    projected, older) is refused, and verification afterwards appends nothing —
    C verifies and projects, B verifies and is refused. D, rejected by the
    verifier before the upgrade, never becomes an action and records nothing."""
    with schema3_database(native, tmp_path) as (lab, migrator):
        now = datetime.now(timezone.utc)
        alert_id, event_id, _ = stage(lab, staged=False)
        first = intent_for(alert_id, event_id, issued=now)
        stage_pre_fence(lab, alert_id, first)
        fire_ack(lab, alert_id, first)
        verify_through_worker_call(lab, first)
        assert project(lab, first["action_id"]) == "projected"
        second = intent_for(alert_id, event_id, issued=now)
        stage_pre_fence(lab, alert_id, second)
        fire_ack(lab, alert_id, second)                       # captured, unverified
        third = intent_for(alert_id, event_id, issued=now)
        stage_pre_fence(lab, alert_id, third)
        fire_ack(lab, alert_id, third)
        held = settle_through_worker_call(lab, third, "hold_capture", "verifier_key_unavailable")
        assert held["state"] == "verifier_hold", "precondition: C is on verifier hold"
        fourth = intent_for(alert_id, event_id, issued=now)
        stage_pre_fence(lab, alert_id, fourth)
        fire_ack(lab, alert_id, fourth)
        rejected = settle_through_worker_call(lab, fourth, "reject_capture", "failed_intent_invalid")
        assert rejected["state"] == "failed", "precondition: D was rejected"
        remove_live_intent(lab, alert_id)
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT count(*) FROM zabalack.actions WHERE alert_id=%s", (alert_id,)).fetchone()[0] == 1
            states = owner.execute("SELECT o.state FROM zabalack.outbox o JOIN zabalack.captures c USING(capture_id) "
                                   "WHERE c.alert_id=%s ORDER BY c.capture_order", (alert_id,)).fetchall()
        assert [row[0] for row in states] == ["verified", "captured_unverified", "verifier_hold", "failed"], "precondition"
        assert INTENT not in attributes_of(lab, alert_id), "precondition: no live intent remains"
        upgrade(lab, migrator)
        assert [(row[0], row[1]) for row in fence(lab, alert_id)] == [
            (first["action_id"], "migration"), (second["action_id"], "migration"), (third["action_id"], "migration")], \
            "recorded from the captures, in capture order; the rejected capture records nothing"
        assert newest(lab, alert_id) == third["action_id"]
        assert project(lab, first["action_id"]) == "projection_action_generation_mismatch", "A is no longer permitted"
        verify_through_worker_call(lab, third)
        assert project(lab, third["action_id"]) == "projected"
        verify_through_worker_call(lab, second)
        assert project(lab, second["action_id"]) == "projection_action_generation_mismatch"
        assert [row[1] for row in fence(lab, alert_id)] == ["migration"] * 3, "verification appended nothing"
        assert newest(lab, alert_id) == third["action_id"]


SEQUENCE = "zabalack.alert_generations_seq_seq"


def _nextval(conn):
    return conn.execute("SELECT nextval('" + SEQUENCE + "')").fetchone()[0]


def test_T41_native_a_cached_sequence_hands_out_numbers_that_do_not_ascend(native):
    """Why the fence's sequence must cache exactly one value.

    `newest_generation()` reads `seq DESC`, so the fence's order is only as
    good as the sequence's. Above a cache of one PostgreSQL gives each backend
    its own block: the numbers ascend within a session and not between them, so
    a later staging from a backend still spending a low block takes a number
    beneath an earlier staging from a higher one — and `last_value` already
    stands at the end of every block allocated, so the position check reads
    entirely healthy. The per-alert advisory lock serialises the two calls; it
    cannot make the numbers they are handed ascend. Two real backends are the
    only way to establish that: PGlite multiplexes onto one and would hand out
    1, 2, 3 whatever the cache says.
    """
    with connect_role(native, "zabalack_owner") as owner:
        owner.execute("ALTER SEQUENCE " + SEQUENCE + " CACHE 32")
    try:
        with connect_role(native, "zabalack_owner") as first:
            with connect_role(native, "zabalack_owner") as second:
                earlier, later = _nextval(first), _nextval(second)
                after = _nextval(first)
        assert later > earlier, "precondition: the second backend took a higher block"
        assert after < later, ("the later call was handed a lower number than the earlier one: "
                               f"{earlier} then {later} then {after}")
    finally:
        with connect_role(native, "zabalack_owner") as owner:
            owner.execute("ALTER SEQUENCE " + SEQUENCE + " CACHE 1")
    # ...and the positive control the installation ships: at a cache of one the
    # same three calls ascend across the two backends, which is the property
    # capture_health() and 13_reseal.sql now require the sequence to keep.
    with connect_role(native, "zabalack_owner") as first:
        with connect_role(native, "zabalack_owner") as second:
            earlier, later = _nextval(first), _nextval(second)
            after = _nextval(first)
    assert earlier < later < after, f"{earlier}, {later}, {after}"
    with connect_role(native, "zabalack_owner") as owner:
        assert owner.execute("SELECT zabalack.generation_order_intact()").fetchone()[0] is True
