"""Forward 006→007 expiry correction; real SQL, isolated rollback, synthetic time.

These tests use signed capture and the restricted SQL facades, with owner-only
deadline advancement/guard fixtures. WASM remains sequential supplemental
evidence: no native concurrency, elapsed four-hour window or source write claim.
"""
from __future__ import annotations

import json
from pathlib import Path
import uuid

from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from zabalack.crypto import build_intent, sign_intent, verify_capture
from conftest import connect_role

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]
KEY = b"synthetic-settled-expiry-key-only!"


class SettlementFixture:
    def __init__(self, connection):
        self.connection = connection
        self.instance = "settled-expiry-" + uuid.uuid4().hex
        self.cohort = {"customer": "Settled Expiry Synthetic", "zabbix_instance": self.instance,
            "zabbix_trigger_id": "98200", "host_ids": ["98100"], "group_ids": ["98300"],
            "revision": "settled-expiry-v1", "policy_revision": "1", "enabled": True,
            "one_to_one": True, "comment_enabled": True, "allow_admin": False}
        self.owner("UPDATE zabalack.outbox SET due_at=clock_timestamp()+interval '1 day' "
                   "WHERE state IN ('captured_unverified','verifier_hold')")
        for instance in self.worker("status")["instances"]:
            self.operator("pause", instance=instance["instance"], reason="synthetic_upgrade_isolation")
        self.operator("capture_bypass", enabled=False, config_revision="settled-expiry-v1")
        self.operator("configure_installation", deployment_id="isolated-settled-expiry", config_revision="settled-expiry-v1")
        self.operator("configure_cohort", cohort=self.cohort)
        self.operator("set_external_writes", enabled=True, confirmation="ENABLE_EXTERNAL_WRITES", gates_reviewed=True)
        self.operator("resume", instance=self.instance)

    def role(self, name):
        self.connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(name)))
        assert self.connection.execute("SELECT current_user").fetchone()[0] == name

    def owner(self, statement, parameters=()):
        self.role("zabalack_owner")
        return self.connection.execute(statement, parameters)

    def worker(self, operation, **payload):
        self.role("zabalack_worker")
        return self.connection.execute("SELECT zabalack.worker_call(%s,%s)",
            (operation, Jsonb({"worker_id": "settled-expiry-worker", **payload}))).fetchone()[0]

    def operator(self, operation, **payload):
        self.role("zabalack_operator")
        return self.connection.execute("SELECT zabalack.operator_call(%s,%s)",
            (operation, Jsonb({"actor_ref": "synthetic-reviewer", "change_ref": "LAB-SETTLED-EXPIRY", **payload}))).fetchone()[0]

    def capture(self, event_id="98400"):
        now = self.owner("SELECT clock_timestamp()").fetchone()[0]
        intent = build_intent(str(uuid.uuid4()), self.cohort["customer"],
            {"zabbix_instance": self.instance, "zabbix_trigger_id": "98200", "zabbix_event_id": event_id},
            {"login": "synthetic.expiry", "customers": [self.cohort["customer"]], "admin": False, "auth_type": "user"},
            "settled-expiry-v1", "1", now=now)
        attributes = {"source_system": "zabbix", **intent["source"], "zabbix_ack_sync_intent": intent}
        self.role("alerta_app")
        self.connection.execute("INSERT INTO alerts(id,resource,event,status,customer,attributes,history) "
            "VALUES(%s,'synthetic-expiry','stable-trigger','open',%s,%s,ARRAY[]::history[])",
            (intent["alert_id"], intent["customer"], Jsonb(attributes)))
        self.connection.execute("UPDATE alerts SET status='ack',history=ARRAY[ROW(%s,'stable-trigger',NULL,'ack',NULL,%s,"
            "'ack',clock_timestamp() AT TIME ZONE 'UTC',%s,NULL)::history] WHERE id=%s",
            (str(uuid.uuid4()), sign_intent(intent, "lab", KEY), intent["actor"]["login"], intent["alert_id"]))
        captured = self.worker("claim_verification")
        assert captured["intent"]["action_id"] == intent["action_id"]
        verified = verify_capture(captured, {"lab": KEY})
        return self.worker("verify_complete", capture_id=captured["capture_id"],
            lease_generation=captured["lease_generation"], intent=verified, actor_ref="synthetic-expiry-opaque")

    def abandon(self, action):
        claim = self.worker("claim_delivery", action_id=action["action_id"])
        lease = {"action_id": action["action_id"], "lease_generation": claim["lease_generation"]}
        assert self.worker("before_write", **lease)["allowed"]
        attempt = self.worker("start_attempt", **lease)
        assert attempt["attempt_id"]
        # Persisted attempt with an unknown synthetic transport outcome; no HTTP.
        self.worker("worker_stopped")
        assert self.inspect(action)["uncertain"]
        return attempt

    def settle(self, action):
        self.operator("settle_unknown", action_id=action["action_id"], disposition="no-effect-confirmed",
            evidence_ref="synthetic-fenced-no-effect-evidence", confirmation="REMOTE_ATTEMPT_FENCED_AND_SETTLED")
        row = self.inspect(action)
        assert row["state"] == "failed" and row["uncertain"] is False
        assert self.worker("claim_delivery", action_id=action["action_id"]) is None

    def inspect(self, action):
        return self.worker("inspect", action_id=action["action_id"])[0]

    def effect(self, action):
        return self.owner("SELECT generation,state,completed_at FROM zabalack.effects WHERE effect_id="
            "(SELECT effect_id FROM zabalack.actions WHERE action_id=%s)", (action["action_id"],)).fetchone()

    def expire(self, action):
        # Explicit synthetic clock advancement; the signed original intent is
        # retained. This is not native elapsed-lifetime evidence.
        return self.owner("UPDATE zabalack.actions SET execute_before=clock_timestamp()-interval '1 second' "
            "WHERE action_id=%s RETURNING execute_before", (action["action_id"],)).fetchone()[0]

    def retry_denied(self, action):
        self.role("zabalack_operator")
        payload = {"action_id": action["action_id"], "actor_ref": "synthetic-reviewer", "change_ref": "LAB-EXPIRED-RETRY"}
        # SQL subtransaction preserves the WASM adapter connection on rejection.
        statement = sql.SQL("""DO $expiry$
          DECLARE rejected boolean:=false;
          BEGIN
            BEGIN PERFORM zabalack.operator_call('retry',{}::jsonb);
            EXCEPTION WHEN raise_exception THEN rejected:=true; END;
            IF NOT rejected THEN RAISE EXCEPTION 'expired action retry was accepted'; END IF;
          END $expiry$""").format(sql.Literal(json.dumps(payload)))
        self.connection.execute(statement)


