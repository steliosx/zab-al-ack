"""Failure diagnostics must remain useful without exporting private test data."""
import json
from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import mutation_failure_summary as summary
from evidence_utils import code_digests
import run_mutation_checks as runner


@pytest.fixture
def tree(tmp_path):
    path = tmp_path / summary.TESTS[0]
    path.parent.mkdir(parents=True)
    path.write_text('def test_known():\n    assert False\n')
    return tmp_path


def test_private_text_and_parameter_ids_are_never_exported(tree):
    raw = tree / 'raw'
    raw.mkdir()
    path = raw / 'baseline.xml'
    path.write_text('''<testsuite><testcase classname="tests.unit.test_crypto_policy" name="test_known[private-parameter]">
    <failure message="private-proof">private-stack</failure><system-out>private-output</system-out></testcase>
    <testcase classname="private-module" name="private-name"><error message="private-secret"/></testcase></testsuite>''')
    result = summary.failure_cases(path, tree, raw)
    assert result == {'availability': 'available', 'cases': [
        {'file': summary.TESTS[0], 'test': 'test_known', 'kinds': ['failure']}], 'omitted': 1}
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('value', ['broken xml', '<!DOCTYPE x [<!ENTITY x "secret">]><testsuite/>'])
def test_missing_or_unparseable_junit_has_no_invented_case(tree, value):
    path = tree / 'baseline.xml'
    assert summary.failure_cases(path, tree, tree)['availability'] == 'unavailable'
    path.write_text(value)
    assert summary.failure_cases(path, tree, tree)['availability'] == 'unavailable'


def test_symlink_input_is_never_read(tree):
    target = tree / 'outside.xml'
    target.write_text('<testsuite/>')
    link = tree / 'baseline.xml'
    link.symlink_to(target)
    assert summary.failure_cases(link, tree, tree)['availability'] == 'unavailable'


def report(tree, **changes):
    record = {'status': 'FAIL', 'ci_context': summary.context(), 'code_digests': code_digests(tree),
              'baseline': {'outcome': 'ERROR', 'exit_code': 2, 'errors': 1,
                           'diagnostics': {'availability': 'available', 'omitted': 0, 'cases': [
                               {'file': summary.TESTS[0], 'test': 'test_known', 'kinds': ['error'], 'secret': 'private'},
                               {'file': summary.TESTS[0], 'test': 'private-name', 'kinds': ['error']} ]},
                           'private_log': 'private-credentials'}, 'reason': 'private-proof'}
    record.update(changes)
    (tree / 'evidence').mkdir(exist_ok=True)
    (tree / 'evidence/mutation-run.json').write_text(json.dumps(record))
    return record


def test_summary_filters_every_field_and_binds_attempt_and_source(tree, monkeypatch):
    monkeypatch.setenv('GITHUB_SHA', 'a' * 40)
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    report(tree, code_digests={**code_digests(tree), 'private': 'private-secret'})
    result = summary.summarize(tree)
    assert result['availability'] == 'current_report'
    assert result['baseline']['outcome'] == 'ERROR' and result['baseline']['exit_code'] == 2
    assert result['baseline']['cases'] == [{'file': summary.TESTS[0], 'test': 'test_known', 'kinds': ['error']}]
    assert 'private' not in json.dumps(result)
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '3')
    assert summary.summarize(tree)['availability'] == 'unavailable'
    report(tree)
    (tree / summary.TESTS[0]).write_text('def test_different(): pass\n')
    assert summary.summarize(tree)['availability'] == 'unavailable'


def test_invalid_environment_is_not_echoed(tree, monkeypatch):
    for key in ('GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
        monkeypatch.setenv(key, 'private-env')
    assert summary.context() == {}
    assert 'private-env' not in json.dumps(summary.summarize(tree))


def test_failed_baseline_leaves_diagnostic_and_still_fails_gate(tree, monkeypatch):
    for name in runner.COPY_DIRS:
        (tree / name).mkdir(exist_ok=True)
    (tree / 'src/example.py').write_text('answer = True\n')
    monkeypatch.setattr(runner, 'run_pytest', lambda *a: {'outcome': 'ERROR', 'exit_code': 2, 'errors': 1})
    assert runner.run(tree) == 1
    result = summary.summarize(tree)
    assert result['status'] == 'FAIL' and result['baseline']['outcome'] == 'ERROR'
    record = json.loads((tree / 'evidence/mutation-run.json').read_text())
    assert all(item['outcome'] == 'NOT_RUN' for item in record['mutants'])
    with pytest.raises(ValueError):
        runner.validate_report(record, tree)


def test_success_is_labelled_diagnostic_not_gate_evidence(tree):
    report(tree, status='PASS', baseline={'outcome': 'BASELINE_PASS', 'exit_code': 0})
    result = summary.summarize(tree)
    assert result['purpose'] == 'diagnostic_only' and result['baseline']['outcome'] == 'BASELINE_PASS'


def test_workflow_uploads_only_summary_even_after_failure():
    root = Path(__file__).resolve().parents[2]
    steps = yaml.safe_load((root / '.github/workflows/ci.yml').read_text())['jobs']['mutation']['steps']
    generate = next(s for s in steps if s.get('run') == 'python tools/mutation_failure_summary.py')
    upload = next(s for s in steps if s.get('with', {}).get('name', '').startswith('zabalack-mutation-diagnostics-'))
    assert generate['if'] == upload['if'] == 'always()'
    assert upload['with']['path'] == 'evidence/mutation-failure-summary.json'
    assert upload['with']['retention-days'] == 3
    assert 'continue-on-error' not in next(s for s in steps if s.get('run', '').startswith('python tools/run_mutation_checks.py'))


def test_utf16_entities_cannot_bypass_the_xml_boundary(tree):
    path = tree / 'baseline.xml'
    path.write_bytes('<!DOCTYPE x [<!ENTITY x "secret">]><testsuite>&x;</testsuite>'.encode('utf-16'))
    assert summary.failure_cases(path, tree, tree)['availability'] == 'unavailable'
