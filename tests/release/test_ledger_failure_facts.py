"""Offline export-boundary checks for minimized ledger counterexamples; no database."""
import base64
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
RUN = "0123456789"
OLD_RUN = "abcdefabcd"
BLOB = "AXicY2BwZHRkZAAhZgZHBgZHJjCbE8gG8RgYHaEsRhDBDmQCAHO0BK4="
FILE = "ledger-settled_retry-nightly-failure.txt"
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


def failure_text(header, steps=STEPS, *, blob=BLOB):
    """The exact layout test_stateful_ledger.py writes: header, reports, formatted exception."""
    frame = f'  File "/{PRIVATE}/{facts_tool.TEST}", line 365, in settle'
    return "\n".join([json.dumps(header), "", "Traceback (most recent call last):", frame,
                      "    assert self.inspect(action)['uncertain'] is True",
                      f"AssertionError: {PRIVATE}", "Failing test case:", "state = LedgerMachine()",
                      *steps, "",
                      "You can reproduce this test case by temporarily adding "
                      f"@reproduce_failure('6.168.0', b'{blob}') as a decorator on your test function"]) + "\n"


def write_failure(tmp_path, name, text):
    directory = tmp_path / "build/private/hypothesis"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text)
    return directory


def header(digests, **changes):
    value = {"seed": None, "profile": "nightly", "focus": "settled_retry", "engine": "native_postgres18",
             "invocation": RUN, "hypothesis": "6.168.0", **digests}
    value.update(changes)
    return value


def collect(tmp_path, *, invocation=RUN, engine="native_postgres18", profile="nightly", focuses=("settled_retry",)):
    return facts_tool.collect(tmp_path, EXCEPTIONS, invocation=invocation, engine=engine, profile=profile,
                              focuses=set(focuses))


def test_counterexample_exports_only_validated_steps_frame_class_and_replay_digest(tmp_path):
    digests = source_tree(tmp_path)
    directory = write_failure(tmp_path, FILE, failure_text(header(digests)))
    # The example database shares the directory and is never read.
    (directory / "examples").mkdir()
    (directory / "examples/entry").write_text(PRIVATE)
    result = collect(tmp_path)
    assert result["status"] == "AVAILABLE" and result["malformed_files"] == 0 and result["other_files"] == 0
    item = result["files"][0]
    assert item["focus"] == "settled_retry" and item["profile"] == "nightly"
    assert item["steps"] == STEPS
    assert item["failing_frame"] == {"function": "settle", "line": 365}
    assert item["exception"] == "assertion"
    assert item["replay"] == {"hypothesis": "6.168.0", "blob_sha256": hashlib.sha256(BLOB.encode()).hexdigest()}
    assert item["header"]["engine"] == "native_postgres18" and item["header"]["invocation"] == RUN
    assert item["bound_to_tree"] is True
    assert item["file_sha256"] == hashlib.sha256((directory / FILE).read_bytes()).hexdigest()
    assert result["focuses_without_file"] == []
    encoded = json.dumps(result)
    assert PRIVATE not in encoded and str(tmp_path) not in encoded and "examples" not in encoded
    assert BLOB not in encoded


@pytest.mark.parametrize("blob", [
    base64.b64encode(PRIVATE.encode()).decode(),
    base64.b64encode(b"private").decode(),
    "not-base64-at-all",
])
def test_replay_blob_is_never_exported_even_when_it_looks_like_base64(tmp_path, blob):
    digests = source_tree(tmp_path)
    write_failure(tmp_path, FILE, failure_text(header(digests), blob=blob))
    item = collect(tmp_path)["files"][0]
    encoded = json.dumps(item)
    assert blob not in encoded and PRIVATE not in encoded
    assert all(value != blob for value in item.get("replay", {}).values())


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
    write_failure(tmp_path, FILE, failure_text(header(digests), [STEPS[0], line, STEPS[-1]]))
    item = collect(tmp_path)["files"][0]
    assert item["steps"] == [STEPS[0], STEPS[-1]]
    assert item["dropped_lines"] >= 1
    assert PRIVATE not in json.dumps(item)


def test_header_fields_of_unknown_shape_are_dropped_and_the_file_is_not_bound(tmp_path):
    digests = source_tree(tmp_path)
    bad = header(digests, seed=PRIVATE, profile=PRIVATE, engine=PRIVATE, hypothesis=PRIVATE,
                 test_sha256=PRIVATE, private=PRIVATE)
    bad["sql_sha256"] = {**digests["sql_sha256"], PRIVATE: "0" * 64, "001_core.sql": PRIVATE}
    text = failure_text(bad)
    item = facts_tool.facts_from_text(text, facts_tool.vocabulary(tmp_path), EXCEPTIONS,
                                      facts_tool.current_digests(tmp_path))
    assert set(item["header"]) == {"focus", "invocation", "sql_sha256"}
    assert "001_core.sql" not in item["header"]["sql_sha256"]
    assert item["dropped_header_fields"] == 8
    assert item["bound_to_tree"] is False
    assert PRIVATE not in json.dumps(item)
    # Without its engine and profile the file cannot belong to this invocation.
    write_failure(tmp_path, FILE, text)
    result = collect(tmp_path)
    assert result["files"] == [] and result["other_files"] == 1
    assert result["focuses_without_file"] == ["settled_retry"]


