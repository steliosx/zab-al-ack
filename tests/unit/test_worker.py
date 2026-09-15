"""Mocked worker/state-machine evidence, not live tenant or notification proof."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from zabalack.config import Settings
from zabalack.crypto import build_intent, sign_intent, timestamp
from zabalack.reconciler import Reconciler
from zabalack.rpc import RPCError
from zabalack.worker import Worker

KEY = b"synthetic-worker-test-key-material!"
COHORT = {"customer": "lab-customer-a", "zabbix_instance": "lab-zabbix", "zabbix_trigger_id": "21001",
          "host_ids": ["11001"], "group_ids": ["31001"], "revision": "1", "policy_revision": "1",
          "enabled": True, "one_to_one": True, "comment_enabled": True, "allow_admin": False}


def signed_capture(*, age: int = 0):
    moment = datetime.now(timezone.utc) - timedelta(seconds=age)
    intent = build_intent(str(uuid.uuid4()), COHORT["customer"],
        {"zabbix_instance": "lab-zabbix", "zabbix_event_id": "41001", "zabbix_trigger_id": "21001"},
        {"login": "synthetic-engineer", "customers": [COHORT["customer"]], "admin": False, "auth_type": "user"},
        "1", "1", now=moment)
    return {"capture_id": str(uuid.uuid4()), "lease_generation": 1, "intent": intent,
        "marker": sign_intent(intent, "lab", KEY), "alert_id": intent["alert_id"], "customer": intent["customer"],
        "source": intent["source"].copy(), "history_type": "ack", "history_user": "synthetic-engineer",
        "resulting_status": "ack", "captured_at": timestamp(moment + timedelta(milliseconds=10))}


def action():
    c = signed_capture()
    i = c["intent"]
    return {"action_id": i["action_id"], "capture_id": c["capture_id"], "lease_generation": 1,
        "alert_id": i["alert_id"], "customer": i["customer"], "instance": "lab-zabbix", "event_id": "41001",
        "trigger_id": "21001", "issued_at": i["issued_at"], "execution_expires_at": i["execution_expires_at"],
        "public_reference": "syntheticpublicreference0001", "cohort": copy.deepcopy(COHORT),
        "source": i["source"], "state": "delivery_claimed", "uncertain": False, "attempts": 0}


def event(*, acknowledged="0", recovery="0", hosts=None):
    return {"eventid": "41001", "objectid": "21001", "source": "0", "object": "0", "value": "1",
            "clock": "1700000000", "acknowledged": acknowledged, "r_eventid": recovery,
            "hosts": [{"hostid": x} for x in (hosts if hosts is not None else ["11001"])]}


class FakeDB:
    def __init__(self, captures=None):
        self.captures = list(captures or [])
        self.calls = []
        self.ready = {"allowed": True}
        self.due = []

    async def call(self, op, payload=None):
        self.calls.append((op, copy.deepcopy(payload)))
        if op == "claim_verification":
            return self.captures.pop(0) if self.captures else None
        if op == "verify_complete":
            return {"action_id": payload["intent"]["action_id"], "state": "authorised"}
        if op == "before_write":
            return self.ready
        if op == "start_attempt":
            return {"attempt_id": str(uuid.uuid4())}
        if op == "claim_background":
            return {"allowed": True, "lease_generation": 7}
        if op == "reconcile_due":
            return self.due
        if op == "projection_due":
            return []
        return {}

    def outcomes(self):
        return [p for o, p in self.calls if o in {"complete", "record_reconciliation"}]


class Client:
    def __init__(self, result=None, *, read_error=None, write_error=None):
        self.result = [event()] if result is None else result
        self.read_error, self.write_error = read_error, write_error
        self.reads, self.writes = [], []

    async def event_get(self, event_id, cohort, **kw):
        self.reads.append((event_id, copy.deepcopy(cohort), kw))
        if self.read_error:
            raise self.read_error
        return copy.deepcopy(self.result)

    async def acknowledge(self, event_id, **kw):
        self.writes.append((event_id, kw))
        if self.write_error:
            raise self.write_error


def worker(db, client=None, *, writes=True, keys=None):
    registry = Mock()
    registry.resolve.return_value = object()
    instance = Worker(Settings(external_writes_enabled=writes, cohorts=(COHORT,)), db,
        {"lab": KEY} if keys is None else keys, registry=registry,
        client_factory=lambda endpoint: client or Client())
    return instance


@pytest.mark.asyncio
async def test_T1_T13_delivery_validates_exact_event_before_one_write():
    db, client, row = FakeDB(), Client(), action()
    await worker(db, client).deliver(row)
    assert client.reads[0][0] == "41001"
    assert len(client.writes) == 1
    assert client.writes[0][0] == "41001"
    assert db.outcomes()[-1]["completion_evidence"] == "write_response"
    assert [op for op, _ in db.calls].index("start_attempt") < [op for op, _ in db.calls].index("complete")
    assert len(client.reads) == 1  # No routine post-write read.


@pytest.mark.asyncio
async def test_T3_unreachable_is_pending_never_sent():
    db, client = FakeDB(), Client(read_error=RPCError("endpoint_unreachable", transient=True))
    await worker(db, client).deliver(action())
    assert not client.writes
    assert db.outcomes()[-1]["state"] == "pending"
    assert not db.outcomes()[-1]["uncertain"]


@pytest.mark.asyncio
async def test_T4_T10_frozen_source_is_used_despite_new_alert_occurrence():
    db, client, row = FakeDB(), Client(), action()
    row["current_alert_attributes"] = {"zabbix_event_id": "41002"}
    await worker(db, client).deliver(row)
    assert client.writes[0][0] == "41001"
    assert all(x[0] != "41002" for x in client.reads)


@pytest.mark.asyncio
async def test_T8_forged_capture_cannot_reserve_genuine_action():
    genuine = signed_capture()
    forged = copy.deepcopy(genuine)
    forged["capture_id"] = str(uuid.uuid4())
    forged["marker"] = forged["marker"].replace("mac=", "mac=f")
    db = FakeDB([forged, genuine])
    w = worker(db)
    await w.verify_once()
    assert not any(o == "verify_complete" for o, p in db.calls)
    w.registry.resolve.assert_not_called()
    await w.verify_once()
    assert len([p for o, p in db.calls if o == "verify_complete"]) == 1


@pytest.mark.asyncio
async def test_T9_timely_capture_can_be_verified_after_sixty_seconds():
    db = FakeDB([signed_capture(age=90)])
    await worker(db).verify_once()
    assert any(o == "verify_complete" for o, _ in db.calls)


@pytest.mark.asyncio
async def test_T11a_customer_scope_denies_without_target_resolution():
    c = signed_capture()
    c["intent"]["actor"]["customers"] = ["other-customer"]
    c["marker"] = sign_intent(c["intent"], "lab", KEY)
    db, w = FakeDB([c]), None
    w = worker(db)
    await w.verify_once()
    assert any(o == "reject_capture" for o, _ in db.calls)
    w.registry.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_T11b_valid_same_cohort_target_allowed_trusted_operator_boundary():
    db, client = FakeDB(), Client()
    await worker(db, client).deliver(action())
    assert len(client.writes) == 1  # This deliberately does not assert ingestion provenance.


@pytest.mark.asyncio
async def test_T12_T33_all_hosts_required_no_replacement_by_name():
    db, client = FakeDB(), Client([event(hosts=["11001", "11002"])])
    await worker(db, client).deliver(action())
    assert not client.writes
    assert db.outcomes()[-1]["reason"] == "host_set_mismatch"


@pytest.mark.asyncio
async def test_T25_already_acknowledged_observed_no_write_or_attribution():
    db, client = FakeDB(), Client([event(acknowledged="1")])
    await worker(db, client).deliver(action())
    assert not client.writes
    assert db.outcomes()[-1]["completion_evidence"] == "source_ack_observed"


@pytest.mark.asyncio
async def test_T26_resolved_original_event_remains_target():
    db, client = FakeDB(), Client([event(recovery="49001")])
    await worker(db, client).deliver(action())
    assert client.writes[0][0] == "41001"


@pytest.mark.asyncio
async def test_T28_permission_error_terminal_no_repeat():
    db, client = FakeDB(), Client(write_error=RPCError("api_permission_or_request_rejected"))
    await worker(db, client).deliver(action())
    assert len(client.writes) == 1
    assert db.outcomes()[-1]["state"] == "failed"


@pytest.mark.asyncio
async def test_T29_response_lost_enters_unknown_then_reference_reconciles_no_write():
    from zabalack.rpc import neutral_comment
    db, row = FakeDB(), action()
    client = Client(write_error=RPCError("outcome_unknown", transient=True, uncertain=True))
    w = worker(db, client)
    await w.deliver(row)
    assert db.outcomes()[-1]["uncertain"]
    row["uncertain"], row["state"] = True, "pending"
    resolved = event(acknowledged="1")
    resolved["acknowledges"] = [{"action": "6", "message": neutral_comment(row["issued_at"], row["public_reference"])}]
    reader = Client([resolved])
    recon = Reconciler(w.settings, db, w.registry, worker_id=w.worker_id, client_factory=lambda endpoint: reader)
    await recon.reconcile_action(row)
    assert not reader.writes
    assert db.outcomes()[-1]["completion_evidence"] == "reference_observed"


@pytest.mark.asyncio
async def test_T31_fenced_after_validation_cannot_start_attempt():
    db, client = FakeDB(), Client()
    db.ready = {"allowed": False, "reason": "delivery_lease_lost"}
    await worker(db, client).deliver(action())
    assert not client.writes
    assert not any(o == "start_attempt" for o, _ in db.calls)


@pytest.mark.asyncio
async def test_T32_pause_after_validation_holds_without_write():
    db, client = FakeDB(), Client()
    db.ready = {"allowed": False, "reason": "maintenance_hold"}
    await worker(db, client).deliver(action())
    assert not client.writes
    assert db.outcomes()[-1]["reason"] == "maintenance_hold"


@pytest.mark.asyncio
async def test_expired_action_cannot_resolve_endpoint():
    db, row = FakeDB(), action()
    row["execution_expires_at"] = timestamp(datetime.now(timezone.utc) - timedelta(seconds=1))
    w = worker(db)
    await w.deliver(row)
    w.registry.resolve.assert_not_called()
    assert db.outcomes()[-1]["state"] == "expired"


@pytest.mark.asyncio
async def test_shadow_mode_does_not_resolve_endpoint_or_credentials():
    db, w = FakeDB(), None
    w = worker(db, writes=False)
    await w.deliver(action())
    w.registry.resolve.assert_not_called()
    assert db.outcomes()[-1]["reason"] == "shadow_hold"


@pytest.mark.asyncio
async def test_missing_verification_key_is_hold_not_forgery():
    db = FakeDB([signed_capture()])
    w = worker(db, keys={})
    await w.verify_once()
    assert any(o == "hold_capture" for o, _ in db.calls)
    w.registry.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_T37_malformed_backlog_never_consumes_delivery_slots():
    records = [signed_capture() for _ in range(50)]
    for capture in records:
        capture["marker"] = "[zabalack:v1 invalid]"
    records.append(signed_capture())
    db = FakeDB(records)
    w = worker(db)
    for _ in records:
        await w.verify_once()
    assert len([1 for o, _ in db.calls if o == "reject_capture"]) == 50
    assert len([1 for o, _ in db.calls if o == "verify_complete"]) == 1
    assert not any(o == "claim_delivery" for o, _ in db.calls)
    w.registry.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_without_reference_never_retries_remote_request():
    row = action()
    row.update(uncertain=True, state="pending")
    db, client = FakeDB(), Client([event()])
    w = worker(db, client)
    recon = Reconciler(w.settings, db, w.registry, worker_id=w.worker_id, client_factory=lambda e: client)
    await recon.reconcile_action(row)
    assert not client.writes
    assert db.outcomes()[-1]["uncertain"]
    assert db.outcomes()[-1]["reason"] == "unknown_outcome_not_confirmed"


@pytest.mark.asyncio
async def test_action_two_unknown_only_observed_attribution():
    row = action()
    row.update(uncertain=True, state="pending")
    row["cohort"]["comment_enabled"] = False
    db, client = FakeDB(), Client([event(acknowledged="1")])
    w = worker(db, client)
    recon = Reconciler(w.settings, db, w.registry, worker_id=w.worker_id, client_factory=lambda e: client)
    await recon.reconcile_action(row)
    assert db.outcomes()[-1]["completion_evidence"] == "source_ack_observed"
    assert not client.reads[0][2]["history"]


@pytest.mark.asyncio
async def test_source_unack_after_success_is_drift_not_new_write():
    row = action()
    row["state"] = "ok"
    db, client = FakeDB(), Client([event()])
    w = worker(db, client)
    recon = Reconciler(w.settings, db, w.registry, worker_id=w.worker_id, client_factory=lambda e: client)
    await recon.reconcile_action(row, drift=True)
    assert not client.writes
    assert db.outcomes()[-1]["reason"] == "source_unacknowledged_drift"


@pytest.mark.asyncio
async def test_empty_reconciliation_has_no_source_reads():
    db, w = FakeDB(), None
    w = worker(db)
    recon = Reconciler(w.settings, db, w.registry, worker_id=w.worker_id)
    assert (await recon.fast_cycle())["read"] == 0
    w.registry.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_read_outage_preserves_delivery_evidence():
    row = action()
    row.update(state="ok", completion_evidence="write_response")
    db, client = FakeDB(), Client(read_error=RPCError("read_transient", transient=True))
    w = worker(db, client)
    recon = Reconciler(w.settings, db, w.registry, worker_id=w.worker_id, client_factory=lambda e: client)
    await recon.reconcile_action(row, drift=True)
    outcome = db.outcomes()[-1]
    assert outcome["state"] == "ok" and outcome["preserve_delivery"] is True
    assert outcome["completion_evidence"] == "write_response"
    assert not client.writes


@pytest.mark.asyncio
async def test_DB_transport_acquisition_has_true_overall_deadline(monkeypatch):
    import asyncio
    import time
    import psycopg
    from zabalack.db import Postgres
    async def blocked(*args, **kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", blocked)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await Postgres("synthetic-unused", overall_timeout_seconds=0.02).call("status")
    assert time.monotonic() - started < 0.2


@pytest.mark.asyncio
async def test_T41_the_standalone_reconcile_command_retires_intents(monkeypatch):
    """With the worker stopped and the standalone reconciliation unit kept, as
    the rollback keeps it, staged intents must still leave live rows: the
    command asks the database to retire on every run, a local operation, and
    still sends no source write. Before this only the worker's loop did."""
    import argparse
    from zabalack import cli
    db = FakeDB()
    monkeypatch.setattr(cli, "load_settings", lambda path: Settings(worker_dsn_file="/reviewed/worker.dsn", cohorts=(COHORT,)))
    monkeypatch.setattr(cli, "read_protected_file", lambda path: b"postgresql://unused")
    monkeypatch.setattr(cli, "Postgres", lambda dsn, **kwargs: db)
    result = await cli.execute(argparse.Namespace(command="reconcile", config="unused", read_only=True, once=True, nightly=False))
    assert result["source_writes"] is False
    assert [op for op, _ in db.calls if op == "retire_intents"] == ["retire_intents"], db.calls
    assert "retirement" in result["result"]


