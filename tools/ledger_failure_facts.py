#!/usr/bin/env python3
"""Export allowlisted facts from minimized ledger counterexamples; never the raw file.

`tests/postgres/test_stateful_ledger.py` writes each failing focus to
`build/private/hypothesis/ledger-<focus>-<profile>-failure.txt`: a JSON header,
Hypothesis step reports and the formatted exception, whose notes carry the
minimized "Failing test case" and the `@reproduce_failure` blob. The file stays
private. This module rebuilds only what it can validate against the test module's
own vocabulary: header fields of known shape, step calls whose rule, keyword and
value the state machine declares, the innermost test-module frame, the exception
class from a caller-supplied allowlist, and only a digest of the replay blob (the
blob is opaque, so it stays private). Messages, paths and any other text are
dropped and counted. Only files written by the caller's own invocation, engine and
profile, for a focus that failed in that invocation, are exported; any other file
is counted, never read into the export and never removed.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[1]
TEST = "tests/postgres/test_stateful_ledger.py"
CORPUS = ("build", "private", "hypothesis")
PROFILES = ("pr", "nightly")
ENGINES = ("postgres18_wasm", "native_postgres18")
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 8
MAX_STEPS = 128
MAX_SQL = 64
MAX_INTEGER = 1_000_000
HEX64 = re.compile(r"[0-9a-f]{64}")
INVOCATION = re.compile(r"[0-9a-f]{10,32}")
VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
SQL_NAME = re.compile(r"[0-9]{3}_[a-z0-9_]{1,64}\.sql")
FRAME = re.compile(r'\s*File "(?P<path>[^"\n]{1,4096})", line (?P<line>[0-9]{1,6}), in (?P<function>[A-Za-z_][A-Za-z0-9_]{0,63})')
EXCEPTION = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*\.){0,4}(?P<name>[A-Za-z_][A-Za-z0-9_]{0,63})(?::.*)?")
REPRODUCE = re.compile(r"@reproduce_failure\('(?P<version>[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4})', "
                       r"b'(?P<blob>[A-Za-z0-9+/]{1,4096}={0,2})'\)")
DECORATORS = {"rule", "initialize", "invariant"}


def _literal(node):
    """A decorator literal the machine declares: an int bound or a sampled tuple of strings."""
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None


def vocabulary(root: Path = ROOT) -> dict:
    """Read the focuses, functions and rule signatures from the test module itself."""
    tree = ast.parse((root / TEST).read_text(encoding="utf-8"))
    focuses: tuple = ()
    functions: set[str] = set()
    rules: dict[str, dict[str, set]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "FOCUSES" for t in node.targets):
            value = _literal(node.value)
            if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
                focuses = value
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.add(node.name)
    machine = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LedgerMachine")
    for node in machine.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            name = (call.func if call else decorator)
            if not (isinstance(name, ast.Name) and name.id in DECORATORS):
                continue
            keywords: dict[str, set] = {}
            for keyword in (call.keywords if call else []):
                strategy = keyword.value
                allowed: set = set()
                if isinstance(strategy, ast.Call) and isinstance(strategy.func, ast.Attribute):
                    kind = strategy.func.attr
                    if kind == "integers":
                        allowed = {"int"}
                    elif kind == "booleans":
                        allowed = {True, False}
                    elif kind == "sampled_from" and strategy.args:
                        values = _literal(strategy.args[0])
                        if isinstance(values, tuple):
                            allowed = {v for v in values if isinstance(v, str) and re.fullmatch(r"[a-z0-9-]{1,32}", v)}
                if keyword.arg and allowed:
                    keywords[keyword.arg] = allowed
            rules[node.name] = keywords
    rules["teardown"] = {}
    return {"focuses": focuses, "functions": functions, "rules": rules}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _header(line: str, words: dict) -> tuple[dict, int]:
    """Copy each header field only when it has its known shape; count the rest."""
    value = json.loads(line, object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise ValueError("invalid header")
    header: dict = {}
    dropped = 0
    seed = value.get("seed")
    if seed is None or (type(seed) is int and 0 <= seed < 2 ** 64):
        header["seed"] = seed
    else:
        dropped += 1
    if isinstance(value.get("invocation"), str) and INVOCATION.fullmatch(value["invocation"]):
        header["invocation"] = value["invocation"]
    else:
        dropped += 1
    for key, allowed in (("profile", PROFILES), ("focus", words["focuses"]), ("engine", ENGINES)):
        if value.get(key) in allowed:
            header[key] = value[key]
        else:
            dropped += 1
    if isinstance(value.get("hypothesis"), str) and VERSION.fullmatch(value["hypothesis"]):
        header["hypothesis"] = value["hypothesis"]
    else:
        dropped += 1
    if isinstance(value.get("test_sha256"), str) and HEX64.fullmatch(value["test_sha256"]):
        header["test_sha256"] = value["test_sha256"]
    else:
        dropped += 1
    sql = value.get("sql_sha256")
    if isinstance(sql, dict) and len(sql) <= MAX_SQL:
        header["sql_sha256"] = {name: digest for name, digest in sql.items()
                                if isinstance(name, str) and SQL_NAME.fullmatch(name)
                                and isinstance(digest, str) and HEX64.fullmatch(digest)}
        dropped += len(sql) - len(header["sql_sha256"])
    else:
        dropped += 1
    dropped += len(set(value) - {"seed", "profile", "focus", "engine", "invocation", "hypothesis",
                                 "test_sha256", "sql_sha256"})
    return header, dropped


def _step(line: str, rules: dict) -> str | None:
    """Rebuild `state.<rule>(<keyword>=<value>, ...)` from validated parts, or None."""
    if not line.startswith("state.") or len(line) > 512:
        return None
    try:
        node = ast.parse(line, mode="eval").body
    except SyntaxError:
        return None
    if (not isinstance(node, ast.Call) or node.args or not isinstance(node.func, ast.Attribute)
            or not isinstance(node.func.value, ast.Name) or node.func.value.id != "state"
            or node.func.attr not in rules):
        return None
    signature = rules[node.func.attr]
    parts = []
    for keyword in node.keywords:
        allowed = signature.get(keyword.arg or "")
        if allowed is None or not isinstance(keyword.value, ast.Constant):
            return None
        value = keyword.value.value
        if type(value) is bool:
            if value not in allowed:
                return None
        elif type(value) is int:
            if "int" not in allowed or abs(value) > MAX_INTEGER:
                return None
        elif not (isinstance(value, str) and value in allowed):
            return None
        parts.append(f"{keyword.arg}={value!r}")
    return f"state.{node.func.attr}({', '.join(parts)})"


def facts_from_text(text: str, words: dict, exceptions: dict, current: dict | None = None) -> dict:
    """Facts from one failure file's text. `current` binds the header to this tree."""
    lines = text.splitlines()
    result: dict = {"steps": [], "dropped_lines": 0, "steps_truncated": False}
    try:
        result["header"], result["dropped_header_fields"] = _header(lines[0], words)
    except (IndexError, ValueError, TypeError, RecursionError):
        result["header"] = {}
        result["dropped_header_fields"] = None
    frame = None
    exception = None
    for line in lines[1:]:
        stripped = line.strip()
        match = FRAME.fullmatch(line)
        if match:
            path = match["path"].replace("\\", "/")
            if (path == TEST or path.endswith("/" + TEST)) and match["function"] in words["functions"]:
                frame = {"function": match["function"], "line": int(match["line"])}
            continue
        step = _step(stripped, words["rules"])
        if step is not None:
            if len(result["steps"]) < MAX_STEPS:
                result["steps"].append(step)
            else:
                result["steps_truncated"] = True
            continue
        reproduce = REPRODUCE.search(stripped)
        if reproduce and stripped.startswith("You can reproduce this test case"):
            # The blob is opaque: its content cannot be checked against the vocabulary,
            # so only its digest leaves; the blob itself stays in the private file.
            result["replay"] = {"hypothesis": reproduce["version"],
                                "blob_sha256": hashlib.sha256(reproduce["blob"].encode()).hexdigest()}
            continue
        if stripped and not line[:1].isspace():
            named = EXCEPTION.fullmatch(stripped)
            if named and named["name"] in exceptions:
                exception = exceptions[named["name"]]
                continue
        if stripped and stripped not in {"state = LedgerMachine()", "Failing test case:",
                                         "Traceback (most recent call last):"}:
            result["dropped_lines"] += 1
    if frame is not None:
        result["failing_frame"] = frame
    if exception is not None:
        result["exception"] = exception
    if current is not None:
        header = result["header"]
        result["bound_to_tree"] = (header.get("test_sha256") == current["test_sha256"]
                                   and header.get("sql_sha256") == current["sql_sha256"])
    return result


