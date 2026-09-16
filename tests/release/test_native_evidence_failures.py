"""Mocked runner failures: no native service or deployment evidence is created."""
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import native_ci
import update_acceptance
from evidence_utils import code_digests

PASS_XML = '<testsuites><testsuite><testcase classname="synthetic" name="test_T38_check"/></testsuite></testsuites>'


@pytest.fixture(autouse=True)
def relay_context(monkeypatch):
    relay = MagicMock()
    relay.__enter__.return_value = relay
    relay.ports = {'postgres': 43123, 'pgbouncer': 43124}
    monkeypatch.setattr(native_ci, 'DockerLabRelays', lambda: relay)
    return relay


def seed_reports(tmp_path):
    evidence = tmp_path / 'evidence'
    raw = tmp_path / 'build/evidence-raw'
    evidence.mkdir()
    raw.mkdir(parents=True)
    for phase in native_ci.PHASES:
        for directory in (evidence, raw):
            (directory / ('native-' + phase + '.xml')).write_text(PASS_XML)
    prior = {'status': 'PASS', 'phases': [{'status': 'PASS', 'exit_code': 0,
             'junit': 'evidence/native-postgres.xml'}]}
    (evidence / 'native-run.json').write_text(json.dumps(prior))
    return evidence, raw


@pytest.mark.parametrize('context', [
    '', 'null', '{}', '[]', '[null]', '[{}]',
    '[{"Endpoints": null}]', '[{"Endpoints": {}}]',
    '[{"Endpoints": {"docker": null}}]',
    '[{"Endpoints": {"docker": {}}}]',
])
def test_native_runner_classifies_malformed_docker_context_as_unavailable(monkeypatch, context):
    monkeypatch.setattr(native_ci.sys, 'platform', 'linux')
    monkeypatch.setattr(native_ci.shutil, 'which', lambda name: '/synthetic/docker')
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setattr(native_ci.subprocess, 'check_output', lambda *args, **kwargs: context)
    monkeypatch.setattr(native_ci.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('Malformed context reached Docker Compose'))
    with pytest.raises(RuntimeError, match='context inspection was malformed'):
        native_ci.ensure_local_docker()


@pytest.mark.parametrize('host', [None, False, 42, [], {}, 'tcp://remote.invalid:2376'])
def test_native_runner_rejects_nonlocal_context_hosts(monkeypatch, host):
    monkeypatch.setattr(native_ci.sys, 'platform', 'linux')
    monkeypatch.setattr(native_ci.shutil, 'which', lambda name: '/synthetic/docker')
    monkeypatch.delenv('DOCKER_HOST', raising=False)
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    document = json.dumps([{'Endpoints': {'docker': {'Host': host}}}])
    monkeypatch.setattr(native_ci.subprocess, 'check_output', lambda *args, **kwargs: document)
    monkeypatch.setattr(native_ci.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('Nonlocal context reached Docker Compose'))
    with pytest.raises(RuntimeError, match='Remote Docker context refused'):
        native_ci.ensure_local_docker()


@pytest.mark.parametrize('failure', ['docker-unavailable', 'startup', 'startup-cleanup-timeout',
                                   'malformed-junit', 'cleanup-timeout', 'cleanup-error'])
