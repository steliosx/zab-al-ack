#!/usr/bin/env python3
"""Limited manual Actions diagnostic; never writes full-CI acceptance evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading

from evidence_utils import cases, code_digests, redact_junit

ROOT = Path(__file__).resolve().parents[1]
# Exact unit-mocked targets in run_reference_suite.py; no optional-services phase.
UNIT_TARGETS = (
    'tests/unit', 'tests/rpc', 'tests/security', 'tests/release',
    'tests/property', 'tests/native_support',
    'tests/alerta/test_http_mocked_storage.py',
    'tests/alerta/test_reader_confidentiality.py',
)
PENDING = ('postgres-wasm', 'worker-sql-wasm', 'alerta-persistence-wasm',
           'performance-wasm', 'native-postgres', 'pgbouncer', 'real-zabbix',
           'mutation', 'package', 'scanner', 'codeql', 'deployment')


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_child(command: list[str], env: dict[str, str], log: Path) -> int:
    # Private raw logs are never an artifact input. On timeout terminate only
    # this new diagnostic process group; no VM/shared-host process is targeted.
    overflow = threading.Event()
    capture_error = threading.Event()
    with log.open('xb') as stream:
        process = subprocess.Popen(command, cwd=ROOT, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)

        def retain_bounded_log() -> None:
            retained = 0
            try:
                assert process.stdout is not None
                while block := process.stdout.read(65536):
                    remaining = 2 * 1024 * 1024 - retained
                    try:
                        stream.write(block[:max(0, remaining)])
                    except OSError:
                        capture_error.set()  # Keep draining; losing capture is failure.
                    retained += min(len(block), max(0, remaining))
                    if len(block) > remaining:
                        overflow.set()
            except BaseException:
                # A read/assertion/write failure in this thread cannot vanish
                # while the child returns zero. Every reader failure fails phase.
                capture_error.set()

        reader = threading.Thread(target=retain_bounded_log, daemon=True)
        reader.start()
        code = 1
        interrupted = False
        try:
            code = process.wait(timeout=600)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            interrupted = isinstance(error, KeyboardInterrupt)
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            code = 124
        finally:
            reader.join(timeout=5)
            if reader.is_alive():
                # A surviving descendant still owns this new group's pipe.
                os.killpg(process.pid, signal.SIGKILL)
                reader.join(timeout=5)
                code = 125
            if process.stdout is not None:
                process.stdout.close()
        if interrupted:
            raise KeyboardInterrupt
        return 125 if overflow.is_set() or capture_error.is_set() else code


def main() -> int:
    output = ROOT / 'build/hosted-diagnostic'
    # No reused passing record: one directory per fresh checkout/job.
    output.mkdir(parents=True, exist_ok=False)
    public = output / 'public'
    private = output / 'private'
    public.mkdir()
    private.mkdir(mode=0o700)
    report_path = public / 'diagnostic.json'
    report = {
        'schema': 'zabalack-hosted-diagnostic-v1', 'status': 'RUNNING',
        'scope': 'quality + exact unit-mocked targets only; not full CI green',
        'historical_quality_only_run': 'does not prove this proposed workload',
        'started_at_utc': timestamp(), 'phases': [],
        'pending': {name: 'NOT_RUN' for name in PENDING},
        'code_digests': code_digests(ROOT),
        'actions': {key: os.environ.get('GITHUB_' + key.upper(), '') for key in
                    ('repository', 'run_id', 'run_attempt', 'sha', 'workflow_ref', 'workflow_sha')},
        'unit_targets': list(UNIT_TARGETS),
    }

    def save() -> None:
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    save()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('ACK_TEST_') and key not in
           {'ACK_DEPLOYMENT_E2E', 'PYTEST_ADDOPTS', 'GH_TOKEN', 'GITHUB_TOKEN'}}
    env.update(TZ='UTC', ACK_HYPOTHESIS_PROFILE='pr',
               HYPOTHESIS_STORAGE_DIRECTORY=str(private / 'hypothesis'))
    try:
        # run_quality.py produces its own source-bound checks. Remove the
        # committed historical report before launching; it cannot be reused.
        quality_path = ROOT / 'evidence/quality-run.json'
        quality_path.unlink(missing_ok=True)
        code = run_child([sys.executable, 'tools/run_quality.py'], env, private / 'quality.log')
        quality = {'phase': 'quality', 'exit_code': code,
                   'status': 'PASS' if code == 0 else 'FAIL'}
        if quality_path.is_file() and quality_path.stat().st_size <= 1024 * 1024:
            value = json.loads(quality_path.read_text())
            quality['checks'] = value.get('checks', [])
            checks = value.get('checks', [])
            if (value.get('status') != 'PASS' or value.get('tree_unchanged') is not True
                    or value.get('code_digests') != report['code_digests']
                    or not isinstance(checks, list) or len(checks) != 3
                    or {check.get('name') for check in checks} != {'ruff', 'mypy', 'pip-check'}
                    or any(check.get('status') != 'PASS' or type(check.get('exit_code')) is not int
                           or check['exit_code'] != 0 for check in checks)):
                quality['status'] = 'FAIL'
        else:
            quality['status'] = 'FAIL'
        report['phases'].append(quality)
        save()
        # Quality failure does not suppress the independently useful tests.
        raw_xml = private / 'unit-mocked.xml'
        code = run_child([sys.executable, '-m', 'pytest', *UNIT_TARGETS, '-q',
                          '--junitxml=' + str(raw_xml)], env, private / 'unit-mocked.log')
        phase = {'phase': 'unit-mocked', 'layer': 'unit_mocked', 'exit_code': code,
                 'status': 'FAIL', 'counts': {'passed': 0, 'failed': 0, 'skipped': 0}}
        if raw_xml.is_file() and raw_xml.stat().st_size <= 8 * 1024 * 1024:
            redact_junit(raw_xml, public / 'unit-mocked.xml', ROOT)
            rows = cases(public / 'unit-mocked.xml')
            phase['counts'] = {status: sum(row['status'] == status for row in rows)
                               for status in ('passed', 'failed', 'skipped')}
            if code == 0 and phase['counts']['passed'] > 0 and phase['counts']['failed'] == 0:
                phase['status'] = 'INCOMPLETE' if phase['counts']['skipped'] else 'PASS'
        report['phases'].append(phase)
        report['tree_unchanged'] = report['code_digests'] == code_digests(ROOT)
        report['status'] = ('PASS' if report['tree_unchanged'] and
                            all(phase['status'] == 'PASS' for phase in report['phases']) else 'FAIL')
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report['status'] = 'FAIL'
        report['reason'] = type(error).__name__
    finally:
        report['finished_at_utc'] = timestamp()
        save()
    return 0 if report['status'] == 'PASS' else 1


def classify_diagnostic_result(context: dict, job: dict | None, report: dict | None) -> str:
    """Pure post-run classification from authenticated outside readback.

    This helper makes no API calls and activates no operational gate. Its caller
    must establish the authenticity of context/job/report. Workflow success by
    itself is insufficient, including a success containing only skipped jobs.
    """
    selected_mode = str(context.get('diagnostic', ''))
    selected_ref = str(context.get('ref', ''))
    manual = context.get('event_name') == 'workflow_dispatch'
    # GitHub expression string equality ignores case; intent must be no weaker.
    intent = manual and (
        selected_mode.casefold() == 'hosted-x64-quality-unit-mocked'
        or selected_ref.casefold() == 'refs/heads/diagnostic/hosted-x64-20261008')
    if not intent:
        return 'NOT_DIAGNOSTIC'  # Never reinterpret this as a diagnostic PASS.
    reviewed = context.get('reviewed_commit')
    if (selected_mode != 'hosted-x64-quality-unit-mocked'
            or selected_ref != 'refs/heads/diagnostic/hosted-x64-20261008'
            or context.get('repository') != 'steliosx/zab-al-ack'
            or not isinstance(reviewed, str) or len(reviewed) != 40
            or any(character not in '0123456789abcdef' for character in reviewed)
            or reviewed != context.get('sha')):
        return 'REFUSED'
    if (not isinstance(job, dict) or job.get('name') != 'quality-unit-mocked'
            or job.get('status') != 'completed' or job.get('conclusion') == 'skipped'
            or str(job.get('run_id', '')) != str(context.get('run_id', ''))
            or str(job.get('run_attempt', '')) != str(context.get('run_attempt', ''))
            or job.get('head_sha') != reviewed):
        return 'REFUSED'
    if job.get('conclusion') != 'success':
        return 'FAIL' if job.get('conclusion') in ('failure', 'timed_out') else 'REFUSED'
    if not isinstance(report, dict):
        return 'REFUSED'
    actions = report.get('actions')
    if (report.get('schema') != 'zabalack-hosted-diagnostic-v1'
            or report.get('status') != 'PASS' or report.get('tree_unchanged') is not True
            or not isinstance(actions, dict)
            or any(actions.get(key) != str(context.get(key, ''))
                   for key in ('repository', 'run_id', 'run_attempt', 'sha'))):
        return 'REFUSED'
    phases = report.get('phases')
    if (not isinstance(phases, list) or len(phases) != 2
            or any(not isinstance(phase, dict) for phase in phases)
            or {phase.get('phase') for phase in phases} != {'quality', 'unit-mocked'}
            or any(phase.get('status') != 'PASS' or type(phase.get('exit_code')) is not int
                   or phase['exit_code'] != 0 for phase in phases)):
        return 'REFUSED'
    unit = next(phase for phase in phases if phase['phase'] == 'unit-mocked')
    counts = unit.get('counts')
    if (not isinstance(counts, dict) or type(counts.get('passed')) is not int
            or counts['passed'] < 1 or counts.get('failed') != 0 or counts.get('skipped') != 0):
        return 'REFUSED'
    return 'PASS'


ALLOWED_PUBLIC_FILES = frozenset({'diagnostic.json', 'unit-mocked.xml'})
MAX_PUBLIC_ARTIFACT_BYTES = 2 * 1024 * 1024


def verify_public_artifact() -> int:
    """Approve only the fixed small public file set; never fall back to raw output."""
    public = ROOT / 'build/hosted-diagnostic/public'
    # Check every component controlled by this producer, not an external path.
    for directory in (ROOT / 'build', public.parent, public):
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError('artifact_directory_not_regular')
    entries = list(public.iterdir())
    names = {entry.name for entry in entries}
    if 'diagnostic.json' not in names or not names <= ALLOWED_PUBLIC_FILES:
        raise ValueError('artifact_file_allowlist_mismatch')
    total = 0
    for entry in entries:
        metadata = entry.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or not 0 < metadata.st_size <= MAX_PUBLIC_ARTIFACT_BYTES):
            raise ValueError('artifact_file_type_or_size_mismatch')
        total += metadata.st_size
    if total > MAX_PUBLIC_ARTIFACT_BYTES:
        raise ValueError('artifact_aggregate_size_exceeded')
    # Genuine Actions output channel; no caller-selected output override.
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
        stream.write('approved=true\n')
    return 0


if __name__ == '__main__':
    if sys.argv[1:] == ['--verify-public-artifact']:
        try:
            raise SystemExit(verify_public_artifact())
        except (OSError, ValueError, KeyError):
            print('Public artifact refused: fixed allowlist/type/aggregate boundary.')
            raise SystemExit(1)
    if sys.argv[1:]:
        raise SystemExit('Unreviewed diagnostic arguments')
    raise SystemExit(main())
