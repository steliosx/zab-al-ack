"""tools/native_local.py lifecycle regressions (R26).

lab/local_runtime.py, PgBouncer, psql and pytest are mocked; no service
starts and nothing here is native service evidence.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import native_local
from lab_retention import RetentionError

PASS_XML = '<testsuites><testsuite><testcase classname="synthetic" name="test_T38_check"/></testsuite></testsuites>'
POLICY = {'policy': {'postgres': {'major': 18}, 'pgbouncer': {'major': 1, 'minimum_minor': 24}},
          'reviewed': {'postgres': [], 'pgbouncer': []}}


class FakeLab:
    """lab/local_runtime.py as tools/native_local.py drives it through local_runtime()."""

    def __init__(self, root, state='absent', stop_exit=0, stop_leaves='stopped'):
        self.root, self.state, self.stop_exit, self.stop_leaves = root, state, stop_exit, stop_leaves
        self.calls = []

    def __call__(self, action):
        self.calls.append(action)
        if action == 'state':
            return subprocess.CompletedProcess(action, 0, json.dumps({'state': self.state, 'detail': 'synthetic ' + self.state}) + '\n', '')
        if action == 'stop':
            self.state = self.stop_leaves
            return subprocess.CompletedProcess(action, self.stop_exit, '', '')
        assert action == 'start'
        lab = self.root / '.lab'
        lab.mkdir(exist_ok=True)
        (lab / 'ISOLATED_ACK_LAB').write_text('synthetic marker\n')
        (lab / 'postgres.log').write_text(f'synthetic postgres log {len(self.calls)}\n')
        self.state = 'running'
        return subprocess.CompletedProcess(action, 0, '', '')


@pytest.fixture
def harness(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'
    root.mkdir()
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    for name in ('psql', 'postgres'):
        (binaries / name).write_text('synthetic')
    pool = tmp_path / 'pgbouncer'
    pool.write_text('synthetic')
    monkeypatch.setenv('ACK_LAB_PG_BIN', str(binaries))
    monkeypatch.setenv('ACK_LAB_PGBOUNCER_BIN', str(pool))
    monkeypatch.setattr(native_local, 'ROOT', root)
    monkeypatch.setattr(native_local, 'code_digests', lambda tree: {'source': 'a' * 64, 'harness': 'b' * 64})
    monkeypatch.setattr(native_local, 'wait_port', lambda port, timeout=30: True)
    monkeypatch.setattr(native_local, 'port_free', lambda port: True)
    monkeypatch.setattr(native_local, 'reviewed_identities', lambda: (POLICY, 'c' * 64))
    monkeypatch.setattr(native_local, 'package_identity', lambda binary: {'kind': 'unpackaged', 'sha256': 'd' * 64})
    monkeypatch.setattr(native_local.subprocess, 'check_output', lambda command, **kwargs:
                        'PgBouncer 1.24.1\nlibevent 2.1.12-stable\n' if command[0] == str(pool) else 'postgres (PostgreSQL) 18.3\n')
    events = []
    pooler = MagicMock()

    def pgbouncer_start(pool, state, holder=None):
        events.append('pool-start')
        if holder is not None:
            holder['process'] = pooler
        return pooler
    monkeypatch.setattr(native_local, 'pgbouncer_start', pgbouncer_start)
    monkeypatch.setattr(native_local, 'pgbouncer_stop', lambda process, state: events.append('pool-stop'))
    lab = FakeLab(root)
    monkeypatch.setattr(native_local, 'local_runtime', lab)
    box = SimpleNamespace(root=root, lab=lab, events=events, pooler=pooler, pytest_exit=0, pytest_error=None)

    def run(command, **kwargs):
        if '-m' in command and 'pytest' in command:
            events.append('pytest')
            if box.pytest_error is not None:
                raise box.pytest_error
            raw = Path(next(item for item in command if item.startswith('--junitxml=')).split('=', 1)[1])
            raw.write_text(PASS_XML)
            return subprocess.CompletedProcess(command, box.pytest_exit)
        events.append('createdb')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(native_local.subprocess, 'run', run)
    monkeypatch.setattr(native_local, 'run_pytest', lambda command, env, log: run(command).returncode)
    return box


def report(box):
    return json.loads((box.root / 'evidence/native-run.json').read_text())


def manifest(box, directory):
    return json.loads((box.root / directory / 'manifest.json').read_text())


def seed_lab(box, log='seeded postgres log\n'):
    lab = box.root / '.lab'
    lab.mkdir()
    (lab / 'ISOLATED_ACK_LAB').write_text('marker\n')
    (lab / 'postgres.log').write_text(log)
    return lab


def test_consecutive_runs_preserve_the_previous_reports_before_invalidating_them(harness):
    box = harness
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 0
    first = report(box)
    first_bytes = (box.root / 'evidence/native-run.json').read_bytes()
    first_xml = (box.root / 'evidence/native-postgres.xml').read_bytes()
    first_log = (box.root / '.lab/postgres.log').read_bytes()
    assert first['status'] == 'PASS' and first['phases'][0]['lab_reset']['before'] == 'absent'
    assert box.lab.state == 'stopped'
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 0
    second = report(box)
    assert second['status'] == 'PASS' and second != first
    previous = manifest(box, second['retention']['previous_run']['directory'])
    assert previous['files']['evidence__native-run.json']['sha256'] == hashlib.sha256(first_bytes).hexdigest()
    assert previous['files']['evidence__native-postgres.xml']['sha256'] == hashlib.sha256(first_xml).hexdigest()
    assert previous['files']['build__evidence-raw__native-postgres.log']['source'] == 'build/evidence-raw/native-postgres.log'
    # The second run found the first run's stopped cluster, preserved its log and only then reset it.
    reset = second['phases'][0]['lab_reset']
    assert reset['before'] == 'stopped' and reset['after'] == 'absent'
    assert manifest(box, reset['retention']['directory'])['files']['.lab__postgres.log']['sha256'] == hashlib.sha256(first_log).hexdigest()
    assert box.lab.calls.count('start') == 2 and 'stop' in box.lab.calls
    assert (box.root / '.lab/postgres.log').read_bytes() != first_log
    directories = sorted(path.name for path in (box.root / 'build/private/retained-phases').iterdir())
    assert len(directories) == 2


@pytest.mark.parametrize('state', ['unmarked', 'ambiguous'])
def test_unowned_or_ambiguous_lab_refuses_reset_and_starts_nothing(harness, state):
    box = harness
    stranger = box.root / '.lab'
    stranger.mkdir()
    (stranger / 'keep.txt').write_text('not ours')
    box.lab.state = state
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 1
    phase = report(box)['phases'][0]
    assert phase['status'] == 'FAIL' and phase['stage'] == 'lab-reset'
    assert phase['reason'].startswith(f'LabStateError: lab_state_{state}_reset_refused')
    assert (stranger / 'keep.txt').read_text() == 'not ours'
    assert box.lab.calls and set(box.lab.calls) == {'state'}
    assert box.events == []
    assert phase['cleanup'] == {'pgbouncer': {'status': 'NOT_STARTED'},
                                'postgres': {'status': 'REFUSED', 'state': state, 'detail': 'synthetic ' + state}}
    assert phase['shutdown_exit_code'] == 1 and phase['shutdown_reason'] == 'postgres'


def test_running_lab_is_stopped_and_reinspected_before_its_log_is_preserved_and_reset(harness):
    box = harness
    seed_lab(box, 'interrupted run log\n')
    box.lab.state = 'running'
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 0
    phase = report(box)['phases'][0]
    assert box.lab.calls[:4] == ['state', 'stop', 'state', 'start']
    assert phase['lab_reset']['before'] == 'running' and phase['lab_reset']['stop_exit_code'] == 0
    retained = manifest(box, phase['lab_reset']['retention']['directory'])
    assert retained['files']['.lab__postgres.log']['sha256'] == hashlib.sha256(b'interrupted run log\n').hexdigest()
    assert phase['status'] == 'PASS'


@pytest.mark.parametrize('stop_exit,leaves', [(1, 'running'), (0, 'running'), (0, 'ambiguous')],
                         ids=['pg_ctl-failed', 'still-running', 'ambiguous-after-stop'])
def test_failed_or_unverified_stop_refuses_the_reset(harness, stop_exit, leaves):
    box = harness
    lab = seed_lab(box)
    box.lab.state, box.lab.stop_exit, box.lab.stop_leaves = 'running', stop_exit, leaves
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 1
    phase = report(box)['phases'][0]
    assert phase['reason'].startswith('LabStateError: lab_stop_failed_reset_refused')
    assert (lab / 'postgres.log').read_text() == 'seeded postgres log\n'
    assert 'start' not in box.lab.calls and box.events == []
    assert phase['cleanup']['postgres']['status'] in ('FAILED', 'REFUSED') and phase['shutdown_exit_code'] == 1


def test_interrupted_previous_run_is_preserved_stopped_and_replaced(harness):
    box = harness
    evidence = box.root / 'evidence'
    raw = box.root / 'build/evidence-raw'
    evidence.mkdir()
    raw.mkdir(parents=True)
    (evidence / 'native-run.json').write_text(json.dumps({'status': 'RUNNING', 'phases': []}))
    (raw / 'native-postgres.log').write_text('partial pytest output\n')
    (raw / 'native-postgres-0123456789-exception.log').write_text('stage: pytest\n')
    seed_lab(box, 'log of the interrupted cluster\n')
    box.lab.state = 'running'
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 0
    record = report(box)
    previous = manifest(box, record['retention']['previous_run']['directory'])
    assert {'evidence__native-run.json', 'build__evidence-raw__native-postgres.log',
            'build__evidence-raw__native-postgres-0123456789-exception.log', '.lab__postgres.log'} <= set(previous['files'])
    assert record['phases'][0]['lab_reset']['before'] == 'running'
    assert record['status'] == 'PASS'


def test_an_interrupt_during_identity_collection_ends_with_a_fail_record_not_running(harness, monkeypatch):
    """R9: an interrupt outside any phase body is caught by the whole-run guard."""
    box = harness
    monkeypatch.setattr(native_local, 'service_identities', lambda pg, pool: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 130
    value = report(box)
    assert value['status'] == 'FAIL' and value['reason'] == 'interrupted' and value['phases'] == []
    assert value['guard'] == {'reason': 'interrupted', 'cleanup': {'status': 'NOT_STARTED', 'exit_code': 0, 'state': 'absent'}}


def test_a_second_interrupt_during_cleanup_still_stops_the_other_service_and_writes_the_record(harness, monkeypatch):
    """R9: cleanup steps are independent under interruption; the record never stays RUNNING."""
    box = harness
    box.pytest_error = KeyboardInterrupt()

    def stop(process, state):
        box.events.append('pool-stop')
        raise KeyboardInterrupt
    monkeypatch.setattr(native_local, 'pgbouncer_stop', stop)
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 130
    value = report(box)
    assert value['status'] == 'FAIL' and value['reason'] == 'interrupted'
    phase, = value['phases']
    assert phase['cleanup']['pgbouncer'] == {'status': 'INTERRUPTED', 'error': 'interrupted'}
    assert phase['cleanup']['postgres'] == {'status': 'STOPPED', 'exit_code': 0, 'state': 'stopped'}
    assert phase['shutdown_exit_code'] == 1 and phase['shutdown_reason'] == 'pgbouncer' and phase['retention']['status'] == 'RETAINED'
    assert 'stop' in box.lab.calls and box.lab.state == 'stopped'


def test_an_interrupt_inside_pgbouncer_startup_stops_the_spawned_process(harness, monkeypatch):
    """R20: a PgBouncer spawned before the interrupt is handed to cleanup, never recorded as NOT_STARTED."""
    box = harness

    def start(pool, state, holder=None):
        box.events.append('pool-start')
        holder['process'] = box.pooler
        raise KeyboardInterrupt
    monkeypatch.setattr(native_local, 'pgbouncer_start', start)
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 130
    phase, = report(box)['phases']
    assert phase['stage'] == 'pgbouncer-startup' and phase['cleanup']['pgbouncer'] == {'status': 'STOPPED'}
    assert 'pool-stop' in box.events and phase['cleanup']['postgres']['status'] == 'STOPPED'


def test_a_deferred_signal_during_phase_cleanup_is_raised_after_the_record_is_written(harness, monkeypatch):
    """A signal arriving while cleanup runs waits for the record; the guard then ends the run as FAIL."""
    import signal as signal_module
    box = harness
    cancellation = native_local.Cancellation()
    monkeypatch.setattr(native_local, 'Cancellation', lambda: cancellation)
    box.pytest_error = KeyboardInterrupt()

    def stop(process, state):
        box.events.append('pool-stop')
        cancellation._handler(signal_module.SIGTERM, None)   # deferred inside the critical section
    monkeypatch.setattr(native_local, 'pgbouncer_stop', stop)
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 130
    value = report(box)
    phase, = value['phases']
    assert phase['cleanup'] == {'pgbouncer': {'status': 'STOPPED'}, 'postgres': {'status': 'STOPPED', 'exit_code': 0, 'state': 'stopped'}}
    assert value['status'] == 'FAIL' and value['guard']['reason'] == 'interrupted (SIGTERM)'


def test_an_interrupted_phase_is_recorded_cleaned_up_and_the_run_ends_as_fail_not_running(harness):
    """SIGINT during pytest: the record must not stay RUNNING with the phase missing."""
    box = harness
    box.pytest_error = KeyboardInterrupt()
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 130
    record = report(box)
    assert record['status'] == 'FAIL' and record['reason'] == 'interrupted' and record['tree_unchanged'] is True
    phase, = record['phases']
    assert phase['status'] == 'FAIL' and phase['reason'] == 'interrupted' and phase['stage'] == 'pytest'
    assert phase['cleanup'] == {'pgbouncer': {'status': 'STOPPED'}, 'postgres': {'status': 'STOPPED', 'exit_code': 0, 'state': 'stopped'}}
    assert phase['shutdown_exit_code'] == 0 and phase['retention']['status'] == 'RETAINED' and 'finished_at' in phase
    assert 'pool-stop' in box.events and 'stop' in box.lab.calls and box.lab.state == 'stopped'
    # The next run treats it as an interrupted previous run: preserved, then replaced.
    box.pytest_error = None
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 0
    assert report(box)['status'] == 'PASS'


@pytest.mark.parametrize('failing', ['pgbouncer', 'postgres', 'both'])
@pytest.mark.parametrize('phase_failed', [False, True], ids=['phase-passed', 'phase-failed'])
def test_cleanup_steps_are_independent_and_the_original_failure_is_kept(harness, monkeypatch, failing, phase_failed):
    box = harness
    if failing in ('pgbouncer', 'both'):
        def broken(process, state):
            box.events.append('pool-stop')
            raise RuntimeError('pgbouncer_still_listening')
        monkeypatch.setattr(native_local, 'pgbouncer_stop', broken)
    if failing in ('postgres', 'both'):
        box.lab.stop_exit, box.lab.stop_leaves = 1, 'running'
    if phase_failed:
        box.pytest_error = subprocess.TimeoutExpired(['pytest'], 900)
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 1
    phase = report(box)['phases'][0]
    cleanup = phase['cleanup']
    assert 'pool-stop' in box.events and 'stop' in box.lab.calls  # both attempted regardless
    assert cleanup['pgbouncer'] == ({'status': 'FAILED', 'error': 'RuntimeError'} if failing != 'postgres' else {'status': 'STOPPED'})
    if failing == 'pgbouncer':
        assert cleanup['postgres'] == {'status': 'STOPPED', 'exit_code': 0, 'state': 'stopped'}
    else:
        assert cleanup['postgres'] == {'status': 'FAILED', 'exit_code': 1, 'state': 'running'}
    assert phase['shutdown_exit_code'] == 1
    assert phase['shutdown_reason'] == {'pgbouncer': 'pgbouncer', 'postgres': 'postgres', 'both': 'pgbouncer,postgres'}[failing]
    if phase_failed:
        assert phase['reason'].startswith('TimeoutExpired:') and phase['stage'] == 'pytest'
        diagnostic, = (box.root / 'build/evidence-raw').glob('native-postgres-*-exception.log')
        assert 'stage: pytest' in diagnostic.read_text()
    else:
        assert 'reason' not in phase and phase['counts'] == {'passed': 1, 'failed': 0, 'skipped': 0}
    assert phase['status'] == 'FAIL'
    assert phase['retention']['status'] == 'RETAINED'


def test_retention_failure_at_run_start_leaves_previous_reports_and_starts_nothing(harness, monkeypatch, capsys):
    box = harness
    evidence = box.root / 'evidence'
    evidence.mkdir()
    (evidence / 'native-run.json').write_text('{"status": "PASS", "phases": []}')
    (evidence / 'native-postgres.xml').write_text(PASS_XML)
    seed_lab(box)

    def refused(root, run_id, label, sources):
        raise RetentionError('synthetic-retention-failure')
    monkeypatch.setattr(native_local, 'preserve', refused)
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 2
    assert (evidence / 'native-run.json').read_text() == '{"status": "PASS", "phases": []}'
    assert (evidence / 'native-postgres.xml').read_text() == PASS_XML
    assert (box.root / '.lab/postgres.log').read_text() == 'seeded postgres log\n'
    assert box.lab.calls == [] and box.events == []
    out = json.loads(capsys.readouterr().out)
    assert out['status'] == 'NOT_RUN' and out['reason'].startswith('retention_failed') and out['previous_reports'] == 'left in place'


def test_retention_failure_before_reset_refuses_the_reset(harness, monkeypatch):
    box = harness
    lab = seed_lab(box)
    real = native_local.preserve

    def refused(root, run_id, label, sources):
        if label.startswith('pre-reset-'):
            raise RetentionError('synthetic-pre-reset-failure')
        return real(root, run_id, label, sources)
    monkeypatch.setattr(native_local, 'preserve', refused)
    box.lab.state = 'stopped'
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 1
    phase = report(box)['phases'][0]
    assert phase['reason'].startswith('RetentionError: synthetic-pre-reset-failure') and phase['stage'] == 'lab-reset'
    assert (lab / 'postgres.log').read_text() == 'seeded postgres log\n'
    assert 'start' not in box.lab.calls and box.events == []


def test_retention_failure_at_phase_end_fails_a_passing_phase_and_stops_before_the_next_reset(harness, monkeypatch):
    box = harness
    real = native_local.preserve

    def refused(root, run_id, label, sources):
        if label == 'phase-postgres':
            raise RetentionError('synthetic-phase-end-failure')
        return real(root, run_id, label, sources)
    monkeypatch.setattr(native_local, 'preserve', refused)
    assert native_local.main(['--isolated-lab']) == 1
    record = report(box)
    assert len(record['phases']) == 1 and record['status'] == 'FAIL'
    phase = record['phases'][0]
    assert phase['counts']['passed'] == 1 and phase['exit_code'] == 0
    assert phase['status'] == 'FAIL' and phase['retention'] == {'status': 'FAILED', 'error': 'synthetic-phase-end-failure'}
    assert phase['reason'] == 'RetentionError: phase diagnostics were not preserved'
    assert phase['cleanup']['postgres']['status'] == 'STOPPED' and phase['shutdown_exit_code'] == 0
    assert box.lab.calls.count('start') == 1
    assert (box.root / '.lab/postgres.log').exists()


def test_every_phase_end_retains_its_raw_diagnostics_and_the_pool_log(harness):
    box = harness
    state = box.root / 'build/private/pgbouncer-local'
    state.mkdir(parents=True)
    (state / 'pgbouncer.log').write_text('synthetic pool log\n')
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 0
    phase = report(box)['phases'][0]
    retained = manifest(box, phase['retention']['directory'])
    assert set(retained['files']) == {'build__evidence-raw__native-postgres.xml', 'build__evidence-raw__native-postgres.log',
                                      'build__private__pgbouncer-local__pgbouncer.log'}
    assert retained['files']['build__evidence-raw__native-postgres.xml']['sha256'] == phase['raw_junit_sha256']
    assert retained['absent'] == [f"build/evidence-raw/native-postgres-{phase['diagnostic_dir'].rsplit('-', 1)[1]}-exception.log"]


@pytest.mark.parametrize('version,accepted', [
    ('PgBouncer 1.22.0', False), ('PgBouncer 1.23.1', False), ('PgBouncer 1.24.0', True), ('PgBouncer 1.24.1', True),
    ('PgBouncer 1.25.0', True), ('PgBouncer 1.9.0', False), ('PgBouncer 2.0.0', False), ('PgBouncer 1.x', False),
    ('pgbouncer version 1.24.1', False)])
def test_pgbouncer_policy_requires_1_24_or_newer(harness, monkeypatch, version, accepted):
    box = harness
    monkeypatch.setattr(native_local.subprocess, 'check_output', lambda command, **kwargs:
                        version + '\n' if command[0] == os.environ['ACK_LAB_PGBOUNCER_BIN'] else 'postgres (PostgreSQL) 18.3\n')
    code = native_local.main(['--isolated-lab', '--phase', 'postgres'])
    record = report(box)
    if accepted:
        assert code == 0 and record['services']['pgbouncer']['version'] == version
    else:
        assert code == 2 and record['status'] == 'NOT_RUN' and record['reason'].startswith('PgBouncer 1.24 or newer required')
        assert box.lab.calls == [] and box.events == []


def test_postgres_policy_still_requires_major_18(harness, monkeypatch):
    box = harness
    monkeypatch.setattr(native_local.subprocess, 'check_output', lambda command, **kwargs:
                        'PgBouncer 1.24.1\n' if command[0] == os.environ['ACK_LAB_PGBOUNCER_BIN'] else 'postgres (PostgreSQL) 17.6\n')
    assert native_local.main(['--isolated-lab', '--phase', 'postgres']) == 2
    assert report(box)['reason'].startswith('PostgreSQL 18 required') and box.lab.calls == []


DPKG = {'kind': 'dpkg', 'package': 'postgresql-18', 'version': '18.3-1.pgdg24.04+1', 'architecture': 'amd64', 'sha256': 'e' * 64}


@pytest.mark.parametrize('identity,reviewed,expected', [
    ({'kind': 'unpackaged', 'sha256': 'd' * 64}, [], 'UNREVIEWED'),
    ({'kind': 'unpackaged', 'sha256': 'd' * 64}, [{'kind': 'binary', 'sha256': 'd' * 64}], 'REVIEWED'),
    ({'kind': 'unpackaged', 'sha256': 'd' * 64}, [{'kind': 'binary', 'sha256': 'f' * 64}], 'UNREVIEWED'),
    (DPKG, [], 'refused'),
    (DPKG, [dict(DPKG, version='18.2-1.pgdg24.04+1')], 'refused'),
    (DPKG, [dict(DPKG, architecture='arm64')], 'refused'),
    (DPKG, [{'kind': 'dpkg', 'package': 'postgresql-18', 'version': '18.3-1.pgdg24.04+1', 'architecture': 'amd64'}], 'REVIEWED'),
    (DPKG, [{'kind': 'binary', 'sha256': 'e' * 64}], 'REVIEWED'),
], ids=['source-unreviewed', 'source-reviewed-digest', 'source-other-digest', 'package-unreviewed', 'package-other-version',
        'package-other-architecture', 'package-reviewed', 'package-reviewed-by-digest'])
def test_packaged_services_must_be_reviewed_identities_and_source_builds_are_labelled(harness, monkeypatch, identity, reviewed, expected):
    box = harness
    record = {'policy': POLICY['policy'], 'reviewed': {'postgres': reviewed, 'pgbouncer': reviewed}}
    monkeypatch.setattr(native_local, 'reviewed_identities', lambda: (record, 'c' * 64))
    monkeypatch.setattr(native_local, 'package_identity', lambda binary: dict(identity))
    code = native_local.main(['--isolated-lab', '--phase', 'postgres'])
    result = report(box)
    if expected == 'refused':
        assert code == 2 and result['status'] == 'NOT_RUN'
        assert result['reason'].startswith('postgres_package_not_reviewed: postgresql-18 18.3-1.pgdg24.04+1 amd64')
        assert box.lab.calls == [] and box.events == []
    else:
        assert code == 0
        for name in ('postgres', 'pgbouncer'):
            assert result['services'][name]['review'] == expected and result['services'][name]['identity'] == identity
        assert result['services']['identities_file'] == 'lab/native_service_identities.json'
        assert result['services']['identities_sha256'] == 'c' * 64
        assert result['not_docker'] is True


def test_shipped_identities_file_carries_the_policy_and_no_unreviewed_entries():
    record = json.loads((Path(__file__).resolve().parents[2] / 'lab/native_service_identities.json').read_text())
    assert record['policy'] == POLICY['policy']
    for name in ('postgres', 'pgbouncer'):
        for entry in record['reviewed'][name]:
            assert entry['kind'] in ('dpkg', 'binary')
            if entry['kind'] == 'dpkg':
                assert set(entry) >= {'package', 'version', 'architecture'}
            else:
                assert len(entry['sha256']) == 64


def test_pgbouncer_start_refuses_a_live_pid_file_before_touching_its_state(tmp_path, monkeypatch):
    state = tmp_path / 'pgbouncer-local'
    state.mkdir()
    (state / 'pgbouncer.pid').write_text(f'{os.getpid()}\n')
    (state / 'users.txt').write_text('synthetic')
    monkeypatch.setattr(native_local, 'port_free', lambda port: True)
    with pytest.raises(RuntimeError, match='pool_pid_alive_before_start'):
        native_local.pgbouncer_start(tmp_path / 'pgbouncer', state)
    assert (state / 'pgbouncer.pid').read_text() == f'{os.getpid()}\n' and (state / 'users.txt').exists()
    assert not (state / 'pgbouncer.ini').exists()