REGISTRY = {"lab-zbx": {"logical_instance": "lab-zbx", "api_version": "7.0.0",
                        "url": "https://zbx.invalid/api_jsonrpc.php",
                        "token_file": "/reviewed/token-that-is-not-there",
                        "ca_file": "/reviewed/ca-that-is-not-there.pem"}}


class DueDB(FakeDB):
    """A database with one due uncertain action, so the cycle reaches the source."""

    def __init__(self):
        super().__init__()
        self.due = [{"action_id": "a1", "instance": "lab-zbx", "event_id": "42", "trigger_id": "12",
                     "cohort": dict(COHORT), "uncertain": True, "state": "pending", "source": None,
                     "public_reference": "ref", "issued_at": "2026-01-01T00:00:00+00:00",
                     "completion_evidence": None}]


class UnreachableDB(DueDB):
    """A database whose due-time scan fails for a reason reconcile_action cannot catch."""

    async def call(self, op, payload=None):
        if op == "reconcile_due":
            self.calls.append((op, copy.deepcopy(payload)))
            raise RuntimeError("terminating connection due to administrator command")
        return await super().call(op, payload)


def _standalone(monkeypatch, db, **settings):
    from zabalack import cli
    monkeypatch.setattr(cli, "load_settings",
        lambda path: Settings(worker_dsn_file="/reviewed/worker.dsn", cohorts=(COHORT,), **settings))
    monkeypatch.setattr(cli, "read_protected_file", lambda path: b"postgresql://unused")
    monkeypatch.setattr(cli, "Postgres", lambda dsn, **kwargs: db)
    return cli


