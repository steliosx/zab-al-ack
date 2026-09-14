"""Generated sequential SQL safety evidence, never an in-memory ledger substitute.

Every command runs through worker_call/operator_call under its restricted role.
Examples roll back one isolated transaction; owner access only seeds the test
environment, observes rows, and advances stored deadlines without sleeping.
Both pg_lab backends are supported. WASM/native labels describe the SQL engine;
neither run proves login authentication, concurrent transactions, HA or remote
Zabbix execution. The independent model records permissions and safety facts,
not a copy of the implementation's state-transition table.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import traceback
import uuid

import hypothesis
from hypothesis import event, strategies as st
from hypothesis.reporting import with_reporter
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule, run_state_machine_as_test
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from zabalack.crypto import BindingError, build_intent, parse_time, sign_intent, verify_capture
from conftest import connect_role
from property.profiles import chosen_seed, corpus_directory, profile_name, property_seed, property_settings

pytestmark = pytest.mark.postgres
KEY = b"synthetic-hypothesis-ledger-key!!!"
INSTANCE = "hypothesis-ledger-synthetic"
COHORT = {
    "customer": "Hypothesis synthetic", "zabbix_instance": INSTANCE,
    "zabbix_trigger_id": "97100", "host_ids": ["97200"], "group_ids": ["97300"],
    "revision": "generated-v1", "policy_revision": "1", "enabled": True,
    "one_to_one": True, "comment_enabled": True, "allow_admin": False,
}
FOCUSES = ("late_response", "settled_retry", "expired_generation", "completed_generation")


@dataclass
class EffectFact:
    event_id: str
    generation: int
    primary: str
    # Completion of the source effect is an independent fact supplied by the
    # synthetic transport/operator or by known never-sent execution expiry.
    closed: bool = False
    terminal: str | None = None


@dataclass
class ActionFact:
    intent: dict
    effect: EffectFact
    deadline: object
    attempts: int = 0
    expired: bool = False
    settlement_requires_retry: bool = False
    reference: str | None = None

    @property
    def aid(self):
        return self.intent["action_id"]


@dataclass
class LeaseFact:
    action: ActionFact
    worker: str
    generation: int
    live: bool = True

    def payload(self):
        return {"action_id": self.action.aid, "worker_id": self.worker,
                "lease_generation": self.generation}


@dataclass
class SendFact:
    lease: LeaseFact
    attempt_id: str
    unresolved: bool = True
    abandoned: bool = False


class LedgerMachine(RuleBasedStateMachine):
    def __init__(self, pg_lab, focus):
        super().__init__()
        self.focus = focus
        self.conn = connect_role(pg_lab, "zabalack_worker")
        # The supplemental socket multiplexes one PostgreSQL backend. Prepared
        # statement lifetime across client reconnections is outside this test's
        # scope; use identical unprepared calls on both SQL engines.
        self.conn.prepare_threshold = None
        self.tx = self.conn.transaction(force_rollback=True)
        self.tx.__enter__()
        self.actions: list[ActionFact] = []
        self.leases: list[LeaseFact] = []
        self.sends: list[SendFact] = []
        self.effects: list[EffectFact] = []
        self.paused = False
        self.sequence = 0
        self.trace: list[dict] = []
        try:
            # Keep other suite queues outside this rolled-back synthetic example.
            self.owner("UPDATE zabalack.outbox SET due_at=clock_timestamp()+interval '1 day' WHERE state IN ('captured_unverified','verifier_hold')")
            self.owner("UPDATE zabalack.verification_leases SET expires_at=clock_timestamp()-interval '1 second'")
            for instance in self.worker("status")["instances"]:
                self.operator("pause", instance=instance["instance"], reason="generated_test_isolation")
            self.operator("capture_bypass", enabled=False, config_revision="generated-v1")
            self.operator("configure_installation", deployment_id="isolated-generated-ledger", config_revision="generated-v1")
            self.operator("configure_cohort", cohort=COHORT)
            self.operator("set_external_writes", enabled=True, confirmation="ENABLE_EXTERNAL_WRITES", gates_reviewed=True)
            self.operator("resume", instance=INSTANCE)
        except BaseException:
            self.tx.__exit__(*__import__("sys").exc_info())
            self.conn.close()
            raise

    def teardown(self):
        self.tx.__exit__(None, None, None)
        self.conn.close()

    def role(self, name):
        self.conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(name)))
        assert self.conn.execute("SELECT current_user").fetchone()[0] == name

    def owner(self, statement, args=()):
        self.role("zabalack_owner")
        return self.conn.execute(statement, args)

    def command(self, role, function, operation, data):
        self.role(role)
        self.trace.append({"role": role, "operation": operation,
                           "action_id": data.get("action_id")})
        result, failure = self.invoke(function, operation, data)
        assert failure == "", ("unexpected_SQL_rejection", operation, failure)
        return result

    def invoke(self, function, operation, data):
        # Keep unexpected errors inside a PostgreSQL subtransaction too, so a
        # real failing property can be minimized without contaminating the
        # WASM adapter's single backend during subsequent examples.
        statement = sql.SQL("""DO $generated$
          DECLARE result jsonb; failure text := '';
          BEGIN
            BEGIN result := zabalack.{}({},{}::jsonb);
            EXCEPTION WHEN OTHERS THEN failure := SQLERRM; END;
            PERFORM set_config('ack_test.generated_result',coalesce(result::text,'null'),true);
            PERFORM set_config('ack_test.generated_error',failure,true);
          END $generated$""").format(sql.Identifier(function), sql.Literal(operation), sql.Literal(json.dumps(data)))
        self.conn.execute(statement)
        return self.conn.execute("SELECT current_setting('ack_test.generated_result')::jsonb,current_setting('ack_test.generated_error')").fetchone()

    def denied(self, kind, operation, expected, **data):
        """Catch expected SQL rejection inside a PostgreSQL subtransaction.

        The identical DO block runs on both engines, under the restricted role.
        This avoids treating WASM wire-protocol error recovery as SQL evidence.
        Unexpected acceptance/error text fails the Python assertion.
        """
        role = "zabalack_worker" if kind == "worker" else "zabalack_operator"
        payload = ({"worker_id": "generated-verifier"} if kind == "worker" else
                   {"actor_ref": "synthetic-reviewer", "change_ref": "LAB-GENERATED"}) | data
        self.role(role)
        _, failure = self.invoke(kind + "_call", operation, payload)
        assert expected in failure, ("expected_rejection", operation, expected, failure)

    def worker(self, operation, **data):
        return self.command("zabalack_worker", "worker_call", operation,
                            {"worker_id": "generated-verifier", **data})

    def operator(self, operation, **data):
        return self.command("zabalack_operator", "operator_call", operation,
                            {"actor_ref": "synthetic-reviewer", "change_ref": "LAB-GENERATED", **data})

    def inspect(self, action):
        rows = self.worker("inspect", action_id=action.aid)
        assert len(rows) == 1
        return rows[0]

    def fresh_uuid(self, label):
        self.sequence += 1
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"zabalack-generated:{label}:{self.sequence}"))

    def capture(self, intent, marker):
        self.role("alerta_app")
        attributes = {"source_system": "zabbix", **intent["source"], "zabbix_ack_sync_intent": intent}
        self.conn.execute(
            "INSERT INTO alerts(id,resource,event,status,customer,attributes,history) "
            "VALUES(%s,'synthetic-generated','stable-trigger','open',%s,%s,ARRAY[]::history[]) "
            "ON CONFLICT(id) DO UPDATE SET status='open',attributes=excluded.attributes",
            (intent["alert_id"], intent["customer"], Jsonb(attributes)),
        )
        self.conn.execute(
            "UPDATE alerts SET status='ack',history=ARRAY[ROW(%s,'stable-trigger',NULL,'ack',NULL,%s,'ack',"
            "clock_timestamp() AT TIME ZONE 'UTC',%s,NULL)::history] WHERE id=%s",
            (self.fresh_uuid("history"), marker, intent["actor"]["login"], intent["alert_id"]),
        )
        captured = self.worker("claim_verification")
        assert captured and captured["intent"]["action_id"] == intent["action_id"]
        return captured

    def new_action(self, event_id="97400", *, forged_first=False):
        # Verification performs the same due-expiry cleanup. Run the scan before
        # constructing a strictly-later fixture timestamp, then update expected
        # completion solely from the model's known deadline/send/lease facts.
        self.worker("reconcile_due")
        self.close_safely_expired_effects()
        now = self.owner("SELECT clock_timestamp()").fetchone()[0]
        previous = next((effect for effect in reversed(self.effects) if effect.event_id == event_id), None)
        if previous is not None and previous.closed:
            # The contract requires issuance strictly AFTER settled evidence.
            # WASM clock resolution may give successive statements equal times.
            # Establish the fixture precondition with bounded clock reads, not
            # an expectation inferred from the returned generation/state.
            completed = self.owner("SELECT completed_at FROM zabalack.effects WHERE primary_action_id=%s", (previous.primary,)).fetchone()[0]
            assert completed is not None
            for _ in range(100):
                now = self.owner("SELECT clock_timestamp()").fetchone()[0]
                if now > completed:
                    break
            assert now > completed, "test clock did not advance beyond prior source completion"
        intent = build_intent(
            self.fresh_uuid("alert"), COHORT["customer"],
            {"zabbix_instance": INSTANCE, "zabbix_trigger_id": "97100", "zabbix_event_id": event_id},
            {"login": "synthetic.generated", "customers": [COHORT["customer"]], "admin": False, "auth_type": "user"},
            "generated-v1", "1", now=now, action_id=self.fresh_uuid("action"),
        )
        proof = sign_intent(intent, "lab", KEY)
        if forged_first:
            # A bad capture presents the future genuine action's identity. It
            # must not reserve that identity or consume any delivery capacity.
            bad = self.capture(intent, proof[:-2] + ("0" if proof[-2] != "0" else "1") + "]")
            with pytest.raises(BindingError, match="mac_invalid"):
                verify_capture(bad, {"lab": KEY})
            self.worker("reject_capture", capture_id=bad["capture_id"], lease_generation=bad["lease_generation"], reason="mac_invalid")
            assert self.worker("inspect", action_id=intent["action_id"]) == []
            assert self.worker("claim_delivery", action_id=intent["action_id"]) is None
            assert self.owner("SELECT count(*) FROM zabalack.delivery_slots WHERE instance=%s", (INSTANCE,)).fetchone()[0] == 0
        captured = self.capture(intent, proof)
        verified = verify_capture(captured, {"lab": KEY})
        # SQL independently binds the verifier result to its captured snapshot.
        altered = {**verified, "customer": "other synthetic customer"}
        self.denied("worker", "verify_complete", "verified intent differs", capture_id=captured["capture_id"],
                    lease_generation=captured["lease_generation"], intent=altered, actor_ref="synthetic-opaque")
        result = self.worker("verify_complete", capture_id=captured["capture_id"],
                             lease_generation=captured["lease_generation"], intent=verified, actor_ref="synthetic-opaque")
        if previous is None or previous.closed:
            effect = EffectFact(event_id, 1 if previous is None else previous.generation + 1, intent["action_id"])
            self.effects.append(effect)
        else:
            effect = previous
        action = ActionFact(intent, effect, parse_time(intent["execution_expires_at"]))
        action.reference = result["public_reference"]
        self.actions.append(action)
        return action

    def unresolved(self, action, *, abandoned=False):
        return any(s.lease.action.effect is action.effect and s.unresolved and (not abandoned or s.abandoned)
                   for s in self.sends)

    def active(self, action):
        return any(lease.live and lease.action.effect.event_id == action.effect.event_id for lease in self.leases)

    def close_safely_expired_effects(self):
        for action in self.actions:
            if (not action.effect.closed and action.expired and action.effect.primary == action.aid
                    and not self.active(action) and not self.unresolved(action)):
                action.effect.closed, action.effect.terminal = True, "expired"

    def claim(self, action, worker="generated-a"):
        result = self.worker("claim_delivery", action_id=action.aid, worker_id=worker)
        self.close_safely_expired_effects()
        if result is None:
            return None
        assert not self.paused and not action.expired and not action.settlement_requires_retry
        assert not action.effect.closed and action.effect.primary == action.aid
        assert not self.active(action), "two delivery owners for the same source event"
        prior = [lease.generation for lease in self.leases if lease.action.effect.event_id == action.effect.event_id]
        assert result["lease_generation"] > max(prior, default=0)
        lease = LeaseFact(action, worker, result["lease_generation"])
        self.leases.append(lease)
        if self.unresolved(action, abandoned=True):
            assert result["uncertain"] is True
            assert self.worker("before_write", **lease.payload())["allowed"] is False
            assert self.worker("start_attempt", **lease.payload())["allowed"] is False
        return lease

    def start(self, lease):
        # An actual worker sends at most once for a claimed attempt. Later
        # retry/ownership paths are generated separately.
        if any(s.lease is lease for s in self.sends):
            return None
        permitted = self.worker("before_write", **lease.payload())
        blocked = (not lease.live or self.paused or lease.action.expired
                   or lease.action.effect.closed or lease.action.settlement_requires_retry
                   or self.unresolved(lease.action, abandoned=True))
        if blocked:
            assert permitted["allowed"] is False
            if lease.live:
                assert self.worker("start_attempt", **lease.payload())["allowed"] is False
            else:
                self.denied("worker", "start_attempt", "delivery lease lost", **lease.payload())
            return None
        assert permitted["allowed"] is True
        result = self.worker("start_attempt", **lease.payload())
        assert result["allowed"] is True
        lease.action.attempts += 1
        send = SendFact(lease, result["attempt_id"])
        self.sends.append(send)
        return send

    def expire_lease(self, lease):
        if not lease.live:
            return
        self.owner("UPDATE zabalack.delivery_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=%s AND generation=%s",
                   (lease.action.aid, lease.generation))
        self.owner("UPDATE zabalack.delivery_slots SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=%s AND generation=%s",
                   (lease.action.aid, lease.generation))
        lease.live = False
        for send in self.sends:
            if send.lease is lease and send.unresolved:
                send.abandoned = True

    def reply(self, send, outcome="ok"):
        lease = send.lease
        before = {a.aid: self.inspect(a) for a in self.actions}
        payload = {**lease.payload(), "attempt_id": send.attempt_id,
                   "state": "ok" if outcome == "ok" else "unknown",
                   "reason": "synthetic_response", "uncertain": outcome != "ok",
                   "completion_evidence": "write_response" if outcome == "ok" else None}
        result = self.worker("complete", **payload)
        if lease.live:
            assert result["state"] == payload["state"]
            lease.live = False
            if outcome == "ok":
                send.unresolved = False
                lease.action.effect.closed = True
                lease.action.effect.terminal = "ok"
            else:
                send.abandoned = True
        else:
            assert result["state"] == "late_response"
            if send.unresolved:
                send.abandoned = True
            # Old responses cannot overwrite another source generation, or an
            # explicit settlement even after a separate retry has queued it.
            for action in self.actions:
                protected = action.effect is not lease.action.effect or not send.unresolved
                if protected:
                    after = self.inspect(action)
                    for key in ("state", "uncertain", "attempts", "completion_evidence"):
                        assert after[key] == before[action.aid][key], ("stale_completion", key)

    def settle(self, action):
        if not self.unresolved(action, abandoned=True) or self.active(action):
            return
        self.worker("reconcile_due")
        self.close_safely_expired_effects()
        assert self.inspect(action)["uncertain"] is True
        self.denied("operator", "settle_unknown", "explicit fenced-and-settled", action_id=action.aid)
        self.operator("settle_unknown", action_id=action.aid, disposition="no-effect-confirmed",
                      evidence_ref="synthetic-fenced-no-effect-fixture", confirmation="REMOTE_ATTEMPT_FENCED_AND_SETTLED")
        for send in self.sends:
            if send.lease.action.effect is action.effect:
                send.unresolved = False
        action.settlement_requires_retry = True
        if action.expired:
            action.effect.closed, action.effect.terminal = True, "expired"
        row = self.inspect(action)
        assert row["uncertain"] is False and row["state"] in {"failed", "expired"}
        assert self.claim(action) is None, "settlement itself queued a new source effect"

    def retry(self, action):
        before = self.inspect(action)
        if action.expired or action.effect.terminal in {"ok", "expired"}:
            self.denied("operator", "retry", "terminal/expired", action_id=action.aid)
            return
        self.operator("retry", action_id=action.aid)
        action.settlement_requires_retry = False
        after = self.inspect(action)
        assert after["execution_expires_at"] == before["execution_expires_at"]
        assert after["public_reference"] == before["public_reference"]
        assert after["uncertain"] == before["uncertain"]

    def expire_action(self, action):
        if action.expired or action.effect.closed:
            return
        deadline = self.owner("SELECT clock_timestamp()-interval '1 second'").fetchone()[0]
        self.owner("UPDATE zabalack.actions SET execute_before=%s WHERE action_id=%s", (deadline, action.aid))
        action.deadline, action.expired = deadline, True
        self.worker("reconcile_due")
        self.close_safely_expired_effects()
        for lease in self.leases:
            if lease.action is action:
                assert self.worker("before_write", **lease.payload())["allowed"] is False

    @initialize()
    def seed_regression(self):
        action = self.new_action(forged_first=True)
        if self.focus == "expired_generation":
            self.expire_action(action)
            newer = self.new_action()
            assert newer.effect.generation == 2
            assert self.claim(newer) is not None
            return
        lease = self.claim(action)
        assert lease is not None
        send = self.start(lease)
        assert send is not None
        if self.focus == "completed_generation":
            self.reply(send)
            newer = self.new_action()
            assert newer.effect.generation == 2
            assert self.claim(newer) is not None
            self.reply(send, "unknown")
            return
        self.expire_lease(lease)
        self.reply(send, "unknown")
        assert self.claim(action) is None
        if self.focus == "settled_retry":
            self.settle(action)
            self.reply(send)
            self.retry(action)
            replacement = self.claim(action, "generated-b")
            assert replacement is not None
            self.reply(send, "unknown")
            assert self.worker("before_write", **replacement.payload())["allowed"] is True

    @rule(event_number=st.integers(min_value=0, max_value=2))
    def explicit_action(self, event_number):
        if len(self.actions) < 6:
            self.new_action(str(97400 + event_number))

    @rule(index=st.integers(min_value=0, max_value=7), worker=st.sampled_from(("generated-a", "generated-b", "generated-c")))
    def delivery_claim(self, index, worker):
        self.claim(self.actions[index % len(self.actions)], worker)

    @rule(index=st.integers(min_value=0, max_value=7))
    def begin_send(self, index):
        if self.leases:
            self.start(self.leases[index % len(self.leases)])

    @rule(index=st.integers(min_value=0, max_value=7), outcome=st.sampled_from(("ok", "unknown")))
    def response_arrives(self, index, outcome):
        if self.sends:
            self.reply(self.sends[index % len(self.sends)], outcome)

    @rule(index=st.integers(min_value=0, max_value=7))
    def delivery_lease_expires(self, index):
        if self.leases:
            self.expire_lease(self.leases[index % len(self.leases)])

    @rule(worker=st.sampled_from(("generated-a", "generated-b", "generated-c")))
    def worker_stops(self, worker):
        self.worker("worker_stopped", worker_id=worker)
        for lease in self.leases:
            if lease.worker == worker:
                lease.live = False
                for send in self.sends:
                    if send.lease is lease and send.unresolved:
                        send.abandoned = True

    @rule(paused=st.booleans())
    def maintenance(self, paused):
        counts = {a.aid: self.inspect(a)["attempts"] for a in self.actions}
        self.operator("pause" if paused else "resume", instance=INSTANCE, reason="synthetic_maintenance")
        self.paused = paused
        for action in self.actions:
            assert self.inspect(action)["attempts"] == counts[action.aid]
        if paused:
            for lease in self.leases:
                assert self.worker("before_write", **lease.payload())["allowed"] is False

    @rule(index=st.integers(min_value=0, max_value=7))
    def execution_deadline_passes(self, index):
        self.expire_action(self.actions[index % len(self.actions)])

    @rule(index=st.integers(min_value=0, max_value=7))
    def explicit_retry(self, index):
        self.retry(self.actions[index % len(self.actions)])

    @rule(index=st.integers(min_value=0, max_value=7))
    def operator_settlement(self, index):
        action = self.actions[index % len(self.actions)]
        if action.effect.primary == action.aid:
            self.settle(action)

    @rule()
    def reconciliation_scan(self):
        self.worker("reconcile_due")
        self.close_safely_expired_effects()

    @invariant()
    def immutable_authority_and_send_budget(self):
        rows = self.owner(
            "SELECT to_jsonb(a) FROM zabalack.actions a WHERE instance=%s", (INSTANCE,),
        ).fetchall()
        observed = {row[0]["action_id"]: row[0] for row in rows}
        assert set(observed) == {a.aid for a in self.actions}, "unverified identity reserved an action"
        for action in self.actions:
            row = observed[action.aid]
            assert row["event_id"] == action.intent["source"]["zabbix_event_id"]
            assert row["alert_id"] == action.intent["alert_id"] and row["customer"] == action.intent["customer"]
            assert parse_time(row["execute_before"]) == action.deadline, "retry/pause extended execution lifetime"
            assert row["attempts"] == action.attempts
            assert row["public_reference"] == action.reference
            if action.effect.terminal == "ok":
                assert row["state"] == "ok" and row["uncertain"] is False
            if action.settlement_requires_retry:
                assert row["state"] in {"failed", "expired"} and row["uncertain"] is False
        generations = self.owner(
            "SELECT event_id,generation,primary_action_id::text FROM zabalack.effects WHERE instance=%s ORDER BY event_id,generation", (INSTANCE,),
        ).fetchall()
        assert generations == sorted((e.event_id, e.generation, e.primary) for e in self.effects)
        attempts = self.owner(
            "SELECT action_id::text,frozen_event_id,public_reference FROM zabalack.attempts WHERE action_id IN "
            "(SELECT action_id FROM zabalack.actions WHERE instance=%s)", (INSTANCE,),
        ).fetchall()
        assert len(attempts) == len(self.sends)
        for aid, event_id, reference in attempts:
            action = next(a for a in self.actions if a.aid == aid)
            assert event_id == action.intent["source"]["zabbix_event_id"] and reference == action.reference
        event(f"focus={self.focus}; sends={min(len(self.sends), 3)}; generations={min(len(self.effects), 4)}")


@pytest.mark.parametrize("focus", FOCUSES)
def test_generated_delivery_ledger(pg_lab, focus, record_property):
    """Four fixed regression prefixes followed by freely generated operations."""
    record_property("engine", pg_lab["backend"])
    record_property("evidence_scope", "sequential restricted-role PostgreSQL state exploration")
    record_property("hypothesis_profile", profile_name())
    record_property("stateful_focus", focus)
    reports = []
    factory = property_seed()(lambda: LedgerMachine(pg_lab, focus))
    try:
        with with_reporter(reports.append):
            run_state_machine_as_test(factory, settings=property_settings(stateful=True))
    except BaseException as error:
        # Hypothesis shrinks before raising. Keep the minimized, printable
        # sequence plus replay metadata in the private area even on fixed-seed PRs.
        path = corpus_directory() / f"ledger-{focus}-{profile_name()}-failure.txt"
        source = Path(__file__)
        migrations = source.parents[2] / "sql" / "migrations"
        path.write_text(json.dumps({"seed": chosen_seed(), "profile": profile_name(),
                                    "focus": focus, "engine": pg_lab["backend"],
                                    "hypothesis": hypothesis.__version__,
                                    "test_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                                    "sql_sha256": {item.name: hashlib.sha256(item.read_bytes()).hexdigest()
                                                   for item in sorted(migrations.glob("*.sql"))}}) + "\n"
                        + "\n".join(map(str, reports)) + "\n"
                        + "".join(traceback.format_exception(error)))
        path.chmod(0o600)
        raise


def test_T9_T31_settled_no_effect_expiry_allows_fresh_explicit_action(pg_lab):
    """Direct SQL reproduction, independent of generated model expectations."""
    machine = LedgerMachine(pg_lab, "late_response")
    try:
        original = machine.new_action()
        claim = machine.worker("claim_delivery", action_id=original.aid, worker_id="generated-a")
        lease = {"action_id": original.aid, "worker_id": "generated-a", "lease_generation": claim["lease_generation"]}
        assert machine.worker("start_attempt", **lease)["allowed"] is True
        machine.worker("worker_stopped", worker_id="generated-a")
        machine.operator("settle_unknown", action_id=original.aid, disposition="no-effect-confirmed",
                         evidence_ref="synthetic-fenced-no-effect-fixture", confirmation="REMOTE_ATTEMPT_FENCED_AND_SETTLED")
        assert machine.inspect(original)["state"] == "failed"
        machine.owner("UPDATE zabalack.actions SET execute_before=clock_timestamp()-interval '1 second' WHERE action_id=%s", (original.aid,))
        machine.worker("reconcile_due")
        now = machine.owner("SELECT clock_timestamp()").fetchone()[0]
        fresh = build_intent(machine.fresh_uuid("fresh-alert"), COHORT["customer"], dict(original.intent["source"]),
                             dict(original.intent["actor"]), "generated-v1", "1", now=now,
                             action_id=machine.fresh_uuid("fresh-action"))
        capture = machine.capture(fresh, sign_intent(fresh, "lab", KEY))
        verified = verify_capture(capture, {"lab": KEY})
        result = machine.worker("verify_complete", capture_id=capture["capture_id"],
                                lease_generation=capture["lease_generation"], intent=verified, actor_ref="synthetic-opaque")
        generations = machine.owner("SELECT generation,state,completed_at FROM zabalack.effects WHERE instance=%s ORDER BY generation", (INSTANCE,)).fetchall()
        assert result["state"] == "authorised", ("known_no_effect_expiry_trapped_new_action", result["state"], generations)
        assert [row[0] for row in generations] == [1, 2]
    finally:
        machine.teardown()
