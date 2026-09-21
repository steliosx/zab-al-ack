"""Populated **delivery ledger** upgraded through 008-012 (A6, T41).

`test_native_generation_fence.py` already walks a populated schema-3 database
through `sql/migrate.py`, the installer record, the application grant and the
re-seal. What it populates is the capture/staging side: it contains no
`claim_delivery` and no `start_attempt`, so every delivery-ledger row it upgrades
is one the migration never had to reason about.

Migrations 009-012 are exactly the ones that act on in-flight delivery state --
the abandoned-attempt sweep, and the ambiguous-API suspension and its two
corrections. Their populated-upgrade behaviour is what this file covers: an
earlier installation that already holds leased, abandoned, uncertain, delivered
and operator-settled work, upgraded through the real entry points in the
runbook's order, then read back as the actual callers.

Native only, for the same reason the file it reuses is: a second database.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import psycopg
import pytest

from conftest import connect_role
from test_generation_fence import (INSTANCE, cleanup, enrol, fire_ack, intent_for, stage,
                                   stage_pre_fence, verify_through_worker_call)
from test_worker_path import RoleSelectedLabPostgres
from zabalack.config import Settings
from zabalack.rpc import EndpointRegistry
from zabalack.worker import Worker
from test_native_generation_fence import ROOT, schema3_database, upgrade, upgrade_health

pytestmark = pytest.mark.postgres

WORKER = "populated-upgrade-worker"
CONFIRMATION = "REMOTE_ATTEMPT_FENCED_AND_SETTLED"


@pytest.fixture
def native(pg_lab):
    """Native only: `schema3_database` builds a second database to upgrade, which
    the single-backend WASM layer cannot provide."""
    if pg_lab["backend"] != "native_postgres18":
        pytest.skip("NOT_RUN_NATIVE: a populated upgrade needs a second database; "
                    "PGlite multiplexes onto one backend and cannot provide it")
    enrol(pg_lab)
    yield pg_lab
    cleanup(pg_lab)


def worker(lab, op, **payload):
    with connect_role(lab, "zabalack_worker") as conn:
        return conn.execute("SELECT zabalack.worker_call(%s,%s)", (op, psycopg.types.json.Jsonb(payload))).fetchone()[0]


def operator(lab, op, **payload):
    data = {"actor_ref": "synthetic-reviewer", "change_ref": "LAB-POPULATED-UPGRADE", **payload}
    with connect_role(lab, "zabalack_operator") as conn:
        return conn.execute("SELECT zabalack.operator_call(%s,%s)", (op, psycopg.types.json.Jsonb(data))).fetchone()[0]


def inspect(lab, action_id):
    return worker(lab, "inspect", action_id=action_id)[0]


def enable_delivery(lab):
    """Turn the baseline installation into one whose ledger accepts delivery.

    Through `operator_call`, not raw UPDATEs: the states this file upgrades have
    to be the ones an operator could really have left behind.

    Note what this does and does not switch on. The database-level external
    writes policy is genuinely **enabled** -- without it `claim_delivery` refuses
    and none of the seeded states is reachable. **No external Zabbix API call is
    performed anywhere in this file**: no `ZabbixClient` is constructed and no
    HTTP request leaves the process. Delivery is exercised only as far as the
    ledger -- claim, attempt, outcome -- so the real transport is out of scope
    here and remains covered by the real-Zabbix suites.
    """
    operator(lab, "capture_bypass", enabled=False, config_revision="populated-v1")
    operator(lab, "configure_installation", deployment_id="isolated-populated-upgrade",
             config_revision="populated-v1")
    operator(lab, "set_external_writes", enabled=True, confirmation="ENABLE_EXTERNAL_WRITES",
             gates_reviewed=True)
    operator(lab, "resume", instance=INSTANCE)


def new_delivery_action(lab):
    """A verified acknowledgement at schema 3, through the real entry points."""
    alert_id, event_id, _ = stage(lab, staged=False)
    payload = intent_for(alert_id, event_id, issued=datetime.now(timezone.utc))
    stage_pre_fence(lab, alert_id, payload)
    fire_ack(lab, alert_id, payload)
    verify_through_worker_call(lab, payload)
    return payload["action_id"]


def claim_and_send(lab, action_id, worker_id=WORKER):
    claim = worker(lab, "claim_delivery", worker_id=worker_id, action_id=action_id)
    assert claim is not None, "precondition: the action is claimable at the baseline"
    lease = {"worker_id": worker_id, "action_id": action_id, "lease_generation": claim["lease_generation"]}
    attempt = worker(lab, "start_attempt", **lease)
    return lease, attempt


def expire_lease(lab, action_id):
    with connect_role(lab, "zabalack_owner") as owner:
        owner.execute("UPDATE zabalack.delivery_leases SET expires_at=clock_timestamp()-interval '1 second' "
                      "WHERE action_id=%s", (action_id,))
        owner.execute("UPDATE zabalack.delivery_slots SET expires_at=clock_timestamp()-interval '1 second' "
                      "WHERE action_id=%s", (action_id,))


def seed_delivery_ledger(lab):
    """Five representative in-flight states, all through real callers at schema 3.

    Returns the action ids. Every state is the one the shipped schema-3
    `worker_call`/`operator_call` actually leaves behind, so the upgrade sees
    what a real earlier installation would hold. One exception, named because
    the claim would otherwise be wrong: `expire_lease` below ages
    `delivery_leases` and `delivery_slots` as the owner, because no caller can
    make wall-clock time pass. Nothing else writes a ledger row directly.
    """
    enable_delivery(lab)
    seeded = {}

    # pending: verified and queued, never claimed.
    seeded["pending"] = new_delivery_action(lab)

    # leased/abandoned: claimed and sent, lease expired with no outcome.
    abandoned = new_delivery_action(lab)
    claim_and_send(lab, abandoned)
    expire_lease(lab, abandoned)
    # The sweep that turns an expired lease into `abandoned_attempt` is called
    # here rather than relied on from a later block: without it this state is
    # fenced only by the `reconcile_due` inside the settled seed, so reordering
    # the seeds would change this baseline silently.
    worker(lab, "reconcile_due")
    seeded["abandoned"] = abandoned

    # uncertain: an unknown outcome reported while the lease was live.
    uncertain = new_delivery_action(lab)
    lease, attempt = claim_and_send(lab, uncertain)
    worker(lab, "complete", **lease, attempt_id=attempt["attempt_id"], state="unknown",
           reason="synthetic_response", uncertain=True, completion_evidence=None)
    seeded["uncertain"] = uncertain

    # delivered: an ok outcome, effect closed.
    delivered = new_delivery_action(lab)
    lease, attempt = claim_and_send(lab, delivered)
    worker(lab, "complete", **lease, attempt_id=attempt["attempt_id"], state="ok",
           reason="synthetic_response", uncertain=False, completion_evidence="write_response")
    seeded["delivered"] = delivered

    # settled: uncertain, then explicitly disposed of by the operator.
    settled = new_delivery_action(lab)
    lease, attempt = claim_and_send(lab, settled)
    worker(lab, "complete", **lease, attempt_id=attempt["attempt_id"], state="unknown",
           reason="synthetic_response", uncertain=True, completion_evidence=None)
    expire_lease(lab, settled)
    worker(lab, "reconcile_due")
    operator(lab, "settle_unknown", action_id=settled, disposition="no-effect-confirmed",
             evidence_ref="synthetic-populated-upgrade", confirmation=CONFIRMATION)
    seeded["settled"] = settled

    # ambiguous: claimed and sent at the baseline, lease still live, no outcome
    # yet -- the attempt whose API error arrives *after* the upgrade.
    ambiguous = new_delivery_action(lab)
    lease, attempt = claim_and_send(lab, ambiguous)
    seeded["ambiguous"] = ambiguous
    seeded["ambiguous_lease"] = lease
    seeded["ambiguous_attempt"] = attempt["attempt_id"]
    return seeded


VOLATILE = ()


def strip_volatile(row):
    return {k: v for k, v in row.items() if k not in VOLATILE}


def ledger_actions(seeded):
    """Only the seeded action ids; the ambiguous lease bookkeeping is not one."""
    return {k: v for k, v in seeded.items() if not k.startswith("ambiguous_")}


def ledger_snapshot(lab, seeded):
    """Whole rows, as JSON, for every ledger table the seeded actions touch.

    `to_jsonb(t)` rather than a column list: a field left out of the comparison
    is a field the migration could rewrite unobserved, and the point of this
    snapshot is that it is not curated. The keys in VOLATILE below are the only
    ones excluded, each because the contract lets it move for reasons unrelated
    to the upgrade; everything else -- identities, ordering, references,
    evidence, timestamps of record -- is compared exactly.
    """
    snapshot = {}
    with connect_role(lab, "zabalack_owner") as owner:
        for name, action_id in ledger_actions(seeded).items():
            action = owner.execute("SELECT to_jsonb(t) FROM zabalack.actions t WHERE action_id=%s",
                                   (action_id,)).fetchone()[0]
            attempts = [r[0] for r in owner.execute(
                "SELECT to_jsonb(t) FROM zabalack.attempts t WHERE action_id=%s ORDER BY created_at",
                (action_id,)).fetchall()]
            outcomes = [r[0] for r in owner.execute(
                "SELECT to_jsonb(t) FROM zabalack.outcomes t WHERE action_id=%s ORDER BY recorded_at",
                (action_id,)).fetchall()]
            effect = owner.execute(
                "SELECT to_jsonb(e) FROM zabalack.effects e JOIN zabalack.actions a ON a.effect_id=e.effect_id "
                "WHERE a.action_id=%s", (action_id,)).fetchone()[0]
            snapshot[name] = {"action": strip_volatile(action),
                              "attempts": [strip_volatile(r) for r in attempts],
                              "outcomes": [strip_volatile(r) for r in outcomes],
                              "effect": strip_volatile(effect)}
    return snapshot


BASELINE_STATES = {
    "pending":   {"state": "authorised", "uncertain": False, "attempts": 0},
    "abandoned": {"state": "unknown",    "uncertain": True,  "attempts": 1},
    "uncertain": {"state": "unknown",    "uncertain": True,  "attempts": 1},
    "delivered": {"state": "ok",         "uncertain": False, "attempts": 1},
    "settled":   {"state": "failed",     "uncertain": False, "attempts": 1},
    "ambiguous": {"state": "delivery_claimed", "uncertain": False, "attempts": 1},
}


def assert_baseline(lab, seeded):
    """The seed really reached each represented state, before anything upgrades."""
    for name, expected in BASELINE_STATES.items():
        row = inspect(lab, seeded[name])
        observed = {key: row[key] for key in expected}
        assert observed == expected, ("baseline state not reached", name, row)
    assert inspect(lab, seeded["settled"])["reason"] == "operator_no_effect_confirmed"
    assert inspect(lab, seeded["abandoned"])["reason"] == "abandoned_attempt"


def test_a_populated_delivery_ledger_keeps_its_records_and_proofs(native, tmp_path):
    """Every protected field and every historical proof survives 008-012.

    The comparison is the whole snapshot, not a summary of it: the action's
    state, reason, evidence, uncertainty, attempt count, public reference and
    both deadlines, plus every `attempts` and `outcomes` row and the effect.
    A migration that rewrote any of them changes this dictionary.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        seeded = seed_delivery_ledger(lab)
        assert_baseline(lab, seeded)
        before = ledger_snapshot(lab, seeded)
        upgrade(lab, migrator)
        after = ledger_snapshot(lab, seeded)
        assert after == before, "the upgrade altered a protected record or proof"
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0] == 4
        upgrade_health(lab, healthy=True)


