"""Offline export-boundary checks; no native database or service evidence."""
import json
import os
from pathlib import Path
import sys

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
