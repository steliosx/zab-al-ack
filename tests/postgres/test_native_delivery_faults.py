"""Native PostgreSQL + real worker processes + loopback HTTPS fault evidence.

Run as a fresh isolated native phase. WASM explicitly skips this module. No
database clock, lease timestamp, signing verifier, endpoint validation, RPC
transport or process death is mocked. The upstream source is synthetic only.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import time
import uuid

import psycopg
from psycopg.types.json import Jsonb
import pytest

from zabalack.crypto import build_intent, sign_intent
from zabalack.db import Postgres
from conftest import connect_role
from native_support.fault_harness import SyntheticSource, WorkerProcess, protected_file

pytestmark = pytest.mark.postgres
KEY = b"synthetic-native-fault-key-only!!"
REVISION = "native-fault-v1"


class FaultLab:
    def __init__(self, pg_lab, directory: Path, source: SyntheticSource) -> None:
        self.pg_lab, self.directory = pg_lab, directory
        self.instance = "lab-native-fault-" + uuid.uuid4().hex[:12]
        self.cohort = {"customer": "Native Fault Synthetic", "zabbix_instance": self.instance,
            "zabbix_trigger_id": "87200", "host_ids": ["87100"], "group_ids": ["87300"],
            "revision": REVISION, "policy_revision": "1", "enabled": True,
            "one_to_one": True, "comment_enabled": True, "allow_admin": False}
        self.children = []
        self.intents = []
        self.source = source
        parameters = psycopg.conninfo.conninfo_to_dict(pg_lab["admin_dsn"])
        parameters.pop("options", None)
        parameters.pop("password", None)
        parameters.update(user="zabalack_worker", connect_timeout="3")
        self.dsn = psycopg.conninfo.make_conninfo(**parameters)
        worker_dsn = protected_file(directory / "worker-dsn", self.dsn.encode())
        key_file = protected_file(directory / "signing-key", KEY)
        probe_parameters = parameters | {"user": "zabalack_probe"}
        probe_dsn = protected_file(directory / "probe-dsn",
            psycopg.conninfo.make_conninfo(**probe_parameters).encode())
        settings = {"enabled": True, "external_writes_enabled": True, "lab_mode": True,
            "cohorts": [self.cohort], "config_revision": REVISION,
            "deployment_id": "isolated-native-fault-test", "database_name": pg_lab["database_name"],
            "signing_kid": "lab", "signing_keys": {"lab": str(key_file)},
            "worker_dsn_file": str(worker_dsn), "probe_dsn_file": str(probe_dsn),
            "endpoint_registry": {self.instance: self.source.endpoint | {"logical_instance": self.instance}}}
        self.config_path = protected_file(directory / "worker.json", json.dumps(settings).encode())
        with connect_role(pg_lab, "zabalack_operator") as connection:
            audit = {"actor_ref": "synthetic-native-operator", "change_ref": "LAB-NATIVE-FAULT-ONLY"}
            for operation, payload in [
                ("capture_bypass", {"enabled": False, "config_revision": REVISION}),
                ("configure_installation", {"deployment_id": settings["deployment_id"], "config_revision": REVISION}),
                ("configure_cohort", {"cohort": self.cohort}),
                ("set_external_writes", {"enabled": True, "confirmation": "ENABLE_EXTERNAL_WRITES", "gates_reviewed": True}),
                ("resume", {"instance": self.instance}),
            ]:
                connection.execute("SELECT zabalack.operator_call(%s,%s)",
                                   (operation, Jsonb(audit | payload))).fetchone()

    def process(self, **kwargs) -> WorkerProcess:
        child = WorkerProcess(self.config_path, "native-" + uuid.uuid4().hex,
                              evidence_dir=self.directory, **kwargs)
        self.children.append(child)
        return child

    def seed(self, count=1, *, behavior="normal"):
        added = []
        for _ in range(count):
            event_id = str(87400000 + len(self.intents))
            self.source.add(event_id, behavior=behavior)
            intent = build_intent(str(uuid.uuid4()), self.cohort["customer"],
                {"zabbix_instance": self.instance, "zabbix_trigger_id": "87200", "zabbix_event_id": event_id},
                {"login": "synthetic-native-engineer", "customers": [self.cohort["customer"]],
                 "admin": False, "auth_type": "user"}, REVISION, "1")
            attributes = {"source_system": "zabbix", **intent["source"], "zabbix_ack_sync_intent": intent}
            with connect_role(self.pg_lab, "alerta_app") as connection:
                connection.execute(
                    "INSERT INTO alerts(id,resource,event,status,customer,attributes,history) "
                    "VALUES(%s,'synthetic-native','stable-trigger','open',%s,%s,ARRAY[]::history[])",
                    (intent["alert_id"], intent["customer"], Jsonb(attributes)))
                connection.execute(
                    "UPDATE alerts SET status='ack',history=ARRAY[ROW(%s,'stable-trigger',NULL,'ack',NULL,%s,"
                    "'ack',clock_timestamp() AT TIME ZONE 'UTC',%s,NULL)::history],"
                    "update_time=clock_timestamp() AT TIME ZONE 'UTC' WHERE id=%s",
                    (str(uuid.uuid4()), "synthetic note " + sign_intent(intent, "lab", KEY),
                     intent["actor"]["login"], intent["alert_id"]))
            self.intents.append(intent)
            added.append(intent)
        verifier = self.process(mode="verify", count=count)
        assert verifier.finish()["results"] == [True] * count
        for intent in added:
            assert self.inspect(intent)["state"] == "authorised"
        assert self.source.requests == [], "verification must not resolve/read a source endpoint"
        return added

    def call(self, operation, payload=None):
        return asyncio.run(Postgres(self.dsn).call(operation, payload))

    def inspect(self, intent):
        rows = self.call("inspect", {"action_id": intent["action_id"]})
        assert len(rows) == 1
        return rows[0]

    def observe(self, statement, parameters=()):
        # Observation only: administration is never used to mutate leases/actions.
        with connect_role(self.pg_lab, "zabalack_owner") as connection:
            connection.execute("SET statement_timeout='3000ms'")
            return connection.execute(statement, parameters).fetchall()

    def wait_lease_expired(self, intent):
        assert self.observe("SELECT l.expires_at-a.updated_at=interval '30 seconds' "
            "FROM zabalack.delivery_leases l JOIN zabalack.actions a USING(action_id) WHERE a.action_id=%s",
            (intent["action_id"],)) == [(True,)], "the native lease profile must remain exactly 30 seconds"
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            row = self.observe("SELECT expires_at<=clock_timestamp(),"
                "extract(epoch FROM expires_at-clock_timestamp()) FROM zabalack.delivery_leases WHERE action_id=%s",
                (intent["action_id"],))[0]
            if row[0]:
                return
            time.sleep(min(0.25, max(0.01, float(row[1]))))
        pytest.fail("native 30-second lease did not expire within the bounded wait")

    def assert_one_unsettled(self, intent):
        assert self.inspect(intent)["uncertain"] is True
        assert self.observe("SELECT count(*) FROM zabalack.attempts WHERE action_id=%s",
                            (intent["action_id"],))[0][0] == 1
        assert self.observe("SELECT e.completed_at FROM zabalack.effects e JOIN zabalack.actions a USING(effect_id) "
                            "WHERE a.action_id=%s", (intent["action_id"],))[0][0] is None
        event = self.source.events[intent["source"]["zabbix_event_id"]]
        assert event["commits"] == len(event["messages"]) == 1

    def assert_no_resend(self, intent):
        # Poll only after the persisted retry time is eligible. Immediate polls
        # alone could pass because of backoff even if uncertainty fencing broke.
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            due, remaining = self.observe("SELECT next_due<=clock_timestamp(),"
                "extract(epoch FROM next_due-clock_timestamp()) FROM zabalack.actions WHERE action_id=%s",
                (intent["action_id"],))[0]
            if due:
                break
            time.sleep(min(0.25, max(0.01, float(remaining))))
        else:
            pytest.fail("native uncertain delivery retry time did not become due within the bounded wait")
        children = [self.process(count=3) for _ in range(2)]
        assert [child.finish()["results"] for child in children] == [[False] * 3] * 2
        self.assert_one_unsettled(intent)

    def reconcile(self, intent):
        child = self.process(mode="reconcile", action_id=intent["action_id"])
        assert child.finish()["results"] == [True]
        row = self.inspect(intent)
        assert (row["state"], row["uncertain"], row["completion_evidence"]) == (
            "ok", False, "reference_observed")
        event_id = intent["source"]["zabbix_event_id"]
        assert self.source.events[event_id]["commits"] == 1
        assert self.observe("SELECT count(*) FROM zabalack.attempts WHERE action_id=%s",
                            (intent["action_id"],))[0][0] == 1

    def close(self):
        # Keep bounded, allowlisted failure evidence; never erase database/source state.
        failures = []
        for child in self.children:
            try:
                child.close()
            except Exception as error:
                failures.append(type(error).__name__)
        snapshots = [{key: value for key, value in self.inspect(intent).items()
                      if key in {"action_id", "state", "reason", "uncertain", "attempts", "completion_evidence"}}
                     for intent in self.intents]
        protected_file(self.directory / "ledger-summary.json", json.dumps(snapshots, indent=2).encode())
        assert not failures, "native child cleanup failed: " + ",".join(failures)


@pytest.fixture
def fault_lab(request, tmp_path):
    # Check before requesting pg_lab so an explicitly supplemental run does not
    # initialize a database for these native-only cases.
    if os.environ.get("ACK_TEST_WASM") == "1":
        pytest.skip("NOT_RUN_NATIVE: WASM cannot establish multi-process delivery crash/race behavior")
    if os.name != "posix":
        pytest.skip("NOT_RUN_NATIVE: POSIX SIGKILL process semantics are required")
    if not shutil.which("openssl"):
        pytest.skip("NOT_RUN_NATIVE: openssl is required for ephemeral loopback HTTPS certificates")
    pg_lab = request.getfixturevalue("pg_lab")
    assert pg_lab["backend"] == "native_postgres18"
    # The shared fixture already checks the observed server version for PGlite.
    # Register source cleanup before database/configuration setup can fail.
    with SyntheticSource(tmp_path, trigger_id="87200", host_id="87100", group_id="87300") as source:
        lab = FaultLab(pg_lab, tmp_path, source)
        try:
            yield lab
        finally:
            lab.close()
    assert not source.errors, "synthetic HTTPS source rejected a request"


def test_T29_native_competing_processes_hold_only_two_instance_slots(fault_lab):
    lab = fault_lab
    intents = lab.seed(5, behavior="hold_after_commit")
    gates = ("before:claim_delivery", "after:claim_delivery", "after:before_write", "after:start_attempt")
    children = [lab.process(gates=gates) for _ in range(5)]
    for child in children:
        child.wait("before:claim_delivery")
    for child in children:
        child.release("before:claim_delivery")
    claims = [(child, child.wait("after:claim_delivery")) for child in children]
    owners = [(child, claim) for child, claim in claims if claim["present"]]
    assert len(owners) == 2
    assert len({child.process.pid for child, _ in owners}) == 2
    assert {claim["slot"] for _, claim in owners} == {1, 2}
    rows = lab.observe("SELECT slot,owner,generation FROM zabalack.delivery_slots WHERE instance=%s "
                       "AND expires_at>clock_timestamp() ORDER BY slot", (lab.instance,))
    assert len(rows) == 2 and len({row[1] for row in rows}) == 2
    assert lab.source.requests == []
    for child, claim in claims:
        child.release("after:claim_delivery")
        if not claim["present"]:
            assert child.finish()["results"] == [False]
    for child, _ in owners:
        assert child.wait("after:before_write")["allowed"] is True
    assert lab.observe("SELECT count(*) FROM zabalack.attempts t JOIN zabalack.actions a USING(action_id) "
                       "WHERE a.instance=%s", (lab.instance,))[0][0] == 0
    for child, _ in owners:
        child.release("after:before_write")
    for child, _ in owners:
        assert child.wait("after:start_attempt")["attempt_id"]
    assert lab.observe("SELECT count(*) FROM zabalack.attempts t JOIN zabalack.actions a USING(action_id) "
                       "WHERE a.instance=%s", (lab.instance,))[0][0] == 2
    assert all(event["commits"] == 0 for event in lab.source.events.values())
    # Start the extra interpreter before the timed HTTP requests. Its DB claim
    # stays gated until both effects are committed, avoiding spawn-time races
    # against the production three-second read timeout.
    extra = lab.process(gates=("before:claim_delivery",))
    extra.wait("before:claim_delivery")
    for child, _ in owners:
        child.release("after:start_attempt")
    selected = {claim["action_id"] for _, claim in owners}
    for intent in intents:
        if intent["action_id"] in selected:
            lab.source.wait_committed(intent["source"]["zabbix_event_id"])
    assert len([r for r in lab.source.requests if r["method"] == "event.acknowledge"]) == 2
    # A real extra claimant competes while both source effects await responses.
    extra.release("before:claim_delivery")
    assert extra.finish()["results"] == [False]
    for intent in intents:
        lab.source.release_commit(intent["source"]["zabbix_event_id"])
    for child, _ in owners:
        assert child.finish()["results"] == [True]
    assert lab.process(count=3).finish()["results"] == [True] * 3
    assert all(lab.inspect(intent)["state"] == "ok" for intent in intents)
    assert all(event["commits"] == 1 for event in lab.source.events.values())
    assert lab.observe("SELECT count(*) FROM zabalack.delivery_slots WHERE instance=%s", (lab.instance,))[0][0] == 0


def test_T29_native_dropped_committed_response_stays_uncertain_without_resend(fault_lab):
    lab = fault_lab
    intent = lab.seed(behavior="drop_after_commit")[0]
    child = lab.process(gates=("after:start_attempt", "before:complete", "after:complete"))
    attempt = child.wait("after:start_attempt")
    assert lab.observe("SELECT count(*) FROM zabalack.attempts WHERE attempt_id=%s",
                       (attempt["attempt_id"],))[0][0] == 1
    child.release("after:start_attempt")
    lab.source.wait_committed(intent["source"]["zabbix_event_id"])
    completion = child.wait("before:complete")
    assert (completion["state"], completion["uncertain"], completion["reason"]) == (
        "pending", True, "outcome_unknown")
    child.release("before:complete")
    assert child.wait("after:complete")["uncertain"] is True
    lab.assert_one_unsettled(intent)
    child.release("after:complete")
    child.finish()
    lab.assert_no_resend(intent)
    lab.reconcile(intent)


def test_T31_native_sigkill_after_remote_commit_recovers_without_resend(fault_lab):
    lab = fault_lab
    intent = lab.seed(behavior="hold_after_commit")[0]
    child = lab.process(gates=("after:start_attempt",))
    child.wait("after:start_attempt")
    child.release("after:start_attempt")
    lab.source.wait_committed(intent["source"]["zabbix_event_id"])
    child.kill()  # Actual SIGKILL: no Worker cancellation/complete/shutdown handler runs.
    lab.source.release_commit(intent["source"]["zabbix_event_id"])
    assert lab.observe("SELECT count(*) FROM zabalack.outcomes WHERE action_id=%s", (intent["action_id"],))[0][0] == 0
    lab.wait_lease_expired(intent)
    replacement = lab.process(gates=("after:claim_delivery",))
    claim = replacement.wait("after:claim_delivery")
    assert (claim["lease_generation"], claim["uncertain"]) == (2, True)
    replacement.release("after:claim_delivery")
    assert replacement.finish()["results"] == [True]
    lab.assert_no_resend(intent)
    lab.reconcile(intent)


def test_T29_native_stale_presend_owner_cannot_start_attempt(fault_lab):
    lab = fault_lab
    intent = lab.seed()[0]
    old = lab.process(gates=("after:before_write",))
    assert old.wait("after:before_write")["allowed"] is True
    lab.wait_lease_expired(intent)
    replacement = lab.process(gates=("after:claim_delivery",))
    claim = replacement.wait("after:claim_delivery")
    assert claim["lease_generation"] == 2 and not claim["uncertain"]
    old.release("after:before_write")
    assert old.wait("error")["sqlstate"] == "P0001"
    old.process.join(timeout=3)
    assert old.process.exitcode == 1
    assert lab.source.events[intent["source"]["zabbix_event_id"]]["commits"] == 0
    assert lab.observe("SELECT count(*) FROM zabalack.attempts WHERE action_id=%s", (intent["action_id"],))[0][0] == 0
    replacement.release("after:claim_delivery")
    assert replacement.finish()["results"] == [True]
    assert lab.inspect(intent)["state"] == "ok"
    assert lab.source.events[intent["source"]["zabbix_event_id"]]["commits"] == 1


def test_T31_native_stale_success_completion_cannot_settle_replacement(fault_lab):
    lab = fault_lab
    intent = lab.seed()[0]
    old = lab.process(gates=("before:complete", "after:complete"))
    completion = old.wait("before:complete")
    assert (completion["state"], completion["completion_evidence"]) == ("ok", "write_response")
    lab.source.wait_committed(intent["source"]["zabbix_event_id"])
    lab.wait_lease_expired(intent)
    replacement = lab.process(gates=("after:claim_delivery",))
    claim = replacement.wait("after:claim_delivery")
    assert (claim["lease_generation"], claim["uncertain"]) == (2, True)
    replacement.release("after:claim_delivery")
    assert replacement.finish()["results"] == [True]
    before = lab.inspect(intent)
    old.release("before:complete")
    assert old.wait("after:complete")["state"] == "late_response"
    after = lab.inspect(intent)
    assert {k: after[k] for k in ("state", "reason", "uncertain", "completion_evidence")} == {
        k: before[k] for k in ("state", "reason", "uncertain", "completion_evidence")}
    assert lab.observe("SELECT state,uncertain FROM zabalack.outcomes WHERE attempt_id=%s",
                       (completion["attempt_id"],)) == [("late_response", True)]
    old.release("after:complete")
    old.finish()
    lab.assert_no_resend(intent)
    lab.reconcile(intent)