def test_the_upgrade_does_not_convert_uncertainty_into_permission_to_retry(native, tmp_path):
    """Two explicit operator acts stand between carried-over uncertainty and a
    fresh send, and the upgrade removes neither.

    The ladder, each rung read through the real `claim_delivery` answer a sender
    would get:

      after the upgrade        not claimable
      after `retry` alone      not claimable   <- retrying does not resolve it
      after `reconcile_due`    not claimable   <- nor does a reconciliation pass
      after `settle_unknown`   not claimable   <- disposal is not re-authorisation
      after settle + retry     claimable

    Only the operator's recorded disposition clears `uncertain`, and only a
    separate `retry` restores deliverability. A migration that pre-resolved
    carried-over uncertainty, or left the action claimable, collapses a rung.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        seeded = seed_delivery_ledger(lab)
        assert_baseline(lab, seeded)
        upgrade(lab, migrator)
        for name in ("abandoned", "uncertain"):
            action_id = seeded[name]
            assert inspect(lab, action_id)["uncertain"] is True, (name, "the upgrade cleared uncertainty")
            assert worker(lab, "claim_delivery", worker_id="after-upgrade", action_id=action_id) is None, \
                (name, "the upgrade left an unresolved attempt claimable by a fresh sender")

        action_id = seeded["uncertain"]
        before = inspect(lab, action_id)
        operator(lab, "retry", action_id=action_id)
        after = inspect(lab, action_id)
        assert after["uncertain"] is True, "an operator retry silently resolved the uncertainty it was retrying"
        assert after["public_reference"] == before["public_reference"], "retry reissued the public reference"
        assert after["execution_expires_at"] == before["execution_expires_at"], "retry moved the deadline"
        assert worker(lab, "claim_delivery", worker_id="after-retry", action_id=action_id) is None, \
            "retry alone turned an unresolved attempt into permission to send"

        worker(lab, "reconcile_due")
        assert worker(lab, "claim_delivery", worker_id="after-reconcile", action_id=action_id) is None, \
            "a reconciliation pass turned an unresolved attempt into permission to send"

        operator(lab, "settle_unknown", action_id=action_id, disposition="no-effect-confirmed",
                 evidence_ref="synthetic-populated-upgrade", confirmation=CONFIRMATION)
        settled = inspect(lab, action_id)
        assert settled["uncertain"] is False and settled["reason"] == "operator_no_effect_confirmed", \
            "only the operator's recorded disposition may clear the uncertainty"
        assert worker(lab, "claim_delivery", worker_id="after-settle", action_id=action_id) is None, \
            "disposal alone was treated as re-authorisation to send"

        operator(lab, "retry", action_id=action_id)
        claim = worker(lab, "claim_delivery", worker_id="after-settle-retry", action_id=action_id)
        assert claim is not None, "settle then retry did not restore deliverability"
        assert claim["uncertain"] is False, "the re-authorised send still reports an unresolved attempt"


def test_a_stale_callback_after_the_upgrade_does_not_reopen_settled_work(native, tmp_path):
    """Delivered and operator-settled work is closed before and after, and a
    callback replayed from the pre-upgrade attempt does not reopen it.

    This is 011/012's property read against *existing* data: the replayed
    completion arrives with the old lease generation, after the upgrade, and must
    land as a late response that changes nothing -- not as a live outcome, and
    not as an instance suspension behind the operator's disposal.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        seeded = seed_delivery_ledger(lab)
        stale = {}
        for name in ("delivered", "settled"):
            with connect_role(lab, "zabalack_owner") as owner:
                stale[name] = owner.execute(
                    "SELECT attempt_id,owner,lease_generation FROM zabalack.attempts "
                    "WHERE action_id=%s ORDER BY created_at LIMIT 1", (seeded[name],)).fetchone()
        assert_baseline(lab, seeded)
        upgrade(lab, migrator)
        before = ledger_snapshot(lab, seeded)
        for name in ("delivered", "settled"):
            attempt_id, owner_id, generation = stale[name]
            result = worker(lab, "complete", worker_id=owner_id, action_id=seeded[name],
                            lease_generation=generation, attempt_id=str(attempt_id),
                            state="unknown", reason="api_application_error", uncertain=True,
                            completion_evidence=None)
            assert result["state"] == "late_response", (name, "a stale callback was accepted as live", result)
        after = ledger_snapshot(lab, seeded)
        for name in ("delivered", "settled"):
            assert after[name]["action"] == before[name]["action"], (name, "a stale callback altered the action")
            assert after[name]["effect"] == before[name]["effect"], (name, "a stale callback reopened the effect")
            assert after[name]["attempts"] == before[name]["attempts"], (name, "a stale callback added an attempt")
            kept = after[name]["outcomes"][:len(before[name]["outcomes"])]
            assert kept == before[name]["outcomes"], (name, "a stale callback rewrote a recorded outcome")
            # Recording the late arrival is the contract; it is an audit row, not
            # a live outcome, so anything appended must say `late_response`.
            for appended in after[name]["outcomes"][len(before[name]["outcomes"]):]:
                assert appended["state"] == "late_response", \
                    (name, "a stale callback landed as a live outcome", appended)
        untouched = [n for n in ledger_actions(seeded) if n not in ("delivered", "settled")]
        assert untouched, "precondition: there are other actions to be disturbed"
        assert {n: after[n] for n in untouched} == {n: before[n] for n in untouched}, \
            ("a stale callback for one action disturbed another", untouched)
        with connect_role(lab, "zabalack_owner") as owner:
            circuit = owner.execute("SELECT circuit_open FROM zabalack.instance_control "
                                    "WHERE instance=%s", (INSTANCE,)).fetchone()
        assert circuit is not None and circuit[0] is False, \
            "a stale ambiguous callback suspended the instance behind an operator disposal"


