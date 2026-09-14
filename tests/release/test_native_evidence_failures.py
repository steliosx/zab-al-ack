"""Mocked runner failures: no native service or deployment evidence is created."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import native_ci
import update_acceptance
from evidence_utils import code_digests

PASS_XML = '<testsuites><testsuite><testcase classname="synthetic" name="test_T38_check"/></testsuite></testsuites>'


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


def test_native_success_records_only_fresh_selected_reports(tmp_path, monkeypatch):
    evidence, _ = seed_reports(tmp_path)
    monkeypatch.setattr(native_ci, 'ROOT', tmp_path)
    monkeypatch.setattr(native_ci, 'ensure_local_docker', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['native_ci.py', '--isolated-lab', '--phase', 'postgres'])

    def child(command, **kwargs):
        if '-m' in command and 'pytest' in command:
            output = Path(next(value.split('=', 1)[1] for value in command if value.startswith('--junitxml=')))
            output.write_text(PASS_XML)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(native_ci.subprocess, 'run', child)
    assert native_ci.main() == 0
    report = json.loads((evidence / 'native-run.json').read_text())
    assert report['status'] == 'PASS'
    assert report['phases'][0]['counts'] == {'passed': 1, 'failed': 0, 'skipped': 0}
    assert [path.name for path in evidence.glob('native-*.xml')] == ['native-postgres.xml']


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
