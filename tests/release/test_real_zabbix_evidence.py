"""Synthetic report failures; no real-Zabbix service or permission evidence."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import update_acceptance
from evidence_utils import code_digests

PASS_XML = ('<testsuites><testsuite><testcase classname="tests.real_zabbix.test_contract" '
            'name="test_T13_real_exact_original_event_read"/></testsuite></testsuites>')
COUNTS = {'passed': 1, 'failed': 0, 'skipped': 0}


def seed_reference(tmp_path):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    for name in ('unit', 'wasm', 'real-zabbix'):
        (evidence / (name + '.xml')).write_text(PASS_XML)
    (evidence / 'reference-run.json').write_text(json.dumps({'phases': [
        {'layer': 'unit_mocked', 'junit': 'evidence/unit.xml'},
        {'layer': 'postgres_wasm', 'junit': 'evidence/wasm.xml'}], 'code_digests': code_digests(tmp_path)}))
    return evidence


@pytest.mark.parametrize('problem', [
    'failed-run-with-old-pass', 'running-run', 'missing-status', 'malformed-record',
    'malformed-json', 'missing-junit', 'missing-file', 'malformed-junit', 'empty-junit',
    'failed-junit', 'skipped-junit', 'missing-counts', 'counts-mismatch', 'counts-wrong-type',
    'counts-boolean', 'failed-command', 'not-run-with-old-pass', 'not-run-nonzero-counts',
])
def test_acceptance_rejects_inconsistent_real_zabbix_evidence(tmp_path, monkeypatch, problem):
    evidence = seed_reference(tmp_path)
    run = {'status': 'PASS', 'counts': dict(COUNTS), 'junit': 'evidence/real-zabbix.xml',
           'code_digests': code_digests(tmp_path)}
    if problem == 'failed-run-with-old-pass':
        run['status'] = 'FAIL'
    elif problem == 'running-run':
        run['status'] = 'RUNNING'
    elif problem == 'missing-status':
        del run['status']
    elif problem == 'malformed-record':
        run = []
    elif problem == 'missing-junit':
        del run['junit']
    elif problem == 'missing-file':
        (evidence / 'real-zabbix.xml').unlink()
    elif problem in {'malformed-junit', 'empty-junit', 'failed-junit', 'skipped-junit'}:
        xml = {'malformed-junit': '<broken', 'empty-junit': '<testsuites/>',
               'failed-junit': PASS_XML.replace('/></testsuite>', '><failure/></testcase></testsuite>'),
               'skipped-junit': PASS_XML.replace('/></testsuite>', '><skipped/></testcase></testsuite>')}[problem]
        (evidence / 'real-zabbix.xml').write_text(xml)
    elif problem == 'missing-counts':
        del run['counts']
    elif problem == 'counts-mismatch':
        run['counts'] = {'passed': 0, 'failed': 5, 'skipped': 0}
    elif problem == 'counts-wrong-type':
        run['counts'] = [1, 0, 0]
    elif problem == 'counts-boolean':
        run['counts']['passed'] = True
    elif problem == 'failed-command':
        run['exit_code'] = 1
    elif problem == 'not-run-with-old-pass':
        run['status'] = 'NOT_RUN'
    elif problem == 'not-run-nonzero-counts':
        run = {'status': 'NOT_RUN', 'counts': dict(COUNTS)}
    (evidence / 'real-zabbix-run.json').write_text('<broken' if problem == 'malformed-json' else json.dumps(run))
    monkeypatch.setattr(update_acceptance, 'ROOT', tmp_path)
    monkeypatch.setattr(update_acceptance.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('Invalid real-Zabbix evidence reached the mapper'))
    with pytest.raises(RuntimeError, match='Real-Zabbix'):
        update_acceptance.main()


@pytest.mark.parametrize('availability', ['pass', 'not-run', 'not-run-zero-counts', 'absent'])
def test_acceptance_maps_only_consistent_executed_real_zabbix_report(tmp_path, monkeypatch, availability):
    evidence = seed_reference(tmp_path)
    # A stale XML may still exist after explicit NOT_RUN or with no selected run;
    # its mere presence must never contribute a real-source pass.
    run = {'status': 'PASS', 'counts': dict(COUNTS), 'junit': 'evidence/real-zabbix.xml',
           'code_digests': code_digests(tmp_path)}
    if availability.startswith('not-run'):
        run = {'status': 'NOT_RUN', 'reason': 'Synthetic absent disposable fixture'}
        if availability == 'not-run-zero-counts':
            run['counts'] = {'passed': 0, 'failed': 0, 'skipped': 0}
    if availability != 'absent':
        (evidence / 'real-zabbix-run.json').write_text(json.dumps(run))
    monkeypatch.setattr(update_acceptance, 'ROOT', tmp_path)
    commands = []

    def mapped(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(update_acceptance.subprocess, 'run', mapped)
    assert update_acceptance.main() == 0
    assert ('--real-zabbix-xml' in commands[0]) == (availability == 'pass')
