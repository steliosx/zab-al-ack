"""Offline export-boundary checks; no native database or service evidence."""
import hashlib
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import evidence_utils
import native_failure_summary as summary_tool

PRIVATE = "DO-NOT-EXPORT-private-token-https://secret.invalid/api"
DIRECTORY = "build/evidence-raw/native-delivery_faults-0123456789"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture_tree(tmp_path, *, status="FAIL"):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/source.py").write_text("SYNTHETIC = True\n")
    record = {"status": status, "code_digests": evidence_utils.code_digests(tmp_path),
              "reason": PRIVATE, "phases": [{"phase": "delivery_faults", "status": "FAIL",
              "exit_code": 1, "shutdown_exit_code": 0, "diagnostic_dir": DIRECTORY, "project": PRIVATE}]}
    write_json(tmp_path / "evidence/native-run.json", record)
    (tmp_path / DIRECTORY).mkdir(parents=True)
    return record, tmp_path / DIRECTORY


def test_failure_summary_exports_only_known_facts_and_never_raw_files(tmp_path):
    _, directory = fixture_tree(tmp_path)
    case = directory / "test_synthetic"
    write_json(case / ("native-" + "a" * 32 + ".json"), {
        "exitcode": -9, "private": PRIVATE, "events": [
            {"event": "after:start_attempt", "attempt_id": PRIVATE, "worker_id": PRIVATE,
             "uncertain": True, "reason": "outcome_unknown", "state": "pending", "message": PRIVATE},
            {"event": PRIVATE, "reason": PRIVATE}, {"event": "error", "error_class": PRIVATE}]})
    write_json(case / "source-effects.json", {"events": {
        PRIVATE: {"commits": 1, "messages": [PRIVATE]}, "another": {"commits": 0}},
        "requests": [PRIVATE], "errors": [PRIVATE]})
    write_json(case / "ledger-summary.json", [{"action_id": PRIVATE, "state": "pending",
        "reason": "outcome_unknown", "uncertain": True, "attempts": 1, "completion_evidence": PRIVATE}])
    for filename in ("source-token", "source-key.pem", "worker-dsn", PRIVATE.replace("/", "_") + ".log"):
        (case / filename).write_text(PRIVATE)
    result = summary_tool.build_summary(tmp_path)
    assert result["status"] == "FAIL" and result["native_record_bound"] is True
    phase = result["phases"][0]
    assert phase["exit_code"] == 1 and phase["shutdown_exit_code"] == 0
    data = phase["diagnostics"]
    assert data["source_commit_counts"] == [1, 0]
    assert data["processes"] == [{"exit_code": -9, "checkpoints": [
        {"event": "after:start_attempt", "state": "pending", "reason": "outcome_unknown", "uncertain": True},
        {"event": "error"}]}]
    assert data["ledger"] == [{"state": "pending", "reason": "outcome_unknown", "uncertain": True, "attempts": 1}]
    encoded = json.dumps(result)
    for forbidden in (PRIVATE, "attempt_id", "worker_id", "action_id", "messages", "source-key", str(tmp_path), DIRECTORY):
        assert forbidden not in encoded
    assert data["ignored_files"] == 4


@pytest.mark.parametrize("status", ["PASS", "RUNNING", "unexpected-private-value"])
def test_nonfailure_record_exports_no_details(tmp_path, status):
    fixture_tree(tmp_path, status=status)
    result = summary_tool.build_summary(tmp_path)
    assert result["status"] == "NO_FAILURE_DETAILS"
    assert result["phases"] == [] and result["native_record_bound"] is True
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("flaw", ["missing", "stale", "malformed", "duplicate", "symlink", "nonobject"])
def test_untrusted_record_cannot_attach_diagnostics_to_current_source(tmp_path, flaw):
    record, _ = fixture_tree(tmp_path)
    path = tmp_path / "evidence/native-run.json"
    if flaw == "missing":
        path.unlink()
    elif flaw == "stale":
        (tmp_path / "src/source.py").write_text("SYNTHETIC = False\n")
    elif flaw == "malformed":
        path.write_text(PRIVATE)
    elif flaw == "duplicate":
        path.write_text('{"status":"FAIL","status":"PASS"}')
    elif flaw == "symlink":
        target = tmp_path / "private-record"
        path.rename(target)
        path.symlink_to(target)
    else:
        path.write_text("[]")
    result = summary_tool.build_summary(tmp_path)
    assert result["status"] == "UNAVAILABLE" and result["phases"] == []
    assert result["native_record_bound"] is False
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("unsafe", ["../private", "/tmp/private", "build/evidence-raw/../private",
    "build/evidence-raw/native-delivery_faults-0123456789/extra", "build/evidence-raw/native-worker-0123456789"])