@pytest.mark.asyncio
async def test_T41_an_unreachable_source_does_not_stop_the_standalone_drain(monkeypatch):
    """The reading a review round proposed, executed rather than assumed.

    The concern was that a missing or unreadable Zabbix token file would end
    the command before its database-only retirement drain. It does not:
    `rpc.read_protected_file` turns the `OSError` into an `RPCError`, and
    `reconcile_action` records that failure and returns — as it does for an
    endpoint that will not resolve, a bad CA, a timeout or a malformed
    response, every one of which is an `RPCError` or its `ValidationError`
    subclass. The drain runs and the run succeeds. This pins that, so an
    exception path that stopped being wrapped would fail here.
    """
    import argparse
    db = DueDB()
    cli = _standalone(monkeypatch, db, endpoint_registry=REGISTRY)
    result = await cli.execute(argparse.Namespace(command="reconcile", config="unused",
                                                  read_only=True, once=True, nightly=False))
    assert result["source_writes"] is False
    assert _sweeps(db) == ["retire_intents"], db.calls
    assert [p for o, p in db.calls if o == "record_reconciliation"][0]["check_failure"] == "secret_file_unavailable"


@pytest.mark.asyncio
async def test_T41_a_reconciliation_that_fails_outright_does_not_take_the_drain_with_it(monkeypatch):
    """And the part of that concern which does hold, closed by ordering.

    Retirement is a local database operation, and the rollback makes a
    completed drain its condition for removing the application adapter. It
    must not be contingent on the half of this command that reaches the
    source completing — so it runs first. Here the due-time scan fails with
    something `reconcile_action` never sees; the run still fails, as it must,
    but the intents have already left the live rows.
    """
    import argparse
    db = UnreachableDB()
    cli = _standalone(monkeypatch, db, endpoint_registry=REGISTRY)
    with pytest.raises(RuntimeError, match="terminating connection"):
        await cli.execute(argparse.Namespace(command="reconcile", config="unused",
                                             read_only=True, once=True, nightly=False))
    assert _sweeps(db) == ["retire_intents"], db.calls


