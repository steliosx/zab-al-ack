"""Gate runners give every pytest invocation an owned basetemp and retain it; synthetic children only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

import quickstart
import run_property_nightly
import run_reference_suite
import security_scan


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    root = tmp_path / 'system-temp'
    root.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(root))
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
    labels = sorted(json.loads(record['owner'])['label'] for record in records.values())
    assert labels == sorted('reference-' + phase for phase in (
        'unit-mocked', 'postgres-wasm', 'worker-sql-wasm', 'alerta-persistence-wasm', 'performance-wasm', 'optional-services'))
    # The published record names the basetemp generically, never its random local path.
    report = json.loads((tmp_path / 'evidence/reference-run.json').read_text())
    shown = [part for phase in report['phases'] for part in phase['command'] if part.startswith('--basetemp')]
    assert shown == ['--basetemp=<owned>'] * 6


def test_property_run_gets_an_owned_basetemp_retained(tmp_path, temp_root, monkeypatch):
    commands = []
    monkeypatch.setattr(run_property_nightly, 'ROOT', tmp_path)
    monkeypatch.setattr(run_property_nightly.subprocess, 'run', writing_child(commands))
    assert run_property_nightly.main(['--isolated-lab']) == 1
    assert basetemp_of(commands[0]).parent.parent == temp_root
    (record,) = retained(tmp_path / 'build/evidence-raw', 'property-nightly-').values()
    assert record['status'] == 'COMPLETE' and json.loads(record['owner'])['label'] == 'property-nightly'


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
    assert sorted(json.loads(record['owner'])['label'] for record in records.values()) == ['quickstart-2', 'quickstart-3']


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