def test_native_attempt_replaces_previous_evidence_before_service_operations(tmp_path, monkeypatch, failure):
    evidence, raw = seed_reports(tmp_path)
    monkeypatch.setattr(native_ci, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['native_ci.py', '--isolated-lab', '--phase', 'postgres'])

    def ensure():
        # Even an unavailable daemon must invalidate old public CI artifacts.
        assert not list(evidence.glob('native-*.xml'))
        assert not list(raw.glob('native-*.xml'))
        assert json.loads((evidence / 'native-run.json').read_text())['status'] == 'RUNNING'
        if failure == 'docker-unavailable':
            raise RuntimeError('Synthetic unavailable daemon')

    def child(command, **kwargs):
        if 'up' in command and failure.startswith('startup'):
            raise subprocess.CalledProcessError(1, command)
        if '-m' in command and 'pytest' in command:
            output = Path(next(value.split('=', 1)[1] for value in command if value.startswith('--junitxml=')))
            output.write_text('<broken' if failure == 'malformed-junit' else PASS_XML)
        if 'stop' in command:
            if failure.endswith('cleanup-timeout'):
                raise subprocess.TimeoutExpired(command, 60)
            if failure == 'cleanup-error':
                return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(native_ci, 'ensure_local_docker', ensure)
    monkeypatch.setattr(native_ci.subprocess, 'run', child)
    assert native_ci.main() == (2 if failure == 'docker-unavailable' else 1)
    report = json.loads((evidence / 'native-run.json').read_text())
    assert report['status'] == ('NOT_RUN' if failure == 'docker-unavailable' else 'FAIL')
    assert not (evidence / 'native-worker.xml').exists()
    if failure.startswith('startup') or failure in {'docker-unavailable', 'malformed-junit'}:
        assert not (evidence / 'native-postgres.xml').exists()
    if failure.endswith('cleanup-timeout'):
        assert report['phases'][0]['shutdown_reason'] == 'TimeoutExpired'


def test_native_success_records_only_fresh_selected_reports(tmp_path, monkeypatch, relay_context):
    evidence, _ = seed_reports(tmp_path)
    monkeypatch.setattr(native_ci, 'ROOT', tmp_path)
    monkeypatch.setattr(native_ci, 'ensure_local_docker', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['native_ci.py', '--isolated-lab', '--phase', 'postgres'])
    monkeypatch.setenv('ACK_TEST_ADMIN_DSN', 'inherited-admin-must-be-replaced')
    monkeypatch.setenv('ACK_TEST_PGBOUNCER_DSN', 'inherited-pool-must-be-replaced')
    monkeypatch.setenv('ACK_TEST_PGBOUNCER_EXPECTED_PORT', '56432')
    monkeypatch.setenv('ACK_TEST_UNRELATED', 'must-be-removed')

    def child(command, **kwargs):
        if '-m' in command and 'pytest' in command:
            relay_context.activate.assert_called_once()
            env = kwargs['env']
            assert env['ACK_TEST_ADMIN_DSN'] == 'postgresql://lab_admin@127.0.0.1:43123/alerta'
            assert env['ACK_TEST_PGBOUNCER_DSN'] == 'postgresql://zabalack_probe@127.0.0.1:43124/alerta'
            assert env['ACK_TEST_PGBOUNCER_EXPECTED_PORT'] == '43124'
            assert env['ACK_TEST_DESTRUCTIVE_LAB'] == 'YES_ISOLATED_LAB'
            assert 'ACK_TEST_UNRELATED' not in env
            output = Path(next(value.split('=', 1)[1] for value in command if value.startswith('--junitxml=')))
            output.write_text(PASS_XML)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(native_ci.subprocess, 'run', child)
    assert native_ci.main() == 0
    report = json.loads((evidence / 'native-run.json').read_text())
    assert report['status'] == 'PASS'
    assert report['phases'][0]['raw_junit_sha256'] == hashlib.sha256(PASS_XML.encode()).hexdigest()
    assert report['phases'][0]['counts'] == {'passed': 1, 'failed': 0, 'skipped': 0}
    assert [path.name for path in evidence.glob('native-*.xml')] == ['native-postgres.xml']


@pytest.mark.parametrize('failure', ['activate', 'close'])
def test_native_relay_failure_fails_phase_and_still_stops_owned_compose(tmp_path, monkeypatch, relay_context, failure):
    evidence, _ = seed_reports(tmp_path)
    monkeypatch.setattr(native_ci, 'ROOT', tmp_path)
    monkeypatch.setattr(native_ci, 'ensure_local_docker', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['native_ci.py', '--isolated-lab', '--phase', 'postgres'])
    if failure == 'activate':
        relay_context.activate.side_effect = RuntimeError('synthetic relay unavailable')
    else:
        relay_context.__exit__.side_effect = RuntimeError('synthetic relay cleanup failed')
    commands = []
    def child(command, **kwargs):
        commands.append(command)
        if '-m' in command and 'pytest' in command:
            Path(next(value.split('=', 1)[1] for value in command if value.startswith('--junitxml='))).write_text(PASS_XML)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(native_ci.subprocess, 'run', child)
    assert native_ci.main() == 1
    report = json.loads((evidence / 'native-run.json').read_text())
    assert report['status'] == 'FAIL' and report['phases'][0]['reason'] == 'RuntimeError'
    assert report['phases'][0]['shutdown_exit_code'] == 0
    assert commands[-1][-3:] == ['stop', '--timeout', '10']
    if failure == 'activate':
        assert not any('pytest' in command for command in commands)