def migrate_only(lab, migrator):
    """The migrations alone: the intermediate state a real upgrade passes through
    between `sql/migrate.py` and `13_reseal.sql`."""
    with psycopg.connect(lab["admin_dsn"], autocommit=True) as db:
        db.execute("SET ROLE zabalack_owner")
        migrator.migrate(db)
        db.execute("RESET ROLE")


def grant_and_reseal(lab):
    """The installer record, the application grant and the explicit re-seal."""
    with psycopg.connect(lab["admin_dsn"], autocommit=True) as db:
        db.execute("SET ROLE zabalack_owner")
        db.execute((ROOT / "sql/install/09_installer_roles.sql").read_text().replace(":'installer_roles'", "''"))
        db.execute((ROOT / "sql/install/12_application_grant.sql").read_text().replace(':"alert_app_role"', '"alerta_app"'))
        db.execute((ROOT / "sql/install/13_reseal.sql").read_text())
        db.execute("RESET ROLE")


def real_worker_health(lab, doctor):
    """`Worker.check_health()` itself, run against this lab's `doctor` response.

    Not a copy of the predicate: the shipped coroutine is awaited, with the real
    `Settings` and the `RoleSelectedLabPostgres` bridge the worker-path suite
    already uses, so `worker_call('doctor')` executes as `zabalack_worker`.
    Settings are taken from the lab's own doctor values, so the identity terms
    match and the gate turns on the schema and seal terms alone.
    """
    settings = Settings(database_name=doctor["database_name"],
                        deployment_id=doctor["deployment_id"],
                        config_revision=doctor["config_revision"])
    worker = Worker(settings, RoleSelectedLabPostgres(lab["admin_dsn"]), {"lab": b"x" * 32},
                    registry=EndpointRegistry({}, lab=True, approved_versions=settings.approved_versions))
    return asyncio.run(worker.check_health())


