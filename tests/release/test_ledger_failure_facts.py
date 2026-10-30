"""Offline export-boundary checks for minimized ledger counterexamples; no database."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import evidence_utils
import ledger_failure_facts as facts_tool
import native_failure_summary as summary_tool

PRIVATE = "DO-NOT-EXPORT-private-token-https://secret.invalid/api"
EXCEPTIONS = {name: value[0] for name, value in summary_tool.EXCEPTIONS.items()}
STEPS = [
    "state.seed_regression()",
    "state.immutable_authority_and_send_budget()",
    "state.begin_send(index=1)",
    "state.delivery_claim(index=0, worker='generated-a')",
    "state.maintenance(paused=True)",
    "state.operator_settlement(index=0)",
    "state.teardown()",
]


def source_tree(tmp_path):
    """The real test module and migrations: the vocabulary and the tree binding come from them."""
    for relative in (facts_tool.TEST, *(p.relative_to(ROOT).as_posix() for p in (ROOT / "sql/migrations").glob("*.sql"))):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return facts_tool.current_digests(tmp_path)


def failure_text(header, steps=STEPS, *, tail=()):
    """The exact layout test_stateful_ledger.py writes: header, reports, formatted exception."""
    frame = f'  File "/{PRIVATE}/{facts_tool.TEST}", line 365, in settle'
    return "\n".join([json.dumps(header), "", "Traceback (most recent call last):", frame,
                      "    assert self.inspect(action)['uncertain'] is True",
                      f"AssertionError: {PRIVATE}", "Failing test case:", "state = LedgerMachine()",
                      *steps, "",
                      "You can reproduce this test case by temporarily adding "
                      "@reproduce_failure('6.168.0', b'AXicY2BwZHRkZAAhZgZHBgZHJjCbE8gG8RgYHaEsRhDBDmQCAHO0BK4=') "
                      "as a decorator on your test function", *tail]) + "\n"


def write_failure(tmp_path, name, text):
    directory = tmp_path / "build/private/hypothesis"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text)
    return directory


def header(digests, **changes):
    value = {"seed": None, "profile": "nightly", "focus": "settled_retry", "engine": "native_postgres18",
             "hypothesis": "6.168.0", **digests}
    value.update(changes)
    return value


def test_counterexample_exports_only_validated_steps_frame_class_and_replay(tmp_path):
    digests = source_tree(tmp_path)
    directory = write_failure(tmp_path, "ledger-settled_retry-nightly-failure.txt", failure_text(header(digests)))
    # The example database shares the directory and is never read.
    (directory / "examples").mkdir()
    (directory / "examples/entry").write_text(PRIVATE)
    result = facts_tool.collect(tmp_path, EXCEPTIONS)
    assert result["status"] == "AVAILABLE" and result["malformed_files"] == 0
    item = result["files"][0]
    assert item["focus"] == "settled_retry" and item["profile"] == "nightly"
    assert item["steps"] == STEPS
    assert item["failing_frame"] == {"function": "settle", "line": 365}
    assert item["exception"] == "assertion"
    assert item["reproduce"] == {"hypothesis": "6.168.0",
                                 "blob": "AXicY2BwZHRkZAAhZgZHBgZHJjCbE8gG8RgYHaEsRhDBDmQCAHO0BK4="}
    assert item["header"]["engine"] == "native_postgres18" and item["bound_to_tree"] is True
    encoded = json.dumps(result)
    assert PRIVATE not in encoded and str(tmp_path) not in encoded and "examples" not in encoded


@pytest.mark.parametrize("line", [
    f"state.delivery_claim(index=0, worker='{PRIVATE}')",
    "state.delivery_claim(index=0, worker='generated-z')",
    "state.begin_send(index=10000000)",
    "state.begin_send(index='1')",
    "state.begin_send(1)",
    "state.begin_send(index=1, extra=2)",
    "state.not_a_rule(index=1)",
    "state.maintenance(paused=1)",
    "state.begin_send(index=__import__('os').getpid())",
    "other.begin_send(index=1)",
])
def test_step_outside_the_declared_rule_vocabulary_is_dropped_and_counted(tmp_path, line):
    digests = source_tree(tmp_path)
    write_failure(tmp_path, "ledger-settled_retry-nightly-failure.txt",
                  failure_text(header(digests), [STEPS[0], line, STEPS[-1]]))
    item = facts_tool.collect(tmp_path, EXCEPTIONS)["files"][0]
    assert item["steps"] == [STEPS[0], STEPS[-1]]
    assert item["dropped_lines"] >= 1
    assert PRIVATE not in json.dumps(item)


def test_header_fields_of_unknown_shape_are_dropped_and_unbound(tmp_path):
    digests = source_tree(tmp_path)
    bad = header(digests, seed=PRIVATE, profile=PRIVATE, engine=PRIVATE, hypothesis=PRIVATE,
                 test_sha256=PRIVATE, private=PRIVATE)
    bad["sql_sha256"] = {**digests["sql_sha256"], PRIVATE: "0" * 64, "001_core.sql": PRIVATE}
    write_failure(tmp_path, "ledger-settled_retry-nightly-failure.txt", failure_text(bad))
    item = facts_tool.collect(tmp_path, EXCEPTIONS)["files"][0]
    assert set(item["header"]) == {"focus", "sql_sha256"}
    assert "001_core.sql" not in item["header"]["sql_sha256"]
    assert item["dropped_header_fields"] == 8
    assert item["bound_to_tree"] is False
    assert PRIVATE not in json.dumps(item)


def test_unlisted_exception_and_message_text_are_never_exported(tmp_path):
    digests = source_tree(tmp_path)
    text = failure_text(header(digests)).replace("AssertionError: ", "hypothesis.errors.Flaky: ")
    write_failure(tmp_path, "ledger-settled_retry-nightly-failure.txt", text)
    item = facts_tool.collect(tmp_path, EXCEPTIONS)["files"][0]
    assert "exception" not in item
    assert "Flaky" not in json.dumps(item) and PRIVATE not in json.dumps(item)


def test_steps_are_capped_and_other_names_symlinks_and_oversize_files_are_not_read(tmp_path):
    digests = source_tree(tmp_path)
    many = ["state.begin_send(index=1)"] * (facts_tool.MAX_STEPS + 5)
    directory = write_failure(tmp_path, "ledger-settled_retry-nightly-failure.txt", failure_text(header(digests), many))
    (directory / f"ledger-{PRIVATE.replace('/', '_')}-nightly-failure.txt").write_text(PRIVATE)
    (directory / "ledger-late_response-pr-failure.txt").symlink_to(tmp_path / facts_tool.TEST)
    (directory / "ledger-expired_generation-pr-failure.txt").write_bytes(b"x" * (facts_tool.MAX_FILE_BYTES + 1))
    result = facts_tool.collect(tmp_path, EXCEPTIONS)
    assert [item["focus"] for item in result["files"]] == ["settled_retry"]
    assert result["malformed_files"] == 2
    assert len(result["files"][0]["steps"]) == facts_tool.MAX_STEPS and result["files"][0]["steps_truncated"] is True


def test_symlinked_corpus_is_not_followed(tmp_path):
    source_tree(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "ledger-settled_retry-nightly-failure.txt").write_text(PRIVATE)
    (tmp_path / "build/private").mkdir(parents=True)
    (tmp_path / "build/private/hypothesis").symlink_to(elsewhere)
    assert facts_tool.collect(tmp_path, EXCEPTIONS) == {"files": [], "malformed_files": 0, "truncated": False,
                                                       "status": "NO_CORPUS"}


def test_vocabulary_is_read_from_the_test_module():
    words = facts_tool.vocabulary(ROOT)
    assert words["focuses"] == ("late_response", "settled_retry", "expired_generation", "completed_generation")
    assert words["rules"]["delivery_claim"] == {"index": {"int"}, "worker": {"generated-a", "generated-b", "generated-c"}}
    assert words["rules"]["maintenance"] == {"paused": {True, False}}
    assert {"seed_regression", "immutable_authority_and_send_budget", "teardown"} <= set(words["rules"])


def test_native_summary_adds_counterexamples_and_focus_to_the_postgres_phase(tmp_path, monkeypatch, capsys):
    digests = source_tree(tmp_path)
    write_failure(tmp_path, "ledger-settled_retry-nightly-failure.txt", failure_text(header(digests)))
    raw = tmp_path / "build/evidence-raw/native-postgres.xml"
    raw.parent.mkdir(parents=True, exist_ok=True)
    suite = ET.Element("testsuite", hostname=PRIVATE)
    for focus in ("settled_retry", PRIVATE):
        case = ET.SubElement(suite, "testcase", classname="tests.postgres.test_stateful_ledger",
                             name=f"test_generated_delivery_ledger[{focus}]")
        ET.SubElement(case, "failure", message="AssertionError: " + PRIVATE).text = PRIVATE
    ET.ElementTree(suite).write(raw, encoding="utf-8", xml_declaration=True)
    record = {"status": "FAIL", "code_digests": evidence_utils.code_digests(tmp_path), "phases": [
        {"phase": "postgres", "status": "FAIL", "exit_code": 1, "shutdown_exit_code": 0,
         "raw_junit_sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}]}
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/native-run.json").write_text(json.dumps(record))
    monkeypatch.setattr(summary_tool, "ROOT", tmp_path)
    assert summary_tool.main() == 0
    output = capsys.readouterr().out
    assert PRIVATE not in output and str(tmp_path) not in output
    phase = json.loads(output)["phases"][0]
    assert {"stateful_focus": "settled_retry", "count": 1}.items() <= phase["pytest"]["failures"][0].items()
    assert not any("stateful_focus" in item for item in phase["pytest"]["failures"][1:])
    assert phase["ledger_counterexamples"]["files"][0]["steps"] == STEPS


def test_runners_export_counterexamples_only_on_ledger_failure():
    reference = (ROOT / "tools/run_reference_suite.py").read_text()
    assert ("        if name == 'postgres-wasm' and code != 0:\n"
            "            # Job log only, not the reference record") in reference
    assert reference.count("ledger_counterexamples(") == 1
    summary = (ROOT / "tools/native_failure_summary.py").read_text()
    assert "if ledger_failure_facts.TEST in NATIVE_PHASES[name]:" in summary
