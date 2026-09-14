"""Bounded read-only Zabbix reconciliation and durable drift reporting.

No function in this module calls event.acknowledge. An ambiguous request remains
on hold when a read cannot establish its outcome; expiry never authorises retry.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .db import Database
from .metrics import log_event
from .rpc import EndpointRegistry, RPCError, Timeouts, ZabbixClient, observed_reference, validate_event


class Reconciler:
    def __init__(self, settings: Any, db: Database, registry: EndpointRegistry, *, worker_id: str,
                 client_factory: Callable[..., ZabbixClient] = ZabbixClient) -> None:
        self.settings, self.db, self.registry = settings, db, registry
        self.worker_id, self.client_factory = worker_id, client_factory
        self._last_hosts = 0.0
        self._last_nightly = 0.0
        self._nightly_cursor: str | None = None
        self._nightly_pending = False

    def make_client(self, endpoint: Any) -> ZabbixClient:
        if self.client_factory is not ZabbixClient:
            return self.client_factory(endpoint)
        return ZabbixClient(endpoint, response_limit=self.settings.response_limit_bytes,
            background_timeouts=Timeouts(self.settings.background_connect_timeout_seconds,
                self.settings.background_read_timeout_seconds, self.settings.background_overall_timeout_seconds))

    async def _background(self, instance: str) -> dict[str, Any] | None:
        result = await self.db.call("claim_background", {"instance": instance, "worker_id": self.worker_id})
        if not result or not result.get("allowed", bool(result.get("lease_generation"))):
            return None
        return {"instance": instance, "worker_id": self.worker_id, "lease_generation": result["lease_generation"]}

    async def reconcile_action(self, action: dict[str, Any], *, drift: bool = False) -> bool:
        lease = await self._background(action["instance"])
        if not lease:
            return False
        try:
            client = self.make_client(self.registry.resolve(action["instance"]))
            source = action.get("source") or {"zabbix_instance": action["instance"],
                "zabbix_event_id": action["event_id"], "zabbix_trigger_id": action["trigger_id"]}
            history = bool(action.get("uncertain") and action["cohort"]["comment_enabled"])
            event = validate_event(await client.event_get(action["event_id"], action["cohort"],
                                   history=history, background=True), source, action["cohort"])
            reference_found = history and observed_reference(event, public_reference=action["public_reference"],
                                                             issued_at=action["issued_at"])
            acknowledged = str(event["acknowledged"]) == "1"
            state, reason, evidence = action["state"], "state_observed", None
            uncertain = bool(action.get("uncertain"))
            if acknowledged:
                state = "ok"
                reason = "reference_observed" if reference_found else "already_acknowledged"
                evidence = "reference_observed" if reference_found else "source_ack_observed"
                uncertain = False
            elif reference_found or drift or action["state"] == "ok":
                # Never automatically undo a deliberate source unacknowledgement.
                state, reason, evidence, uncertain = "failed", "source_unacknowledged_drift", "source_state_observed", False
            elif uncertain:
                state, reason = "pending", "unknown_outcome_not_confirmed"
            else:
                # Ordinary never-sent/transient work remains available to the delivery queue;
                # reconciliation does not mint a new source action or extend lifetime.
                reason = "unacknowledged_observed"
            await self.db.call("record_reconciliation", {"action_id": action["action_id"],
                "instance": action["instance"], "worker_id": self.worker_id,
                "background_generation": lease["lease_generation"], "state": state, "reason": reason,
                "completion_evidence": evidence, "uncertain": uncertain, "retry_delay": 60})
            await self.db.call("project", {"action_id": action["action_id"]})
            return True
        except RPCError as exc:
            await self.db.call("record_reconciliation", {"action_id": action["action_id"],
                "instance": action["instance"], "worker_id": self.worker_id,
                "background_generation": lease["lease_generation"],
                "state": action["state"] if action["state"] in {"ok", "failed", "expired", "skipped"} else
                    ("pending" if exc.transient or exc.reason == "event_missing_or_not_visible" else "failed"),
                "reason": "reconciliation_check_failed" if action["state"] in {"ok", "failed", "expired", "skipped"} else exc.reason,
                "completion_evidence": action.get("completion_evidence"),
                "check_failure": exc.reason, "preserve_delivery": action["state"] in {"ok", "failed", "expired", "skipped"},
                "uncertain": bool(action.get("uncertain")), "retry_delay": 300})
            log_event("reconciliation_failure", action_id=action["action_id"], instance=action["instance"], reason=exc.reason)
            return True
        finally:
            await self.db.call("finish_background", lease)

    async def fast_cycle(self) -> dict[str, int]:
        # Indexed due-time scan, including abandoned attempts and expired leases. SQL repairs
        # lease state before returning eligible rows; terminal history is not scanned here.
        rows = await self.db.call("reconcile_due", {"limit": self.settings.fast_budget}) or []
        processed = 0
        for action in rows[:self.settings.fast_budget]:
            if action.get("uncertain"):
                processed += int(await self.reconcile_action(action))
        projections = await self.db.call("projection_due", {"limit": self.settings.fast_budget}) or []
        for row in projections[:self.settings.fast_budget]:
            await self.db.call("project", {"action_id": row["action_id"] if isinstance(row, dict) else row})
        return {"due": len(rows), "read": processed, "projections": len(projections)}

    async def host_inventory(self) -> int:
        cohorts = await self.db.call("host_inventory_due", {"limit": self.settings.fast_budget,
                                        "interval_seconds": self.settings.host_inventory_seconds}) or []
        count = 0
        # One cohort per logical instance per cycle; same limiter as other background reads.
        for cohort in cohorts:
            instance = cohort["zabbix_instance"]
            lease = await self._background(instance)
            if not lease:
                continue
            payload = {"instance": instance, "worker_id": self.worker_id,
                "background_generation": lease["lease_generation"], "customer": cohort["customer"],
                "trigger_id": cohort["zabbix_trigger_id"], "cohort_revision": cohort["revision"]}
            try:
                # Max 50 per query. Cursor the approved cohort host set without changing it.
                ids = cohort["host_ids"]
                offset = int(cohort.get("host_cursor", 0))
                batch = ids[offset:offset + 50]
                client = self.make_client(self.registry.resolve(instance))
                hosts = await client.host_get(batch)
                actual = {str(x.get("hostid")): x for x in hosts}
                missing = sorted(set(batch) - set(actual))
                unknown = sorted(set(actual) - set(batch))
                bad_groups = [host_id for host_id in batch if host_id in actual and not
                    (set(cohort["group_ids"]) & {str(g.get("groupid")) for g in actual[host_id].get("hostgroups", [])})]
                reason = ("cohort_host_missing_or_not_visible" if missing else "cohort_host_group_drift"
                          if bad_groups or unknown else "host_inventory_ok")
                await self.db.call("record_host_inventory", {**payload, "reason": reason,
                    "missing_ids": missing, "unknown_ids": unknown, "group_mismatch_ids": bad_groups,
                    "next_cursor": offset + 50 if offset + 50 < len(ids) else 0})
                count += 1
            except RPCError as exc:
                await self.db.call("record_host_inventory", {**payload, "reason": "host_inventory_failed",
                                                            "error_class": exc.reason})
            finally:
                await self.db.call("finish_background", lease)
        return count

    async def nightly_sweep(self) -> dict[str, Any]:
        result = await self.db.call("nightly_due", {"cursor": self._nightly_cursor,
            "limit": self.settings.fast_budget, "recent_terminal_seconds": self.settings.recent_terminal_seconds}) or {}
        rows = result.get("actions", []) if isinstance(result, dict) else result
        processed = 0
        next_cursor = self._nightly_cursor
        # Advance the cursor only across records actually inspected; contention must not skip work.
        for action in rows:
            if not await self.reconcile_action(action, drift=action["state"] == "ok"):
                break
            processed += 1
            next_cursor = action["action_id"]
        self._nightly_pending = processed < len(rows) or len(rows) >= self.settings.fast_budget
        self._nightly_cursor = next_cursor if self._nightly_pending else None
        return {"read": processed, "cursor": self._nightly_cursor, "pending": self._nightly_pending}

    async def retire_intents(self) -> dict[str, Any]:
        """Retire staged intents from live alert rows (A5 stage two, T41).

        Deliberately not gated on verification or delivery health: the lifecycle
        paths that most need retirement are the ones where neither ever
        succeeds — capture bypass, a verification key unavailable, a stopped or
        backlogged worker, a cohort absent from `zabalack.cohorts`, and staging
        that failed before its history write. SQL bounds the batch and decides
        eligibility; nothing here chooses which rows are swept.
        """
        result = await self.db.call("retire_intents", {"limit": self.settings.fast_budget})
        # `{"skipped": "not_due"}` is the ordinary answer: another worker, or this
        # one on an earlier cycle, already claimed this interval's slot.
        return result if isinstance(result, dict) else {}

    async def scheduled_sweeps(self) -> None:
        now = time.monotonic()
        # Asked every cycle; SQL decides whether it is due. The cadence is
        # deployment state rather than this process's clock, so N workers do not
        # each run their own unindexed scan of public.alerts per interval.
        await self.retire_intents()
        # SQL's 15-minute due timestamp governs eligibility. Checking the indexed due
        # queue each fast cycle drains large cohorts without a 15-minute per-row delay.
        await self.host_inventory()
        if self._nightly_pending or now - self._last_nightly >= self.settings.nightly_cycle_seconds:
            await self.nightly_sweep()
            if not self._nightly_pending:
                self._last_nightly = now
