"""Synthetic transport regressions; these do not claim a hosted CI execution."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import collect_ci_evidence as ci
from evidence_utils import code_digests
from mutation_fixtures import passing_mutation_record

ROOT = Path(__file__).resolve().parents[2]
CONTEXT = {'repository': 'synthetic/zabalack', 'run_id': '123', 'run_attempt': '2', 'sha': 'a' * 40}
PR_CONTEXT = dict(CONTEXT, pull_request={'number': 17, 'head_sha': 'b' * 40, 'base_sha': 'c' * 40})


def write_record(root, name, value):
    ci.write_json(root / 'evidence' / name, dict(value, code_digests=code_digests(root), tree_unchanged=True))


def write_xml(root, filename, skipped=False):
    body = '<skipped message="NOT_RUN" />' if skipped else ''
    name = filename.removesuffix('.xml')
    (root / 'evidence' / filename).write_text(
        f'<testsuites><testsuite><testcase classname="tests.synthetic" name="{name}">{body}'
        '</testcase></testsuite></testsuites>')
    return [{'nodeid': 'tests.synthetic::' + name, 'status': 'skipped' if skipped else 'passed'}]


def create_reports(root, kind):
    if kind == 'mutation':
        write_record(root, 'mutation-run.json', passing_mutation_record(root, os.environ.get('ACK_HYPOTHESIS_PROFILE', 'pr')))
        return
    if kind == 'quality':
        write_record(root, 'quality-run.json', {'status': 'PASS', 'checks': [
            {'name': name, 'status': 'PASS', 'exit_code': 0} for name in ('ruff', 'mypy', 'pip-check')]})
        return
    phases = []
    by_layer = {}
    for name in ci.REFERENCE_PHASES if kind == 'reference' else ci.NATIVE_PHASES:
        filename = ('native-' if kind == 'native' else '') + name + '.xml'
        rows = write_xml(root, filename, skipped=name == 'optional-services')
        phase = {'phase': name, 'junit': 'evidence/' + filename, 'exit_code': 0}
        if kind == 'native':
            phase.update(status='PASS', shutdown_exit_code=0, counts=ci.counts(rows))
        else:
            layer = ci.REFERENCE_PHASES[name]
            phase['layer'] = layer
            by_layer.setdefault(layer, []).extend(rows)
        phases.append(phase)
    if kind == 'native':
        write_record(root, 'native-run.json', {'status': 'PASS', 'phases': phases})
    else:
        write_record(root, 'reference-run.json', {'all_executed_phases_succeeded': True, 'phases': phases,
            'counts': ci.counts([row for rows in by_layer.values() for row in rows]),
            'by_layer': {layer: ci.counts(rows) for layer, rows in by_layer.items()},
            'native_postgres': 'NOT_RUN', 'pgbouncer': 'NOT_RUN', 'real_zabbix': 'NOT_RUN',
            'deployment_gates': 'MANUAL_GATE_PENDING'})
        write_record(root, 'runtime_wasm_sql.json', {'status': 'PASS', 'exit_code': 0,
            'results': [{'test': 'synthetic', 'status': 'PASS'}]})


@pytest.fixture
def collection(tmp_path, monkeypatch, request):
    # These transport fixtures model a PR; scheduled-profile behavior has its
    # own negative test and must not depend on the caller's pytest environment.
    monkeypatch.setenv('ACK_HYPOTHESIS_PROFILE', 'pr')
    context = getattr(request, 'param', CONTEXT)
    root = tmp_path / 'source'
    (root / 'src/zabalack').mkdir(parents=True)
    (root / 'src/zabalack/synthetic.py').write_text('SYNTHETIC = True\n')
    (root / 'tests').mkdir()
    (root / 'tests/synthetic.py').write_text('SYNTHETIC_HARNESS = True\n')
    inputs = tmp_path / 'downloads'
    inputs.mkdir()
    for kind in ci.KINDS:
        ci.prepare(root, kind, context)
        create_reports(root, kind)
        output = ci.pack(root, kind, context)
        shutil.copytree(output, inputs / ci.artifact_name(kind, context))
    return root, inputs


@pytest.fixture
def actions_event(tmp_path, monkeypatch):
    event = {'number': 17, 'repository': {'full_name': CONTEXT['repository']}, 'pull_request': {
        'number': 17, 'head': {'sha': 'b' * 40, 'repo': {'full_name': 'fork-owner/zabalack'}},
        'base': {'sha': 'c' * 40, 'repo': {'full_name': CONTEXT['repository']}},
        # The webhook field can lag the checkout and must not replace its SHA.
        'merge_commit_sha': 'd' * 40, 'body': 'SYNTHETIC_PRIVATE_EVENT_BODY',
    }}
    path = tmp_path / 'event.json'
    ci.write_json(path, event)
    for key, value in CONTEXT.items():
        monkeypatch.setenv('GITHUB_' + key.upper(), value)
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(path))
    monkeypatch.setattr(ci.subprocess, 'check_output', lambda *args, **kwargs: CONTEXT['sha'] + '\n')
    return path


def test_pr_event_retains_head_base_and_number_separately_from_tested_checkout(actions_event, tmp_path):
    context = ci.environment_context(tmp_path)
    assert context == PR_CONTEXT
    assert context['sha'] != context['pull_request']['head_sha']
    assert 'SYNTHETIC_PRIVATE_EVENT_BODY' not in json.dumps(context)


@pytest.mark.parametrize('event_name', ['push', 'schedule', 'workflow_dispatch'])
def test_non_pr_events_keep_the_existing_context_shape(actions_event, tmp_path, monkeypatch, event_name):
    monkeypatch.setenv('GITHUB_EVENT_NAME', event_name)
    actions_event.unlink()  # Non-PR runs do not require or export a PR payload.
    assert ci.environment_context(tmp_path) == CONTEXT


@pytest.mark.parametrize('change', ['not_actions', 'wrong_checkout', 'missing_event_name', 'invalid_event_name'])
def test_environment_context_still_requires_actions_and_matching_checkout(actions_event, tmp_path, monkeypatch, change):
    if change == 'not_actions':
        monkeypatch.setenv('GITHUB_ACTIONS', 'false')
    elif change == 'wrong_checkout':
        monkeypatch.setattr(ci.subprocess, 'check_output', lambda *args, **kwargs: 'e' * 40)
    elif change == 'missing_event_name':
        monkeypatch.delenv('GITHUB_EVENT_NAME')
    else:
        monkeypatch.setenv('GITHUB_EVENT_NAME', 'SYNTHETIC_PRIVATE_EVENT_BODY\n')
    with pytest.raises(ci.EvidenceError):
        ci.environment_context(tmp_path)


@pytest.mark.parametrize('change', ['missing_file', 'malformed', 'duplicate', 'no_pull', 'wrong_pull_type',
    'missing_head', 'bad_sha', 'bool_number', 'string_number', 'zero_number', 'number_mismatch',
    'foreign_event_repository', 'foreign_base_repository'])
def test_pr_event_rejects_missing_or_ambiguous_metadata(actions_event, tmp_path, change):
    if change == 'missing_file':
        actions_event.unlink()
    elif change in {'malformed', 'duplicate'}:
        actions_event.write_text('{SYNTHETIC_PRIVATE_EVENT_BODY' if change == 'malformed' else
                                 '{"number":17,"number":18}')
    else:
        event = json.loads(actions_event.read_text())
        pull = event['pull_request']
        if change == 'no_pull':
            del event['pull_request']
        elif change == 'wrong_pull_type':
            event['pull_request'] = []
        elif change == 'missing_head':
            del pull['head']
        elif change == 'bad_sha':
            pull['base']['sha'] = 'SYNTHETIC_PRIVATE_EVENT_BODY'
        elif change in {'bool_number', 'string_number', 'zero_number'}:
            event['number'] = pull['number'] = {'bool_number': True, 'string_number': '17', 'zero_number': 0}[change]
        elif change == 'number_mismatch':
            event['number'] = 18
        elif change == 'foreign_event_repository':
            event['repository']['full_name'] = 'other/repository'
        else:
            pull['base']['repo']['full_name'] = 'other/repository'
        ci.write_json(actions_event, event)
    with pytest.raises(ci.EvidenceError) as error:
        ci.environment_context(tmp_path)
    assert 'SYNTHETIC_PRIVATE_EVENT_BODY' not in str(error.value)


@pytest.mark.parametrize('pull', [None, {}, {'number': 17, 'head_sha': 'b' * 40, 'base_sha': 'c' * 40, 'extra': 1},
    {'number': True, 'head_sha': 'b' * 40, 'base_sha': 'c' * 40},
    {'number': 17, 'head_sha': 42, 'base_sha': 'c' * 40}])
def test_context_rejects_invalid_pr_metadata(pull):
    with pytest.raises(ci.EvidenceError):
        ci.validate_context(dict(CONTEXT, pull_request=pull))


@pytest.mark.parametrize('collection', [PR_CONTEXT], indirect=True)
def test_pr_producers_and_collection_preserve_the_full_context(collection):
    root, inputs = collection
    for kind in ci.KINDS:
        assert ci.read_json(root / f'build/ci-preparation/{kind}.json')['context'] == PR_CONTEXT
        assert ci.read_json(artifact(inputs, kind) / 'manifest.json')['context'] == PR_CONTEXT
    ci.collect(root, inputs, PR_CONTEXT)
    assert ci.read_json(root / 'evidence/ci-collection.json')['context'] == PR_CONTEXT


@pytest.mark.parametrize('collection', [PR_CONTEXT], indirect=True)
@pytest.mark.parametrize('field', ['number', 'head_sha', 'base_sha', 'omit'])
def test_pr_collection_rejects_mismatched_or_downgraded_producer_metadata(collection, field):
    root, inputs = collection
    def change(value):
        if field == 'omit':
            del value['context']['pull_request']
        else:
            value['context']['pull_request'][field] = 18 if field == 'number' else 'e' * 40
    edit_json(artifact(inputs) / 'manifest.json', change)
    with pytest.raises(ci.EvidenceError, match='Foreign CI'):
        ci.collect(root, inputs, PR_CONTEXT)


@pytest.mark.parametrize('field', ['number', 'head_sha', 'base_sha', 'omit'])
def test_pack_rejects_pr_metadata_changed_after_preparation(collection, field):
    root, _ = collection
    ci.prepare(root, 'quality', PR_CONTEXT)
    create_reports(root, 'quality')
    context = deepcopy(PR_CONTEXT)
    if field == 'omit':
        del context['pull_request']
    else:
        context['pull_request'][field] = 18 if field == 'number' else 'e' * 40
    with pytest.raises(ci.EvidenceError, match='prepar'):
        ci.pack(root, 'quality', context)


@pytest.mark.parametrize('number,stored_number', [(17, 17.0), (1, True)], ids=['float', 'boolean'])
def test_pack_rejects_equal_but_invalid_saved_pr_number(collection, number, stored_number):
    root, _ = collection
    context = deepcopy(PR_CONTEXT)
    context['pull_request']['number'] = number
    ci.prepare(root, 'quality', context)
    create_reports(root, 'quality')
    path = root / 'build/ci-preparation/quality.json'
    edit_json(path, lambda value: value['context']['pull_request'].update(number=stored_number))
    # Dict equality alone conflates float/bool with int; validate the stored type.
    assert ci.read_json(path)['context'] == context
    with pytest.raises(ci.EvidenceError, match='Invalid pull request number'):
        ci.pack(root, 'quality', context)


def artifact(inputs, kind='reference'):
    return inputs / ci.artifact_name(kind, CONTEXT)


def edit_json(path, edit):
    value = json.loads(path.read_text())
    edit(value)
    path.write_text(json.dumps(value))


def refresh_manifest(directory, filename):
    data = (directory / filename).read_bytes()
    def update(manifest):
        item = next(item for item in manifest['files'] if item['path'] == filename)
        item.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    edit_json(directory / 'manifest.json', update)


def test_collection_preserves_exact_bytes_and_marks_optional_service_not_run(collection):
    root, inputs = collection
    (root / 'evidence/old-native-pass.xml').write_text('old')
    (root / 'evidence/acceptance-results.json').write_text('{"status":"PASS"}')
    expected = (artifact(inputs) / 'evidence/reference-run.json').read_bytes()
    ci.collect(root, inputs, CONTEXT)
    assert (root / 'evidence/reference-run.json').read_bytes() == expected
    assert not (root / 'evidence/old-native-pass.xml').exists()
    assert not (root / 'evidence/acceptance-results.json').exists()
    real = json.loads((root / 'evidence/real-zabbix-run.json').read_text())
    assert real['status'] == 'NOT_RUN' and real['junit'] is None
    assert real['counts'] == {'passed': 0, 'failed': 0, 'skipped': 0}
    assert real['code_digests'] == code_digests(root)
    assert real['deployment_gates'] == 'MANUAL_GATE_PENDING'
    assert json.loads((root / 'evidence/ci-collection.json').read_text())['signed_attestation'] is False


@pytest.mark.parametrize('field,value', [
    ('repository', 'foreign/zabalack'), ('run_id', '456'), ('run_attempt', '1'), ('sha', 'b' * 40),
])
def test_collection_rejects_foreign_run_context(collection, field, value):
    root, inputs = collection
    edit_json(artifact(inputs) / 'manifest.json', lambda item: item['context'].update({field: value}))
    with pytest.raises(ci.EvidenceError, match='Foreign CI'):
        ci.collect(root, inputs, CONTEXT)
    assert not (root / 'evidence/reference-run.json').exists()


@pytest.mark.parametrize('scope', ['source', 'harness'])
def test_collection_rejects_stale_tree_digest(collection, scope):
    root, inputs = collection
    filename = 'src/zabalack/synthetic.py' if scope == 'source' else 'tests/synthetic.py'
    (root / filename).write_text('CHANGED = True\n')
    with pytest.raises(ci.EvidenceError, match='stale|foreign tree'):
        ci.collect(root, inputs, CONTEXT)


@pytest.mark.parametrize('missing', ci.KINDS)
def test_collection_requires_every_gate_artifact(collection, missing):
    root, inputs = collection
    shutil.rmtree(artifact(inputs, missing))
    with pytest.raises(ci.EvidenceError, match='artifact set missing'):
        ci.collect(root, inputs, CONTEXT)


@pytest.mark.parametrize('path', ['../outside.json', '/tmp/outside.json', 'evidence/../outside.json',
    'evidence//reference-run.json', 'evidence\\reference-run.json', 'evidence/nested/reference-run.json'])
def test_collection_rejects_traversal_and_noncanonical_paths_before_file_read(collection, path):
    root, inputs = collection
    edit_json(artifact(inputs) / 'manifest.json', lambda item: item['files'][0].update(path=path))
    with pytest.raises(ci.EvidenceError, match='Unsafe or noncanonical'):
        ci.collect(root, inputs, CONTEXT)


def test_collection_rejects_modified_bytes(collection):
    root, inputs = collection
    (artifact(inputs) / 'evidence/reference-run.json').write_text('{}')
    with pytest.raises(ci.EvidenceError, match='hash mismatch'):
        ci.collect(root, inputs, CONTEXT)


def test_collection_rejects_duplicate_json_manifest_fields(collection):
    root, inputs = collection
    path = artifact(inputs) / 'manifest.json'
    path.write_text(path.read_text().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
    with pytest.raises(ci.EvidenceError, match='Duplicate JSON'):
        ci.collect(root, inputs, CONTEXT)


@pytest.mark.parametrize('mutation', ['duplicate', 'extra', 'missing', 'symlink', 'directory-symlink'])
def test_collection_rejects_unsafe_or_inexact_file_sets(collection, mutation):
    root, inputs = collection
    directory = artifact(inputs)
    if mutation == 'duplicate':
        edit_json(directory / 'manifest.json', lambda item: item['files'].append(deepcopy(item['files'][0])))
    elif mutation == 'extra':
        (directory / 'unmanifested.json').write_text('{}')
    elif mutation == 'missing':
        (directory / 'evidence/reference-run.json').unlink()
    elif mutation == 'symlink':
        target = directory / 'evidence/reference-run.json'
        target.unlink()
        target.symlink_to(root / 'src/zabalack/synthetic.py')
    else:
        target = directory / 'evidence'
        moved = directory.parent / 'external-evidence'
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=True)
        # Test the individual artifact so the extra sibling does not fail first.
        with pytest.raises(ci.EvidenceError, match='unsafe'):
            ci.validate_artifact(root, directory, 'reference', CONTEXT)
        return
    with pytest.raises(ci.EvidenceError):
        ci.collect(root, inputs, CONTEXT)


@pytest.mark.parametrize('kind,field,value', [
    ('native', 'status', 'NOT_RUN'), ('native', 'status', 'FAIL'),
    ('reference', 'all_executed_phases_succeeded', False),
    ('quality', 'status', 'FAIL'), ('quality', 'checks', []),
    ('mutation', 'status', 'FAIL'), ('mutation', 'mutants', []),
])
def test_collection_rejects_failed_or_unexecuted_required_gate(collection, kind, field, value):
    root, inputs = collection
    directory = artifact(inputs, kind)
    filename = f'evidence/{kind}-run.json'
    edit_json(directory / filename, lambda item: item.update({field: value}))
    refresh_manifest(directory, filename)
    with pytest.raises(ci.EvidenceError, match='gate|quality'):
        ci.collect(root, inputs, CONTEXT)


def test_collection_rejects_forged_report_binding_even_when_manifest_matches(collection):
    root, inputs = collection
    directory = artifact(inputs, 'quality')
    filename = 'evidence/quality-run.json'
    edit_json(directory / filename, lambda item: item['code_digests'].update(source='f' * 64))
    refresh_manifest(directory, filename)
    with pytest.raises(ci.EvidenceError, match='stale|foreign tree'):
        ci.collect(root, inputs, CONTEXT)


@pytest.mark.parametrize('mutation', ['failed', 'skipped', 'bad-counts', 'cleanup', 'missing-phase'])
def test_native_gate_requires_complete_successful_junit_and_cleanup(collection, mutation):
    root, inputs = collection
    directory = artifact(inputs, 'native')
    filename = 'evidence/native-run.json'
    report = json.loads((directory / filename).read_text())
    phase = report['phases'][0]
    if mutation in ('failed', 'skipped'):
        path = directory / phase['junit']
        element = '<failure message="synthetic" />' if mutation == 'failed' else '<skipped message="NOT_RUN" />'
        path.write_text(path.read_text().replace('</testcase>', element + '</testcase>'))
        refresh_manifest(directory, phase['junit'])
    elif mutation == 'bad-counts':
        phase['counts']['passed'] += 1
    elif mutation == 'cleanup':
        phase['shutdown_exit_code'] = 1
    else:
        report['phases'].pop()
    (directory / filename).write_text(json.dumps(report))
    refresh_manifest(directory, filename)
    with pytest.raises(ci.EvidenceError, match='JUnit|cleanup|phase set|skipped'):
        ci.collect(root, inputs, CONTEXT)


@pytest.mark.parametrize('mutation', ['missing-required-check', 'false-exit-code', 'empty-phase-list',
                                     'foreign-xml-path', 'wrong-layer', 'sql-failure', 'boolean-count'])
def test_collection_validates_schema_and_selected_required_checks(collection, mutation):
    root, inputs = collection
    kind = 'quality' if mutation in ('missing-required-check', 'false-exit-code') else 'reference'
    directory = artifact(inputs, kind)
    filename = 'evidence/runtime_wasm_sql.json' if mutation == 'sql-failure' else f'evidence/{kind}-run.json'
    report = json.loads((directory / filename).read_text())
    if mutation == 'missing-required-check':
        report['checks'].pop()
    elif mutation == 'false-exit-code':
        report['checks'][0]['exit_code'] = False
    elif mutation == 'empty-phase-list':
        report['phases'] = []
    elif mutation == 'foreign-xml-path':
        report['phases'][0]['junit'] = '../foreign.xml'
    elif mutation == 'wrong-layer':
        report['phases'][0]['layer'] = 'native_postgres'
    elif mutation == 'sql-failure':
        report['results'][0]['status'] = 'FAIL'
    else:
        report['counts']['skipped'] = True
    (directory / filename).write_text(json.dumps(report))
    refresh_manifest(directory, filename)
    with pytest.raises(ci.EvidenceError):
        ci.collect(root, inputs, CONTEXT)


def test_pack_requires_preparation_and_refuses_tree_changes(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    with pytest.raises(ci.EvidenceError, match='missing'):
        ci.pack(root, 'quality', CONTEXT)
    ci.prepare(root, 'quality', CONTEXT)
    create_reports(root, 'quality')
    (root / 'src').mkdir()
    (root / 'src/changed.py').write_text('CHANGED = True')
    with pytest.raises(ci.EvidenceError, match='Tree changed'):
        ci.pack(root, 'quality', CONTEXT)


def test_scheduled_collection_requires_the_nightly_mutation_catalogue(collection, monkeypatch):
    root, inputs = collection
    monkeypatch.setenv('ACK_HYPOTHESIS_PROFILE', 'nightly')
    with pytest.raises(ci.EvidenceError, match='mutation_evidence_profile_invalid'):
        ci.collect(root, inputs, CONTEXT)


def test_prepare_discards_committed_prior_run_reports(tmp_path):
    root = tmp_path / 'source'
    (root / 'evidence').mkdir(parents=True)
    (root / 'evidence/README.md').write_text('Evidence interpretation')
    (root / 'evidence/reference-run.json').write_text('{"status":"PASS"}')
    ci.prepare(root, 'reference', CONTEXT)
    assert [path.name for path in (root / 'evidence').iterdir()] == ['README.md']


def test_alpha_workflow_filters_pushes_without_filtering_prs_or_required_events():
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    assert workflow['name'] == 'Alpha reference checks'
    events = workflow['on']
    assert set(events) == {'push', 'pull_request', 'workflow_dispatch', 'workflow_call', 'schedule'}
    # Branch and tag filters are separate in Actions: keeping only a branch
    # filter would silently remove tag validation. No PR base/path filter may
    # drop required checks for stacked PRs or documentation-only changes.
    assert events['push'] == {'branches': ['main'], 'tags': ['**']}
    for event in ('pull_request', 'workflow_dispatch', 'workflow_call'):
        assert events[event] == ''
    assert events['schedule'] == [{'cron': '23 3 * * *'}]
    assert set(workflow['jobs']) == {'quality', 'reference', 'native', 'mutation', 'package'}
    for name, job in workflow['jobs'].items():
        assert job.get('name', name) == name
        assert 'if' not in job


def test_ci_package_waits_for_same_run_required_jobs_and_rebuilds_mapping():
    workflow = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(workflow['jobs']['package']['needs']) == set(ci.KINDS)
    steps = workflow['jobs']['package']['steps']
    download = next(step for step in steps if step.get('uses', '').startswith('actions/download-artifact@'))
    assert download['with']['merge-multiple'] == 'false'
    assert 'github.run_id' in download['with']['pattern'] and 'github.run_attempt' in download['with']['pattern']
    assert 'github-token' not in download['with']
    commands = [step['run'] for step in steps if 'run' in step]
    collect = 'python tools/collect_ci_evidence.py collect --input build/ci-download'
    assert commands.index(collect) < commands.index('python tools/update_acceptance.py')
    assert commands.index('python tools/update_acceptance.py') < commands.index('python tools/public_audit.py --write-report')
    assert commands.index('python tools/public_audit.py --write-report') < commands.index('python tools/build_release.py')
    for kind in ci.KINDS:
        steps = workflow['jobs'][kind]['steps']
        commands = [step['run'] for step in steps if 'run' in step]
        prepare_index = commands.index(f'python tools/collect_ci_evidence.py prepare --kind {kind}')
        # Only the trust gate and the fresh-venv setup precede invalidation.
        assert prepare_index == 2
        assert 'test "$EVENT" != pull_request' in commands[0]
        assert 'python -m venv "$RUNNER_TEMP/venv"' in commands[1]
        assert 'python -m pip install --require-hashes -r requirements-quality.lock' in commands
        assert f'python tools/collect_ci_evidence.py pack --kind {kind}' in commands
        uploads = [step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@')]
        evidence_upload = next(step for step in uploads if step['with']['name'].startswith('zabalack-evidence-'))
        assert 'if' not in evidence_upload
        always = [step for step in steps if step.get('if') == 'always()']
        if kind == 'native':
            assert len(always) == 2
            assert always[0]['run'] == 'python tools/native_failure_summary.py'
            assert always[1]['with']['name'].startswith('zabalack-native-diagnostics-')
            assert always[1]['with']['path'] == 'evidence/native-failure-summary.json'
        else:
            assert not always
    assert workflow['env']['TZ'] == 'UTC'
    assert workflow['on']['schedule']


def test_inactive_publication_template_reuses_required_ci_and_verifies_before_upload():
    workflow = yaml.load((ROOT / 'release/publish.yml.example').read_text(), Loader=yaml.BaseLoader)
    assert workflow['jobs']['build']['uses'] == './.github/workflows/ci.yml'
    assert workflow['jobs']['approve']['needs'] == 'build'
    assert workflow['jobs']['publish']['needs'] == 'approve'
    steps = workflow['jobs']['approve']['steps']
    verify = next(i for i, step in enumerate(steps)
                  if step.get('run') == 'python tools/verify_release.py --candidate build/publish-input')
    gate = next(i for i, step in enumerate(steps) if 'public_audit.py --publication' in step.get('run', ''))
    upload = next(i for i, step in enumerate(steps) if step.get('uses', '').startswith('actions/upload-artifact@'))
    assert verify < gate < upload
    assert not (ROOT / '.github/workflows/publish.yml').exists()


def test_service_workflow_requires_explicit_enablement_and_exports_only_fixed_report():
    value = yaml.load((ROOT / '.github/workflows/deployment-e2e.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(value['on']) == {'workflow_dispatch'}
    assert value['permissions'] == {'contents': 'read'}
    job = value['jobs']['service-lab']
    assert job['if'] == "${{ vars.ZABALACK_DEPLOYMENT_E2E_ENABLED == 'true' }}"
    assert any(step.get('run') == 'flock -n /home/codex/zabalack-task-runs/shared-linux-lab.lock '
               'python tools/run_deployment_e2e.py --isolated-lab' for step in job['steps'])
    uploads = [step for step in job['steps'] if step.get('uses', '').startswith('actions/upload-artifact@')]
    assert len(uploads) == 1 and uploads[0]['with']['path'] == 'evidence/deployment-e2e-run.json'
    assert uploads[0]['if'] == 'always()'


def test_every_active_job_requires_the_dedicated_x64_runner_without_hosted_fallback():
    paths = list((ROOT / '.github/workflows').glob('*.yml'))
    assert {path.name for path in paths} == {'ci.yml', 'security.yml', 'codeql.yml', 'deployment-e2e.yml'}
    for path in paths:
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for job in workflow['jobs'].values():
            assert job['runs-on'] == ['self-hosted', 'Linux', 'X64', 'zabalack']
            assert 'continue-on-error' not in job
            for step in job['steps']:
                # Context comes from Actions; local preflight must never forge it.
                assert not any(key.startswith('GITHUB_') for key in step.get('env', {}))
                if step.get('uses', '').startswith('actions/upload-artifact@'):
                    assert step['with']['retention-days'] == '3'
                    assert step['with']['if-no-files-found'] == 'error'


def test_dependency_installations_use_a_new_environment_before_any_installs_or_evidence():
    for name in ('ci.yml', 'security.yml', 'deployment-e2e.yml'):
        workflow = yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)
        for job in workflow['jobs'].values():
            steps = job['steps']
            setup_index = next(i for i, step in enumerate(steps)
                               if step.get('uses', '').startswith('actions/setup-python@'))
            commands = [(i, step['run']) for i, step in enumerate(steps) if 'run' in step]
            venvs = [(i, script) for i, script in commands if 'python -m venv' in script]
            assert len(venvs) == 1
            venv_index, venv = venvs[0]
            assert setup_index < venv_index
            assert venv.splitlines() == [
                'test ! -e "$RUNNER_TEMP/venv"',
                'python -m venv "$RUNNER_TEMP/venv"',
                'printf \'%s\\n\' "$RUNNER_TEMP/venv/bin" >> "$GITHUB_PATH"',
            ]
            assert all(venv_index < i for i, script in commands
                       if 'pip install' in script or 'collect_ci_evidence.py' in script)


def test_both_service_entrypoints_hold_the_shared_host_lock_for_the_whole_runner():
    targets = {'ci.yml': ('native', 'tools/native_ci.py'),
               'deployment-e2e.yml': ('service-lab', 'tools/run_deployment_e2e.py')}
    for filename, (job_name, runner) in targets.items():
        workflow = yaml.load((ROOT / '.github/workflows' / filename).read_text(), Loader=yaml.BaseLoader)
        scripts = [step['run'] for step in workflow['jobs'][job_name]['steps'] if 'run' in step]
        invocations = [script for script in scripts if runner in script]
        retirement = (' --retire-empty-networks --network-evidence-root /var/lib/zabalack-evidence/native-networks'
                      if filename == 'ci.yml' else '')
        assert invocations == ['flock -n /home/codex/zabalack-task-runs/shared-linux-lab.lock '
                               f'python {runner} --isolated-lab' + retirement]
        assert not any('docker system prune' in script or 'down --volumes' in script for script in scripts)


def test_alpha_workflow_refuses_untrusted_revisions_before_any_checkout():
    """Every privileged job stops an outside revision before it runs anything.

    The jobs run on a persistent self-hosted runner whose account has
    root-equivalent Docker access and owns the retained private attempts, so a
    pull request from outside this repository must not reach a checkout. This
    gate is defence in depth and not the trust boundary — a pull request can
    edit this file — so `docs/self-hosted-runner.md` carries the boundary it
    supplements. What is asserted here is that the gate exists, is first, and
    is checked before any repository code is fetched or executed.
    """
    guarded = 0
    for path in sorted((ROOT / '.github/workflows').glob('*.yml')):
        workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        for name, job in workflow['jobs'].items():
            if job.get('runs-on') != ['self-hosted', 'Linux', 'X64', 'zabalack']:
                continue  # Nothing else may use this runner; see the assertion below.
            guarded += 1
            name = f'{path.name}/{name}'
            gate = job['steps'][0]
            assert 'uses' not in gate, f'{name}: an action runs before the trust gate'
            assert gate['env'] == {
                'EVENT': '${{ github.event_name }}',
                'HEAD_REPO': '${{ github.event.pull_request.head.repo.full_name }}',
                'THIS_REPO': '${{ github.repository }}',
            }, name
            # Compared in the shell from the environment, never interpolated into
            # the script, so a repository name cannot become part of the command.
            assert 'test "$EVENT" != pull_request || test "$HEAD_REPO" = "$THIS_REPO"' in gate['run'], name
            assert '${{' not in gate['run'], f'{name}: the gate interpolates into its own script'
            checkout = next(index for index, step in enumerate(job['steps'])
                            if step.get('uses', '').startswith('actions/checkout@'))
            assert checkout > 0, f'{name}: checkout precedes the trust gate'
    # Every job on the persistent runner, not only this workflow's: leaving one
    # ungated would leave the exposure open through that workflow instead.
    assert guarded == 8, guarded

