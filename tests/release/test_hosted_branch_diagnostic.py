"""Focused source contracts for the one proposed manual hosted exception."""
import ast
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FILENAME = 'ci.yml'
PINS = {
    'actions/checkout': '11bd71901bbe5b1630ceea73d27597364c9af683',
    'actions/setup-python': 'a26af69be951a213d495a4c3e4e4022e16d87065',
    'actions/setup-node': '49933ea5288caeca8642d1e84afbd3f7d6820020',
    'actions/upload-artifact': 'ea165f8d65b6e75b540449e92b4886f43607fa02',
}


def hosted_contract(workflow):
    assert workflow['name'] == 'Alpha reference checks'
    assert set(workflow['on']) == {'push', 'pull_request', 'workflow_dispatch', 'workflow_call', 'schedule'}
    assert workflow['on']['push'] == {'branches': ['main'], 'tags': ['**']}
    assert workflow['on']['pull_request'] == workflow['on']['workflow_call'] == ''
    assert workflow['on']['schedule'] == [{'cron': '23 3 * * *'}]
    assert workflow['on']['workflow_dispatch'] == {'inputs': {
        'diagnostic': {'description': 'Full reference checks or the separately reviewed hosted diagnostic',
                       'required': 'true', 'default': 'full-reference', 'type': 'choice',
                       'options': ['full-reference', 'hosted-x64-quality-unit-mocked']},
        'reviewed_commit': {'description': 'Exact reviewed diagnostic branch commit (40 lowercase hex)',
                            'required': 'false', 'default': '', 'type': 'string'}}}
    assert workflow['permissions'] == {'contents': 'read'}
    assert workflow['env'] == {
        'TZ': 'UTC', 'ACK_HYPOTHESIS_PROFILE': "${{ github.event_name == 'schedule' && 'nightly' || 'pr' }}"}
    full_guard = "${{ !(github.event_name == 'workflow_dispatch' && (github.event.inputs.diagnostic == 'hosted-x64-quality-unit-mocked' || github.ref == 'refs/heads/diagnostic/hosted-x64-20261008')) }}"
    assert workflow['concurrency'] == {
        'group': '${{ github.workflow }}-${{ github.ref }}', 'cancel-in-progress': full_guard}
    originals = {'quality', 'mutation', 'reference', 'native', 'package'}
    assert set(workflow['jobs']) == originals | {'quality-unit-mocked'}
    for name in originals:
        assert workflow['jobs'][name].get('if') == full_guard
        assert workflow['jobs'][name]['runs-on'] == ['self-hosted', 'Linux', 'ARM64', 'zabalack']
    assert workflow['jobs']['package']['needs'] == ['quality', 'reference', 'native', 'mutation']
    job = workflow['jobs']['quality-unit-mocked']
    assert job['runs-on'] == 'ubuntu-24.04'
    assert job['timeout-minutes'] == '25'
    assert set(job) == {'if', 'env', 'runs-on', 'timeout-minutes', 'steps'}
    assert job['if'] == "${{ github.event_name == 'workflow_dispatch' && github.event.inputs.diagnostic == 'hosted-x64-quality-unit-mocked' && github.ref == 'refs/heads/diagnostic/hosted-x64-20261008' && github.event.inputs.reviewed_commit == github.sha }}"
    assert job['env'] == {'ACK_HYPOTHESIS_PROFILE': 'pr'}
    steps = job['steps']
    assert len(steps) == 13
    gate = steps[0]
    assert 'uses' not in gate
    assert gate['env'] == {
        'EVENT': '${{ github.event_name }}', 'THIS_REPO': '${{ github.repository }}',
        'REF': '${{ github.ref }}', 'SOURCE_SHA': '${{ github.sha }}',
        'REVIEWED_COMMIT': '${{ github.event.inputs.reviewed_commit }}',
        'DIAGNOSTIC': '${{ github.event.inputs.diagnostic }}'}
    assert gate['run'].splitlines() == [
        'test "$EVENT" = workflow_dispatch', 'test "$THIS_REPO" = steliosx/zab-al-ack',
        'test "$REF" = refs/heads/diagnostic/hosted-x64-20261008',
        'test "$DIAGNOSTIC" = hosted-x64-quality-unit-mocked',
        '[[ "$REVIEWED_COMMIT" =~ ^[0-9a-f]{40}$ ]]',
        'test "$SOURCE_SHA" = "$REVIEWED_COMMIT"',
        'test "$(uname -s)" = Linux', 'test "$(uname -m)" = x86_64']
    actions = [step for step in steps if 'uses' in step]
    assert [step['uses'] for step in actions] == [name + '@' + pin for name, pin in PINS.items()]
    assert actions[0]['with'] == {'ref': '${{ github.sha }}', 'persist-credentials': 'false'}
    assert actions[1]['with'] == {'python-version': '3.12'}
    assert actions[2]['with'] == {'node-version': '22'}
    assert steps[4]['run'].splitlines() == [
        'test ! -e "$RUNNER_TEMP/venv"', 'python -m venv "$RUNNER_TEMP/venv"',
        'printf \'%s\\n\' "$RUNNER_TEMP/venv/bin" >> "$GITHUB_PATH"']
    assert steps[5]['run'] == 'command -v openssl >/dev/null'
    assert [step['run'].removesuffix('\n') for step in steps[6:11]] == [
        'python -m pip install -r requirements.lock',
        'python -m pip install --require-hashes --only-binary=:all: -r requirements-quality.lock',
        'python -m pip install --no-deps --no-build-isolation -e .',
        'python -m pip check', 'python tools/run_hosted_diagnostic.py']
    assert steps[11] == {
        'name': 'Check the exact public files and aggregate byte cap before upload',
        'if': 'always()', 'id': 'artifact_boundary',
        'run': 'python tools/run_hosted_diagnostic.py --verify-public-artifact'}
    assert actions[3]['if'] == "${{ always() && steps.artifact_boundary.outputs.approved == 'true' }}"
    assert actions[3]['with'] == {
        'name': 'zabalack-hosted-diagnostic-${{ github.run_id }}-${{ github.run_attempt }}',
        'path': 'build/hosted-diagnostic/public/diagnostic.json\n' +
                'build/hosted-diagnostic/public/unit-mocked.xml\n',
        'if-no-files-found': 'error', 'retention-days': '3'}
    for step in steps:
        assert 'continue-on-error' not in step
        assert 'secrets' not in step
        assert not any(key.startswith('GITHUB_') for key in step.get('env', {}))
        assert '${{' not in step.get('run', '')
    assert all('if' not in step for step in steps[:-2])