def test_directory_must_be_exact_current_phase_basetemp(tmp_path, unsafe):
    record, _ = fixture_tree(tmp_path)
    record["phases"][0]["diagnostic_dir"] = unsafe
    write_json(tmp_path / "evidence/native-run.json", record)
    result = summary_tool.build_summary(tmp_path)
    assert result["phases"][0]["diagnostics_status"] == "UNAVAILABLE"
    assert unsafe not in json.dumps(result)


def test_old_directories_and_symlink_files_are_not_read(tmp_path):
    _, directory = fixture_tree(tmp_path)
    old = tmp_path / "build/evidence-raw/native-delivery_faults-aaaaaaaaaa"
    write_json(old / "source-effects.json", {"events": {PRIVATE: {"commits": 999}}})
    outside = tmp_path / "outside.json"
    write_json(outside, {"events": {PRIVATE: {"commits": 666}}})
    (directory / "source-effects.json").symlink_to(outside)
    result = summary_tool.build_summary(tmp_path)["phases"][0]["diagnostics"]
    assert result["source_commit_counts"] == [] and result["malformed_files"] == 1


def test_malformed_fields_are_omitted_and_nonregular_files_do_not_block(tmp_path):
    _, directory = fixture_tree(tmp_path)
    write_json(directory / "source-effects.json", {"events": {
        PRIVATE: {"commits": PRIVATE}, "boolean": {"commits": True}, "huge": {"commits": 10**20}}})
    write_json(directory / "ledger-summary.json", [{"state": PRIVATE, "reason": PRIVATE,
        "uncertain": PRIVATE, "attempts": True}, {"state": {"private": PRIVATE}}])
    os.mkfifo(directory / ("native-" + "b" * 32 + ".json"))
    result = summary_tool.build_summary(tmp_path)
    encoded = json.dumps(result)
    assert PRIVATE not in encoded
    assert result["phases"][0]["diagnostics"]["source_commit_counts"] == []
    assert result["phases"][0]["diagnostics"]["malformed_files"] == 2


def test_diagnostic_directory_symlink_is_rejected(tmp_path):
    record, directory = fixture_tree(tmp_path)
    directory.rmdir()
    other = tmp_path / "private"
    other.mkdir()
    directory.symlink_to(other, target_is_directory=True)
    result = summary_tool.build_summary(tmp_path)
    assert result["phases"][0]["diagnostics_status"] == "UNAVAILABLE"


def test_main_invalidates_prior_summary_when_no_run_exists(tmp_path, monkeypatch):
    (tmp_path / "evidence").mkdir()
    path = tmp_path / "evidence/native-failure-summary.json"
    path.write_text(PRIVATE)
    monkeypatch.setattr(summary_tool, "ROOT", tmp_path)
    assert summary_tool.main() == 0
    result = json.loads(path.read_text())
    assert result["status"] == "UNAVAILABLE" and result["phases"] == []
    assert PRIVATE not in path.read_text()