def test_required_checks_refuse_the_unsealed_intermediate_state(native, tmp_path):
    """Between the migrations and the re-seal, both gates refuse.

    A populated upgrade is not atomic: the runner applies 008-012, and the
    readiness manifest is stale until `13_reseal.sql` runs as the schema owner.
    In that window the capture path must refuse (`health.py` raises
    `capture_health_schema`) and the worker must not consider itself healthy
    (`worker.py::check_health` is false), so nothing acknowledges or delivers
    against a half-installed tree. After the explicit re-seal, both pass.
    """
    from zabalack.health import CaptureHealthProbe, CaptureUnhealthy, ProbeConfig

    with schema3_database(native, tmp_path) as (lab, migrator):
        seeded = seed_delivery_ledger(lab)
        assert_baseline(lab, seeded)
        with connect_role(lab, "zabalack_probe") as probe:
            initial = probe.execute("SELECT zabalack.capture_health()").fetchone()[0]

        async def connect(dsn):
            connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
            await connection.execute("SET ROLE zabalack_probe")
            return connection

        migrate_only(lab, migrator)

        unsealed = upgrade_health(lab, healthy=False)
        assert unsealed["zabalack_schema_version"] == 4, "the migrations did apply; the seal is what is missing"
        with connect_role(lab, "zabalack_worker") as w:
            doctor = w.execute("SELECT zabalack.worker_call('doctor','{}'::jsonb)").fetchone()[0]
        assert doctor["functions_ok"] is False
        assert real_worker_health(lab, doctor) is False, \
            "Worker.check_health() accepted a migrated but unsealed tree"

        # The assessment reports that `before_write` still answers `allowed` in
        # this window; assert it rather than leaving it an unchecked remark. It
        # is a lease-level answer with the seal gate above it, so this records
        # the shipped division of responsibility, not an approval to write.
        claim = worker(lab, "claim_delivery", worker_id="unsealed-probe", action_id=seeded["pending"])
        if claim is not None:
            permitted = worker(lab, "before_write", worker_id="unsealed-probe",
                               action_id=seeded["pending"], lease_generation=claim["lease_generation"])
            assert permitted["allowed"] is True, \
                ("before_write is a lease-level answer and does not consult the seal", permitted)

        probe_obj = CaptureHealthProbe(ProbeConfig(lab["admin_dsn"], initial["database_name"],
                                       initial["deployment_id"], initial["config_revision"]), connector=connect)
        try:
            with pytest.raises(CaptureUnhealthy) as refusal:
                probe_obj.check(force=True)
            assert str(refusal.value) == "capture_health_schema", \
                "the capture path refused for some other reason than the unsealed schema"
        finally:
            probe_obj.close()

        grant_and_reseal(lab)

        sealed = upgrade_health(lab, healthy=True)
        assert sealed["zabalack_schema_version"] == 4
        with connect_role(lab, "zabalack_worker") as w:
            doctor = w.execute("SELECT zabalack.worker_call('doctor','{}'::jsonb)").fetchone()[0]
        assert real_worker_health(lab, doctor) is True, \
            "Worker.check_health() still refuses a fully resealed tree"
        probe_obj = CaptureHealthProbe(ProbeConfig(lab["admin_dsn"], initial["database_name"],
                                       initial["deployment_id"], initial["config_revision"]), connector=connect)
        try:
            assert probe_obj.check(force=True)["in_recovery"] is False
        finally:
            probe_obj.close()


