#!/usr/bin/env python3
"""Show what a migration really changes in the functions it re-creates.

Migrations replace whole function bodies (worker_call is several hundred
lines), so a small correction arrives as a very large file. For each function
a migration defines, this prints a unified diff against the newest earlier
migration that defined the same function, or says that the function is new.

It reads files only; it is a review aid and decides nothing.

    python tools/migration_function_diff.py            # newest migration
    python tools/migration_function_diff.py sql/migrations/016_claim_delivery_order.sql
    python tools/migration_function_diff.py --stat
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "sql" / "migrations"
_HEADER = re.compile(r"^[ \t]*CREATE[ \t]+(?:OR[ \t]+REPLACE[ \t]+)?FUNCTION[ \t]+([A-Za-z_][\w.]*)[ \t]*\(",
                     re.IGNORECASE | re.MULTILINE)
_QUOTE = re.compile(r"\$[A-Za-z_]*\$")


class ParseError(ValueError):
    pass


def functions(text: str) -> dict[str, str]:
    """Each function's full CREATE statement, keyed by lower-cased name.

    A later definition of the same name in the same file wins, as it does in
    the database. A header with no complete dollar-quoted body is an error:
    silently skipping it would hide exactly the change under review.
    """
    found: dict[str, str] = {}
    position = 0
    while (header := _HEADER.search(text, position)) is not None:
        opening = _QUOTE.search(text, header.end())
        if opening is None:
            raise ParseError(f"no dollar-quoted body for {header.group(1)}")
        closing = text.find(opening.group(0), opening.end())
        if closing < 0:
            raise ParseError(f"unterminated body for {header.group(1)}")
        end = text.find(";", closing)
        if end < 0:
            raise ParseError(f"unterminated statement for {header.group(1)}")
        if _HEADER.search(text, header.end(), opening.start()) is not None:
            raise ParseError(f"no dollar-quoted body for {header.group(1)}")
        found[header.group(1).lower()] = text[header.start():end + 1]
        position = end + 1
    return found


def compare(target: Path, earlier: list[Path]) -> list[tuple[str, str | None, list[str]]]:
    """(function, previous migration name or None, unified diff lines)."""
    previous = [(path, functions(path.read_text(encoding="utf-8"))) for path in earlier]
    rows = []
    for name, body in functions(target.read_text(encoding="utf-8")).items():
        origin = next(((path, defined[name]) for path, defined in reversed(previous) if name in defined), None)
        if origin is None:
            rows.append((name, None, []))
            continue
        diff = list(difflib.unified_diff(origin[1].splitlines(), body.splitlines(),
                                         f"{origin[0].name}:{name}", f"{target.name}:{name}", lineterm=""))
        rows.append((name, origin[0].name, diff))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("migration", nargs="?", type=Path, help="default: the newest migration")
    parser.add_argument("--dir", type=Path, default=MIGRATIONS, help="migration directory")
    parser.add_argument("--stat", action="store_true", help="changed-line counts only")
    args = parser.parse_args(argv)
    ordered = sorted(args.dir.glob("[0-9][0-9][0-9]_*.sql"))
    if not ordered:
        print(f"no migrations in {args.dir}", file=sys.stderr)
        return 2
    target = args.migration.resolve() if args.migration else ordered[-1]
    names = [path.name for path in ordered]
    if target.name not in names:
        print(f"{target.name} is not a migration in {args.dir}", file=sys.stderr)
        return 2
    try:
        rows = compare(target, ordered[:names.index(target.name)])
    except ParseError as exc:
        print(f"cannot parse: {exc}", file=sys.stderr)
        return 2
    if not rows:
        print(f"{target.name} defines no functions")
    for name, origin, diff in rows:
        if origin is None:
            print(f"{name}: new in {target.name}")
        elif not diff:
            print(f"{name}: identical to {origin}")
        else:
            added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
            removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
            print(f"{name}: +{added} -{removed} against {origin}")
            if not args.stat:
                print("\n".join(diff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