def test_old_same_tree_native_counterexample_is_not_attached_to_a_later_wasm_pr_failure(tmp_path):
    digests = source_tree(tmp_path)
    directory = write_failure(tmp_path, FILE, failure_text(header(digests, invocation=OLD_RUN)))
    write_failure(tmp_path, "ledger-settled_retry-pr-failure.txt",
                  failure_text(header(digests, invocation=OLD_RUN, profile="pr")))
    result = collect(tmp_path, invocation=RUN, engine="postgres18_wasm", profile="pr")
    assert result["files"] == [] and result["other_files"] == 2
    assert result["focuses_without_file"] == ["settled_retry"]
    # Historical failures stay where they are.
    assert (directory / FILE).exists() and (directory / "ledger-settled_retry-pr-failure.txt").exists()


def test_non_ledger_failure_exports_no_counterexample(tmp_path):
    digests = source_tree(tmp_path)
    write_failure(tmp_path, FILE, failure_text(header(digests)))
    result = collect(tmp_path, focuses=())
    assert result["files"] == [] and result["other_files"] == 1 and result["focuses_without_file"] == []


def test_interrupted_handler_reports_the_focus_without_a_file_and_never_an_older_one(tmp_path):
    digests = source_tree(tmp_path)
    write_failure(tmp_path, FILE, failure_text(header(digests, invocation=OLD_RUN)))
    result = collect(tmp_path, focuses=("settled_retry", "late_response"))
    assert result["files"] == [] and result["other_files"] == 1
    assert result["focuses_without_file"] == ["late_response", "settled_retry"]


@pytest.mark.parametrize("changes", [{"engine": "postgres18_wasm"}, {"profile": "pr"}, {"focus": "late_response"},
                                     {"invocation": None}])
def test_engine_profile_focus_or_invocation_mismatch_is_not_exported(tmp_path, changes):
    digests = source_tree(tmp_path)
    write_failure(tmp_path, FILE, failure_text(header(digests, **changes)))
    result = collect(tmp_path)
    assert result["files"] == [] and result["other_files"] == 1


@pytest.mark.parametrize("arguments", [{"invocation": None}, {"invocation": PRIVATE}, {"engine": PRIVATE},
                                       {"profile": PRIVATE}])
def test_collection_without_a_valid_binding_reads_nothing(tmp_path, arguments):
    digests = source_tree(tmp_path)
    write_failure(tmp_path, FILE, failure_text(header(digests)))
    assert collect(tmp_path, **arguments) == {"files": [], "other_files": 0, "malformed_files": 0,
                                              "truncated": False, "status": "UNBOUND"}


def test_unlisted_exception_and_message_text_are_never_exported(tmp_path):
    digests = source_tree(tmp_path)
    write_failure(tmp_path, FILE, failure_text(header(digests)).replace("AssertionError: ", "hypothesis.errors.Flaky: "))
    item = collect(tmp_path)["files"][0]
    assert "exception" not in item
    assert "Flaky" not in json.dumps(item) and PRIVATE not in json.dumps(item)


def test_steps_are_capped_and_other_names_symlinks_and_oversize_files_are_not_read(tmp_path):
    digests = source_tree(tmp_path)
    many = ["state.begin_send(index=1)"] * (facts_tool.MAX_STEPS + 5)
    directory = write_failure(tmp_path, FILE, failure_text(header(digests), many))
    (directory / f"ledger-{PRIVATE.replace('/', '_')}-nightly-failure.txt").write_text(PRIVATE)
    (directory / "ledger-late_response-nightly-failure.txt").symlink_to(tmp_path / facts_tool.TEST)
    (directory / "ledger-expired_generation-nightly-failure.txt").write_bytes(b"x" * (facts_tool.MAX_FILE_BYTES + 1))
    result = collect(tmp_path, focuses=("settled_retry", "late_response", "expired_generation"))
    assert [item["focus"] for item in result["files"]] == ["settled_retry"]
    assert result["malformed_files"] == 2
    assert len(result["files"][0]["steps"]) == facts_tool.MAX_STEPS and result["files"][0]["steps_truncated"] is True


def test_symlinked_corpus_is_not_followed(tmp_path):
    source_tree(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / FILE).write_text(PRIVATE)
    (tmp_path / "build/private").mkdir(parents=True)
    (tmp_path / "build/private/hypothesis").symlink_to(elsewhere)
    assert collect(tmp_path) == {"files": [], "other_files": 0, "malformed_files": 0, "truncated": False,
                                 "status": "NO_CORPUS", "focuses_without_file": ["settled_retry"]}