CALLER_DENIALS = [
    ("alerta_app", "worker_call", "inspect"),
    ("alerta_app", "operator_call", "pause"),
    ("zabalack_probe", "worker_call", "inspect"),
    ("zabalack_probe", "operator_call", "pause"),
    ("zabalack_worker", "operator_call", "pause"),
    ("zabalack_operator", "worker_call", "inspect"),
]


def test_role_callers_keep_their_own_permissions_after_a_populated_upgrade(native, tmp_path):
    """Each role is exercised as the actual caller, not through the owner.

    A privileged session can reach every routine, so it cannot prove any of
    this. Each case below runs as the role itself and asserts the refusal is an
    insufficient-privilege error, and that the permitted call still works.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        seeded = seed_delivery_ledger(lab)
        assert_baseline(lab, seeded)
        upgrade(lab, migrator)

        for role, routine, operation in CALLER_DENIALS:
            with connect_role(lab, role) as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    conn.execute(f"SELECT zabalack.{routine}(%s,%s)",
                                 (operation, psycopg.types.json.Jsonb({"action_id": seeded["pending"]})))

        # The permitted callers still work, as themselves.
        assert inspect(lab, seeded["pending"])["state"] == "authorised"
        assert operator(lab, "pause", instance=INSTANCE, reason="populated-upgrade-probe") is not None
        operator(lab, "resume", instance=INSTANCE)
        with connect_role(lab, "zabalack_probe") as probe:
            assert probe.execute("SELECT zabalack.capture_health()").fetchone()[0]["functions_ok"] is True
        with connect_role(lab, "alerta_app") as app:
            assert app.execute("SELECT count(*) FROM public.alerts").fetchone()[0] >= 1


def test_the_reseal_refuses_a_prohibited_grant_on_a_populated_tree(native, tmp_path):
    """`13_reseal.sql` does not bless a prohibited grant, populated ledger or not.

    The refusal itself is already covered on a fresh tree
    (`test_T41_only_the_installations_own_grants_survive_a_reseal`). What this
    adds is the populated case: the grant is made *after* 008-012 have run over
    real delivery history, which is when an operator is most likely to widen a
    grant to unblock something, and the re-seal must still refuse rather than
    record it.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        seeded = seed_delivery_ledger(lab)
        upgrade(lab, migrator)
        before = ledger_snapshot(lab, seeded)
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("GRANT EXECUTE ON FUNCTION zabalack.worker_call(text,jsonb) TO PUBLIC")
        with psycopg.connect(lab["admin_dsn"], autocommit=True) as db:
            db.execute("SET ROLE zabalack_owner")
            with pytest.raises(psycopg.errors.RaiseException) as refusal:
                db.execute((ROOT / "sql/install/13_reseal.sql").read_text())
        assert "PUBLIC" in str(refusal.value), \
            ("the re-seal refused for an unrelated reason", str(refusal.value))
        # The refusal is a refusal, not a repair: it neither revokes the grant
        # nor touches the ledger it was protecting.
        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute(
                "SELECT has_function_privilege('public','zabalack.worker_call(text,jsonb)','EXECUTE')"
            ).fetchone()[0] is True, "the re-seal silently revoked the grant instead of refusing"
            owner.execute("REVOKE EXECUTE ON FUNCTION zabalack.worker_call(text,jsonb) FROM PUBLIC")
        assert ledger_snapshot(lab, seeded) == before, "a refused re-seal altered the delivery ledger"