@pytest.fixture
def settlement(pg_lab, record_property):
    record_property("engine", pg_lab["backend"])
    record_property("evidence_scope", "sequential restricted-role SQL with synthetic deadlines; no remote writes")
    with connect_role(pg_lab, "zabalack_owner") as connection:
        connection.prepare_threshold = None
        with connection.transaction(force_rollback=True):
            yield SettlementFixture(connection)


def test_T9_T21_T31_forward_007_releases_previously_settled_expired_generation(settlement):
    lab = settlement
    migration006 = (ROOT / "sql/migrations/006_late_response_fencing.sql").read_text()
    begin = migration006.index("CREATE OR REPLACE FUNCTION zabalack.expire_due_effects")
    end = migration006.index("CREATE OR REPLACE FUNCTION zabalack.worker_call")
    # Restore only the shipped 006 expiry function in this rollback transaction;
    # the real migration ledger and every earlier file stay unchanged.
    lab.owner(migration006[begin:end])
    lab.owner("UPDATE zabalack.installation SET schema_version=2 WHERE singleton")
    lab.role("alerta_owner")
    lab.connection.execute("SELECT zabalack.seal_installation()")
    original = lab.capture()
    attempt = lab.abandon(original)
    lab.settle(original)
    assert lab.effect(original)[2] is None
    assert lab.owner("SELECT zabalack.attempt_unsettled(%s)", (attempt["attempt_id"],)).fetchone() == (False,)
    deadline = lab.expire(original)
    lab.worker("reconcile_due")
    trapped = lab.capture()
    assert trapped["state"] == "held", "006 fixture must reproduce the open settled-effect defect"
    assert lab.effect(original)[0] == lab.effect(trapped)[0] == 1
    assert lab.effect(original)[2] is None
    lab.retry_denied(original)

    lab.owner((ROOT / "sql/migrations/007_settled_expiry.sql").read_text())
    health = lab.worker("doctor")
    assert health["zabalack_schema_version"] == 3
    assert health["functions_ok"] is False, "forward migration must require explicit function resealing"
    lab.worker("reconcile_due")
    assert lab.effect(original) == (1, "expired", deadline)
    assert lab.inspect(original)["state"] == lab.inspect(trapped)["state"] == "expired"
    assert lab.inspect(original)["uncertain"] is False
    lab.retry_denied(original)
    lab.role("alerta_owner")
    lab.connection.execute("SELECT zabalack.seal_installation()")
    assert lab.worker("doctor")["functions_ok"] is True
    fresh = lab.capture()
    assert fresh["state"] == "authorised" and lab.effect(fresh)[0] == 2
    assert lab.worker("claim_delivery", action_id=original["action_id"]) is None
    assert lab.worker("claim_delivery", action_id=trapped["action_id"]) is None
    claim = lab.worker("claim_delivery", action_id=fresh["action_id"])
    assert claim["action_id"] == fresh["action_id"]
    assert lab.worker("before_write", action_id=fresh["action_id"], lease_generation=claim["lease_generation"])["allowed"]
    assert lab.owner("SELECT count(*) FROM zabalack.attempts WHERE action_id IN (%s,%s,%s)",
        (original["action_id"], trapped["action_id"], fresh["action_id"])).fetchone() == (1,), "expiry must not initiate another attempt"