def workflow():
    return yaml.load((ROOT / '.github/workflows' / FILENAME).read_text(), Loader=yaml.BaseLoader)


def test_manual_hosted_diagnostic_has_only_the_reviewed_exception():
    hosted_contract(workflow())


@pytest.mark.parametrize('mutation', ['auto-event', 'broad-route', 'extra-job', 'write-token',
                                      'no-guard', 'no-fresh-venv', 'unpin', 'failure-hidden',
                                      'raw-artifact', 'full-reference', 'arm-intake', 'main-ref'])
def test_hosted_exception_refuses_scope_or_trust_expansion(mutation):
    value = deepcopy(workflow())
    job = value['jobs']['quality-unit-mocked']
    steps = job['steps']
    if mutation == 'auto-event':
        value['on']['push'] = {'branches': ['**']}
    elif mutation == 'broad-route':
        job['runs-on'] = ['self-hosted', 'Linux', 'X64', 'zabalack']
    elif mutation == 'extra-job':
        value['jobs']['unreviewed'] = deepcopy(job)
    elif mutation == 'write-token':
        value['permissions']['contents'] = 'write'
    elif mutation == 'no-guard':
        steps.pop(0)
    elif mutation == 'no-fresh-venv':
        steps[4]['run'] = 'python -m pip install -r requirements.lock'
    elif mutation == 'unpin':
        steps[1]['uses'] = 'actions/checkout@v4'
    elif mutation == 'failure-hidden':
        job['continue-on-error'] = 'true'
    elif mutation == 'raw-artifact':
        steps[-1]['with']['path'] = 'build/'
    elif mutation == 'full-reference':
        steps[10]['run'] = 'python tools/run_reference_suite.py --wasm'
    elif mutation == 'arm-intake':
        value['jobs']['quality'].pop('if')
    else:
        steps[0]['run'] = steps[0]['run'].replace('refs/heads/diagnostic/hosted-x64-20261008', 'refs/heads/main')
    with pytest.raises(AssertionError):
        hosted_contract(value)


