#!/usr/bin/env python3
"""Export bounded, allowlisted native failure facts; never upload raw lab files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

from evidence_utils import binding_error, code_digests

ROOT = Path(__file__).resolve().parents[1]
PHASES = {"postgres", "delivery_faults", "worker", "alerta", "performance"}
STATES = {"captured_unverified", "authorised", "pending", "sending", "unknown", "ok",
          "failed", "expired", "skipped", "late_response", "verifier_hold"}
REASONS = {"acknowledged", "already_acknowledged", "outcome_unknown", "lease_fenced",
           "unknown_outcome_reconciliation", "reference_observed", "state_observed",
           "execution_expired", "execution_lifetime_expired", "shadow_hold", "shutdown_hold",
           "shutdown_outcome_unknown", "event_missing_or_not_visible", "source_unacknowledged_drift",
           "unknown_outcome_not_confirmed", "unacknowledged_observed", "reconciliation_check_failed",
           "operator_retry", "operator_no_effect_confirmed", "delivery_lease_expired"}
OPERATIONS = {"doctor", "claim_verification", "verify_complete", "project", "hold_capture",
              "reject_capture", "claim_delivery", "before_write", "start_attempt", "complete",
              "inspect", "claim_background", "record_reconciliation", "finish_background",
              "worker_stopped"}
CHECKPOINTS = {"ready", "done", "error"} | {
    prefix + operation for prefix in ("before:", "after:") for operation in OPERATIONS}
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 256
MAX_ITEMS = 128


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _safe_bytes(path: Path, root: Path) -> bytes:
    path.relative_to(root)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("unsafe file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError("invalid file")
        data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("invalid file")
        return data


def _json(path: Path, root: Path):
    return json.loads(_safe_bytes(path, root), object_pairs_hook=_pairs)


def _integer(value, minimum=0, maximum=1_000_000):
    return type(value) is int and minimum <= value <= maximum


def _facts(value):
    """Do not copy free text, identifiers, keys, messages, URLs or paths."""
    if not isinstance(value, dict):
        return {}
    result = {}
    if value.get("state") in STATES:
        result["state"] = value["state"]
    if value.get("reason") in REASONS:
        result["reason"] = value["reason"]
    if type(value.get("uncertain")) is bool:
        result["uncertain"] = value["uncertain"]
    if _integer(value.get("attempts")):
        result["attempts"] = value["attempts"]
    return result


def _directory(root: Path, value, phase):
    if not isinstance(value, str):
        raise ValueError("missing diagnostic directory")
    relative = PurePosixPath(value)
    if (relative.as_posix() != value or len(relative.parts) != 3
            or relative.parts[:2] != ("build", "evidence-raw")
            or not re.fullmatch(r"native-" + re.escape(phase) + r"-[0-9a-f]{10,32}", relative.name)):
        raise ValueError("unsafe diagnostic directory")
    directory = root.joinpath(*relative.parts)
    if (not directory.is_dir() or directory.is_symlink()
            or any(parent.is_symlink() for parent in directory.parents)):
        raise ValueError("unsafe diagnostic directory")
    return directory


def _diagnostics(root: Path, directory: Path) -> dict:
    result = {"processes": [], "source_commit_counts": [], "ledger": [],
              "ignored_files": 0, "malformed_files": 0, "truncated": False}
    visited = 0
    for parent, directories, files in os.walk(directory, followlinks=False):
        current = Path(parent)
        directories[:] = sorted(name for name in directories
            if not (current / name).is_symlink() and len((current / name).relative_to(directory).parts) <= 4)
        for name in sorted(files):
            visited += 1
            if visited > MAX_FILES:
                result["truncated"] = True
                return result
            if name not in {"source-effects.json", "ledger-summary.json"} and not re.fullmatch(r"native-[0-9a-f]{32}\.json", name):
                result["ignored_files"] += 1
                continue
            try:
                value = _json(current / name, root)
                if name == "source-effects.json":
                    if not isinstance(value, dict) or not isinstance(value.get("events"), dict):
                        raise ValueError("invalid effect ledger")
                    for event in list(value["events"].values())[:MAX_ITEMS]:
                        if isinstance(event, dict) and _integer(event.get("commits")):
                            result["source_commit_counts"].append(event["commits"])
                elif name == "ledger-summary.json":
                    if not isinstance(value, list):
                        raise ValueError("invalid ledger summary")
                    result["ledger"].extend(facts for item in value[:MAX_ITEMS] if (facts := _facts(item)))
                else:
                    if not isinstance(value, dict) or not isinstance(value.get("events"), list):
                        raise ValueError("invalid process summary")
                    process = {"checkpoints": []}
                    if _integer(value.get("exitcode"), -255, 255):
                        process["exit_code"] = value["exitcode"]
                    for item in value["events"][:MAX_ITEMS]:
                        if isinstance(item, dict) and item.get("event") in CHECKPOINTS:
                            process["checkpoints"].append({"event": item["event"], **_facts(item)})
                    result["processes"].append(process)
            except (OSError, ValueError, TypeError, UnicodeError):
                result["malformed_files"] += 1
    return result


def build_summary(root: Path) -> dict:
    digests = code_digests(root)
    summary = {"schema_version": 1, "scope": "native lab failure diagnostics; not acceptance evidence",
               "code_digests": digests, "status": "UNAVAILABLE", "native_record_bound": False, "phases": []}
    try:
        raw = _safe_bytes(root / "evidence/native-run.json", root)
        record = json.loads(raw, object_pairs_hook=_pairs)
        if binding_error(record, root, "Native"):
            return summary
        summary["native_record_bound"] = True
        summary["native_record_sha256"] = hashlib.sha256(raw).hexdigest()
        if record.get("status") not in {"FAIL", "NOT_RUN"}:
            summary["status"] = "NO_FAILURE_DETAILS"
            return summary
        summary["status"] = record["status"]
        if not isinstance(record.get("phases"), list):
            return summary
        seen = set()
        for phase in record["phases"][:len(PHASES)]:
            if not isinstance(phase, dict) or phase.get("phase") not in PHASES:
                continue
            name = phase["phase"]
            if name in seen or phase.get("status") not in {"FAIL", "NOT_RUN"}:
                continue
            seen.add(name)
            item = {"phase": name, "status": phase["status"]}
            for key in ("exit_code", "shutdown_exit_code"):
                if _integer(phase.get(key), -255, 255):
                    item[key] = phase[key]
            try:
                item["diagnostics"] = _diagnostics(root, _directory(root, phase.get("diagnostic_dir"), name))
            except (OSError, ValueError, TypeError, UnicodeError):
                item["diagnostics_status"] = "UNAVAILABLE"
            summary["phases"].append(item)
    except (OSError, ValueError, TypeError, UnicodeError):
        pass
    if code_digests(root) != digests:
        # An edited harness cannot attach observations to a prior source record.
        return {"schema_version": 1, "scope": summary["scope"], "code_digests": code_digests(root),
                "status": "UNAVAILABLE", "native_record_bound": False, "phases": []}
    return summary


def main() -> int:
    output = ROOT / "evidence/native-failure-summary.json"
    if output.parent.is_symlink():
        print("Native failure summary unavailable: unsafe output directory")
        return 1
    try:
        output.parent.mkdir(exist_ok=True)
        output.unlink(missing_ok=True)
        summary = build_summary(ROOT)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    except (OSError, ValueError, TypeError, UnicodeError):
        print("Native failure summary unavailable: export could not be written")
        return 1
    print("Native failure summary: " + summary["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