class FailingSweepDB(FakeDB):
    """A database whose retirement sweep fails — its scan outrunning its budget."""

    async def call(self, op, payload=None):
        if op == "retire_intents":
            self.calls.append((op, copy.deepcopy(payload)))
            raise RuntimeError("canceling statement due to statement timeout")
        return await super().call(op, payload)


class PagedSweepDB(FakeDB):
    """A database whose retirement sweep answers from a script of pages."""

    def __init__(self, pages):
        super().__init__()
        self.pages = list(pages)

    async def call(self, op, payload=None):
        if op == "retire_intents":
            self.calls.append((op, copy.deepcopy(payload)))
            return self.pages.pop(0) if self.pages else {"examined": 0, "retired": 0, "deferred": 0}
        return await super().call(op, payload)


def _sweeps(db):
    return [op for op, _ in db.calls if op == "retire_intents"]


def _standalone_reconcile(monkeypatch, db):
    from zabalack import cli
    monkeypatch.setattr(cli, "load_settings", lambda path: Settings(worker_dsn_file="/reviewed/worker.dsn", cohorts=(COHORT,)))
    monkeypatch.setattr(cli, "read_protected_file", lambda path: b"postgresql://unused")
    monkeypatch.setattr(cli, "Postgres", lambda dsn, **kwargs: db)
    return cli