def write_junit(tmp_path, failures):
    path = tmp_path / "build/evidence-raw/native-delivery_faults.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    suite = ET.Element("testsuite", hostname=PRIVATE)
    for kind, message, body in failures:
        case = ET.SubElement(suite, "testcase", classname=PRIVATE, name=PRIVATE)
        if kind:
            element = ET.SubElement(case, kind, message=message)
            element.text = body
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    record_path = tmp_path / "evidence/native-run.json"
    record = json.loads(record_path.read_text())
    record["phases"][0]["raw_junit_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    write_json(record_path, record)
    return path


def test_pytest_fixture_errors_are_aggregated_without_messages_or_identifiers(tmp_path):
    fixture_tree(tmp_path)
    write_junit(tmp_path, [
        ("error", 'failed on setup with "psycopg.errors.InsufficientPrivilege: ' + PRIVATE + '"',
         "private traceback " + PRIVATE)] * 24)
    result = summary_tool.build_summary(tmp_path)
    diagnostic = result["phases"][0]["pytest"]
    assert diagnostic == {"status": "AVAILABLE", "counts": {"passed": 0, "failed": 24, "skipped": 0},
        "failures": [{"kind": "error", "stage": "setup", "category": "database_permission",
                      "exception_class": "InsufficientPrivilege", "sqlstate": "42501", "count": 24}],
        "truncated": False}
    assert PRIVATE not in json.dumps(result)
    for forbidden in ("hostname", "classname", "message", "traceback", str(tmp_path)):
        assert forbidden not in json.dumps(result)


@pytest.mark.parametrize("heading,expected", [
    ("psycopg.OperationalError", {"exception_class": "OperationalError", "category": "database_operational"}),
    ("psycopg.errors.InvalidCatalogName", {"exception_class": "InvalidCatalogName", "category": "database_catalog", "sqlstate": "3D000"}),
    ("AssertionError", {"exception_class": "AssertionError", "category": "assertion"}),
    (PRIVATE, {"category": "unclassified"}),
])
def test_exception_classification_is_allowlisted_and_never_copies_sqlstate_text(tmp_path, heading, expected):
    fixture_tree(tmp_path)
    write_junit(tmp_path, [("failure", heading + ": " + PRIVATE + " SQLSTATE=SECRET", PRIVATE)])
    result = summary_tool.build_summary(tmp_path)
    assert result["phases"][0]["pytest"]["failures"] == [
        {"kind": "failure", "stage": "unspecified", "count": 1, **expected}]
    assert PRIVATE not in json.dumps(result) and "SECRET" not in json.dumps(result)


def test_multiline_message_cannot_override_initial_exception_heading(tmp_path):
    fixture_tree(tmp_path)
    write_junit(tmp_path, [("error", 'failed on setup with "RuntimeError: ' + PRIVATE +
        '\npsycopg.errors.InvalidPassword: embedded text"', PRIVATE)])
    result = summary_tool.build_summary(tmp_path)["phases"][0]["pytest"]
    assert result["failures"] == [{"kind": "error", "stage": "setup", "count": 1,
        "exception_class": "RuntimeError", "category": "runtime"}]


def test_pytest_body_fallback_uses_final_exception_heading_and_preserves_counts(tmp_path):
    fixture_tree(tmp_path)
    write_junit(tmp_path, [("error", "failed on teardown with " + PRIVATE,
        "E   ValueError: " + PRIVATE + "\n\nE   psycopg.errors.QueryCanceled: " + PRIVATE),
        ("skipped", PRIVATE, PRIVATE), (None, "", "")])
    result = summary_tool.build_summary(tmp_path)["phases"][0]["pytest"]
    assert result["counts"] == {"passed": 1, "failed": 1, "skipped": 1}
    assert result["failures"] == [{"kind": "error", "stage": "teardown", "count": 1,
        "exception_class": "QueryCanceled", "category": "database_timeout", "sqlstate": "57014"}]
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("detail,category", [
    ("Connection refused", "database_connection_refused"),
    ("fe_sendauth: no password supplied", "database_password_required"),
    ("connection timeout expired", "database_connection_timeout"),
    ("password authentication failed", "database_authentication"),
])
def test_operational_connection_failures_have_fixed_categories_without_connection_details(tmp_path, detail, category):
    fixture_tree(tmp_path)
    write_junit(tmp_path, [("error", 'failed on setup with "psycopg.OperationalError: ' + PRIVATE + '"',
        "E   psycopg.OperationalError: " + detail + " " + PRIVATE)])
    result = summary_tool.build_summary(tmp_path)
    assert result["phases"][0]["pytest"]["failures"] == [{"kind": "error", "stage": "setup", "count": 1,
        "exception_class": "OperationalError", "category": category}]
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize("flaw", ["missing", "malformed", "doctype", "utf16", "oversized", "symlink", "fifo"])
def test_unsafe_or_unavailable_pytest_xml_exports_no_details(tmp_path, flaw):
    fixture_tree(tmp_path)
    path = write_junit(tmp_path, [("error", "RuntimeError: " + PRIVATE, PRIVATE)])
    if flaw == "missing":
        path.unlink()
    elif flaw == "malformed":
        path.write_text(PRIVATE)
    elif flaw == "doctype":
        path.write_text('<!DOCTYPE testsuite [<!ENTITY private "' + PRIVATE + '">]><testsuite>&private;</testsuite>')
    elif flaw == "utf16":
        path.write_bytes(path.read_text().encode("utf-16"))
    elif flaw == "oversized":
        path.write_bytes(b"x" * (summary_tool.MAX_FILE_BYTES + 1))
    elif flaw == "symlink":
        other = tmp_path / "private-junit.xml"
        path.rename(other)
        path.symlink_to(other)
    else:
        path.unlink()
        os.mkfifo(path)
    if flaw in {"malformed", "doctype", "utf16", "oversized"}:
        record_path = tmp_path / "evidence/native-run.json"
        record = json.loads(record_path.read_text())
        record["phases"][0]["raw_junit_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(record_path, record)
    result = summary_tool.build_summary(tmp_path)
    assert result["phases"][0]["pytest"] == {"status": "UNAVAILABLE"}
    assert PRIVATE not in json.dumps(result)


def test_pytest_summary_counts_every_case_and_ignores_other_phase_files(tmp_path):
    """Counts cover the whole bounded document; another phase's file is not read.

    This case previously asserted that reading stopped at MAX_ITEMS cases. The
    bound moved to the exported detail — the document is already capped at
    MAX_FILE_BYTES — so a phase with more cases than that limit now reports all
    of them rather than reporting the first MAX_ITEMS and calling the rest
    truncated. Identical facts remain a single group, so nothing is dropped here.
    """
    fixture_tree(tmp_path)
    write_junit(tmp_path, [("failure", "AssertionError: " + PRIVATE, "")] * (summary_tool.MAX_ITEMS + 1))
    # A different phase's raw file is unreadable garbage: this phase must not touch it.
    (tmp_path / "build/evidence-raw/native-postgres.xml").write_text(PRIVATE)
    result = summary_tool.build_summary(tmp_path)["phases"][0]["pytest"]
    assert result["status"] == "AVAILABLE"
    assert result["counts"]["failed"] == summary_tool.MAX_ITEMS + 1
    assert result["failures"][0]["count"] == summary_tool.MAX_ITEMS + 1
    assert result["truncated"] is False


@pytest.mark.parametrize("flaw", ["missing", "stale", "malformed"])
def test_pytest_xml_must_match_digest_in_current_run_record(tmp_path, flaw):
    fixture_tree(tmp_path)
    write_junit(tmp_path, [("error", "RuntimeError: " + PRIVATE, PRIVATE)])
    record_path = tmp_path / "evidence/native-run.json"
    record = json.loads(record_path.read_text())
    if flaw == "missing":
        del record["phases"][0]["raw_junit_sha256"]
    else:
        record["phases"][0]["raw_junit_sha256"] = "0" * 64 if flaw == "stale" else PRIVATE
    write_json(record_path, record)
    result = summary_tool.build_summary(tmp_path)
    assert result["phases"][0]["pytest"] == {"status": "UNAVAILABLE"}
    assert PRIVATE not in json.dumps(result)


def test_main_prints_only_sanitized_summary_for_hosted_job_diagnosis(tmp_path, monkeypatch, capsys):
    fixture_tree(tmp_path)
    write_junit(tmp_path, [("error", 'failed on setup with "psycopg.OperationalError: ' + PRIVATE + '"', PRIVATE)])
    monkeypatch.setattr(summary_tool, "ROOT", tmp_path)
    assert summary_tool.main() == 0
    output = capsys.readouterr().out
    assert PRIVATE not in output
    assert json.loads(output) == json.loads((tmp_path / "evidence/native-failure-summary.json").read_text())
    assert json.loads(output)["phases"][0]["pytest"]["failures"][0]["exception_class"] == "OperationalError"
