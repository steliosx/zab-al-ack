"""Operational CLI; inspection is separate from audited, explicitly selected control."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .config import ConfigError, load_settings, read_protected_file
from .db import Postgres
from .rpc import EndpointRegistry

if TYPE_CHECKING:
    from .reconciler import Reconciler

# Pages the standalone command drains per run: each is one bounded sweep of
# `fast_budget` rows under `retirement_timeout_seconds`, so a run is bounded
# too, and what remains is the next run's, reported as such.
RETIREMENT_PAGES_PER_RUN = 10


def uuid_argument(value: str) -> str:
    try:
        result = str(uuid.UUID(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("a complete action UUID is required") from exc
    return result


def timestamp_argument(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise argparse.ArgumentTypeError("a timestamp with timezone is required") from exc
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="zabalack", description=__doc__)
    result.add_argument("--config", required=True, help="Reviewed local JSON configuration path")
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("config-check", "status", "doctor", "verifier-health", "worker", "metrics"):
        sub.add_parser(name)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--state", choices=("held", "failed", "expired", "pending", "unknown", "ok"), required=True)
    inspect.add_argument("--limit", type=int, default=50)
    recon = sub.add_parser("reconcile")
    recon.add_argument("--read-only", action="store_true", help="Never sends source writes (this is always enforced)")
    recon.add_argument("--once", action="store_true", help="One bounded cycle (default)")
    recon.add_argument("--nightly", action="store_true", help="Also run a bounded mapped-event drift sweep")
    for name in ("pause", "resume", "retry", "repair", "bypass", "enable-delivery", "disable-delivery", "reset-circuit", "settle-unknown"):
        command = sub.add_parser(name)
        command.add_argument("--owner", required=True, help="Approved operator reference, not a password")
        command.add_argument("--change-ref", required=True, help="Approved change/audit record reference")
        if name in {"pause", "resume", "enable-delivery", "disable-delivery", "reset-circuit"}:
            command.add_argument("--instance", required=True)
        if name in {"retry", "repair", "settle-unknown"}:
            command.add_argument("--action", required=True, type=uuid_argument)
        if name == "settle-unknown":
            command.add_argument("--disposition", choices=("no-effect-confirmed",), required=True)
            command.add_argument("--evidence-ref", required=True)
            command.add_argument("--confirmation", choices=("REMOTE_ATTEMPT_FENCED_AND_SETTLED",), required=True)
        if name == "enable-delivery":
            command.add_argument("--gates-reviewed", action="store_true", required=True)
            command.add_argument("--confirmation", choices=("ENABLE_EXTERNAL_WRITES",), required=True)
        if name == "pause":
            command.add_argument("--reason", required=True)
            command.add_argument("--expected-end", type=timestamp_argument)
        if name == "bypass":
            flags = command.add_mutually_exclusive_group(required=True)
            flags.add_argument("--enabled", dest="enabled", action="store_true")
            flags.add_argument("--disabled", dest="enabled", action="store_false")
    return result


class RetirementError(RuntimeError):
    """The standalone reconciliation did not finish retiring staged intents.

    The worker's loop reports a failed sweep and retries it next cycle, and
    lets the slot carry an unfinished batch to its next cycle; the standalone
    command has no next cycle of its own, and a run that leaves intents on
    live rows must not read as a completed cleanup.
    """


async def drain_retirement(recon: Reconciler, *, pages: int) -> dict[str, Any]:
    """Retire due intents page by page and say whether the drain finished.

    `complete` is true only when a sweep ran, left nothing due behind it, and
    counted no row still carrying the key (`remaining`) — not when the slot was
    not due or another sweep held it (`skipped`), not when the sweep failed
    (`error`), not when the run stopped with work remaining (`continues`): its
    pages spent, or a page that made no progress over rows other sessions
    hold, which asking again within one run would only spin on — and not
    while a staging inside its window and grace is still on a row, which a
    pass leaves to a later one by design. The count is the database's, made
    by the same pass; absent, the drain is not complete.
    """
    totals: dict[str, Any] = {"pages": 0, "examined": 0, "retired": 0, "deferred": 0}
    for _ in range(max(1, pages)):
        page = await recon.retire_intents(report_remaining=True)
        if "error" in page:
            return {**totals, "error": page["error"], "complete": False}
        if page.get("skipped"):
            return {**totals, "skipped": page["skipped"], "complete": False}
        totals["pages"] += 1
        for key in ("examined", "retired", "deferred"):
            totals[key] += int(page.get(key) or 0)
        if not page.get("continues"):
            remaining = page.get("remaining")
            report: dict[str, Any] = {**totals, "remaining": remaining, "complete": remaining == 0}
            if remaining and page.get("retry_at") is not None:
                # The database's word: the time by which every row it counted
                # can be due, with the slot brought forward to no later than it.
                report["retry_at"] = page["retry_at"]
            return report
        if not int(page.get("examined") or 0):
            break
    return {**totals, "continues": True, "complete": False}


async def execute(args: argparse.Namespace) -> Any:
    settings = load_settings(args.config)
    if args.command == "config-check":
        return {"valid": True, "configuration": settings.redacted(), "deployment_gates": "NOT_VERIFIED"}
    mutating = args.command in {"pause", "resume", "retry", "repair", "bypass", "enable-delivery", "disable-delivery", "reset-circuit", "settle-unknown"}
    dsn_file = settings.operator_dsn_file if mutating else settings.worker_dsn_file
    if not dsn_file:
        raise ConfigError("operator_dsn_required" if mutating else "worker_dsn_required")
    db = Postgres(read_protected_file(dsn_file).decode().strip(),
                  operation_budgets={"retire_intents": settings.retirement_timeout_seconds})
    if args.command == "metrics":
        from .metrics import prometheus_text
        return prometheus_text(await db.call("status"), settings)
    if args.command == "worker":
        from .crypto import load_key_files
        from .worker import Worker
        await Worker(settings, db, load_key_files(settings.signing_keys)).run()
        return {"stopped": True}
    if args.command == "reconcile":
        from .reconciler import Reconciler
        recon = Reconciler(settings, db, EndpointRegistry(settings.endpoint_registry, lab=settings.lab_mode,
                approved_versions=settings.approved_versions),
                           worker_id=str(uuid.uuid4()))
        # Retirement of staged intents (A5 stage two) rides every reconciliation
        # cycle, not only the worker's: with the worker stopped and the
        # standalone unit kept, as the rollback keeps it, abandoned intents on
        # live rows must still be retired. The worker asks once per cycle and
        # the slot carries the rest to its next cycle; this command drains page
        # by page, bounded. It is a local database operation; this command
        # still sends no source write.
        #
        # It runs before the cycle that reaches the source, not after, because
        # the rollback makes a completed drain its condition for removing the
        # application adapter: that cleanup must not become contingent on the
        # remote half of this command finishing. Every source-side failure —
        # an endpoint that will not resolve, an unreadable token file, TLS, a
        # malformed response — is an RPCError that `reconcile_action` already
        # records and swallows, so ordering changes nothing for those; it is
        # every other way `fast_cycle()` can end that this places beyond reach.
        retirement = await drain_retirement(recon, pages=RETIREMENT_PAGES_PER_RUN)
        result = await recon.fast_cycle()
        result["retirement"] = retirement
        if args.nightly:
            result["nightly"] = await recon.nightly_sweep()
        # A sweep that failed — its scan outrunning `retirement_timeout_seconds`
        # on an unindexed table, a transient error — or a drain that stopped
        # with work remaining is the worker's next cycle to retry or continue;
        # this command has none, so it fails instead of reporting the run as
        # complete, and the rollback's automation cannot take a cleanup that
        # did not happen for one that did.
        if "error" in retirement or retirement.get("continues"):
            raise RetirementError("retirement_failed" if "error" in retirement else "retirement_incomplete")
        return {"source_writes": False, "result": result}
    if args.command in {"status", "doctor", "verifier-health", "inspect"}:
        payload = {"state": args.state, "limit": min(200, max(1, args.limit))} if args.command == "inspect" else {}
        return await db.call(args.command.replace("-", "_"), payload)
    payload = {"owner": args.owner, "actor_ref": args.owner, "change_ref": args.change_ref}
    if hasattr(args, "instance"):
        if args.instance not in settings.endpoint_registry:
            raise ConfigError("operator_instance_not_selected_in_registry")
        payload["instance"] = args.instance
    if hasattr(args, "action"):
        payload["action_id"] = args.action
    if args.command == "pause":
        payload.update(reason=args.reason, expected_end=args.expected_end)
    if args.command == "bypass":
        payload["enabled"] = args.enabled
        payload["config_revision"] = settings.config_revision
    operation = {"repair": "repair_projection", "bypass": "capture_bypass",
                 "reset-circuit": "reset_circuit", "disable-delivery": "pause", "settle-unknown": "settle_unknown"}.get(args.command, args.command)
    if args.command == "settle-unknown":
        payload.update(disposition=args.disposition, evidence_ref=args.evidence_ref, confirmation=args.confirmation)
    if args.command == "disable-delivery":
        payload["reason"] = "operator_write_disable"
    if args.command == "enable-delivery":
        await db.operator("set_external_writes", {**payload, "enabled": True,
            "confirmation": args.confirmation, "gates_reviewed": args.gates_reviewed})
        operation = "resume"
    return await db.operator(operation, payload)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    try:
        result = asyncio.run(execute(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Do not render exceptions containing SQL payloads, DSNs, tokens or proof text.
        print(json.dumps({"ok": False, "error_class": type(exc).__name__}), file=sys.stderr)
        return 1
    if isinstance(result, str) and args.command == "metrics":
        print(result, end="")
    else:
        print(json.dumps(result, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
