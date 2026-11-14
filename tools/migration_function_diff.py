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
_WORD = re.compile(r"[A-Za-z_][A-Za-z_0-9$]*")
_DOLLAR = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")


class ParseError(ValueError):
    pass


def _statements(text: str):
    """Lexical statement boundaries, ignoring comments and quoted contents.

    This is deliberately not a PostgreSQL grammar. Unsupported function
    signatures are refused rather than silently compared under the wrong name.
    """
    tokens = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        if text.startswith('--', pos):
            end = text.find('\n', pos)
            pos = len(text) if end < 0 else end + 1
            continue
        if text.startswith('/*', pos):
            depth = 1
            pos += 2
            while pos < len(text) and depth:
                if text.startswith('/*', pos):
                    depth += 1
                    pos += 2
                elif text.startswith('*/', pos):
                    depth -= 1
                    pos += 2
                else:
                    pos += 1
            if depth:
                raise ParseError('unterminated comment')
            continue
        start = pos
        dollar = _DOLLAR.match(text, pos)
        if dollar:
            end = text.find(dollar[0], dollar.end())
            if end < 0:
                raise ParseError('unterminated dollar quote')
            pos = end + len(dollar[0])
            kind = 'body'
        elif text[pos] in "\"'":
            quote = text[pos]
            escaped = quote == "'" and pos > 0 and text[pos - 1] in 'eE' and (
                pos < 2 or not (text[pos - 2].isalnum() or text[pos - 2] == '_'))
            pos += 1
            while pos < len(text):
                if escaped and text[pos] == '\\':
                    pos += 2
                elif text[pos] == quote:
                    pos += 1
                    if pos < len(text) and text[pos] == quote:
                        pos += 1
                    else:
                        break
                else:
                    pos += 1
            else:
                raise ParseError('unterminated quoted value')
            kind = 'quoted'
        elif word := _WORD.match(text, pos):
            pos = word.end()
            kind = 'word'
        else:
            pos += 1
            kind = 'symbol'
        tokens.append((kind, text[start:pos], start, pos))
        if kind == 'symbol' and text[start:pos] == ';':
            yield tokens
            tokens = []
    if tokens:
        # A missing terminator is never evidence of no function changes.
        raise ParseError('unterminated statement')


def _definitions(text: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    found = {}
    for tokens in _statements(text):
        words = [t[1].lower() if t[0] == 'word' else t[1] for t in tokens]
        offset = 3 if words[:3] == ['create', 'or', 'replace'] else 1
        if words[:1] != ['create'] or words[offset:offset + 1] != ['function']:
            continue
        index = offset + 1
        parts = []
        while index < len(tokens):
            if tokens[index][0] != 'word':
                raise ParseError('only unquoted function names are supported')
            parts.append(words[index])
            index += 1
            if words[index:index + 1] != ['.']:
                break
            index += 1
        name = '.'.join(parts)
        if words[index:index + 1] != ['('] or len(parts) > 2:
            raise ParseError('unsupported function header')
        opening = index
        depth = 1
        index += 1
        while index < len(tokens) and depth:
            if words[index] == '(':
                depth += 1
            elif words[index] == ')':
                depth -= 1
            index += 1
        if depth:
            raise ParseError('unterminated function arguments')
        signature = tuple(words[opening:index])
        rest = tokens[index:]
        bodies = [i for i, token in enumerate(rest) if token[0] == 'body']
        if (len(bodies) != 1 or bodies[0] == 0 or
                (rest[bodies[0] - 1][0] != 'word' or rest[bodies[0] - 1][1].lower() != 'as') or
                any(t[0] == 'word' and t[1].lower() == 'create' for t in rest)):
            raise ParseError('function requires one AS dollar-quoted body')
        if name in found:
            raise ParseError(f'duplicate or overloaded function: {name}')
        found[name] = (text[tokens[0][2]:tokens[-1][3]], signature)
    return found


def functions(text: str) -> dict[str, str]:
    """Unquoted function names with complete dollar bodies; ambiguity is fatal."""
    return {name: body for name, (body, _) in _definitions(text).items()}


def compare(target: Path, earlier: list[Path]) -> list[tuple[str, str | None, list[str]]]:
    """(function, previous migration name or None, unified diff lines)."""
    previous = [(path, _definitions(path.read_text(encoding="utf-8"))) for path in earlier]
    rows = []
    for name, (body, signature) in _definitions(target.read_text(encoding="utf-8")).items():
        origin = next(((path, defined[name]) for path, defined in reversed(previous) if name in defined), None)
        if origin is None:
            rows.append((name, None, []))
            continue
        if any(name in definitions and definitions[name][1] != signature for _, definitions in previous):
            raise ParseError(f'changed or overloaded signature: {name}; review full files')
        diff = list(difflib.unified_diff(origin[1][0].splitlines(), body.splitlines(),
                                         f"{origin[0].name}:{name}", f"{target.name}:{name}", lineterm=""))
        rows.append((name, origin[0].name, diff))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("migration", nargs="?", type=Path, help="default: the newest migration")
    parser.add_argument("--dir", type=Path, default=MIGRATIONS, help="migration directory")
    parser.add_argument("--stat", action="store_true", help="changed-line counts only")
    args = parser.parse_args(argv)
    ordered = sorted(path.resolve() for path in args.dir.glob("[0-9][0-9][0-9]_*.sql"))
    if not ordered:
        print(f"no migrations in {args.dir}", file=sys.stderr)
        return 2
    target = args.migration.resolve() if args.migration else ordered[-1]
    if target not in ordered:
        print(f"{target.name} is not a migration in {args.dir}", file=sys.stderr)
        return 2
    try:
        rows = compare(target, ordered[:ordered.index(target)])
    except (ParseError, OSError, UnicodeError) as exc:
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
