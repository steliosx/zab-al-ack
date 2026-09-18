#!/usr/bin/env python3
"""Execute and record the disposable real-Zabbix suite, or record NOT_RUN.

Only --run owns an execution: importing arbitrary old JUnit with --junit is
rejected. The runner binds the pre-execution tree, uses a fresh private report,
checks the final tree and pytest exit code, and publishes only validated evidence.
No prior description, version, token, fixture path or free-text label is inherited.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evidence_utils import redact_junit, cases, code_digests  # noqa: E402
from lab.real_zabbix_safety import validate_lab_fixture  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RECORD = 'evidence/real-zabbix-run.json'
PUBLIC = 'evidence/real-zabbix.xml'
EMPTY = {'passed': 0, 'failed': 0, 'skipped': 0}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def read_terms(path: Path) -> list[str]:
    if path.stat().st_size > 65536:
        raise ValueError('forbidden input exceeds size limit')
    return [line.strip() for line in path.read_text().splitlines() if line.strip()]


def forbidden_terms(fixture: Path, literals: list[str], files: list[Path]) -> list[str]:
    # Validate the literal loopback boundary before opening named token files.
    data = json.loads(fixture.read_text())
    validate_lab_fixture(data)
    terms = [*literals, str(fixture), fixture.name]
    secret_paths = [data.get('token_file')]
    outside = data.get('outside_scope_source')
    if isinstance(outside, dict) and outside.get('witness_token_file'):
        secret_paths.append(outside['witness_token_file'])
    for value in secret_paths:
        if not isinstance(value, str) or not value:
            raise ValueError('fixture token reference missing')
        path = Path(value)
        terms += [str(path), path.name, *read_terms(path)]
    for path in files:
        terms += [str(path), path.name, *read_terms(path)]
    return [term for term in terms if term]


PUBLIC_EVIDENCE_MODE = 0o644


def write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Consumers see complete JSON; status remains RUNNING until XML is complete.
    # mkstemp creates 0600 and os.replace keeps it: a published record must stay
    # readable to every consumer of the checkout and of the exported archives.
    fd, name = tempfile.mkstemp(prefix='.real-zabbix-record-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(record, indent=2) + '\n')
        os.chmod(name, PUBLIC_EVIDENCE_MODE)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def checked_private_path(path: Path) -> Path:
    """Refuse redirected storage; do not resolve away a symlink or traversal."""
    if '..' in path.parts:
        raise ValueError('private_path_traversal')
    path = path.absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError('private_path_redirected')
    return path


def private_file(path: Path, *, create: bool = False) -> int:
    """Open only a single owned regular file, never follow/chmod a link."""
    checked_private_path(path)
    flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid())):
            raise ValueError('private_file_not_owned_regular')
        if hasattr(os, 'fchmod'):
            os.fchmod(fd, 0o600)
        else:
            os.chmod(path, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


class PrivateAttempt:
    """Preserve every attempt; a RUNNING manifest is explicitly unfinished."""

    def __init__(self, root: Path, before: dict[str, str]):
        parent = checked_private_path(root / 'build/evidence-raw')
        # Create missing ancestors privately, but never chmod existing ones.
        missing = []
        cursor = parent
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for path in reversed(missing):
            path.mkdir(mode=0o700)
        self.path = Path(tempfile.mkdtemp(prefix='real-zabbix-', dir=parent))
        self.record = {'schema_version': 1, 'status': 'RUNNING', 'started_at': utc_now(),
                       'root': str(root), 'code_digests_before': before,
                       'command': None, 'pytest_exit_code': None, 'published': False, 'events': []}
        self.checkpoint('PREPARING')

    def checkpoint(self, phase: str) -> None:
        self.record['events'].append({'phase': phase, 'at': utc_now()})
        checked_private_path(self.path / 'manifest.json')
        fd, name = tempfile.mkstemp(prefix='.manifest-', dir=self.path)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(json.dumps(self.record, indent=2) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path / 'manifest.json')
        finally:
            Path(name).unlink(missing_ok=True)

    def finish(self, root: Path, record: dict) -> None:
        files = {}
        for name in ('run.xml', 'console.log', 'public.xml'):
            path = self.path / name
            if not path.exists() and not path.is_symlink():
                continue
            with os.fdopen(private_file(path), 'rb') as stream:
                data = stream.read()
            files[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        self.record.update(status=record['status'], reason=record.get('reason'), published=False,
                           finished_at=utc_now(), code_digests_after=code_digests(root),
                           pytest_exit_code=record.get('pytest_exit_code'), files=files)
        self.checkpoint('FINISHED')


def publish_xml(staged: Path, public: Path) -> None:
    """Retain the private staging bytes; publish a separate atomic 0644 copy."""
    fd, name = tempfile.mkstemp(prefix='.real-zabbix-xml-', dir=public.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(staged.read_bytes())
        os.chmod(name, PUBLIC_EVIDENCE_MODE)
        os.replace(name, public)
    finally:
        Path(name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--run', action='store_true', help='own a fresh, bounded pytest execution of tests/real_zabbix')
    group.add_argument('--not-run', metavar='PRIVATE_REASON', help='record unavailability; the supplied text is not exported')
    group.add_argument('--junit', type=Path, help=argparse.SUPPRESS)  # fail closed for the retired importer
    parser.add_argument('--fixture', type=Path, default=None, help='isolated fixture; defaults to ACK_TEST_REAL_ZABBIX_FIXTURE')
    parser.add_argument('--forbid', action='append', default=[], metavar='TEXT')
    parser.add_argument('--forbid-file', action='append', default=[], type=Path, metavar='PATH')
    parser.add_argument('--timeout', type=int, default=300, help='pytest deadline in seconds (1..1800; default 300)')
    args = parser.parse_args()
    record_path, public = ROOT / RECORD, ROOT / PUBLIC
    record = {'schema_version': 2, 'layer': 'real_zabbix_7_0', 'runner': 'tools/record_real_zabbix.py --run',
              'started_at': utc_now(), 'status': 'RUNNING', 'counts': EMPTY.copy()}
    # Invalidate prior public evidence before parsing fixtures, reading private
    # inputs, starting pytest, or attempting to redact its output.
    public.unlink(missing_ok=True)
    write_record(record_path, record)
    attempt = None

    def finish(status: str, reason: str, exit_code: int = 1) -> int:
        public.unlink(missing_ok=True)
        record.pop('junit', None)
        record.update(status=status, reason=reason, recorded_at=utc_now())
        if attempt is not None:
            try:
                attempt.finish(ROOT, record)
            except (OSError, ValueError):
                # Keep the earlier unfinished manifest and every available raw
                # byte. A failed private finalization can never publish PASS.
                record.update(status='FAIL', reason='private_retention_failed')
                exit_code = 1
        write_record(record_path, record)
        print(json.dumps({'status': record['status'], 'reason': record['reason']}))
        return exit_code

    try:
        record['code_digests'] = code_digests(ROOT)
        if args.not_run is not None:
            return finish('NOT_RUN', 'operator_declared_unavailable', 0)
        if args.junit is not None:
            return finish('FAIL', 'unowned_junit_import_disabled')
        attempt = PrivateAttempt(ROOT, record['code_digests'])
        if not 1 <= args.timeout <= 1800:
            return finish('FAIL', 'invalid_timeout')
        if os.environ.get('ACK_TEST_REAL_ZABBIX_LAB') != 'YES_ISOLATED_LAB':
            return finish('FAIL', 'isolated_lab_opt_in_required')
        fixture = args.fixture or (Path(os.environ['ACK_TEST_REAL_ZABBIX_FIXTURE'])
                                  if os.environ.get('ACK_TEST_REAL_ZABBIX_FIXTURE') else None)
        if fixture is None:
            return finish('FAIL', 'isolated_fixture_required')
        fixture = fixture.resolve()
        terms = forbidden_terms(fixture, args.forbid, args.forbid_file)
        # Runtime context is never exported. A changed fixture or secret input
        # during execution cannot silently change what the leak checker covers.
        private_inputs = terms.copy()
        before = record['code_digests']
        write_record(record_path, record)
        raw, staged = attempt.path / 'run.xml', attempt.path / 'public.xml'
        # Precreate only this fresh attempt's report, so pytest's ordinary open
        # preserves 0600 even when the caller has a permissive umask.
        os.close(private_file(raw, create=True))
        env = os.environ.copy()
        for key in ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'PYTEST_CURRENT_TEST'):
            env.pop(key, None)
        # Bind imported implementation bytes too: an installed editable package
        # or inherited PYTHONPATH must not redirect zabalack to another tree.
        env.update(TZ='UTC', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONPATH=str(ROOT / 'src'),
                   ACK_TEST_REAL_ZABBIX_FIXTURE=str(fixture))
        command = [sys.executable, '-m', 'pytest', '-p', 'pytest_asyncio.plugin', '-p', 'no:cacheprovider',
                   'tests/real_zabbix', '-ra', '--junitxml=' + str(raw)]
        attempt.record['command'] = command
        attempt.record['timeout_seconds'] = args.timeout
        attempt.checkpoint('EXECUTING')
        # Output may contain private fixture material; never relay it into
        # the public report or print a TimeoutExpired/CalledProcessError.
        with os.fdopen(private_file(attempt.path / 'console.log', create=True), 'wb') as console:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=console, stderr=subprocess.STDOUT,
                                    timeout=args.timeout, check=False)
        record['pytest_exit_code'] = result.returncode
        if code_digests(ROOT) != before:
            return finish('FAIL', 'tree_changed_during_run')
        if forbidden_terms(fixture, args.forbid, args.forbid_file) != private_inputs:
            return finish('FAIL', 'private_inputs_changed_during_run')
        if not raw.exists() or raw.stat().st_size == 0:
            return finish('FAIL', 'fresh_junit_missing', result.returncode if result.returncode > 0 else 1)
        os.close(private_file(raw))
        os.close(private_file(staged, create=True))
        redact_junit(raw, staged, ROOT)
        rows = cases(staged)
        if not rows or any(not row['nodeid'].startswith('tests.real_zabbix.') for row in rows):
            return finish('FAIL', 'junit_case_scope_invalid')
        counts = {status: sum(row['status'] == status for row in rows) for status in EMPTY}
        record['counts'] = counts
        versions = sorted({p.get('value', '') for p in ET.parse(staged).iter('property')
                           if p.get('name') == 'zabbix_api_version'})
        if any(re.fullmatch(r'7\.0\.[0-9]+', value) is None for value in versions):
            return finish('FAIL', 'junit_version_property_invalid')
        record['observed_api_versions'] = versions
        record['status'] = 'PASS' if result.returncode == 0 and counts['passed'] == len(rows) else 'FAIL'
        record['recorded_at'] = utc_now()
        record['junit'] = PUBLIC
        if record['status'] == 'FAIL':
            record['reason'] = 'pytest_failed_or_incomplete'
        public_json = json.dumps(record, indent=2)
        if any(term in staged.read_text() or term in public_json for term in terms):
            record.pop('observed_api_versions', None)
            return finish('FAIL', 'forbidden_material_in_public_evidence')
        # Preserve and hash raw/staged files before publishing. If publication
        # fails, the exception path invalidates public evidence and updates the
        # private attempt to FAIL. Nothing deletes its diagnostic bytes.
        attempt.finish(ROOT, record)
        publish_xml(staged, public)
        write_record(record_path, record)
        # FINISHED describes retained execution bytes, not successful export.
        # An unhandled interruption before this checkpoint leaves published=False.
        attempt.record['published'] = True
        attempt.checkpoint('PUBLISHED')
        print(json.dumps({'status': record['status'], 'counts': counts, 'pytest_exit_code': result.returncode}))
        return result.returncode if result.returncode > 0 else (0 if record['status'] == 'PASS' else 1)
    except subprocess.TimeoutExpired:
        return finish('FAIL', 'pytest_deadline_exceeded')
    except (OSError, ValueError, ET.ParseError):
        return finish('FAIL', 'execution_or_private_input_invalid')
    except KeyboardInterrupt:
        return finish('FAIL', 'execution_interrupted', 130)
    except Exception:
        # Unexpected errors cannot leave newly unchecked XML or an old PASS.
        return finish('FAIL', 'recorder_internal_error')


if __name__ == '__main__':
    raise SystemExit(main())