@pytest.mark.asyncio
async def test_T41_the_standalone_reconcile_command_fails_when_retirement_fails(monkeypatch):
    """The worker reports a failed sweep and retries it next cycle; the
    standalone command has no next cycle, so a sweep that fails must fail the
    run rather than report it complete — the rollback's automation must not
    take a cleanup that did not happen for one that did. The rest of the cycle
    still runs first; the failure is the run's verdict, not a short-circuit."""
    import argparse
    db = FailingSweepDB()
    cli = _standalone_reconcile(monkeypatch, db)
    with pytest.raises(cli.RetirementError):
        await cli.execute(argparse.Namespace(command="reconcile", config="unused", read_only=True, once=True, nightly=False))
    assert [op for op, _ in db.calls if op == "retire_intents"] == ["retire_intents"]


def test_T41_a_failed_retirement_is_a_non_zero_exit_with_no_error_text_rendered(monkeypatch, capsys):
    """Through the entry point automation runs: exit status 1, the error class
    on stderr and nothing of the database's message, as for every failure."""
    cli = _standalone_reconcile(monkeypatch, FailingSweepDB())
    assert cli.main(["--config", "unused", "reconcile", "--read-only", "--once"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.err.strip().splitlines()[-1]) == {"ok": False, "error_class": "RetirementError"}
    assert "statement timeout" not in captured.err and captured.out == ""
    assert cli.main(["--config", "unused", "reconcile", "--read-only", "--once"]) == 1, "every run fails alike"


@pytest.mark.asyncio
async def test_T41_the_standalone_reconcile_command_drains_the_due_intents_page_by_page(monkeypatch):
    """The worker asks once per cycle and the slot carries a full or deferred
    batch to its next cycle; the standalone command has no next cycle inside a
    run, so it keeps asking while the sweep reports `continues`, and reports
    the drain complete only when a sweep left nothing due behind it."""
    import argparse
    db = PagedSweepDB([{"examined": 200, "retired": 200, "deferred": 0, "continues": True},
                       {"examined": 200, "retired": 199, "deferred": 1, "continues": True},
                       {"examined": 3, "retired": 3, "deferred": 0, "remaining": 0}])
    cli = _standalone_reconcile(monkeypatch, db)
    result = await cli.execute(argparse.Namespace(command="reconcile", config="unused", read_only=True, once=True, nightly=False))
    assert result["result"]["retirement"] == {"pages": 3, "examined": 403, "retired": 402, "deferred": 1,
                                              "remaining": 0, "complete": True}
    assert len(_sweeps(db)) == 3, "a page that continues is followed by another within the run"


@pytest.mark.asyncio
async def test_T41_a_drain_that_stops_with_work_remaining_fails_the_run(monkeypatch):
    """A run is bounded: after its pages, what remains is the next run's, and
    this run is not a completed cleanup. Before this the command exited 0 after
    one page with `continues` set and staged intents still on live rows."""
    import argparse
    db = PagedSweepDB([{"examined": 200, "retired": 200, "deferred": 0, "continues": True}] * 40)
    cli = _standalone_reconcile(monkeypatch, db)
    with pytest.raises(cli.RetirementError):
        await cli.execute(argparse.Namespace(command="reconcile", config="unused", read_only=True, once=True, nightly=False))
    assert len(_sweeps(db)) == cli.RETIREMENT_PAGES_PER_RUN, "the run drains its pages, then stops"


@pytest.mark.asyncio
async def test_T41_a_drain_over_rows_other_sessions_hold_does_not_spin(monkeypatch):
    """A page that examined nothing left only rows other sessions hold; asking
    again within the run would spin on them. The run stops, incomplete."""
    import argparse
    db = PagedSweepDB([{"examined": 0, "retired": 0, "deferred": 2, "continues": True}] * 10)
    cli = _standalone_reconcile(monkeypatch, db)
    with pytest.raises(cli.RetirementError):
        await cli.execute(argparse.Namespace(command="reconcile", config="unused", read_only=True, once=True, nightly=False))
    assert len(_sweeps(db)) == 1


@pytest.mark.asyncio
async def test_T41_a_slot_that_is_not_due_is_neither_a_failure_nor_a_completed_drain(monkeypatch):
    """Another caller claimed this interval's slot: the ordinary answer beside
    a running worker. The run succeeds and says the drain is not complete."""
    import argparse
    db = PagedSweepDB([{"examined": 0, "retired": 0, "deferred": 0, "skipped": "not_due"}])
    cli = _standalone_reconcile(monkeypatch, db)
    result = await cli.execute(argparse.Namespace(command="reconcile", config="unused", read_only=True, once=True, nightly=False))
    assert result["result"]["retirement"] == {"pages": 0, "examined": 0, "retired": 0, "deferred": 0,
                                              "skipped": "not_due", "complete": False}


@pytest.mark.asyncio
async def test_T41_a_drain_is_not_complete_while_any_row_still_carries_the_key(monkeypatch):
    """A pass that leaves nothing due behind it is not a cleanup while a row
    still carries the key: a staging inside its window and grace is left to a
    later pass by design, and the adapter that keeps the key from readers must
    outlive it. The run succeeds — nothing failed — and says so; a reply
    without the count is not complete either."""
    import argparse
    namespace = argparse.Namespace(command="reconcile", config="unused", read_only=True, once=True, nightly=False)
    db = PagedSweepDB([{"examined": 0, "retired": 0, "deferred": 0, "remaining": 3,
                        "retry_at": "2026-09-15T16:00:00+00:00"}])
    result = await _standalone_reconcile(monkeypatch, db).execute(namespace)
    assert result["result"]["retirement"] == {"pages": 1, "examined": 0, "retired": 0, "deferred": 0,
                                              "remaining": 3, "complete": False,
                                              "retry_at": "2026-09-15T16:00:00+00:00"}, \
        "the run says when the retry is not refused: the slot the database brought forward"
    db = PagedSweepDB([{"examined": 0, "retired": 0, "deferred": 0}])
    result = await _standalone_reconcile(monkeypatch, db).execute(namespace)
    assert result["result"]["retirement"]["complete"] is False and result["result"]["retirement"]["remaining"] is None


@pytest.mark.asyncio
async def test_T41_the_standalone_drain_asks_for_the_count_and_the_worker_does_not(monkeypatch):
    """The count is one more scan of public.alerts; the drain asks for it on
    every page (the database answers only on a page that does not continue),
    the worker's periodic call never does."""
    import argparse
    from zabalack.rpc import EndpointRegistry
    db = PagedSweepDB([{"examined": 0, "retired": 0, "deferred": 0, "remaining": 0}])
    await _standalone_reconcile(monkeypatch, db).execute(
        argparse.Namespace(command="reconcile", config="unused", read_only=True, once=True, nightly=False))
    assert [payload for op, payload in db.calls if op == "retire_intents"] == [{"limit": 200, "report_remaining": True}]
    settings = Settings(worker_dsn_file="/reviewed/worker.dsn", cohorts=(COHORT,))
    worker_db = FakeDB()
    recon = Reconciler(settings, worker_db, EndpointRegistry(settings.endpoint_registry, lab=settings.lab_mode,
                                                             approved_versions=settings.approved_versions),
                       worker_id="unit-worker")
    await recon.retire_intents()
    assert [payload for op, payload in worker_db.calls if op == "retire_intents"] == [{"limit": 200}], \
        "the periodic call pays no second scan"


def test_T41_an_incomplete_drain_is_a_non_zero_exit(monkeypatch, capsys):
    cli = _standalone_reconcile(monkeypatch, PagedSweepDB([{"examined": 200, "retired": 200, "deferred": 0, "continues": True}] * 40))
    assert cli.main(["--config", "unused", "reconcile", "--read-only", "--once"]) == 1
    assert json.loads(capsys.readouterr().err.strip().splitlines()[-1]) == {"ok": False, "error_class": "RetirementError"}


@pytest.mark.asyncio
async def test_T41_the_retirement_sweep_runs_under_its_own_statement_budget(monkeypatch):
    """Every other call keeps the 3-second statement timeout; the sweep's scan
    of public.alerts is bounded by the installed table, so it gets the
    configured budget, with an overall deadline that covers it."""
    from zabalack.db import Postgres
    db = Postgres("synthetic-unused", operation_budgets={"retire_intents": 30})
    assert db.budget_for("worker", "status") == (3000, 8.0)
    assert db.budget_for("worker", "retire_intents") == (30000, 34.0)
    assert db.budget_for("operator", "retire_intents") == (3000, 8.0), "an operator call never inherits it"
    import psycopg
    statements = []

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class Cursor:
        async def fetchone(self):
            return ({"examined": 0, "retired": 0},)

    class Connection:
        def transaction(self):
            return Transaction()

        async def execute(self, statement, params=None):
            statements.append(statement)
            return Cursor()

        async def close(self):
            pass

    async def connect(*args, **kwargs):
        return Connection()

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    assert await db.call("retire_intents", {"limit": 200}) == {"examined": 0, "retired": 0}
    assert statements[0] == "SET LOCAL statement_timeout = 30000"
    statements.clear()
    await db.call("status")
    assert statements[0] == "SET LOCAL statement_timeout = 3000"


@pytest.mark.asyncio
async def test_T31_cancel_after_send_records_unknown_not_unsent():
    import asyncio
    started = asyncio.Event()
    class BlockingClient(Client):
        async def acknowledge(self, event_id, **kwargs):
            self.writes.append((event_id, kwargs))
            started.set()
            await asyncio.Event().wait()
    db, client = FakeDB(), BlockingClient()
    w = worker(db, client)
    task = asyncio.create_task(w.deliver(action()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert db.outcomes()[-1]["uncertain"] is True
    assert db.outcomes()[-1]["reason"] == "shutdown_outcome_unknown"
    assert len(client.writes) == 1


def test_metrics_export_uses_actual_instance_gauges_and_incident_grouping():
    from zabalack.metrics import prometheus_text
    settings = Settings(config_revision="v1", deployment_id="lab", endpoint_registry={"lab-zabbix": {}})
    text = prometheus_text({"per_instance": [{"instance": "lab-zabbix", "pending_count": 3,
        "oldest_pending_seconds": 121}], "active_incidents": [{"instance": "lab-zabbix",
        "failure_class": "api_permission_or_request_rejected"}], "health": {"in_recovery": False,
        "functions_ok": True, "trigger_ok": True, "capture_disabled": False, "config_revision": "v1", "deployment_id": "lab"}}, settings)
    assert 'zabalack_oldest_pending_seconds{instance="lab-zabbix"} 121.0' in text
    assert 'zabalack_instance_failure{failure_class="permission",instance="lab-zabbix"} 1.0' in text
    assert "zabalack:v1" not in text
