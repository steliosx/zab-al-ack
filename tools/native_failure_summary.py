#!/usr/bin/env python3
"""Export bounded, allowlisted native failure facts; never upload raw lab files."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import xml.etree.ElementTree as ET

from evidence_utils import binding_error, code_digests
from native_ci import PHASES as NATIVE_PHASES

ROOT = Path(__file__).resolve().parents[1]
PHASES = set(NATIVE_PHASES)
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
MAX_TEST_ID_CHARS = 256
# Values originate here, never from exception messages or arbitrary SQLSTATEs.
EXCEPTIONS = {
    "AssertionError": ("assertion", None), "Failed": ("pytest_failure", None),
    "ImportError": ("import", None), "ModuleNotFoundError": ("import", None),
    "RuntimeError": ("runtime", None), "ValueError": ("value", None),
    "TypeError": ("type", None), "KeyError": ("lookup", None),
    "AttributeError": ("lookup", None), "FileNotFoundError": ("filesystem", None),
    "PermissionError": ("filesystem", None), "TimeoutError": ("timeout", None),
    "OperationalError": ("database_operational", None),
    "InterfaceError": ("database_interface", None),
    "ProgrammingError": ("database_programming", None),
    "InsufficientPrivilege": ("database_permission", "42501"),
    "InvalidPassword": ("database_authentication", "28P01"),
    "InvalidAuthorizationSpecification": ("database_authentication", "28000"),
    "InvalidCatalogName": ("database_catalog", "3D000"),
    "InvalidSchemaName": ("database_schema", "3F000"),
    "UndefinedTable": ("database_schema", "42P01"),
    "UndefinedColumn": ("database_schema", "42703"),
    "UndefinedFunction": ("database_schema", "42883"),
    "DuplicateObject": ("database_schema", "42710"),
    "DuplicateTable": ("database_schema", "42P07"),
    "SyntaxError": ("syntax", None),
    "UniqueViolation": ("database_constraint", "23505"),
    "CheckViolation": ("database_constraint", "23514"),
    "NotNullViolation": ("database_constraint", "23502"),
    "ForeignKeyViolation": ("database_constraint", "23503"),
    "SerializationFailure": ("database_concurrency", "40001"),
    "DeadlockDetected": ("database_concurrency", "40P01"),
    "QueryCanceled": ("database_timeout", "57014"),
    "ConnectionFailure": ("database_connection", "08006"),
    "ConnectionDoesNotExist": ("database_connection", "08003"),
    "CannotConnectNow": ("database_connection", "57P03"),
    "TooManyConnections": ("database_connection", "53300"),
}
EXCEPTION_LINE = re.compile(
    r"(?:^|\n)(?:E\s+)?(?:failed on (?:setup|teardown) with )?[\"']?"
    r"(?:(?:psycopg(?:\.errors)?|builtins)\.)?(?P<name>"
    + "|".join(sorted(EXCEPTIONS, key=len, reverse=True)) + r")(?=[:\s\"']|$)")
OPERATIONAL_CATEGORIES = (
    ("fe_sendauth: no password supplied", "database_password_required"),
    ("password authentication failed", "database_authentication"),
    ("connection refused", "database_connection_refused"),
    ("connection timeout expired", "database_connection_timeout"),
    ("timeout expired", "database_connection_timeout"),
    ("could not translate host name", "database_name_resolution"),
    ("server closed the connection unexpectedly", "database_connection_closed"),
)


def _failure_facts(element) -> dict:
    message = element.get("message", "")
    stage = "setup" if message.startswith("failed on setup with ") else (
        "teardown" if message.startswith("failed on teardown with ") else "unspecified")
    result = {"kind": element.tag, "stage": stage, "category": "unclassified"}
    # The exception heading is authoritative; do not scrape assertion bodies,
    # arbitrary test identifiers, or free-form SQLSTATE-looking strings.
    heading = EXCEPTION_LINE.match(message)
    if heading is None:
        matches = list(EXCEPTION_LINE.finditer(element.text or ""))
        heading = matches[-1] if matches else None
    if heading:
        name = heading["name"]
        category, sqlstate = EXCEPTIONS[name]
        if name == "OperationalError":
            private_text = (message + "\n" + (element.text or "")).lower()
            for marker, known_category in OPERATIONAL_CATEGORIES:
                if marker in private_text:
                    category = known_category
                    break
        result.update(exception_class=name, category=category)
        if sqlstate:
            result["sqlstate"] = sqlstate
    return result


def _source_tests(root: Path, phase: str) -> dict[tuple[str, str], str]:
    """Catalog fixed phase targets without importing tests or following XML paths."""
    result = {}
    for relative in NATIVE_PHASES[phase]:
        try:
            document = ast.parse(_safe_bytes(root / relative, root))
        except (OSError, ValueError, UnicodeError, SyntaxError, RecursionError):
            continue
        module = ".".join(PurePosixPath(relative).with_suffix("").parts)
        for node in document.body:
            if (not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    or not re.fullmatch(r"test_[A-Za-z0-9_]+", node.name)):
                continue
            identity = relative + "::" + node.name
            if len(identity) > MAX_TEST_ID_CHARS:
                continue
            result[module, node.name] = identity
            if len(result) >= MAX_ITEMS:
                return result
    return result


def _pytest_summary(root: Path, phase: str, expected_digest) -> dict:
    """Classify only this phase's JUnit; return no raw XML strings or paths."""
    raw = _safe_bytes(root / "build/evidence-raw" / ("native-" + phase + ".xml"), root)
    if (not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
            or hashlib.sha256(raw).hexdigest() != expected_digest):
        raise ValueError("unbound JUnit")
    # No DTDs/entities, including declarations obscured by UTF-16 encoding.
    xml = raw.decode("utf-8")
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml or "\x00" in xml:
        raise ValueError("unsafe XML")
    document = ET.fromstring(xml)
    if document.tag not in {"testsuite", "testsuites"}:
        raise ValueError("invalid JUnit root")
    result = {"status": "AVAILABLE", "counts": {"passed": 0, "failed": 0, "skipped": 0},
              "failures": [], "truncated": False}
    source_tests = _source_tests(root, phase)
    groups = {}
    for index, case in enumerate(document.iter("testcase")):
        if index >= MAX_ITEMS:
            result["truncated"] = True
            break
        failures = [child for child in case if child.tag in {"failure", "error"}]
        state = "failed" if failures else "skipped" if case.find("skipped") is not None else "passed"
        result["counts"][state] += 1
        name, bracket, parameters = case.get("name", "").partition("[")
        identity = source_tests.get((case.get("classname", ""), name)) if (
            not bracket or parameters.endswith("]")) else None
        for failure in failures[:2]:
            facts = _failure_facts(failure)
            if identity is not None:
                facts["source_test"] = identity
            key = tuple(sorted(facts.items()))
            if key not in groups:
                groups[key] = {**facts, "count": 0}
            groups[key]["count"] += 1
    result["failures"] = list(groups.values())
    return result


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
                item["pytest"] = _pytest_summary(root, name, phase.get("raw_junit_sha256"))
            except (OSError, ValueError, TypeError, UnicodeError, ET.ParseError):
                item["pytest"] = {"status": "UNAVAILABLE"}
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
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
