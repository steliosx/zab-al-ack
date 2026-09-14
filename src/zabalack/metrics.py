"""Low-cardinality, allowlisted observability; never logs proofs, notes or secrets."""
from __future__ import annotations

import json
import logging
from collections import Counter
from threading import Lock
from typing import Any

LOG = logging.getLogger("zabalack")
SAFE_FIELDS = frozenset({"action_id", "capture_id", "instance", "event_id", "result", "reason", "attempt", "latency_ms", "error_class", "worker_id"})


def log_event(event: str, **fields: Any) -> None:
    record = {"event": event}
    record.update({k: str(v)[:180] for k, v in fields.items() if k in SAFE_FIELDS})
    LOG.info(json.dumps(record, sort_keys=True, separators=(",", ":")))


class Metrics:
    """Counters plus incident de-duplication; scrape stdout or export the snapshot."""

    def __init__(self) -> None:
        self._counters: Counter[tuple[str, str, str]] = Counter()
        self._incidents: set[tuple[str, str]] = set()
        self._lock = Lock()

    def increment(self, name: str, instance: str = "unknown", reason: str = "") -> None:
        with self._lock:
            self._counters[(name, instance, reason)] += 1

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

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"metric": k[0], "instance": k[1], "reason": k[2], "value": v}
                    for k, v in sorted(self._counters.items())]


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
    healthy = (health.get("in_recovery") is False and health.get("functions_ok") is True and
               health.get("trigger_ok") is True and health.get("capture_disabled") is False and
               health.get("config_revision") == settings.config_revision and
               str(health.get("deployment_id")) == str(settings.deployment_id))
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
        expiry = settings.endpoint_registry[instance].get("token_expires_at")
        if expiry:
            try:
                remaining = (datetime.fromisoformat(expiry.replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()
                emit("zabalack_credential_expiry_seconds", remaining, instance=instance)
            except (TypeError, ValueError):
                failures.add((instance, "credential_expiry_metadata"))
    if status.get("verifier_unhealthy", 0):
        failures.add(("verifier", "verifier_key"))
    for instance, code in sorted(failures):
        emit("zabalack_instance_failure", 1, instance=instance, failure_class=code)
    for row in status.get("instances", []):
        emit("zabalack_instance_paused", int(bool(row.get("paused"))), instance=row["instance"])
        emit("zabalack_instance_circuit_open", int(bool(row.get("circuit_open"))), instance=row["instance"])
    return "\n".join(lines) + "\n"