def current_digests(root: Path) -> dict:
    migrations = root / "sql" / "migrations"
    return {"test_sha256": hashlib.sha256((root / TEST).read_bytes()).hexdigest(),
            "sql_sha256": {item.name: hashlib.sha256(item.read_bytes()).hexdigest()
                           for item in sorted(migrations.glob("*.sql"))}}


def _safe_text(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError("invalid file")
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("invalid file")
    return data.decode("utf-8")


FAILED_CASE = re.compile(r"tests\.postgres\.test_stateful_ledger::test_generated_delivery_ledger\[(?P<focus>[a-z_]{1,32})\]")


def failed_focuses(nodeids, focuses) -> set[str]:
    """Focuses of failed generated-ledger cases, from JUnit node ids of this invocation."""
    return {match["focus"] for nodeid in nodeids
            if isinstance(nodeid, str) and (match := FAILED_CASE.fullmatch(nodeid)) and match["focus"] in focuses}


def collect(root: Path, exceptions: dict, *, invocation: str, engine: str, profile: str,
            focuses: set[str]) -> dict:
    """Facts for this invocation's counterexamples of the failed focuses; nothing else is read.

    A file left by another invocation, engine or profile, or for a focus that did
    not fail here, is counted in `other_files` and stays private and in place. A
    failed focus without a matching file (for example a run killed before the
    test's handler wrote one) is listed in `focuses_without_file`.
    """
    result: dict = {"files": [], "other_files": 0, "malformed_files": 0, "truncated": False}
    if (not INVOCATION.fullmatch(invocation or "") or engine not in ENGINES or profile not in PROFILES):
        result["status"] = "UNBOUND"
        return result
    directory = root.joinpath(*CORPUS)
    if not directory.is_dir() or any(p.is_symlink() for p in (directory, *directory.parents)):
        result["status"] = "NO_CORPUS"
        result["focuses_without_file"] = sorted(focuses)
        return result
    words = vocabulary(root)
    current = current_digests(root)
    focuses = {focus for focus in focuses if focus in words["focuses"]}
    names = re.compile(r"ledger-(?P<focus>" + "|".join(map(re.escape, words["focuses"]))
                       + r")-(?P<profile>pr|nightly)-failure\.txt")
    found: set[str] = set()
    for name in sorted(name for name in os.listdir(directory) if names.fullmatch(name)):
        match = names.fullmatch(name)
        if match["focus"] not in focuses or match["profile"] != profile:
            result["other_files"] += 1
            continue
        if len(result["files"]) >= MAX_FILES:
            result["truncated"] = True
            break
        try:
            data = _safe_text(directory / name)
            item = {"focus": match["focus"], "profile": match["profile"]}
            item.update(facts_from_text(data, words, exceptions, current))
        except (OSError, ValueError, UnicodeError):
            result["malformed_files"] += 1
            continue
        header = item["header"]
        if (header.get("invocation") != invocation or header.get("engine") != engine
                or header.get("profile") != profile or header.get("focus") != match["focus"]):
            result["other_files"] += 1
            continue
        item["file_sha256"] = hashlib.sha256(data.encode("utf-8")).hexdigest()
        result["files"].append(item)
        found.add(match["focus"])
    result["focuses_without_file"] = sorted(focuses - found)
    result["status"] = "AVAILABLE" if result["files"] else "NO_COUNTEREXAMPLE"
    return result
