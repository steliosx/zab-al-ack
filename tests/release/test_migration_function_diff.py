"""The migration review aid: per-function diffs between migrations."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import migration_function_diff as mfd  # noqa: E402

ONE = """CREATE TABLE zabalack.t(a int);
CREATE FUNCTION zabalack.f(p int) RETURNS int LANGUAGE sql AS $$
 SELECT p + 1
$$;
CREATE OR REPLACE FUNCTION zabalack.g() RETURNS int
LANGUAGE plpgsql AS $body$
BEGIN RETURN 1; END $body$;
"""
TWO = """create or replace function zabalack.F(p int) RETURNS int LANGUAGE sql AS $$
 SELECT p + 2
$$;
CREATE FUNCTION zabalack.h() RETURNS int LANGUAGE sql AS $$ SELECT 3 $$;
"""


def write(tmp_path: Path) -> Path:
    (tmp_path / "001_one.sql").write_text(ONE, encoding="utf-8")
    (tmp_path / "002_two.sql").write_text(TWO, encoding="utf-8")
    return tmp_path


def test_functions_are_split_on_their_own_dollar_quotes():
    found = mfd.functions(ONE)
    assert list(found) == ["zabalack.f", "zabalack.g"]
    assert found["zabalack.f"].endswith("$$;") and "SELECT p + 1" in found["zabalack.f"]
    assert found["zabalack.g"].endswith("$body$;") and "CREATE TABLE" not in found["zabalack.g"]


def test_a_replaced_function_is_diffed_against_its_previous_definition(tmp_path, capsys):
    assert mfd.main(["--dir", str(write(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "zabalack.f: +2 -2 against 001_one.sql" in out
    assert "- SELECT p + 1" in out and "+ SELECT p + 2" in out
    assert "zabalack.h: new in 002_two.sql" in out
    assert "zabalack.g" not in out


def test_stat_prints_counts_without_bodies(tmp_path, capsys):
    assert mfd.main(["--dir", str(write(tmp_path)), "--stat"]) == 0
    out = capsys.readouterr().out
    assert "zabalack.f: +2 -2" in out and "SELECT" not in out


@pytest.mark.parametrize("text", [
    "CREATE FUNCTION zabalack.f() RETURNS int LANGUAGE sql AS $$ SELECT 1;",
    "CREATE FUNCTION zabalack.f() RETURNS int;\nCREATE FUNCTION zabalack.g() RETURNS int AS $$ SELECT 1 $$;",
])
def test_a_function_without_a_complete_body_is_refused_not_skipped(tmp_path, capsys, text):
    (tmp_path / "001_bad.sql").write_text(text, encoding="utf-8")
    assert mfd.main(["--dir", str(tmp_path)]) == 2
    assert "cannot parse" in capsys.readouterr().err


def test_unknown_migration_and_empty_directory_are_errors(tmp_path):
    assert mfd.main(["--dir", str(tmp_path)]) == 2
    write(tmp_path)
    assert mfd.main(["--dir", str(tmp_path), str(tmp_path / "003_absent.sql")]) == 2


def test_migration_016_changes_only_the_reviewed_lines_of_worker_call(capsys):
    """The real shape: 016 re-creates a several-hundred-line function to change a few lines."""
    assert mfd.main([str(mfd.MIGRATIONS / "016_claim_delivery_order.sql")]) == 0
    out = capsys.readouterr().out
    assert "zabalack.worker_call: +15 -7 against 014_claim_expiry_fence.sql" in out
    assert "+  IF vl.owner<>worker OR vl.generation IS DISTINCT FROM" in out


def test_every_shipped_migration_parses():
    for path in sorted(mfd.MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        mfd.functions(path.read_text(encoding="utf-8"))