def test_unit_target_set_matches_reference_unit_phase_without_optional_services():
    source = ast.parse((ROOT / 'tools/run_reference_suite.py').read_text())
    phase_assignment = next(node for node in ast.walk(source) if isinstance(node, ast.Assign)
                            and any(isinstance(target, ast.Name) and target.id == 'phases'
                                    for target in node.targets))
    first_phase = phase_assignment.value.elts[0]
    assert ast.literal_eval(first_phase.elts[0]) == 'unit-mocked'
    command = first_phase.elts[2]
    targets = [ast.literal_eval(node) for node in command.elts[3:-1]]
    diagnostic = ast.parse((ROOT / 'tools/run_hosted_diagnostic.py').read_text())
    target_assignment = next(node for node in ast.walk(diagnostic) if isinstance(node, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == 'UNIT_TARGETS'
                                     for target in node.targets))
    assert list(ast.literal_eval(target_assignment.value)) == targets
    assert len(targets) == 8
    assert not any('tests/postgres/' in target or 'deployment_e2e' in target for target in targets)
    text = (ROOT / 'tools/run_hosted_diagnostic.py').read_text()
    assert "[sys.executable, 'tools/run_quality.py']" in text
    assert "[sys.executable, '-m', 'pytest', *UNIT_TARGETS, '-q'," in text
    assert "'reference-run.json'" not in text and 'update_acceptance.py' not in text


@pytest.mark.parametrize('failure', ['none', 'quality', 'unit', 'skip', 'timeout', 'missing-junit'])
def test_limited_diagnostic_keeps_failure_and_never_reuses_full_reference(tmp_path, monkeypatch, failure):
    import run_hosted_diagnostic as diagnostic
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    old_reference = '{"status": "historical-reference"}\n'
    (evidence / 'reference-run.json').write_text(old_reference)
    (evidence / 'quality-run.json').write_text('{"status": "historical-quality"}')
    monkeypatch.setattr(diagnostic, 'ROOT', tmp_path)
    monkeypatch.setattr(diagnostic, 'code_digests', lambda _: {'source': 'a' * 64, 'harness': 'b' * 64})
    calls = []

    def synthetic_child(command, env, log):
        calls.append(command)
        assert 'ACK_TEST_ADMIN_DSN' not in env and 'GH_TOKEN' not in env
        if command[1] == 'tools/run_quality.py':
            assert not (evidence / 'quality-run.json').exists()
            (evidence / 'quality-run.json').write_text(json.dumps({
                'status': 'FAIL' if failure == 'quality' else 'PASS',
                'tree_unchanged': True, 'code_digests': {'source': 'a' * 64, 'harness': 'b' * 64},
                'checks': [{'name': name, 'status': 'PASS', 'exit_code': 0}
                           for name in ('ruff', 'mypy', 'pip-check')]}))
            return 1 if failure == 'quality' else 0
        assert command[3:11] == list(diagnostic.UNIT_TARGETS)
        if failure not in ('timeout', 'missing-junit'):
            raw = Path(command[-1].removeprefix('--junitxml='))
            result = '<skipped />' if failure == 'skip' else '<failure />' if failure == 'unit' else ''
            raw.write_text('<testsuites><testsuite><testcase classname="synthetic" name="case">' +
                           result + '</testcase></testsuite></testsuites>')
        return 124 if failure == 'timeout' else 1 if failure == 'unit' else 0

    monkeypatch.setenv('ACK_TEST_ADMIN_DSN', 'synthetic-unapproved-inheritance')
    monkeypatch.setenv('GH_TOKEN', 'synthetic-unapproved-inheritance')
    monkeypatch.setattr(diagnostic, 'run_child', synthetic_child)
    assert diagnostic.main() == (0 if failure == 'none' else 1)
    record = json.loads((tmp_path / 'build/hosted-diagnostic/public/diagnostic.json').read_text())
    assert len(calls) == 2  # Quality failure never suppresses the unit attempt.
    assert record['status'] == ('PASS' if failure == 'none' else 'FAIL')
    assert set(record['pending'].values()) == {'NOT_RUN'}
    assert (evidence / 'reference-run.json').read_text() == old_reference
    assert not (evidence / 'test-summary.json').exists()
    with pytest.raises(FileExistsError):
        diagnostic.main()  # A second invocation cannot reuse this output.


