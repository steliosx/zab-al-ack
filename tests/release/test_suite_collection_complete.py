"""Every database-backed test file is collected by at least one CI suite.

`tests/postgres/test_source_binding.py` (F1, migration 015) merged without being in
`tools/native_ci.PHASES` or the `postgres-wasm` group of `tools/run_reference_suite.py`,
so CI never ran it. These lists are explicit by design; this check makes a file that is
in neither of them fail the release suite instead of passing unnoticed.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATABASE_TEST_DIRS = ("tests/postgres", "tests/alerta", "tests/performance")


def listed_targets() -> set[str]:
    text = (ROOT / "tools/native_ci.py").read_text() + (ROOT / "tools/run_reference_suite.py").read_text()
    return {path for directory in DATABASE_TEST_DIRS
            for path in (p.relative_to(ROOT).as_posix() for p in (ROOT / directory).glob("test_*.py"))
            if f"'{path}'" in text}


def test_every_database_test_file_is_in_a_ci_suite():
    present = {p.relative_to(ROOT).as_posix() for directory in DATABASE_TEST_DIRS
               for p in (ROOT / directory).glob("test_*.py")}
    assert present, "no database test files found: the directory layout changed"
    assert sorted(present - listed_targets()) == []


def test_the_f1_sql_tests_run_natively_and_on_wasm():
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import native_ci
    assert "tests/postgres/test_source_binding.py" in native_ci.PHASES["postgres"]
    suite = (ROOT / "tools/run_reference_suite.py").read_text()
    wasm_line = next(line for line in suite.splitlines() if "('postgres-wasm', [" in line)
    assert "'tests/postgres/test_source_binding.py'" in wasm_line


def test_every_test_directory_is_named_by_a_ci_suite():
    """A new tests/<dir> must be named in native_ci.py or run_reference_suite.py
    (the database directories are checked file by file above)."""
    text = (ROOT / "tools/native_ci.py").read_text() + (ROOT / "tools/run_reference_suite.py").read_text()
    directories = sorted(p.parent.relative_to(ROOT).as_posix() for p in (ROOT / "tests").glob("*/test_*.py"))
    assert directories, "no test directories found: the layout changed"
    unnamed = {d for d in directories if d not in DATABASE_TEST_DIRS and f"'{d}'" not in text}
    assert sorted(unnamed) == []


def test_the_ambiguous_api_migration_tests_run_natively():
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import native_ci
    assert "tests/postgres/test_ambiguous_api_migration.py" in native_ci.PHASES["postgres"]