def test_an_interrupted_upgrade_keeps_the_ledger_and_its_checksums(native, tmp_path):
    """A migration that fails part-way leaves the delivery ledger intact.

    The runner applies 008-012 in one pass. This aborts it inside that pass and
    asserts the documented safe recovery: no ledger row is deleted, no recorded
    checksum is rewritten, the schema version does not advance, and re-running
    the real runner afterwards completes forward.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        seeded = seed_delivery_ledger(lab)
        assert_baseline(lab, seeded)
        before = ledger_snapshot(lab, seeded)
        with connect_role(lab, "zabalack_owner") as owner:
            checksums_before = owner.execute(
                "SELECT version,sha256 FROM zabalack.schema_migrations ORDER BY version").fetchall()

        # Abort inside the pass: the statement runs in the migration's own
        # transaction, so the failure rolls the whole pass back.
        with pytest.raises(psycopg.errors.DivisionByZero):
            with psycopg.connect(lab["admin_dsn"]) as db:
                db.execute("SET ROLE zabalack_owner")
                migrator.migrate(db)
                db.execute("SELECT 1/0")

        with connect_role(lab, "zabalack_owner") as owner:
            assert owner.execute("SELECT schema_version FROM zabalack.installation").fetchone()[0] == 3, \
                "an aborted pass advanced the schema version"
            assert owner.execute(
                "SELECT version,sha256 FROM zabalack.schema_migrations ORDER BY version"
            ).fetchall() == checksums_before, "an aborted pass rewrote a recorded checksum"
        assert ledger_snapshot(lab, seeded) == before, "an aborted pass altered the delivery ledger"

        upgrade(lab, migrator)
        assert ledger_snapshot(lab, seeded) == before, "recovering forward altered the delivery ledger"
        upgrade_health(lab, healthy=True)


def test_negative_controls_the_assertions_detect_the_wrong_behaviour(native, tmp_path):
    """Each control injects the failure its assertion exists to catch.

    Without these, a snapshot comparison that silently compared nothing, or a
    ladder that never reached its rung, would pass just as quietly.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        seeded = seed_delivery_ledger(lab)
        assert_baseline(lab, seeded)
        upgrade(lab, migrator)
        baseline = ledger_snapshot(lab, seeded)

        # 1. A rewritten proof is detected by the snapshot comparison.
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("UPDATE zabalack.actions SET completion_evidence='tampered' WHERE action_id=%s",
                          (seeded["delivered"],))
        assert ledger_snapshot(lab, seeded) != baseline, "the snapshot comparison does not detect a rewritten proof"
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("UPDATE zabalack.actions SET completion_evidence='write_response' WHERE action_id=%s",
                          (seeded["delivered"],))
        assert ledger_snapshot(lab, seeded) == baseline, "the control did not restore the ledger"

        # 2. Cleared uncertainty is detected.
        assert inspect(lab, seeded["uncertain"])["uncertain"] is True
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("UPDATE zabalack.actions SET uncertain=false WHERE action_id=%s", (seeded["uncertain"],))
        assert inspect(lab, seeded["uncertain"])["uncertain"] is False, \
            "the uncertainty assertion reads a field that cannot change"
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("UPDATE zabalack.actions SET uncertain=true WHERE action_id=%s", (seeded["uncertain"],))

        # 3. A reopened effect is detected.
        assert ledger_snapshot(lab, seeded)["delivered"]["effect"]["state"] == "ok"
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("UPDATE zabalack.effects SET state='authorised' WHERE effect_id="
                          "(SELECT effect_id FROM zabalack.actions WHERE action_id=%s)", (seeded["delivered"],))
        assert ledger_snapshot(lab, seeded)["delivered"]["effect"]["state"] != "ok", \
            "the reopened-effect assertion reads a field that cannot change"
        with connect_role(lab, "zabalack_owner") as owner:
            owner.execute("UPDATE zabalack.effects SET state='ok' WHERE effect_id="
                          "(SELECT effect_id FROM zabalack.actions WHERE action_id=%s)", (seeded["delivered"],))
        assert ledger_snapshot(lab, seeded) == baseline, "control 3 did not restore the ledger"


