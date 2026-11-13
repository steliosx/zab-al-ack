#!/usr/bin/env python3
"""Content-free mutation diagnostics, separate from passing gate evidence."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from evidence_utils import binding_error
from mutation_catalog import TESTS

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 4 * 1024 * 1024
MAX_CASES = 64
OUTCOMES = {'BASELINE_PASS', 'KILLED', 'SURVIVED', 'INVALID', 'ERROR', 'TIMEOUT', 'NOT_RUN'}
KINDS = {'failure', 'error', 'skipped'}


def context() -> dict:
    """Only validated GitHub identifiers, never arbitrary environment text."""
    result = {}
    for key, variable, pattern in [('sha', 'GITHUB_SHA', '[0-9a-f]{40}'),
                                   ('run_id', 'GITHUB_RUN_ID', '[1-9][0-9]{0,19}'),
                                   ('run_attempt', 'GITHUB_RUN_ATTEMPT', '[1-9][0-9]{0,9}')]:
        value = os.environ.get(variable, '')
        if re.fullmatch(pattern, value):
            result[key] = value
    return result


def safe_bytes(path: Path, root: Path) -> bytes:
    if (not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() or
            any(p.is_symlink() for p in path.parents) or not path.is_file() or
            path.stat().st_size > MAX_BYTES):
        raise ValueError('unsafe diagnostic input')
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError('oversized diagnostic input')
    return data


def source_cases(root: Path) -> dict[str, set[str]]:
    found = {}
    for relative in TESTS:
        try:
            tree = ast.parse(safe_bytes(root / relative, root))
        except (OSError, ValueError, SyntaxError):
            continue
        found[relative] = {node.name for node in tree.body
                           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                           and node.name.startswith('test_')}
    return found


def failure_cases(junit: Path, tree: Path, raw: Path) -> dict:
    """Export source-known function names only, dropping parameter IDs and text."""
    result = {'availability': 'unavailable', 'cases': [], 'omitted': 0}
    try:
        data = safe_bytes(junit, raw).decode('utf-8')
        if '<!DOCTYPE' in data or '<!ENTITY' in data or '\x00' in data:
            return result
        document = ET.fromstring(data)
        known = source_cases(tree)
        modules = {str(Path(p).with_suffix('')).replace('/', '.'): p for p in known}
        result['availability'] = 'available'
        for case in document.iter('testcase'):
            kinds = [kind for kind in sorted(KINDS) if case.find(kind) is not None]
            if not kinds:
                continue
            relative = modules.get(case.get('classname', ''))
            name = case.get('name', '').split('[', 1)[0]
            if relative is None or name not in known[relative] or len(result['cases']) >= MAX_CASES:
                result['omitted'] += 1
                continue
            result['cases'].append({'file': relative, 'test': name, 'kinds': kinds})
    except (OSError, ValueError, ET.ParseError):
        pass
    return result


def summarize(root: Path = ROOT) -> dict:
    """Project a current report onto a small allowlist; never copy free text."""
    output = {'schema_version': 1, 'purpose': 'diagnostic_only', 'ci_context': context(),
              'availability': 'unavailable', 'baseline': None}
    try:
        record = json.loads(safe_bytes(root / 'evidence/mutation-run.json', root))
        if (not isinstance(record, dict) or record.get('ci_context') != output['ci_context'] or
                binding_error(record, root, 'mutation') is not None):
            return output
        baseline = record.get('baseline')
        output['availability'] = 'current_report'
        output['status'] = record.get('status') if record.get('status') in {'PASS', 'FAIL', 'RUNNING'} else 'UNKNOWN'
        output['code_digests'] = {key: record['code_digests'][key] for key in ('source', 'harness')}
        if not isinstance(baseline, dict):
            return output
        row = {'outcome': baseline.get('outcome') if baseline.get('outcome') in OUTCOMES else 'ERROR'}
        for key in ('tests', 'failures', 'errors', 'skipped', 'assertion_failures', 'duration_ms'):
            value = baseline.get(key)
            if type(value) is int and 0 <= value <= 10**9:
                row[key] = value
        value = baseline.get('exit_code')
        row['exit_code'] = value if type(value) is int and -255 <= value <= 255 else None
        diagnostics = baseline.get('diagnostics')
        if isinstance(diagnostics, dict):
            known = source_cases(root)
            cases = diagnostics.get('cases', [])
            row['cases'] = []
            if isinstance(cases, list):
                for case in cases[:MAX_CASES]:
                    if not isinstance(case, dict):
                        continue
                    path, name, kinds = case.get('file'), case.get('test'), case.get('kinds')
                    if (isinstance(path, str) and isinstance(name, str) and name in known.get(path, set()) and
                            isinstance(kinds, list) and kinds and all(isinstance(k, str) and k in KINDS for k in kinds)):
                        row['cases'].append({'file': path, 'test': name, 'kinds': sorted(set(kinds))})
            value = diagnostics.get('omitted')
            row['omitted'] = value if type(value) is int and 0 <= value <= 10**9 else 0
            row['case_availability'] = 'available' if diagnostics.get('availability') == 'available' else 'unavailable'
        output['baseline'] = row
    except (OSError, ValueError, TypeError):
        pass
    return output


def main() -> int:
    target = ROOT / 'evidence/mutation-failure-summary.json'
    if target.is_symlink() or any(p.is_symlink() for p in target.parents):
        raise ValueError('unsafe diagnostic output')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summarize(), indent=2, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
