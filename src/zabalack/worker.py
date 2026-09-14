"""Verified, database-fenced asynchronous delivery. SQL is coordination authority."""
from __future__ import annotations

import asyncio
import hashlib
import signal
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .db import Database
from .metrics import Metrics, log_event
from .rpc import EndpointRegistry, RPCError, Timeouts, ValidationError, ZabbixClient, validate_event


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def expired(value: str, now: datetime | None = None) -> bool:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) <= (now or utcnow())


class Worker:
    def __init__(self, settings: Any, db: Database, keys: dict[str, bytes], *,
                 worker_id: str | None = None,
                 verifier: Callable[..., dict[str, Any]] | None = None,
                 client_factory: Callable[..., ZabbixClient] = ZabbixClient,
                 registry: EndpointRegistry | None = None) -> None:
        from .crypto import verify_capture
        self.settings, self.db, self.keys = settings, db, keys
        self.worker_id = worker_id or str(uuid.uuid4())
        self.verifier = verifier or verify_capture
        self.client_factory = client_factory
        self.registry = registry or EndpointRegistry(settings.endpoint_registry, lab=getattr(settings, "lab_mode", False),
            approved_versions=settings.approved_versions)
        self.metrics = Metrics()
        self.stop = asyncio.Event()
        self._healthy = False
        self._last_health = 0.0

    def lease_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        fields = {k: row[k] for k in ("capture_id", "action_id", "lease_generation") if k in row}
        fields["worker_id"] = self.worker_id
        return fields

    async def verify_once(self) -> bool:
        from .crypto import BindingError, KeyUnavailable
        from .policy import authorize_intent, select_cohort, PolicyError
        capture = await self.db.call("claim_verification", {"worker_id": self.worker_id, "limit": 1})
        if not capture:
            return False
        lease = self.lease_payload(capture)
        try:
            # Absolutely no endpoint/credential resolution occurs before this succeeds.
            intent = self.verifier(capture, self.keys)
            cohort = select_cohort(self.settings, intent["customer"], intent["source"])
            if not cohort:
                raise BindingError("actor_or_cohort_policy")
            authorize_intent(intent, cohort)
            if intent["actor"]["admin"] and intent["actor"]["login"] not in self.settings.admin_exceptions:
                raise BindingError("admin_exception_required")
            # Opaque per-action actor reference: direct person mapping stays in restricted SQL capture.
            actor_ref = hashlib.sha256((intent["action_id"] + "|actor").encode()).hexdigest()
            result = await self.db.call("verify_complete", {**lease, "intent": intent,
                "actor_ref": actor_ref})
            self.metrics.increment("verified", intent["source"]["zabbix_instance"])
            if result and result.get("action_id"):
                await self.db.call("project", {"action_id": result["action_id"]})
        except KeyUnavailable:
            await self.db.call("hold_capture", {**lease, "reason": "verifier_key_unavailable"})
            # Untrusted instance claims do not establish a real logical instance incident key.
            self.metrics.incident("verifier", "key_unavailable")
        except (BindingError, PolicyError) as exc:
            reason = str(exc) if re.fullmatch(r"[a-z_]{1,96}", str(exc)) else "failed_intent_invalid"
            await self.db.call("reject_capture", {**lease, "reason": reason, "failure_class": "failed_intent_invalid"})
            self.metrics.increment("invalid_candidates")
        except (ValueError, TypeError, KeyError):
            await self.db.call("reject_capture", {**lease, "reason": "failed_intent_invalid"})
            self.metrics.increment("invalid_candidates")
        return True

    def make_client(self, endpoint: Any) -> ZabbixClient:
        if self.client_factory is not ZabbixClient:
            return self.client_factory(endpoint)
        return ZabbixClient(endpoint, timeouts=Timeouts(self.settings.connect_timeout_seconds,
            self.settings.read_timeout_seconds, self.settings.overall_timeout_seconds),
            response_limit=self.settings.response_limit_bytes,
            background_timeouts=Timeouts(self.settings.background_connect_timeout_seconds,
                self.settings.background_read_timeout_seconds, self.settings.background_overall_timeout_seconds))

    async def complete(self, action: dict[str, Any], state: str, reason: str, *,
                       evidence: str | None = None, uncertain: bool = False,
                       attempt_id: str | None = None, retry_delay: int = 0) -> None:
        payload = {**self.lease_payload(action), "state": state, "reason": reason,
                   "completion_evidence": evidence, "uncertain": uncertain, "retry_delay": retry_delay}
        if attempt_id:
            payload["attempt_id"] = attempt_id
        await self.db.call("complete", payload)
        await self.db.call("project", {"action_id": action["action_id"]})
        self.metrics.increment(state, action["instance"], reason)
        log_event("delivery_result", action_id=action["action_id"], instance=action["instance"], result=state, reason=reason)

    async def delivery_once(self) -> bool:
        # Claim only authorises DB coordination. Endpoint resolution happens below.
        action = await self.db.call("claim_delivery", {"worker_id": self.worker_id})
        if not action:
            return False
        await self.deliver(action)
        return True

    async def deliver(self, action: dict[str, Any]) -> None:
        if action.get("uncertain"):
            await self.complete(action, "pending", "unknown_outcome_reconciliation", uncertain=True, retry_delay=60)
            return
        if expired(action["execution_expires_at"]):
            await self.complete(action, "expired", "execution_expired")
            return
        if not self.settings.external_writes_enabled:
            await self.complete(action, "pending", "shadow_hold", retry_delay=60)
            return
        if self.stop.is_set():
            await self.complete(action, "pending", "shutdown_hold", retry_delay=5)
            return
        attempt_id = None
        started = time.monotonic()
        try:
            endpoint = self.registry.resolve(action["instance"])
            client = self.make_client(endpoint)
            source = action.get("source") or {"zabbix_instance": action["instance"],
                "zabbix_event_id": action["event_id"], "zabbix_trigger_id": action["trigger_id"]}
            result = await client.event_get(action["event_id"], action["cohort"])
            event = validate_event(result, source, action["cohort"])
            if str(event["acknowledged"]) == "1":
                await self.complete(action, "ok", "already_acknowledged", evidence="source_ack_observed")
                return
            if self.stop.is_set():
                await self.complete(action, "pending", "shutdown_hold", retry_delay=5)
                return
            ready = await self.db.call("before_write", self.lease_payload(action))
            if not ready or not ready.get("allowed"):
                await self.complete(action, "expired" if ready and ready.get("reason") in {"execution_expired", "execution_lifetime_expired"} else "pending",
                                    ready.get("reason", "lease_fenced") if ready else "lease_fenced", retry_delay=60)
                return
            # DB persists attempt and rechecks pause, lifetime, revision, ownership atomically.
            attempt = await self.db.call("start_attempt", self.lease_payload(action))
            if not attempt or not attempt.get("attempt_id"):
                await self.complete(action, "pending", "lease_fenced", retry_delay=60)
                return
            attempt_id = str(attempt["attempt_id"])
            await client.acknowledge(action["event_id"], comment=action["cohort"]["comment_enabled"],
                                     public_reference=action["public_reference"], issued_at=action["issued_at"])
            # A valid response suffices; no mandatory post-write API read.
            await self.complete(action, "ok", "acknowledged", evidence="write_response", attempt_id=attempt_id)
        except ValidationError as exc:
            state = "pending" if exc.reason == "event_missing_or_not_visible" else "failed"
            await self.complete(action, state, exc.reason, retry_delay=3600 if state == "pending" else 0,
                                attempt_id=attempt_id, uncertain=exc.uncertain)
        except RPCError as exc:
            state = "pending" if exc.transient or exc.uncertain else "failed"
            retry = min(300, 2 ** min(int(action.get("attempts", 0)) + 1, 8)) if state == "pending" else 0
            await self.complete(action, state, exc.reason, uncertain=exc.uncertain,
                                attempt_id=attempt_id, retry_delay=retry)
            if not exc.transient:
                self.metrics.incident(action["instance"], exc.reason)
        except asyncio.CancelledError:
            # Cancellation/lease expiry cannot establish that an already sent request stopped.
            if attempt_id:
                await asyncio.shield(self.complete(action, "pending", "shutdown_outcome_unknown",
                    uncertain=True, attempt_id=attempt_id, retry_delay=60))
            else:
                await asyncio.shield(self.complete(action, "pending", "shutdown_hold", retry_delay=5))
            raise
        finally:
            log_event("delivery_latency", action_id=action["action_id"], instance=action["instance"],
                      latency_ms=round((time.monotonic() - started) * 1000, 2))

    async def check_health(self) -> bool:
        """Drift readiness is separately stored; startup never auto-repairs DDL/grants."""
        result = await self.db.call("doctor", {})
        health = result.get("health", result) if isinstance(result, dict) else {}
        self._healthy = (health.get("in_recovery") is False and
                         health.get("database_name") == self.settings.database_name and
                         str(health.get("deployment_id")) == str(self.settings.deployment_id) and
                         str(health.get("zabalack_schema_version")) == "3" and
                         health.get("trigger_ok") is True and health.get("functions_ok") is True and
                         health.get("capture_disabled") is False and
                         health.get("config_revision") == self.settings.config_revision)
        self._last_health = time.monotonic()
        return self._healthy

    async def _loop(self, step: Callable[[], Any], *, health_gate: bool = False) -> None:
        idle = getattr(self.settings, "idle_poll_seconds", 0.25)
        delay = idle
        while not self.stop.is_set():
            try:
                if health_gate and not self._healthy:
                    worked = False
                else:
                    worked = await step()
            except Exception as exc:
                # Print only the class. SQL errors may contain private capture material.
                self.metrics.incident("worker", "database_or_internal_failure")
                log_event("worker_error", error_class=type(exc).__name__)
                worked = False
            if worked:
                delay = idle
            else:
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=delay)
                except TimeoutError:
                    pass
                delay = min(2.0, delay * 1.5)

    async def run(self) -> None:
        from .reconciler import Reconciler
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        await self.check_health()
        reconciler = Reconciler(self.settings, self.db, self.registry,
                                worker_id=self.worker_id, client_factory=self.client_factory)
        async def maintenance() -> None:
            while not self.stop.is_set():
                try:
                    await self.check_health()
                    await reconciler.fast_cycle()
                    await reconciler.scheduled_sweeps()
                except Exception as exc:
                    self._healthy = False
                    log_event("reconciler_error", error_class=type(exc).__name__)
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=self.settings.fast_cycle_seconds)
                except TimeoutError:
                    pass
        tasks = [asyncio.create_task(self._loop(self.verify_once)), asyncio.create_task(maintenance())]
        # Local capacity is bounded; the database remains the ALL-site instance authority.
        tasks.extend(asyncio.create_task(self._loop(self.delivery_once, health_gate=True)) for _ in range(4))
        await self.stop.wait()
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=25)
        except TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.db.call("worker_stopped", {"worker_id": self.worker_id})