def branch_push_contract(value):
    assert value['on']['push'] == {'branches-ignore': ['diagnostic/hosted-x64-20261008'], 'tags': ['**']}
    assert value['on']['pull_request'] == ''  # No PR exception is silently added.
    assert value['on']['workflow_dispatch'] == ''


@pytest.mark.parametrize('filename', ['security.yml', 'codeql.yml'])
def test_only_exact_diagnostic_branch_is_excluded_from_other_push_workflows(filename):
    value = yaml.load((ROOT / '.github/workflows' / filename).read_text(), Loader=yaml.BaseLoader)
    branch_push_contract(value)


@pytest.mark.parametrize('filename', ['security.yml', 'codeql.yml'])
@pytest.mark.parametrize('mutation', ['missing-exclusion', 'main-excluded', 'glob-excluded', 'pr-excluded'])
def test_diagnostic_branch_push_exception_cannot_remove_other_required_events(filename, mutation):
    value = yaml.load((ROOT / '.github/workflows' / filename).read_text(), Loader=yaml.BaseLoader)
    value = deepcopy(value)
    if mutation == 'missing-exclusion':
        value['on']['push'] = ''
    elif mutation == 'main-excluded':
        value['on']['push']['branches-ignore'].append('main')
    elif mutation == 'glob-excluded':
        value['on']['push']['branches-ignore'] = ['diagnostic/**']
    else:
        value['on']['pull_request'] = {'branches-ignore': ['diagnostic/hosted-x64-20261008']}
    with pytest.raises(AssertionError):
        branch_push_contract(value)


@pytest.mark.parametrize('boundary', ['valid', 'extra-file', 'aggregate', 'single-file', 'symlink', 'hardlink'])
def test_public_upload_guard_requires_exact_files_and_post_redaction_aggregate_cap(tmp_path, monkeypatch, boundary):
    import run_hosted_diagnostic as diagnostic
    monkeypatch.setattr(diagnostic, 'ROOT', tmp_path)
    public = tmp_path / 'build/hosted-diagnostic/public'
    public.mkdir(parents=True)
    (public / 'diagnostic.json').write_text('{"status":"FAIL"}\n')
    (public / 'unit-mocked.xml').write_text('<testsuites />')
    output = tmp_path / 'genuine-actions-output'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    if boundary == 'extra-file':
        (public / 'raw.log').write_text('synthetic-private')
    elif boundary == 'aggregate':
        (public / 'diagnostic.json').write_bytes(b'x' * (1024 * 1024 + 1))
        (public / 'unit-mocked.xml').write_bytes(b'x' * (1024 * 1024 + 1))
    elif boundary == 'single-file':
        (public / 'unit-mocked.xml').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
    elif boundary in ('symlink', 'hardlink'):
        (public / 'unit-mocked.xml').unlink()
        private = tmp_path / 'private-raw.xml'
        private.write_text('synthetic-private')
        if boundary == 'symlink':
            (public / 'unit-mocked.xml').symlink_to(private)
        else:
            (public / 'unit-mocked.xml').hardlink_to(private)
    if boundary == 'valid':
        assert diagnostic.verify_public_artifact() == 0
        assert output.read_text() == 'approved=true\n'
    else:
        with pytest.raises(ValueError):
            diagnostic.verify_public_artifact()
        assert not output.exists()  # Failed boundary cannot enable always-upload.


