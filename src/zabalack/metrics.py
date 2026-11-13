"""Low-cardinality, allowlisted observability; never logs proofs, notes or secrets."""
from __future__ import annotations

import json
import logging
import re
from threading import Lock
from typing import Any

from .config import token_expiry

LOG = logging.getLogger("zabalack")
SAFE_FIELDS = frozenset({"action_id", "capture_id", "instance", "event_id", "result", "reason", "attempt", "latency_ms", "error_class", "worker_id"})


def log_event(event: str, **fields: Any) -> None:
    record = {"event": event}
    record.update({k: str(v)[:180] for k, v in fields.items() if k in SAFE_FIELDS})
    LOG.info(json.dumps(record, sort_keys=True, separators=(",", ":")))


_CODE = re.compile(r"[a-z_]{1,96}")
_ACTION_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def capture_disposition(outcome: Any) -> dict[str, str]:
    """What the database did with a capture, from verify_complete's own answer.

    The worker's signature and policy checks passing is not the result: the
    database decides again (the cohort can change in between) and may refuse
    without raising. Only fixed codes and the action id leave here; nothing
    else of the answer is copied.
    """
    if isinstance(outcome, dict):
        action_id, state, reason = outcome.get("action_id"), outcome.get("state"), outcome.get("reason")
        if (isinstance(action_id, str) and _ACTION_ID.fullmatch(action_id)
                and isinstance(state, str) and _CODE.fullmatch(state) and outcome.get("replay") in (None, True)):
            # reason carries the action's state: authorised, held, expired, ...
            return {"result": "verified_replay" if outcome.get("replay") else "verified",
                    "action_id": action_id, "reason": state}
        if (action_id is None and state == "failed" and "replay" not in outcome
                and isinstance(reason, str) and _CODE.fullmatch(reason)):
            return {"result": "refused", "reason": reason}
    return {"result": "unknown", "reason": "unexpected_database_result"}


class Metrics:
    """Incident de-duplication for one process. Counts are not kept here: the
    ledger holds them (`status`, exported by prometheus_text) and each result
    is one log_event line."""

    def __init__(self) -> None:
        self._incidents: set[tuple[str, str]] = set()
        self._lock = Lock()

    def incident(self, instance: str, failure_class: str) -> bool:
        """One incident per instance/class per process; SQL is cross-process authority."""
        key = (instance, failure_class)
        with self._lock:
            if key in self._incidents:
                return False
            self._incidents.add(key)
        log_event("incident_open", instance=instance, reason=failure_class)
        return True

    def clear_incident(self, instance: str, failure_class: str) -> None:
        with self._lock:
            self._incidents.discard((instance, failure_class))


def capture_health_ok(health: dict[str, Any], settings: Any) -> bool:
    """The one capture-health rule shared by the worker's delivery gate and the
    exported zabalack_capture_healthy gauge (capture_health() fields only)."""
    return (health.get("in_recovery") is False and
            health.get("database_name") == settings.database_name and
            str(health.get("deployment_id")) == str(settings.deployment_id) and
            str(health.get("zabalack_schema_version")) == "5" and
            health.get("trigger_ok") is True and health.get("functions_ok") is True and
            health.get("capture_disabled") is False and
            health.get("config_revision") == settings.config_revision)


def prometheus_text(status: dict[str, Any], settings: Any) -> str:
    """Render authoritative DB gauges for an existing textfile collector; no server."""
    from datetime import datetime, timezone
    lines = ["# Zab-al-ack acknowledgement gauges; no proofs, notes or personal identities."]
    def emit(name: str, value: float, **labels: str) -> None:
        encoded = ",".join(k + "=" + json.dumps(str(v), ensure_ascii=True) for k, v in sorted(labels.items()))
        lines.append(name + ("{" + encoded + "}" if encoded else "") + " " + str(float(value)))
    for row in status.get("per_instance", []):
        instance = row["instance"]
        for key in ("pending_count", "oldest_pending_seconds", "uncertain_count", "success_count", "failure_count", "skipped_count", "retries_sum"):
            value = row.get(key)
            if isinstance(value, (int, float)):
                emit("zabalack_" + key, value, instance=instance)
    health = status.get("health", {})
    healthy = capture_health_ok(health, settings)
    emit("zabalack_capture_healthy", int(healthy))
    failures = set()
    for row in status.get("active_incidents", []):
        code = row.get("failure_class", "unknown")
        if "permission" in code:
            code = "permission"
        elif any(token in code for token in ("token", "credential", "auth", "secret", "tls", "certificate")):
            code = "credential"
        failures.add((row["instance"], code))
    for instance in settings.endpoint_registry:
        if not healthy:
            failures.add((instance, "capture_health"))
        entry = settings.endpoint_registry[instance]
        # An absent key means no expiry is declared. Any other value that is not
        # a usable time is this instance's failure, never the whole export's.
        if not isinstance(entry, dict):
            failures.add((instance, "credential_expiry_metadata"))
        elif "token_expires_at" in entry:
            expires = token_expiry(entry["token_expires_at"])
            if expires is None:
                failures.add((instance, "credential_expiry_metadata"))
            else:
                emit("zabalack_credential_expiry_seconds", (expires - datetime.now(timezone.utc)).total_seconds(), instance=instance)
    if status.get("verifier_unhealthy", 0):
        failures.add(("verifier", "verifier_key"))
    for instance, code in sorted(failures):
        emit("zabalack_instance_failure", 1, instance=instance, failure_class=code)
    for row in status.get("instances", []):
        emit("zabalack_instance_paused", int(bool(row.get("paused"))), instance=row["instance"])
        emit("zabalack_instance_circuit_open", int(bool(row.get("circuit_open"))), instance=row["instance"])
    return "\n".join(lines) + "\n"