@pytest.mark.parametrize('stage', ['relay-reservation', 'compose-startup', 'relay-activation'])
def test_native_startup_exception_details_are_private_and_cleanup_still_runs(
        tmp_path, monkeypatch, relay_context, capsys, stage):
    evidence, raw = seed_reports(tmp_path)
    monkeypatch.setattr(native_ci, 'ROOT', tmp_path)
    monkeypatch.setattr(native_ci, 'ensure_local_docker', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['native_ci.py', '--isolated-lab', '--phase', 'postgres'])
    private_message = 'synthetic-private-startup-detail'
    private_path = str(tmp_path / 'private-endpoint.sock')
    error = OSError(errno.EADDRINUSE, private_message, private_path)
    if stage == 'relay-reservation':
        relay_context.__enter__.side_effect = error
    elif stage == 'relay-activation':
        relay_context.activate.side_effect = error
    commands = []
    def child(command, **kwargs):
        commands.append(command)
        if 'up' in command and stage == 'compose-startup':
            raise error
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(native_ci.subprocess, 'run', child)
    assert native_ci.main() == 1
    report = json.loads((evidence / 'native-run.json').read_text())
    phase = report['phases'][0]
    assert report['status'] == 'FAIL' and phase['reason'] == 'OSError'
    assert phase['shutdown_exit_code'] == 0
    assert commands[-1][-3:] == ['stop', '--timeout', '10']
    assert not any('pytest' in command for command in commands)
    diagnostic, = raw.glob('native-postgres-*-exception.log')
    details = diagnostic.read_text()
    assert f'stage: {stage}' in details
    assert 'Traceback (most recent call last)' in details
    assert f'[Errno {errno.EADDRINUSE}]' in details
    assert private_message in details and private_path in details
    assert diagnostic.parent == raw and diagnostic.parent != tmp_path / phase['diagnostic_dir']
    if os.name == 'posix':
        assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o600
    public = capsys.readouterr()
    for value in (private_message, private_path, f'[Errno {errno.EADDRINUSE}]'):
        assert value not in public.out + public.err + json.dumps(report)