@pytest.mark.parametrize('error_type', [OSError, ValueError, RuntimeError, AssertionError])
def test_capture_reader_failure_refuses_successful_child(tmp_path, monkeypatch, error_type):
    import run_hosted_diagnostic as diagnostic

    class FailedReader:
        def __init__(self):
            self.calls = 0

        def read(self, _size):
            self.calls += 1
            if self.calls == 1:
                return b'synthetic partial capture\n'
            raise error_type('synthetic reader failure')

        def close(self):
            pass

    class SuccessfulChild:
        pid = 987654321
        stdout = FailedReader()

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(diagnostic.subprocess, 'Popen', lambda *args, **kwargs: SuccessfulChild())
    monkeypatch.setattr(diagnostic.os, 'killpg', lambda *args: pytest.fail('No real process may be signalled'))
    assert diagnostic.run_child(['synthetic-no-execution'], {}, tmp_path / 'private.log') == 125


@pytest.mark.parametrize('filename', ['security.yml', 'codeql.yml'])
def test_exact_branch_exclusion_preserves_all_tag_pushes(filename):
    value = yaml.load((ROOT / '.github/workflows' / filename).read_text(), Loader=yaml.BaseLoader)
    branch_push_contract(value)
    value = deepcopy(value)
    value['on']['push'].pop('tags')
    with pytest.raises(AssertionError):
        branch_push_contract(value)


@pytest.mark.parametrize('mutation', ['none', 'wrong-ref', 'default-on-ref', 'wrong-sha',
                                      'missing-job', 'skipped-job', 'missing-report',
                                      'stale-report', 'skipped-unit', 'uppercase-mode'])
def test_diagnostic_intent_refuses_mismatch_or_missing_execution(mutation):
    import run_hosted_diagnostic as diagnostic
    context = {'event_name': 'workflow_dispatch',
               'diagnostic': 'hosted-x64-quality-unit-mocked',
               'ref': 'refs/heads/diagnostic/hosted-x64-20261008',
               'reviewed_commit': 'a' * 40, 'sha': 'a' * 40,
               'repository': 'steliosx/zab-al-ack', 'run_id': '1', 'run_attempt': '1'}
    job = {'name': 'quality-unit-mocked', 'status': 'completed', 'conclusion': 'success',
           'run_id': 1, 'run_attempt': 1, 'head_sha': 'a' * 40}
    report = {'schema': 'zabalack-hosted-diagnostic-v1', 'status': 'PASS', 'tree_unchanged': True,
              'actions': {key: context[key] for key in ('repository', 'run_id', 'run_attempt', 'sha')},
              'phases': [{'phase': 'quality', 'status': 'PASS', 'exit_code': 0},
                         {'phase': 'unit-mocked', 'status': 'PASS', 'exit_code': 0,
                          'counts': {'passed': 1, 'failed': 0, 'skipped': 0}}]}
    if mutation == 'wrong-ref':
        context['ref'] = 'refs/heads/main'
    elif mutation == 'default-on-ref':
        context['diagnostic'] = 'full-reference'
    elif mutation == 'wrong-sha':
        context['reviewed_commit'] = 'b' * 40
    elif mutation == 'missing-job':
        job = None  # An all-skipped successful workflow cannot prove execution.
    elif mutation == 'skipped-job':
        job['conclusion'] = 'skipped'
    elif mutation == 'missing-report':
        report = None
    elif mutation == 'stale-report':
        report['actions']['run_attempt'] = '0'
    elif mutation == 'skipped-unit':
        report['phases'][1]['counts']['skipped'] = 1
    elif mutation == 'uppercase-mode':
        context['diagnostic'] = context['diagnostic'].upper()
    assert diagnostic.classify_diagnostic_result(context, job, report) == (
        'PASS' if mutation == 'none' else 'REFUSED')
