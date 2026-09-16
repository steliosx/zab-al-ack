"""Synthetic checks of source identity export; no services or source execution."""
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
SOURCE = "tests/postgres/test_native_delivery_faults.py"
MODULE = "tests.postgres.test_native_delivery_faults"
VISIBLE = SOURCE + "::test_visible"


def bound_summary(root, cases, *, phase="delivery_faults", sources=None, passing=()):
    """Seed source first, then bind both that tree and the exact synthetic XML.

    `passing` cases carry no failure child, so a phase can be given the shape a
    long native run has: many passes, then the failures.
    """
    if sources is None:
        sources = {SOURCE: "def test_visible():\n    pass\n"}
    for relative, contents in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    suite = ET.Element("testsuite", hostname=PRIVATE)
    for classname, name in passing:
        ET.SubElement(suite, "testcase", classname=classname, name=name, file=PRIVATE, line=PRIVATE)
    for classname, name in cases:
        case = ET.SubElement(suite, "testcase", classname=classname, name=name,
                             file=PRIVATE, line=PRIVATE)
        failure = ET.SubElement(case, "failure", message="AssertionError: " + PRIVATE)
        failure.text = "private traceback " + PRIVATE
    raw = root / "build/evidence-raw" / ("native-" + phase + ".xml")
    raw.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(raw, encoding="utf-8", xml_declaration=True)
    record = {"status": "FAIL", "code_digests": evidence_utils.code_digests(root),
              "phases": [{"phase": phase, "status": "FAIL", "exit_code": 1,
                          "raw_junit_sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}]}
    record_path = root / "evidence/native-run.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record))
    return summary_tool.build_summary(root)


def pytest_facts(result):
    return result["phases"][0]["pytest"]


def assert_private_absent(result, root):
    exported = json.dumps(result)
    for forbidden in (PRIVATE, str(root), "traceback", "hostname", "classname"):
        assert forbidden not in exported


def test_source_identities_survive_parameter_redaction_without_executing_source(tmp_path):
    source = (f"raise RuntimeError({PRIVATE!r})\n"
              "def test_visible():\n    pass\n"
              "async def test_async_visible():\n    pass\n")
    result = bound_summary(tmp_path, [
        (MODULE, "test_visible[" + PRIVATE + "]"),
        (MODULE, "test_async_visible[" + PRIVATE + "]"),
    ], sources={SOURCE: source})
    facts = pytest_facts(result)
    assert facts["counts"] == {"passed": 0, "failed": 2, "skipped": 0}
    assert {failure["source_test"] for failure in facts["failures"]} == {
        VISIBLE, SOURCE + "::test_async_visible"}
    assert all(failure["category"] == "assertion" for failure in facts["failures"])
    assert_private_absent(result, tmp_path)


def test_parameter_variants_aggregate_but_distinct_source_tests_do_not(tmp_path):
    first = "tests/postgres/test_postgres.py"
    second = "tests/postgres/test_late_response.py"
    source = "def test_shared():\n    pass\n"
    result = bound_summary(tmp_path, [
        ("tests.postgres.test_postgres", "test_shared[" + PRIVATE + "]"),
        ("tests.postgres.test_postgres", "test_shared[another-private-parameter]"),
        ("tests.postgres.test_late_response", "test_shared"),
    ], phase="postgres", sources={first: source, second: source})
    facts = pytest_facts(result)
    assert facts["counts"] == {"passed": 0, "failed": 3, "skipped": 0}
    assert {failure["source_test"]: failure["count"] for failure in facts["failures"]} == {
        first + "::test_shared": 2, second + "::test_shared": 1}
    assert_private_absent(result, tmp_path)
    assert "another-private-parameter" not in json.dumps(result)


@pytest.mark.parametrize("classname,name", [
    (PRIVATE, "test_visible"),
    (MODULE + "." + PRIVATE, "test_visible"),
    (SOURCE, "test_visible"),
    ("tests.postgres.test_postgres", "test_visible"),
    (MODULE, "test_not_in_source"),
    (MODULE, PRIVATE),
    (MODULE, "test_visible[" + PRIVATE),
    (MODULE, "test_visible[" + PRIVATE + "]trailing"),
    (MODULE, "test_visible]" + PRIVATE),
    (MODULE, "test_visible\n" + PRIVATE),
])
def test_unknown_or_malformed_identity_keeps_classification_without_export(tmp_path, classname, name):
    result = bound_summary(tmp_path, [(classname, name)], sources={
        SOURCE: "def test_visible():\n    pass\n",
        "tests/postgres/test_postgres.py": "def test_visible():\n    pass\n",
    })
    facts = pytest_facts(result)
    assert facts["counts"]["failed"] == 1
    assert facts["failures"] == [{"kind": "failure", "stage": "unspecified",
        "category": "assertion", "exception_class": "AssertionError", "count": 1}]
    assert_private_absent(result, tmp_path)


def test_helper_nested_and_class_definitions_are_not_public_test_identities(tmp_path):
    source = ("def helper():\n"
              "    def test_nested():\n        pass\n"
              "class TestContainer:\n"
              "    def test_method(self):\n        pass\n"
              "if True:\n"
              "    def test_conditional():\n        pass\n")
    result = bound_summary(tmp_path, [
        (MODULE, "helper"), (MODULE, "test_nested"),
        (MODULE, "test_method"), (MODULE + ".TestContainer", "test_method"),
        (MODULE, "test_conditional"),
    ], sources={SOURCE: source})
    facts = pytest_facts(result)
    assert facts["counts"]["failed"] == 5
    assert len(facts["failures"]) == 1
    assert facts["failures"][0]["count"] == 5
    assert "source_test" not in facts["failures"][0]
    assert_private_absent(result, tmp_path)


@pytest.mark.parametrize("flaw", ["missing", "malformed", "oversized", "symlink", "fifo", "unreadable"])
def test_unavailable_source_omits_identity_without_losing_failure_facts(tmp_path, monkeypatch, flaw):
    path = tmp_path / SOURCE
    path.parent.mkdir(parents=True)
    if flaw == "malformed":
        path.write_text("def test_visible(:\n" + PRIVATE)
    elif flaw == "oversized":
        path.write_text("def test_visible():\n    pass\n#" + "x" * summary_tool.MAX_FILE_BYTES)
    elif flaw == "symlink":
        private_source = tmp_path / "private-source.py"
        private_source.write_text("def test_visible():\n    pass\n")
        path.symlink_to(private_source)
    elif flaw == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO source check requires POSIX")
        os.mkfifo(path)
    elif flaw == "unreadable":
        path.write_text("def test_visible():\n    pass\n")
        safe_bytes = summary_tool._safe_bytes

        def deny_source(candidate, root):
            if candidate == path:
                raise PermissionError(PRIVATE)
            return safe_bytes(candidate, root)

        monkeypatch.setattr(summary_tool, "_safe_bytes", deny_source)
    result = bound_summary(tmp_path, [(MODULE, "test_visible[" + PRIVATE + "]")], sources={})
    facts = pytest_facts(result)
    assert facts["status"] == "AVAILABLE" and facts["counts"]["failed"] == 1
    assert facts["failures"] == [{"kind": "failure", "stage": "unspecified",
        "category": "assertion", "exception_class": "AssertionError", "count": 1}]
    assert_private_absent(result, tmp_path)


def test_source_parsing_is_bounded_per_module_and_does_not_starve_later_files(tmp_path, monkeypatch):
    """The bound is one parse per module named, not a shared cap over definitions.

    A single catalog cap across a phase's files is spent by whichever files come
    first, so identification stops for the ones that come last as the early ones
    grow — and the last file in a phase is where a native-only failure lives.
    This asserts the replacement bound: a file far larger than the old cap still
    identifies both its first and its last definition, a second file in the same
    phase is unaffected by that size, and each module's source is read exactly
    once however many cases name it.
    """
    limit = summary_tool.MAX_ITEMS
    first, second = "tests/postgres/test_postgres.py", "tests/postgres/test_settled_expiry.py"
    reads = []
    safe_bytes = summary_tool._safe_bytes

    def counted(candidate, root):
        reads.append(str(candidate))
        return safe_bytes(candidate, root)

    monkeypatch.setattr(summary_tool, "_safe_bytes", counted)
    result = bound_summary(tmp_path, [
        ("tests.postgres.test_postgres", "test_source_0"),
        ("tests.postgres.test_postgres", f"test_source_{limit}"),
        ("tests.postgres.test_settled_expiry", "test_last_file"),
        ("tests.postgres.test_settled_expiry", "test_last_file[second-variant]"),
    ], phase="postgres", sources={
        first: "\n".join(f"def test_source_{index}():\n    pass\n" for index in range(limit + 1)),
        second: "def test_last_file():\n    pass\n"})
    facts = pytest_facts(result)
    assert facts["counts"]["failed"] == 4 and facts["truncated"] is False
    assert {failure["source_test"] for failure in facts["failures"]} == {
        first + "::test_source_0", first + f"::test_source_{limit}", second + "::test_last_file"}
    assert sum(failure["count"] for failure in facts["failures"]) == 4
    assert sum(name.endswith(first) for name in reads) == 1, "one parse per module, not per case"
    assert sum(name.endswith(second) for name in reads) == 1


def test_a_failure_after_the_old_case_bound_is_counted_classified_and_identified(tmp_path):
    """The shape a long native phase actually has: many passes, then the failures.

    Reading only the first MAX_ITEMS cases reports `failed: 0` for a phase that
    failed — which is what the hosted native summary exported on 2026-09-16 for
    a postgres phase of 201 passed and 2 failed.
    """
    limit = summary_tool.MAX_ITEMS
    result = bound_summary(tmp_path, [(MODULE, "test_visible[" + PRIVATE + "]")],
                           passing=[(MODULE, f"test_passing_{index}") for index in range(limit + 73)])
    facts = pytest_facts(result)
    assert facts["counts"] == {"passed": limit + 73, "failed": 1, "skipped": 0}
    assert facts["truncated"] is False, "the input is bounded by size, not by case count"
    assert facts["failures"] == [{"kind": "failure", "stage": "unspecified",
        "category": "assertion", "exception_class": "AssertionError",
        "source_test": VISIBLE, "count": 1}]
    assert_private_absent(result, tmp_path)


def test_exported_detail_is_bounded_while_the_counts_stay_complete(tmp_path):
    """What `truncated` now means: dropped export detail, never unread input."""
    limit = summary_tool.MAX_ITEMS
    result = bound_summary(tmp_path, [(MODULE, f"test_group_{index}") for index in range(limit + 5)],
                           sources={SOURCE: "\n".join(f"def test_group_{index}():\n    pass\n"
                                                      for index in range(limit + 5))})
    facts = pytest_facts(result)
    assert facts["counts"] == {"passed": 0, "failed": limit + 5, "skipped": 0}
    assert len(facts["failures"]) == limit and facts["truncated"] is True
    assert sum(failure["count"] for failure in facts["failures"]) == limit


def test_overlong_source_name_is_not_exported_or_allowed_to_hide_short_names(tmp_path):
    long_name = "test_" + "x" * 256
    source = f"def {long_name}():\n    pass\ndef test_visible():\n    pass\n"
    result = bound_summary(tmp_path, [(MODULE, long_name), (MODULE, "test_visible")],
                           sources={SOURCE: source})
    facts = pytest_facts(result)
    assert facts["counts"]["failed"] == 2
    assert {failure.get("source_test") for failure in facts["failures"]} == {VISIBLE, None}
    assert long_name not in json.dumps(result)


def test_new_source_identity_cannot_be_attached_to_stale_run(tmp_path):
    result = bound_summary(tmp_path, [(MODULE, "test_visible")])
    assert pytest_facts(result)["failures"][0]["source_test"] == VISIBLE
    (tmp_path / SOURCE).write_text("def test_different():\n    pass\n")
    stale = summary_tool.build_summary(tmp_path)
    assert stale["status"] == "UNAVAILABLE" and stale["phases"] == []
    assert "source_test" not in json.dumps(stale)


def test_printed_summary_contains_only_source_identity_not_parameter_or_traceback(tmp_path, monkeypatch, capsys):
    bound_summary(tmp_path, [(MODULE, "test_visible[" + PRIVATE + "]")])
    monkeypatch.setattr(summary_tool, "ROOT", tmp_path)
    assert summary_tool.main() == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads((tmp_path / "evidence/native-failure-summary.json").read_text())
    assert printed == saved
    assert pytest_facts(printed)["failures"][0]["source_test"] == VISIBLE
    assert_private_absent(printed, tmp_path)