def test_private_diagnostic_write_failure_preserves_original_reason_and_owned_cleanup(
        tmp_path, monkeypatch, relay_context, capsys):
    evidence, _ = seed_reports(tmp_path)
    monkeypatch.setattr(native_ci, 'ROOT', tmp_path)
    monkeypatch.setattr(native_ci, 'ensure_local_docker', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['native_ci.py', '--isolated-lab', '--phase', 'postgres'])
    relay_context.__enter__.side_effect = OSError(errno.EADDRINUSE, 'synthetic-private-startup')
    def unavailable(*args, **kwargs):
        raise PermissionError('synthetic-private-retention-error')
    monkeypatch.setattr(native_ci.os, 'open', unavailable)
    commands = []
    def child(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(native_ci.subprocess, 'run', child)
    assert native_ci.main() == 1
    phase = json.loads((evidence / 'native-run.json').read_text())['phases'][0]
    assert phase['reason'] == 'OSError' and phase['shutdown_exit_code'] == 0
    assert len(commands) == 1 and commands[0][-3:] == ['stop', '--timeout', '10']
    public = capsys.readouterr()
    assert 'could not be retained' in public.err
    assert 'synthetic-private' not in public.out + public.err


@pytest.mark.parametrize('problem', ['failed-run', 'running-run', 'empty-pass', 'malformed-phases',
                                  'failed-phase', 'cleanup-failed', 'cleanup-missing', 'cleanup-malformed',
                                  'malformed-junit', 'empty-junit',
                                  'failed-junit', 'skipped-junit', 'counts-mismatch'])
def test_acceptance_rejects_failed_or_inconsistent_native_evidence(tmp_path, monkeypatch, problem):
    evidence, _ = seed_reports(tmp_path)
    for name in ('unit', 'wasm'):
        (evidence / (name + '.xml')).write_text(PASS_XML)
    (evidence / 'reference-run.json').write_text(json.dumps({'phases': [
        {'layer': 'unit_mocked', 'junit': 'evidence/unit.xml'},
        {'layer': 'postgres_wasm', 'junit': 'evidence/wasm.xml'}], 'code_digests': code_digests(tmp_path)}))
    phase = {'status': 'PASS', 'exit_code': 0, 'shutdown_exit_code': 0,
             'junit': 'evidence/native-postgres.xml'}
    run = {'status': 'PASS', 'phases': [phase], 'code_digests': code_digests(tmp_path)}
    if problem in {'failed-run', 'running-run'}:
        run['status'] = 'FAIL' if problem == 'failed-run' else 'RUNNING'
    elif problem == 'empty-pass':
        run['phases'] = []
    elif problem == 'malformed-phases':
        run['phases'] = 'invalid'
    elif problem == 'failed-phase':
        phase['status'] = 'FAIL'
    elif problem == 'cleanup-failed':
        phase['shutdown_exit_code'] = 1
    elif problem == 'cleanup-missing':
        del phase['shutdown_exit_code']
    elif problem == 'cleanup-malformed':
        phase['shutdown_exit_code'] = False
    elif problem == 'counts-mismatch':
        phase['counts'] = {'passed': 20, 'failed': 0, 'skipped': 0}
    else:
        contents = {'malformed-junit': '<broken', 'empty-junit': '<testsuites/>',
                    'failed-junit': PASS_XML.replace('/></testsuite>', '><failure/></testcase></testsuite>'),
                    'skipped-junit': PASS_XML.replace('/></testsuite>', '><skipped/></testcase></testsuite>')}[problem]
        (evidence / 'native-postgres.xml').write_text(contents)
    (evidence / 'native-run.json').write_text(json.dumps(run))
    monkeypatch.setattr(update_acceptance, 'ROOT', tmp_path)
    monkeypatch.setattr(update_acceptance.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('Invalid native evidence reached the mapper'))
    with pytest.raises(RuntimeError, match='Native phase'):
        update_acceptance.main()


@pytest.mark.parametrize('status', ['PASS', 'NOT_RUN'])
def test_acceptance_accepts_consistent_native_success_or_explicit_unavailability(tmp_path, monkeypatch, status):
    evidence, _ = seed_reports(tmp_path)
    for name in ('unit', 'wasm'):
        (evidence / (name + '.xml')).write_text(PASS_XML)
    (evidence / 'reference-run.json').write_text(json.dumps({'phases': [
        {'layer': 'unit_mocked', 'junit': 'evidence/unit.xml'},
        {'layer': 'postgres_wasm', 'junit': 'evidence/wasm.xml'}], 'code_digests': code_digests(tmp_path)}))
    phases = [{'status': 'PASS', 'exit_code': 0, 'shutdown_exit_code': 0,
               'junit': 'evidence/native-postgres.xml',
               'counts': {'passed': 1, 'failed': 0, 'skipped': 0}}] if status == 'PASS' else []
    (evidence / 'native-run.json').write_text(json.dumps({'status': status, 'phases': phases,
                                                          'code_digests': code_digests(tmp_path)}))
    monkeypatch.setattr(update_acceptance, 'ROOT', tmp_path)
    commands = []
    def mapped(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(update_acceptance.subprocess, 'run', mapped)
    assert update_acceptance.main() == 0
    assert ('--native-xml' in commands[0]) == (status == 'PASS')
