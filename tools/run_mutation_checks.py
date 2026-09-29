#!/usr/bin/env python3
"""Run reviewed mutations against disposable source copies, failing closed.

Only an assertion failure after the unchanged baseline passed can kill a
mutant. Compilation, collection/import, runtime errors, skips, missing cases,
timeouts and interruption are separate failures, never successful kills.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from evidence_utils import binding_error, code_digests
from mutation_catalog import TESTS, selected
from retained_temp import fresh_basetemp, retain
from review_runs import atomic_json, private_directory, workspace_lock

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = ('KILLED', 'SURVIVED', 'INVALID', 'ERROR', 'TIMEOUT', 'NOT_RUN')
COPY_DIRS = ('src', 'tests', 'tools', 'config')
COPY_FILES = ('pyproject.toml', 'setup.cfg')
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '*.egg-info',
                               '.pytest_cache', '.hypothesis', 'node_modules')
_GUARD = '''from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import sys
import pytest
from _pytest.outcomes import Failed

_items = []
_assertion_failures = 0

def pytest_sessionstart(session):
    if pytest.__version__ != "9.0.3" or importlib.metadata.version("pytest-asyncio") != "1.3.0":
        raise pytest.UsageError("mutation_pytest_version_mismatch")

def pytest_collection_modifyitems(items):
    root = Path(__file__).resolve().parent
    for item in items:
        parameters = sorted(getattr(getattr(item, "callspec", None), "indices", {}).items())
        # Parameter indices preserve every collected case without hashing UUIDs
        # generated at import time into a different identity for each process.
        _items.append([str(item.path.relative_to(root)), item.originalname or item.name, parameters])

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    global _assertion_failures
    outcome = yield
    report = outcome.get_result()
    if report.failed and call.when == "call" and call.excinfo is not None:
        error = call.excinfo.value
        if isinstance(error, AssertionError) or (isinstance(error, Failed) and str(error).startswith("DID NOT RAISE")):
            _assertion_failures += 1

def pytest_sessionfinish(session, exitstatus):
    root = Path(__file__).resolve().parent / "src"
    seen = []
    for name, module in list(sys.modules.items()):
        if name == "zabalack" or name.startswith("zabalack."):
            path = getattr(module, "__file__", None)
            if not path or not Path(path).resolve().is_relative_to(root):
                session.exitstatus = pytest.ExitCode.INTERNAL_ERROR
                return
            seen.append(name)
    if not seen:
        session.exitstatus = pytest.ExitCode.INTERNAL_ERROR
        return
    digest = hashlib.sha256(json.dumps(sorted(_items), separators=(",", ":")).encode()).hexdigest()
    Path(os.environ["ACK_MUTATION_EVENTS"]).write_text(json.dumps({
        "tests": len(_items), "case_digest": digest, "assertion_failures": _assertion_failures}))
'''


PUBLIC_EVIDENCE_MODE = 0o644
PRIVATE_DIAGNOSTIC_MODE = 0o600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, data: dict, *, mode: int = PUBLIC_EVIDENCE_MODE) -> None:
    """Replace a record atomically, keeping the mode its readers expect.

    mkstemp creates 0600; os.replace keeps that mode. Public evidence must stay
    readable to every consumer of the checkout and of the exported archives, so
    the caller's mode is applied before the replace. Private diagnostics keep
    0600 and stay inside their own 0700 directory.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('mutation_report_symlink')
    fd, temporary = tempfile.mkstemp(prefix='.mutation-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, indent=2)
            handle.write('\n')
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _copy_tree(root: Path, target: Path) -> None:
    target.mkdir(mode=0o700)
    for directory in COPY_DIRS:
        source = root / directory
        if not source.is_dir():
            raise ValueError('mutation_source_missing')
        if source.is_symlink() or any(path.is_symlink() for path in source.rglob('*')):
            raise ValueError('mutation_source_symlink')
        shutil.copytree(source, target / directory, ignore=IGNORE)
    for filename in COPY_FILES:
        source = root / filename
        if source.is_symlink():
            raise ValueError('mutation_source_symlink')
        if source.is_file():
            shutil.copy2(source, target / filename)
    (target / '_zabalack_mutation_guard.py').write_text(_GUARD)


def mutate(source: str, before: str, after: str) -> str:
    if before == after or not before or source.count(before) != 1:
        raise ValueError('mutation_anchor_not_unique')
    result = source.replace(before, after, 1)
    compile(result, '<isolated-mutation>', 'exec')
    return result


def _junit(path: Path, events_path: Path | None = None) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
        raise ValueError('mutation_junit_missing_or_oversized')
    root = ET.parse(path).getroot()
    entries = list(root.iter('testcase'))
    if not entries:
        raise ValueError('mutation_no_tests')
    identities = [(entry.get('classname', ''), entry.get('name', '')) for entry in entries]
    if any(not all(identity) for identity in identities) or len(set(identities)) != len(identities):
        raise ValueError('mutation_case_identity_invalid')
    counts = {'tests': len(entries), 'failures': 0, 'errors': 0, 'skipped': 0,
              'assertion_failures': 0}
    for entry in entries:
        for tag in ('failure', 'error', 'skipped'):
            nodes = entry.findall(tag)
            if len(nodes) > 1:
                raise ValueError('mutation_case_result_ambiguous')
            if nodes:
                counts[{'failure': 'failures', 'error': 'errors', 'skipped': 'skipped'}[tag]] += 1
        failure = entry.find('failure')
        if failure is not None and failure.get('type') in {'AssertionError', 'Failed'}:
            # Pytest represents a failed `pytest.raises` expectation as Failed.
            # Runtime exceptions (KeyError, ImportError, etc.) never count.
            counts['assertion_failures'] += 1
    counts['case_digest'] = hashlib.sha256(json.dumps(sorted(identities), separators=(',', ':')).encode()).hexdigest()
    if events_path is not None:
        if events_path.is_symlink() or not events_path.is_file() or events_path.stat().st_size > 4096:
            raise ValueError('mutation_callback_evidence_missing')
        events = json.loads(events_path.read_text())
        if (not isinstance(events, dict) or set(events) != {'tests', 'case_digest', 'assertion_failures'} or
                type(events['tests']) is not int or events['tests'] != counts['tests'] or
                type(events['assertion_failures']) is not int or
                not 0 <= events['assertion_failures'] <= counts['failures'] or
                not isinstance(events['case_digest'], str) or not re.fullmatch('[0-9a-f]{64}', events['case_digest'])):
            raise ValueError('mutation_callback_evidence_invalid')
        counts.update(events)
    return counts


def classify(exit_code: int, counts: dict, baseline: dict | None = None) -> str:
    if (counts['errors'] or counts['skipped'] or
            counts['assertion_failures'] != counts['failures']):
        return 'ERROR'
    if baseline is not None and any(counts[key] != baseline[key] for key in ('case_digest', 'tests')):
        return 'ERROR'
    if exit_code == 0 and counts['failures'] == 0:
        return 'BASELINE_PASS' if baseline is None else 'SURVIVED'
    if baseline is not None and exit_code == 1 and counts['assertion_failures'] > 0:
        return 'KILLED'
    return 'ERROR'


def run_pytest(tree: Path, raw: Path, label: str, timeout: int, baseline: dict | None = None) -> dict:
    log_path, junit = raw / (label + '.log'), raw / (label + '.xml')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('ACK_TEST_', 'PYTEST_', 'ACK_HYPOTHESIS_'))
           and key not in {'PYTHONPATH', 'PYTHONHOME', 'PYTHONOPTIMIZE'}}
    env.update(TZ='UTC', PYTHONPATH=str(tree / 'src') + os.pathsep + str(tree),
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONDONTWRITEBYTECODE='1',
               ACK_HYPOTHESIS_PROFILE='pr', HYPOTHESIS_STORAGE_DIRECTORY=str(raw / 'hypothesis'),
               ACK_MUTATION_EVENTS=str(raw / (label + '-events.json')))
    # An owned, explicit basetemp is never rotated by a later invocation.
    basetemp = fresh_basetemp('mutation-' + label)
    command = [sys.executable, '-B', '-m', 'pytest', '-q', '-o', 'addopts=',
               '-p', 'pytest_asyncio.plugin', '-p', '_zabalack_mutation_guard',
               '--junitxml=' + str(junit), '-o', 'cache_dir=' + str(raw / 'pytest-cache'),
               '--basetemp=' + str(basetemp), *TESTS]
    started = time.monotonic()
    result = {'outcome': 'ERROR', 'exit_code': None}
    process = None
    with log_path.open('w') as log:
        try:
            process = subprocess.Popen(command, cwd=tree, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            result['exit_code'] = process.wait(timeout=timeout)
            result.update(_junit(junit, raw / (label + '-events.json')))
            result['outcome'] = classify(result['exit_code'], result, baseline)
        except subprocess.TimeoutExpired:
            result['outcome'] = 'TIMEOUT'
        except (OSError, ValueError, ET.ParseError):
            result['outcome'] = 'ERROR'
        finally:
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    # Retained before the next invocation; its status stays in the private record.
    retain(basetemp, raw / 'basetemp' / label)
    result['duration_ms'] = round((time.monotonic() - started) * 1000)
    return result


def validate_report(record: object, root: Path, *, expected_profile: str | None = None) -> None:
    """Strict reusable gate for CI aggregation/export; raises a stable reason.

    Artifact transport and JSON duplicate-key rejection remain the caller's
    responsibility. This validates full catalogue/count/baseline/tree binding.
    """
    if not isinstance(record, dict) or binding_error(record, root, 'mutation'):
        raise ValueError('mutation_evidence_binding_invalid')
    profile = record.get('profile')
    if profile not in {'pr', 'nightly'} or expected_profile not in {None, profile}:
        raise ValueError('mutation_evidence_profile_invalid')
    if type(record.get('schema_version')) is not int or record['schema_version'] != 1 or record.get('status') != 'PASS' or record.get('tree_unchanged') is not True:
        raise ValueError('mutation_evidence_not_passed')
    baseline = record.get('baseline')
    if not isinstance(baseline, dict) or baseline.get('outcome') != 'BASELINE_PASS' or type(baseline.get('exit_code')) is not int or baseline['exit_code'] != 0:
        raise ValueError('mutation_baseline_invalid')
    if (type(baseline.get('tests')) is not int or baseline['tests'] < 1 or
            any(type(baseline.get(key)) is not int or baseline[key] != 0
                for key in ('failures', 'errors', 'skipped', 'assertion_failures')) or
            not isinstance(baseline.get('case_digest'), str) or
            not re.fullmatch('[0-9a-f]{64}', baseline['case_digest'])):
        raise ValueError('mutation_baseline_counts_invalid')
    mutants = record.get('mutants')
    names = [item.name for item in selected(profile)]
    if (not isinstance(mutants, list) or not all(isinstance(row, dict) for row in mutants) or
            [row.get('name') for row in mutants] != names):
        raise ValueError('mutation_catalogue_incomplete')
    for row in mutants:
        if (row.get('outcome') != 'KILLED' or type(row.get('exit_code')) is not int or row['exit_code'] != 1 or
                type(row.get('failures')) is not int or not 1 <= row['failures'] <= baseline['tests'] or
                type(row.get('assertion_failures')) is not int or row['assertion_failures'] != row['failures'] or
                any(type(row.get(key)) is not int or row[key] != 0 for key in ('errors', 'skipped')) or
                type(row.get('tests')) is not int or row['tests'] != baseline['tests'] or
                row.get('case_digest') != baseline['case_digest']):
            raise ValueError('mutation_result_not_killed')
    expected_counts = {key: len(names) if key == 'KILLED' else 0 for key in OUTCOMES}
    counts = record.get('counts')
    if (not isinstance(counts, dict) or set(counts) != set(OUTCOMES) or
            any(type(value) is not int for value in counts.values()) or counts != expected_counts or
            type(record.get('selected_count')) is not int or record['selected_count'] != len(names)):
        raise ValueError('mutation_evidence_counts_invalid')


def run(root: Path = ROOT, *, profile: str = 'pr', timeout: int = 90) -> int:
    with workspace_lock(root, 'mutation'):
        return _run_locked(root, profile=profile, timeout=timeout)


def _run_locked(root: Path, *, profile: str, timeout: int) -> int:
    catalogue = selected(profile)
    output = root / 'evidence/mutation-run.json'
    report = {'schema_version': 1, 'status': 'RUNNING', 'profile': profile,
              'code_digests': code_digests(root), 'started_at_utc': _now(),
              'selected_count': len(catalogue), 'baseline': None, 'mutants': [],
              'scope': 'Curated Python trust/worker/RPC assertion mutations with mocked dependencies; no SQL/native/deployment coverage'}
    # Invalidate any previous PASS before preparation or launching a child.
    _atomic_json(output, report)
    profile_output = root / f'evidence/mutation-{profile}-run.json'
    _atomic_json(profile_output, report)
    private = private_directory(root / 'build/private/mutation')
    raw = Path(tempfile.mkdtemp(prefix='run-', dir=private))
    _atomic_json(raw / 'report.json', report, mode=PRIVATE_DIAGNOSTIC_MODE)
    tree = raw / 'tree'
    try:
        _copy_tree(root, tree)
        if code_digests(root) != report['code_digests']:
            raise ValueError('mutation_tree_changed_during_copy')
        # The working copy is removed at the end; keep the executed baseline bytes.
        shutil.copytree(tree, raw / 'tree-baseline')
        baseline = run_pytest(tree, raw, 'baseline', timeout)
        report['baseline'] = baseline
        print(json.dumps({'baseline': baseline['outcome'], 'tests': baseline.get('tests', 0)}), flush=True)
        if baseline['outcome'] != 'BASELINE_PASS':
            raise ValueError('mutation_baseline_failed')
        variants = private_directory(raw / 'variants')
        index: list[dict] = []
        for item in catalogue:
            path = tree / item.path
            original = path.read_bytes()
            result = {'name': item.name, 'outcome': 'INVALID', 'exit_code': None}
            try:
                path.write_text(mutate(original.decode(), item.before, item.after))
                # Keep the exact executed bytes before the run, not a later reconstruction.
                executed = path.read_bytes()
                (variants / item.name).write_bytes(executed)
                index.append({'name': item.name, 'path': item.path, 'sha256': hashlib.sha256(executed).hexdigest()})
                atomic_json(variants / 'index.json', {'variants': index})
                result.update(run_pytest(tree, raw, item.name, timeout, baseline))
            except (SyntaxError, UnicodeError, ValueError):
                result['outcome'] = 'INVALID'
            finally:
                # Only private copies are ever changed. Restore each before the
                # next mutant, and remove the complete copy even on interruption.
                path.write_bytes(original)
            report['mutants'].append(result)
            print(json.dumps({'name': item.name, 'outcome': result['outcome']}), flush=True)
        report['status'] = 'PASS' if all(row['outcome'] == 'KILLED' for row in report['mutants']) else 'FAIL'
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report['status'] = 'FAIL'
        report['reason'] = type(error).__name__
    finally:
        shutil.rmtree(tree, ignore_errors=True)
        done = {row['name'] for row in report['mutants']}
        report['mutants'].extend({'name': item.name, 'outcome': 'NOT_RUN', 'exit_code': None}
                                 for item in catalogue if item.name not in done)
        observed = Counter(row['outcome'] for row in report['mutants'])
        report['counts'] = {key: observed[key] for key in OUTCOMES}
        report['tree_unchanged'] = report['code_digests'] == code_digests(root)
        if not report['tree_unchanged']:
            report['status'] = 'FAIL'
        report['finished_at_utc'] = _now()
        if report['status'] == 'PASS':
            try:
                validate_report(report, root, expected_profile=profile)
            except ValueError:
                report['status'] = 'FAIL'
                report['reason'] = 'mutation_self_validation_failed'
        _atomic_json(output, report)
        _atomic_json(profile_output, report)
        _atomic_json(raw / 'report.json', report, mode=PRIVATE_DIAGNOSTIC_MODE)
    return 0 if report['status'] == 'PASS' else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('pr', 'nightly'), default='pr')
    parser.add_argument('--timeout', type=int, default=90, help='Per baseline/mutant bound, 5–300 seconds')
    arguments = parser.parse_args()
    if not 5 <= arguments.timeout <= 300:
        parser.error('--timeout must be between 5 and 300 seconds')
    def interrupted(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        return run(profile=arguments.profile, timeout=arguments.timeout)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    raise SystemExit(main())