def test_vocabulary_and_failed_focuses_are_read_from_the_test_module():
    words = facts_tool.vocabulary(ROOT)
    assert words["focuses"] == ("late_response", "settled_retry", "expired_generation", "completed_generation")
    assert words["rules"]["delivery_claim"] == {"index": {"int"}, "worker": {"generated-a", "generated-b", "generated-c"}}
    assert words["rules"]["maintenance"] == {"paused": {True, False}}
    assert {"seed_regression", "immutable_authority_and_send_budget", "teardown"} <= set(words["rules"])
    nodeids = ["tests.postgres.test_stateful_ledger::test_generated_delivery_ledger[settled_retry]",
               f"tests.postgres.test_stateful_ledger::test_generated_delivery_ledger[{PRIVATE}]",
               "tests.postgres.test_late_response::test_generated_delivery_ledger[late_response]",
               "tests.postgres.test_stateful_ledger::test_T9_T31_settled_no_effect_expiry_allows_fresh_explicit_action"]
    assert facts_tool.failed_focuses(nodeids, words["focuses"]) == {"settled_retry"}


def native_tree(tmp_path, digests, focuses):
    raw = tmp_path / "build/evidence-raw/native-postgres.xml"
    raw.parent.mkdir(parents=True, exist_ok=True)
    suite = ET.Element("testsuite", hostname=PRIVATE)
    for focus in focuses:
        case = ET.SubElement(suite, "testcase", classname="tests.postgres.test_stateful_ledger",
                             name=f"test_generated_delivery_ledger[{focus}]")
        ET.SubElement(case, "failure", message="AssertionError: " + PRIVATE).text = PRIVATE
    ET.ElementTree(suite).write(raw, encoding="utf-8", xml_declaration=True)
    record = {"status": "FAIL", "code_digests": evidence_utils.code_digests(tmp_path), "phases": [
        {"phase": "postgres", "status": "FAIL", "exit_code": 1, "shutdown_exit_code": 0,
         "diagnostic_dir": "build/evidence-raw/native-postgres-" + RUN,
         "raw_junit_sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}]}
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/native-run.json").write_text(json.dumps(record))


def test_native_summary_adds_this_runs_counterexamples_and_focus_to_the_postgres_phase(tmp_path, monkeypatch, capsys):
    digests = source_tree(tmp_path)
    write_failure(tmp_path, FILE, failure_text(header(digests)))
    write_failure(tmp_path, "ledger-late_response-nightly-failure.txt",
                  failure_text(header(digests, focus="late_response", invocation=OLD_RUN)))
    native_tree(tmp_path, digests, ("settled_retry", "late_response", PRIVATE))
    monkeypatch.setattr(summary_tool, "ROOT", tmp_path)
    monkeypatch.setenv("ACK_HYPOTHESIS_PROFILE", "nightly")
    assert summary_tool.main() == 0
    output = capsys.readouterr().out
    assert PRIVATE not in output and str(tmp_path) not in output and BLOB not in output
    phase = json.loads(output)["phases"][0]
    assert {item.get("stateful_focus") for item in phase["pytest"]["failures"]} == {"settled_retry", "late_response", None}
    ledger = phase["ledger_counterexamples"]
    assert [item["focus"] for item in ledger["files"]] == ["settled_retry"]
    assert ledger["files"][0]["steps"] == STEPS
    assert ledger["other_files"] == 1 and ledger["focuses_without_file"] == ["late_response"]


def test_native_summary_under_another_profile_exports_no_counterexample(tmp_path, monkeypatch, capsys):
    digests = source_tree(tmp_path)
    write_failure(tmp_path, FILE, failure_text(header(digests)))
    native_tree(tmp_path, digests, ("settled_retry",))
    monkeypatch.setattr(summary_tool, "ROOT", tmp_path)
    monkeypatch.setenv("ACK_HYPOTHESIS_PROFILE", "pr")
    assert summary_tool.main() == 0
    ledger = json.loads(capsys.readouterr().out)["phases"][0]["ledger_counterexamples"]
    assert ledger["files"] == [] and ledger["other_files"] == 1


def test_runners_bind_and_export_counterexamples_only_on_ledger_failure():
    reference = (ROOT / "tools/run_reference_suite.py").read_text()
    assert "        env['ACK_LEDGER_INVOCATION'] = secrets.token_hex(16)\n" in reference
    assert ("        if name == 'postgres-wasm' and code != 0:\n"
            "            # Job log only, not the reference record") in reference
    assert reference.count("ledger_counterexamples(") == 1
    assert "'postgres18_wasm', env['ACK_HYPOTHESIS_PROFILE'], focuses)" in reference
    native = (ROOT / "tools/native_ci.py").read_text()
    assert "ACK_LEDGER_INVOCATION=run_id)" in native
    summary = (ROOT / "tools/native_failure_summary.py").read_text()
    assert "if ledger_failure_facts.TEST in NATIVE_PHASES[name]:" in summary
    test = (ROOT / facts_tool.TEST).read_text()
    assert '"invocation": os.environ.get("ACK_LEDGER_INVOCATION"),' in test
