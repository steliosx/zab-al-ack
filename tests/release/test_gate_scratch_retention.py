"""Gate runners give every pytest invocation an owned basetemp and retain it; synthetic children only."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

import quickstart
import retained_temp
import run_property_nightly
import run_reference_suite
import security_scan


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    root = tmp_path / 'system-temp'
    root.mkdir()
    monkeypatch.setattr(retained_temp, 'ROOT_PARENT', str(root))
    return root


def basetemp_of(command):
    values = [part.split('=', 1)[1] for part in command if part.startswith('--basetemp=')]
    assert len(values) == 1, command
    return Path(values[0])


def writing_child(commands):
    """A synthetic pytest child that leaves one scratch file in its basetemp."""
    def child(command, **kwargs):
        commands.append(command)
        if 'pytest' in command:
            basetemp = basetemp_of(command)
            (basetemp / 'test_x0').mkdir(parents=True)
            (basetemp / 'test_x0/out.txt').write_text(basetemp.parent.name)
        return subprocess.CompletedProcess(command, 1)
    return child


def retained(raw, prefix):
    records = {}
    for path in sorted((raw / 'basetemp').glob(prefix + '*/retention.json')):
        records[path.parent.name] = json.loads(path.read_text())
    return records


def test_reference_phases_each_get_a_distinct_owned_basetemp_retained(tmp_path, temp_root, monkeypatch):
    (tmp_path / 'build/evidence-raw').mkdir(parents=True)
    commands = []
    monkeypatch.setattr(run_reference_suite, 'ROOT', tmp_path)
    monkeypatch.setattr(run_reference_suite.subprocess, 'run', writing_child(commands))
    monkeypatch.setattr(sys, 'argv', ['run_reference_suite.py', '--wasm'])
    assert run_reference_suite.main() == 1
    assert len(commands) == 6
    roots = [basetemp_of(command).parent for command in commands]
    assert len(set(roots)) == 6 and all(root.parent == temp_root for root in roots)
    records = retained(tmp_path / 'build/evidence-raw', '')
    assert len(records) == 6 and all(record['status'] == 'COMPLETE' for record in records.values())
    for name, record in records.items():
        # Each copy is named by its phase and owned root, and holds that root's scratch.
        root_name = (tmp_path / 'build/evidence-raw/basetemp' / name / 'scratch/test_x0/out.txt').read_text()
        assert list(record['files']) == ['test_x0/out.txt'] and name.endswith('-' + root_name)
    labels = sorted(record['custody']['label'] for record in records.values())
    assert labels == sorted('reference-' + phase for phase in (
        'unit-mocked', 'postgres-wasm', 'worker-sql-wasm', 'alerta-persistence-wasm', 'performance-wasm', 'optional-services'))
    # The published record names the basetemp generically, never its random local path.
    report = json.loads((tmp_path / 'evidence/reference-run.json').read_text())
    assert [phase['retention'] for phase in report['phases']] == ['COMPLETE'] * 6
    assert report['scratch_retention'] == 'COMPLETE'
    shown = [part for phase in report['phases'] for part in phase['command'] if part.startswith('--basetemp')]
    assert shown == ['--basetemp=<owned>'] * 6


def test_property_run_gets_an_owned_basetemp_retained(tmp_path, temp_root, monkeypatch):
    commands = []
    monkeypatch.setattr(run_property_nightly, 'ROOT', tmp_path)
    monkeypatch.setattr(run_property_nightly.subprocess, 'run', writing_child(commands))
    assert run_property_nightly.main(['--isolated-lab']) == 1
    assert basetemp_of(commands[0]).parent.parent == temp_root
    (record,) = retained(tmp_path / 'build/evidence-raw', 'property-nightly-').values()
    assert record['status'] == 'COMPLETE' and record['custody']['label'] == 'property-nightly'


def test_quickstart_retains_only_its_pytest_invocations(tmp_path, temp_root, monkeypatch):
    commands = []
    child = writing_child(commands)

    def succeed(command, **kwargs):
        child(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(quickstart, 'ROOT', tmp_path)
    monkeypatch.setattr(quickstart.subprocess, 'run', succeed)
    monkeypatch.setattr(sys, 'argv', ['quickstart.py', '--isolated-lab'])
    assert quickstart.main() == 0
    assert [any(part.startswith('--basetemp=') for part in command) for command in commands] == [False, False, True, True]
    records = retained(tmp_path / 'build/evidence-raw', 'quickstart-')
    assert sorted(record['custody']['label'] for record in records.values()) == ['quickstart-2', 'quickstart-3']


def test_secret_scan_keeps_the_scanned_public_copy(tmp_path, monkeypatch):
    repository = tmp_path / 'source'
    raw = repository / 'build/evidence-raw'
    raw.mkdir(parents=True)
    (repository / 'README.md').write_text('synthetic source')
    executable = tmp_path / 'synthetic-scanner'
    executable.write_text('#!/usr/bin/env python3\nimport pathlib,sys\n'
                          'pathlib.Path(sys.argv[sys.argv.index("--report-path")+1]).write_text("[]")\n')
    executable.chmod(0o700)
    monkeypatch.setattr(security_scan, 'ROOT', repository)
    assert security_scan.scan_secrets(str(executable), raw)['status'] == 'PASS'
    (kept,) = (repository / 'build').glob('public-source-*')
    assert (kept / 'README.md').read_text() == 'synthetic source'


def passing_child(commands):
    """A synthetic child whose pytest writes one passing JUnit case and one scratch file."""
    def child(command, **kwargs):
        commands.append(command)
        junit = [part.split('=', 1)[1] for part in command if part.startswith('--junitxml=')][0]
        target = Path(junit) if Path(junit).is_absolute() else kwargs['cwd'] / junit
        target.write_text('<testsuite><testcase classname="tests.test_x" name="test_x" /></testsuite>')
        basetemp = basetemp_of(command)
        basetemp.mkdir(parents=True)
        (basetemp / 'out.txt').write_text('kept')
        return subprocess.CompletedProcess(command, 0)
    return child


def test_incomplete_reference_retention_alone_prevents_a_pass(tmp_path, temp_root, monkeypatch):
    (tmp_path / 'build/evidence-raw').mkdir(parents=True)
    commands = []
    real = retained_temp.retain_status
    monkeypatch.setattr(run_reference_suite, 'ROOT', tmp_path)
    monkeypatch.setattr(run_reference_suite.subprocess, 'run', passing_child(commands))
    monkeypatch.setattr(retained_temp, 'retain_status', lambda destination:
                        'INCOMPLETE' if destination.name.startswith('reference-unit-mocked-') else real(destination))
    monkeypatch.setattr(sys, 'argv', ['run_reference_suite.py'])
    assert run_reference_suite.main() == 1
    report = json.loads((tmp_path / 'evidence/reference-run.json').read_text())
    # Every phase's test status is kept as observed; only retention blocks the pass.
    assert [phase['exit_code'] for phase in report['phases']] == [0, 0]
    assert [phase['retention'] for phase in report['phases']] == ['INCOMPLETE', 'COMPLETE']
    assert report['scratch_retention'] == 'INCOMPLETE'
    assert report['all_executed_phases_succeeded'] is False and report['status'] == 'FAIL'


def test_a_timed_out_reference_phase_is_still_retained(tmp_path, temp_root, monkeypatch):
    (tmp_path / 'build/evidence-raw').mkdir(parents=True)

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 600)

    monkeypatch.setattr(run_reference_suite, 'ROOT', tmp_path)
    monkeypatch.setattr(run_reference_suite.subprocess, 'run', timeout)
    monkeypatch.setattr(sys, 'argv', ['run_reference_suite.py'])
    assert run_reference_suite.main() == 1
    report = json.loads((tmp_path / 'evidence/reference-run.json').read_text())
    assert [phase['exit_code'] for phase in report['phases']] == [124, 124]
    assert [phase['retention'] for phase in report['phases']] == ['NOT_CREATED', 'NOT_CREATED']


def test_incomplete_property_retention_alone_prevents_a_pass(tmp_path, temp_root, monkeypatch):
    commands = []
    monkeypatch.setattr(run_property_nightly, 'ROOT', tmp_path)
    monkeypatch.setattr(run_property_nightly.subprocess, 'run', passing_child(commands))
    monkeypatch.setattr(retained_temp, 'retain_status', lambda destination: 'INCOMPLETE')
    assert run_property_nightly.main(['--isolated-lab']) == 1
    record = json.loads((tmp_path / 'evidence/property-nightly-run.json').read_text())
    assert record['exit_code'] == 0 and record['counts'] == {'passed': 1, 'skipped': 0, 'failed': 0}
    assert record['retention'] == 'INCOMPLETE' and record['status'] == 'FAIL'


def test_quickstart_reports_incomplete_retention_without_hiding_a_test_failure(tmp_path, temp_root, monkeypatch):
    monkeypatch.setattr(quickstart, 'ROOT', tmp_path)
    monkeypatch.setattr(retained_temp, 'retain_status', lambda destination: 'INCOMPLETE')
    monkeypatch.setattr(sys, 'argv', ['quickstart.py', '--isolated-lab'])
    monkeypatch.setattr(quickstart.subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(command, 0))
    assert quickstart.main() == 3
    monkeypatch.setattr(quickstart.subprocess, 'run', lambda command, **kwargs: subprocess.CompletedProcess(
        command, 5 if 'pytest' in command else 0))
    assert quickstart.main() == 5


def interrupting_child(error):
    """A synthetic child that writes scratch, then ends by timeout or interruption."""
    def child(command, **kwargs):
        if 'pytest' in command:
            basetemp = basetemp_of(command)
            basetemp.mkdir(parents=True)
            (basetemp / 'partial.txt').write_text('written before the interruption')
        raise error(command, 1) if error is subprocess.TimeoutExpired else error()
    return child


def custody_records(raw):
    return [json.loads(path.read_text()) for path in sorted((raw / 'basetemp').glob('*/custody.json'))]


def test_an_interrupted_reference_phase_is_retained_and_still_interrupts(tmp_path, temp_root, monkeypatch):
    (tmp_path / 'build/evidence-raw').mkdir(parents=True)
    monkeypatch.setattr(run_reference_suite, 'ROOT', tmp_path)
    monkeypatch.setattr(run_reference_suite.subprocess, 'run', interrupting_child(KeyboardInterrupt))
    monkeypatch.setattr(sys, 'argv', ['run_reference_suite.py'])
    with pytest.raises(KeyboardInterrupt):
        run_reference_suite.main()
    (custody,) = custody_records(tmp_path / 'build/evidence-raw')
    assert custody['status'] == 'COMPLETE' and custody['label'] == 'reference-unit-mocked'
    (copy,) = (tmp_path / 'build/evidence-raw/basetemp').glob('*/scratch/partial.txt')
    assert copy.read_text() == 'written before the interruption'


@pytest.mark.parametrize('runner', ['property', 'quickstart'])
def test_a_timed_out_run_is_retained_and_the_timeout_still_propagates(tmp_path, temp_root, monkeypatch, runner):
    module = run_property_nightly if runner == 'property' else quickstart
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    child = interrupting_child(subprocess.TimeoutExpired)
    if runner == 'quickstart':
        # The two non-pytest steps succeed; the first pytest step times out.
        monkeypatch.setattr(module.subprocess, 'run', lambda command, **kwargs: child(command) if 'pytest' in command
                            else subprocess.CompletedProcess(command, 0))
        monkeypatch.setattr(sys, 'argv', ['quickstart.py', '--isolated-lab'])
    else:
        monkeypatch.setattr(module.subprocess, 'run', child)
    with pytest.raises(subprocess.TimeoutExpired):
        module.main(['--isolated-lab']) if runner == 'property' else module.main()
    (custody,) = custody_records(tmp_path / 'build/evidence-raw')
    assert custody['status'] == 'COMPLETE'
    (copy,) = (tmp_path / 'build/evidence-raw/basetemp').glob('*/scratch/partial.txt')
    assert copy.read_text() == 'written before the interruption'


def test_the_custody_record_exists_before_the_child_starts(tmp_path, temp_root, monkeypatch):
    seen = []

    def child(command, **kwargs):
        seen.extend(custody_records(tmp_path / 'build/evidence-raw'))
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(run_property_nightly, 'ROOT', tmp_path)
    monkeypatch.setattr(run_property_nightly.subprocess, 'run', child)
    run_property_nightly.main(['--isolated-lab'])
    assert [record['status'] for record in seen] == ['PENDING']
    assert seen[0]['root'] == str(Path(seen[0]['root'])) and len(seen[0]['invocation']) == 32
