"""Reviewed behavioural mutants, not a claim of exhaustive mutation coverage.

Every substitution must match exactly once. A source refactor therefore fails
closed until the operator and its independent invariant test are reviewed again.
No SQL, installed service, credential or original checkout is mutated.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Mutant:
    name: str
    path: str
    before: str
    after: str
    invariant: str
    nightly_only: bool = False


# Both profiles use the same bounded, deterministic unit/mock test selection.
# This makes a baseline/mutant's complete collected test identities comparable.
TESTS = (
    'tests/unit/test_crypto_policy.py',
    'tests/unit/test_worker.py',
    'tests/security/test_binding_boundary.py',
    'tests/security/test_rpc_boundaries.py',
    'tests/rpc/test_rpc.py',
    'tests/unit/test_delivery_invariants.py',
)

CATALOG = (
    Mutant('mac_check_removed', 'src/zabalack/crypto.py',
           'if not hmac.compare_digest(hmac.new(key, data, hashlib.sha256).hexdigest(), mac):',
           'if False:', 'T8: a structurally valid forged MAC cannot authorise a capture'),
    Mutant('observed_source_binding_removed', 'src/zabalack/crypto.py',
           'for field in ("alert_id", "customer", "source"):',
           'for field in ("alert_id", "customer"):',
           'T10/T14: the signed source must match the captured source'),
    Mutant('history_actor_binding_removed', 'src/zabalack/crypto.py',
           'or capture.get("history_user") != intent["actor"]["login"]):',
           'or False):', 'T8/T14: committed history must identify the signed actor'),
    Mutant('capture_window_removed', 'src/zabalack/crypto.py',
           'if not issued <= observed <= parse_time(intent["capture_expires_at"]):',
           'if False:', 'T9: database observation must fall within the original capture window'),
    Mutant('actor_customer_scope_removed', 'src/zabalack/policy.py',
           'return actor.get("admin") is False and customer in actor.get("customers", [])',
           'return actor.get("admin") is False',
           'T11a/T24: a human must have authority for the target customer'),
    Mutant('host_drift_gate_removed', 'src/zabalack/policy.py',
           'or not cohort["one_to_one"] or cohort.get("host_drift", False) or cohort["revision"]',
           'or not cohort["one_to_one"] or False or cohort["revision"]',
           'T33: an observed host identity drift blocks delivery authorisation'),
    Mutant('unknown_delivery_gate_removed', 'src/zabalack/worker.py',
           'if action.get("uncertain"):', 'if False:',
           'T29/T31: an unknown prior outcome cannot cause another delivery attempt'),
    Mutant('execution_expiry_gate_removed', 'src/zabalack/worker.py',
           'if expired(action["execution_expires_at"]):', 'if False:',
           'T32: an expired action cannot resolve a target or create a source write'),
    Mutant('shadow_gate_removed', 'src/zabalack/worker.py',
           'if not self.settings.external_writes_enabled:', 'if False:',
           'T32: shadow configuration prevents endpoint and credential resolution'),
    Mutant('presend_fence_removed', 'src/zabalack/worker.py',
           'if not ready or not ready.get("allowed"):', 'if False:',
           'T31/T32: a denied before_write result cannot start a source attempt'),
    Mutant('attempt_ownership_guard_removed', 'src/zabalack/worker.py',
           'if not attempt or not attempt.get("attempt_id"):', 'if False:',
           'T31: no source write without a durable attempt identity'),
    Mutant('full_host_set_weakened_to_overlap', 'src/zabalack/rpc.py',
           'set(actual) != set(cohort["host_ids"])',
           'not set(actual).intersection(cohort["host_ids"])',
           'T12/T33: every returned host must equal the approved host set'),
    Mutant('route_action_binding_removed', 'src/zabalack/crypto.py',
           'if intent["route"] != f\'/alert/{intent["alert_id"]}/action\' or intent["action"] != "ack":',
           'if False:', 'T5/T14: signed intent is restricted to one alert acknowledgement route', True),
    Mutant('source_uint64_bound_removed', 'src/zabalack/policy.py',
           'or int(value) > 18446744073709551615:', 'or False:',
           'T13: source identifiers must be within the upstream integer range', True),
    Mutant('one_to_one_cohort_gate_removed', 'src/zabalack/policy.py',
           'or not cohort["one_to_one"] or cohort.get("host_drift", False)',
           'or False or cohort.get("host_drift", False)',
           'T11b: authorised scope still requires an approved one-to-one cohort', True),
    Mutant('event_trigger_binding_removed', 'src/zabalack/rpc.py',
           'if str(event["eventid"]) != source["zabbix_event_id"] or str(event["objectid"]) != source["zabbix_trigger_id"]:',
           'if False:', 'T13: the pre-read must return the exact frozen event and trigger', True),
    Mutant('single_event_write_scope_removed', 'src/zabalack/rpc.py',
           'if write and (type(params.get("action"))',
           'if False and (type(params.get("action"))',
           'T13: a source write may contain one event and only action 2 or 6', True),
    Mutant('already_acknowledged_gate_removed', 'src/zabalack/worker.py',
           'if str(event["acknowledged"]) == "1":', 'if False:',
           'T25: a prior source acknowledgement never generates another comment', True),
    Mutant('cancelled_sent_attempt_marked_certain', 'src/zabalack/worker.py',
           'uncertain=True, attempt_id=attempt_id, retry_delay=60))',
           'uncertain=False, attempt_id=attempt_id, retry_delay=60))',
           'T31: cancellation after a send preserves unknown outcome', True),
    Mutant('endpoint_resolved_before_verification', 'src/zabalack/worker.py',
           'intent = self.verifier(capture, self.keys)',
           'self.registry.resolve(capture["source"]["zabbix_instance"])\n            intent = self.verifier(capture, self.keys)',
           'T37: no unverified capture may resolve any endpoint or credential', True),
)


def selected(profile: str) -> tuple[Mutant, ...]:
    if profile not in {'pr', 'nightly'}:
        raise ValueError('mutation_profile_invalid')
    return tuple(item for item in CATALOG if profile == 'nightly' or not item.nightly_only)
