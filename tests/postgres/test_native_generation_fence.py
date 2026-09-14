"""T41 — the generation fence under two sessions, on native PostgreSQL (A5).

Native only. Each case here needs two sessions contending for the same
committed row or lock — a sweep against a projection in both orders, two
stagings of one alert, a staging against a projection in both orders — or a
second database to upgrade. PGlite multiplexes sessions onto a single backend
and cannot establish any of them, so they are skipped rather than reported
under the WASM layer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import shutil
import threading
import uuid

import psycopg
from psycopg import conninfo, sql
from psycopg.types.json import Jsonb
import pytest

from conftest import connect_role
from test_generation_fence import (ACTION_KEY, INTENT, attributes_of, cleanup, enrol, fence,
                                   fire_ack, intent_for, new_action, newest, project, stage,
                                   stage_over, stage_pre_fence, sweep, verify_through_worker_call)

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
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            with psycopg.connect(self.lab["admin_dsn"]) as conn:
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

    def __exit__(self, *exc):
        self.release.set()
        self.thread.join(timeout=BARRIER_TIMEOUT)
        assert not self.thread.is_alive(), "the holding session did not commit"
        assert self.error is None, self.error


@pytest.mark.parametrize("order", ["sweep_first", "projection_first"])
def test_T41_native_an_older_projection_never_overwrites_a_newer_staging(native, order):
    """Stage A, then B; retire B's intent without a B action row; resume A's
    projection six hours later. A cannot overwrite B's state, in either lock
    order: the sweep takes the row with SKIP LOCKED and never the generation
    lock; project() takes the generation lock and then the row."""
    alert_id, action_id, first, second = two_generations(native)
    steps = (sweep_in_own_session(native), project_in_own_session(native, action_id))
    outcome = run_concurrently(*(steps if order == "sweep_first" else steps[::-1]))
    # One step returns the projection's reason, the other the sweep's result dict.
    reasons = {value for value in (outcome["first"], outcome["second"]) if isinstance(value, str)}
    assert reasons == {"projection_action_generation_mismatch"}
    after = attributes_of(native, alert_id)
    assert after[ACTION_KEY] == second["action_id"] and after["zabbix_ack_sync"] == "pending"
    assert newest(native, alert_id) == second["action_id"]
    # The racing pass may have found the row locked by the projection and left
    # it to the next pass (SKIP LOCKED); retirement is a property of the sweep,
    # not of that pass. The next pass retires it, and the fence is unchanged.
    sweep(native, grace=0)
    assert INTENT not in attributes_of(native, alert_id), "B's intent is retired by the sweep"
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


@pytest.mark.parametrize("order", ["projection_holds", "staging_holds"])
def test_T41_native_a_staging_and_a_projection_serialise_without_deadlock(native, order):
    """Both take the generation lock and then the alert row, so whichever holds
    first, the other waits — in a third session, so this test cannot block
    itself — and then sees the committed result."""
    alert_id, event_id, first = stage(native, issued=datetime.now(timezone.utc))
    fire_ack(native, alert_id, first)
    verify_through_worker_call(native, first)
    second = intent_for(alert_id, event_id)
    results = []
    if order == "projection_holds":
        def contend():
            with connect_role(native, "alerta_app") as conn:
                results.append(conn.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                                            (alert_id, second["action_id"], Jsonb(second))).fetchone()[0])
        with HeldTransaction(native, "zabalack_worker", "SELECT zabalack.project(%s)", (first["action_id"],)) as held:
            blocked = threading.Thread(target=contend, daemon=True)
            blocked.start()
            blocked.join(timeout=2)
            assert blocked.is_alive(), "the staging must wait for the projection holding the generation lock"
        blocked.join(timeout=BARRIER_TIMEOUT)
        assert not blocked.is_alive() and results, "the staging completed once the projection committed"
        assert held.result[0]["reason"] == "projected", "A was current when its projection took the locks"
        assert newest(native, alert_id) == second["action_id"]
    else:
        def contend():
            results.append(project(native, first["action_id"]))
        with HeldTransaction(native, "alerta_app", "SELECT zabalack.stage_generation(%s,%s,%s)",
                             (alert_id, second["action_id"], Jsonb(second))):
            blocked = threading.Thread(target=contend, daemon=True)
            blocked.start()
            blocked.join(timeout=2)
            assert blocked.is_alive(), "the projection must wait for the staging holding the generation lock"
        blocked.join(timeout=BARRIER_TIMEOUT)
        assert not blocked.is_alive() and results, "the projection completed once the staging committed"
        assert results[0] == "projection_action_generation_mismatch", "A's projection waited for B, then saw it"
        assert newest(native, alert_id) == second["action_id"]


def test_T41_native_only_one_sweep_runs_at_a_time(native):
    stage(native)
    with HeldTransaction(native, "zabalack_owner",
                         "SELECT pg_advisory_xact_lock(hashtextextended('zabalack.retire_intents',0))"):
        assert sweep(native, grace=0) == {"examined": 0, "retired": 0, "skipped": "sweep_in_progress"}
    assert sweep(native, grace=0)["retired"] >= 1


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


def _lab_for(dsn):
    return {"admin_dsn": dsn, "backend": "native_postgres18"}


def _migrator():
    spec = importlib.util.spec_from_file_location("zabalack_upgrade_migrator", ROOT / "sql/migrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_T41_native_a_populated_database_upgrades_through_the_migration_runner(native, tmp_path):
    """A schema-3 database with work in flight, upgraded by `sql/migrate.py`.

    Populated as the pre-fence plugin left it: A acknowledged, verified through
    the schema-3 `worker_call` and projected; B staged afterwards as an attribute
    alone, never signed. After 008, the grant and the re-seal: the fence holds
    A then B at the migration tier, A's projection is refused, B captures,
    verifies and projects, and the first staging through the protected
    operation outranks both. The readiness manifest reports healthy.
    """
    admin_dsn = native["admin_dsn"]
    name = "zabalack_upgrade_" + uuid.uuid4().hex[:8]
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = conninfo.make_conninfo(admin_dsn, dbname=name)
    lab = _lab_for(dsn)
    migrator = _migrator()
    real_root = migrator.ROOT
    try:
        with psycopg.connect(dsn, autocommit=True) as db:
            # Roles are cluster-wide; provision the schema-level part of the lab bootstrap.
            db.execute("GRANT USAGE,CREATE ON SCHEMA public TO alerta_owner")
            db.execute("CREATE SCHEMA zabalack AUTHORIZATION zabalack_owner")
            db.execute("REVOKE ALL ON SCHEMA zabalack FROM PUBLIC")
            db.execute("GRANT USAGE ON SCHEMA zabalack TO zabalack_worker,zabalack_probe,zabalack_operator,alerta_owner")
            db.execute("GRANT USAGE ON SCHEMA public TO zabalack_owner,alerta_app")
            db.execute("SET ROLE alerta_owner")
            db.execute(Path(native["source"]).read_text())
            db.execute("RESET ROLE")
            db.execute((ROOT / "tests/postgres/bootstrap_after_schema.sql").read_text())
            # Schema 3 through the shipped runner, pointed at a copy without 008.
            staged_root = tmp_path / "sql"
            (staged_root / "migrations").mkdir(parents=True)
            for path in sorted((ROOT / "sql/migrations").glob("*.sql")):
                if not path.name.startswith("008"):
                    shutil.copy(path, staged_root / "migrations" / path.name)
            migrator.ROOT = staged_root
            db.execute("SET ROLE zabalack_owner")
            migrator.migrate(db)
            db.execute("RESET ROLE")
            db.execute("SET ROLE alerta_owner")
            db.execute((ROOT / "sql/install/10_trigger.sql").read_text())
            db.execute("RESET ROLE")
            assert db.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0] == 3
        enrol(lab)
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
        # The upgrade, through the real entry points.
        migrator.ROOT = real_root
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute("SET ROLE zabalack_owner")
            migrator.migrate(db)
            db.execute((ROOT / "sql/install/12_application_grant.sql").read_text().replace(':"alert_app_role"', '"alerta_app"'))
            db.execute((ROOT / "sql/install/13_reseal.sql").read_text())
            db.execute("RESET ROLE")
            assert db.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0] == 4
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
    finally:
        migrator.ROOT = real_root
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