@pytest.mark.parametrize("guard", ["uncertain", "unsettled_attempt", "active_lease"])
def test_T9_T31_forward_007_keeps_unsettled_or_live_effects_open(settlement, guard):
    lab = settlement
    original = lab.capture()
    attempt = lab.abandon(original)
    if guard == "active_lease":
        lab.settle(original)
        # An independently live synthetic lease must retain the old effect even
        # when the known-no-effect action now qualifies for the new predicate.
        lab.owner("UPDATE zabalack.delivery_leases SET expires_at=clock_timestamp()+interval '1 minute' "
                  "WHERE action_id=%s", (original["action_id"],))
    elif guard == "unsettled_attempt":
        # Historical inconsistent action flags cannot hide the durable attempt.
        # There is deliberately no operator no-effect outcome settling this send.
        lab.owner("UPDATE zabalack.actions SET state='failed',reason='operator_no_effect_confirmed',uncertain=false "
                  "WHERE action_id=%s", (original["action_id"],))
        assert lab.owner("SELECT zabalack.attempt_unsettled(%s)", (attempt["attempt_id"],)).fetchone() == (True,)
    lab.expire(original)
    lab.worker("reconcile_due")
    assert lab.effect(original)[2] is None
    later = lab.capture()
    assert later["state"] == "held" and lab.effect(later)[0] == 1
    assert lab.worker("claim_delivery", action_id=later["action_id"]) is None
    assert lab.owner("SELECT count(*) FROM zabalack.attempts WHERE action_id=%s",
                     (original["action_id"],)).fetchone() == (1,)