def instance_state(lab):
    with connect_role(lab, "zabalack_owner") as owner:
        circuit = owner.execute("SELECT circuit_open,circuit_reason FROM zabalack.instance_control "
                                "WHERE instance=%s", (INSTANCE,)).fetchone()
        incident = owner.execute("SELECT active FROM zabalack.incidents WHERE instance=%s AND failure_class=%s",
                                 (INSTANCE, "api_application_error")).fetchone()
    return {"circuit_open": circuit[0], "circuit_reason": circuit[1],
            "incident_active": None if incident is None else incident[0]}


def test_an_api_error_on_a_carried_over_attempt_suspends_only_while_it_is_live(native, tmp_path):
    """The 010/011/012 behaviour, read against an attempt that predates the upgrade.

    A worker claimed and sent before the upgrade; its API error arrives after.
    Two legs, and they are the two the migrations disagree about:

      live, unresolved attempt  -> the instance is suspended and an incident opens
      replayed after disposal   -> late response, and the instance stays running

    The first leg fails without 010 (nothing suspends). The second fails with
    011 but without 012, which is the correction 012 exists to make: an operator
    who has disposed of the attempt and re-authorised delivery must not have the
    old callback reopen the suspension behind them.
    """
    with schema3_database(native, tmp_path) as (lab, migrator):
        seeded = seed_delivery_ledger(lab)
        assert_baseline(lab, seeded)
        assert instance_state(lab)["circuit_open"] is False, "precondition: the instance is running"
        upgrade(lab, migrator)

        lease, attempt_id = seeded["ambiguous_lease"], seeded["ambiguous_attempt"]
        live = worker(lab, "complete", **lease, attempt_id=attempt_id, state="unknown",
                      reason="api_application_error", uncertain=True, completion_evidence=None)
        assert live["state"] == "unknown", ("the carried-over attempt was not still live", live)
        suspended = instance_state(lab)
        assert suspended["circuit_open"] is True, \
            "an ambiguous API error on a live carried-over attempt did not suspend the instance"
        assert suspended["circuit_reason"] == "api_application_error"
        assert suspended["incident_active"] is True, "no incident was opened for the suspension"

        # The operator disposes of the attempt and re-authorises delivery.
        operator(lab, "reset_circuit", instance=INSTANCE)
        worker(lab, "reconcile_due")
        operator(lab, "settle_unknown", action_id=seeded["ambiguous"], disposition="no-effect-confirmed",
                 evidence_ref="synthetic-populated-upgrade", confirmation=CONFIRMATION)
        operator(lab, "retry", action_id=seeded["ambiguous"])
        assert instance_state(lab)["circuit_open"] is False, "precondition: the operator cleared the suspension"

        replayed = worker(lab, "complete", **lease, attempt_id=attempt_id, state="unknown",
                          reason="api_application_error", uncertain=True, completion_evidence=None)
        assert replayed["state"] == "late_response", ("the replayed callback was accepted as live", replayed)
        after = instance_state(lab)
        assert after["circuit_open"] is False, \
            "a replayed API error re-suspended the instance behind the operator's disposal and retry"
        assert after["incident_active"] is not True, "the replayed callback reactivated the incident"
