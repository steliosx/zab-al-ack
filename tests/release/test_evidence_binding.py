"""Run-record binding to the source/harness tree; mocked, no service evidence."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import evidence_utils
import public_audit
import record_real_zabbix
import run_reference_suite
import native_ci
import update_acceptance
from mutation_fixtures import passing_mutation_record

PASS_XML = ('<testsuites><testsuite><testcase classname="tests.real_zabbix.test_contract" '
            'name="test_T13_real_exact_original_event_read"/></testsuite></testsuites>')


def synthetic_tree(tmp_path):
    (tmp_path / 'src/pkg').mkdir(parents=True)
    (tmp_path / 'src/pkg/module.py').write_text('VALUE = 1\n')
    (tmp_path / 'sql').mkdir()
    (tmp_path / 'sql/001.sql').write_text('SELECT 1;\n')
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_x.py').write_text('def test_x(): pass\n')
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'tools/runner.py').write_text('print(1)\n')
    (tmp_path / 'lab/node_modules/dep').mkdir(parents=True)
    (tmp_path / 'lab/node_modules/dep/index.js').write_text('ignored\n')
    (tmp_path / 'lab/check.mjs').write_text('export default 1\n')
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "x"\n')
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs/notes.md').write_text('prose\n')
    (tmp_path / 'evidence').mkdir()
    return tmp_path


def test_digests_cover_source_and_harness_scopes_only(tmp_path):
    root = synthetic_tree(tmp_path)
    before = evidence_utils.code_digests(root)
    assert before == evidence_utils.code_digests(root)
    assert before['source'] != before['harness']
    # Documentation, evidence, build output and Node dependencies are outside both scopes.
    (root / 'docs/notes.md').write_text('edited prose\n')
    (root / 'evidence/report.json').write_text('{}')
    (root / 'lab/node_modules/dep/index.js').write_text('changed dependency\n')
    (root / 'tests/__pycache__').mkdir()
    (root / 'tests/__pycache__/x.pyc').write_bytes(b'\x00')
    assert evidence_utils.code_digests(root) == before
    # A harness edit changes only the harness digest; an implementation edit only the source digest.
    (root / 'tests/test_x.py').write_text('def test_x(): assert True\n')
    harness_changed = evidence_utils.code_digests(root)
    assert harness_changed['source'] == before['source'] and harness_changed['harness'] != before['harness']
    (root / 'sql/001.sql').write_text('SELECT 2;\n')
    both = evidence_utils.code_digests(root)
    assert both['source'] != before['source'] and both['harness'] == harness_changed['harness']
    # Renaming a file changes the digest even with identical contents.
    (root / 'src/pkg/module.py').rename(root / 'src/pkg/renamed.py')
    assert evidence_utils.code_digests(root)['source'] != both['source']


def test_setuptools_generated_metadata_does_not_invalidate_run_binding(tmp_path):
    root = synthetic_tree(tmp_path)
    # Execute the real setuptools metadata command used by editable installs
    # and source builds, without installing or downloading any dependency.
    (root / 'pyproject.toml').write_text('[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n')
    bound = {'code_digests': evidence_utils.code_digests(root)}
    result = subprocess.run([
        sys.executable, '-c',
        'from setuptools import setup; setup(name="zabalack-review-fixture", version="0.0.0", '
        'package_dir={"": "src"}, packages=["pkg"])', 'egg_info',
    ], cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    metadata = root / 'src/zabalack_review_fixture.egg-info/PKG-INFO'
    assert metadata.is_file() and b'Name: zabalack-review-fixture' in metadata.read_bytes()
    assert metadata not in public_audit.public_files(root)
    assert evidence_utils.code_digests(root) == bound['code_digests']
    assert evidence_utils.binding_error(bound, root, 'Reference') is None
    # A real source edit still invalidates the same run after metadata exists.
    (root / 'src/pkg/module.py').write_text('VALUE = 2\n')
    assert 'different source tree' in evidence_utils.binding_error(bound, root, 'Reference')


@pytest.mark.parametrize('name', ['.github/codeql/config.yml', 'release/attest.yml.example',
                                 'config/production.example.json', 'deploy/systemd/zabalack-worker.service'])
def test_security_policy_release_and_deployment_inputs_invalidate_evidence(tmp_path, name):
    root = synthetic_tree(tmp_path)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('synthetic original policy\n')
    bound = {'code_digests': evidence_utils.code_digests(root)}
    path.write_text('synthetic modified policy\n')
    assert 'different harness tree' in evidence_utils.binding_error(bound, root, 'Reference')


@pytest.mark.parametrize('record,message', [
    ({'phases': []}, 'carries no code digests'),
    ({'phases': [], 'code_digests': 'x'}, 'carries no code digests'),
    ({'phases': [], 'code_digests': {'source': 'abc', 'harness': 'abc'}}, 'digest is malformed'),
    ({'phases': [], 'code_digests': {'source': '0' * 64}}, 'digest is malformed'),
    ({'phases': [], 'code_digests': {'source': '0' * 64, 'harness': '0' * 64}}, 'different source tree'),
    ([], 'malformed'),
], ids=['missing', 'wrong-type', 'short', 'harness-missing', 'stale', 'not-an-object'])
def test_binding_error_explains_unbound_records(tmp_path, record, message):
    root = synthetic_tree(tmp_path)
    digests = record.get('code_digests') if isinstance(record, dict) else None
    if isinstance(digests, dict) and digests.get('source') == '0' * 64:
        # Exactly one scope is wrong in these cases; the other matches the tree.
        actual = evidence_utils.code_digests(root)
        if 'harness' in digests:
            digests['harness'] = actual['harness']   # stale: source differs
        else:
            digests['source'] = actual['source']     # harness-missing: source matches
    assert message in evidence_utils.binding_error(record, root, 'Reference')
    bound = {'phases': [], 'code_digests': evidence_utils.code_digests(root)}
    assert evidence_utils.binding_error(bound, root, 'Reference') is None


def seed_reference(tmp_path, digests=None):
    evidence = tmp_path / 'evidence'
    evidence.mkdir(exist_ok=True)
    for name in ('unit', 'wasm', 'real-zabbix', 'native-postgres'):
        (evidence / (name + '.xml')).write_text(PASS_XML)
    (evidence / 'reference-run.json').write_text(json.dumps({'phases': [
        {'layer': 'unit_mocked', 'junit': 'evidence/unit.xml'},
        {'layer': 'postgres_wasm', 'junit': 'evidence/wasm.xml'}],
        'code_digests': digests if digests is not None else evidence_utils.code_digests(tmp_path)}))
    return evidence


def stale(tmp_path):
    digests = evidence_utils.code_digests(tmp_path)
    return {'source': digests['source'], 'harness': '1' * 64}


@pytest.mark.parametrize('which', ['reference', 'native', 'real-zabbix'])
@pytest.mark.parametrize('flaw', ['unbound', 'stale'])
def test_acceptance_refuses_records_not_bound_to_this_tree(tmp_path, monkeypatch, which, flaw):
    root = synthetic_tree(tmp_path)
    evidence = seed_reference(root)
    if which == 'reference':
        report = json.loads((evidence / 'reference-run.json').read_text())
        if flaw == 'unbound':
            del report['code_digests']
        else:
            report['code_digests'] = stale(root)
        (evidence / 'reference-run.json').write_text(json.dumps(report))
    native = {'status': 'PASS', 'phases': [{'status': 'PASS', 'exit_code': 0, 'shutdown_exit_code': 0,
              'junit': 'evidence/native-postgres.xml'}], 'code_digests': evidence_utils.code_digests(root)}
    real = {'status': 'PASS', 'counts': {'passed': 1, 'failed': 0, 'skipped': 0}, 'junit': 'evidence/real-zabbix.xml',
            'code_digests': evidence_utils.code_digests(root)}
    target = {'native': native, 'real-zabbix': real}.get(which)
    if target is not None:
        if flaw == 'unbound':
            del target['code_digests']
        else:
            target['code_digests'] = stale(root)
    (evidence / 'native-run.json').write_text(json.dumps(native))
    (evidence / 'real-zabbix-run.json').write_text(json.dumps(real))
    monkeypatch.setattr(update_acceptance, 'ROOT', root)
    monkeypatch.setattr(update_acceptance.subprocess, 'run',
                        lambda *args, **kwargs: pytest.fail('Unbound evidence reached the mapper'))
    with pytest.raises(RuntimeError, match='rerun it on this tree'):
        update_acceptance.main()


def test_acceptance_maps_records_bound_to_this_tree_and_ignores_not_run(tmp_path, monkeypatch):
    root = synthetic_tree(tmp_path)
    evidence = seed_reference(root)
    (evidence / 'native-run.json').write_text(json.dumps({'status': 'PASS', 'phases': [
        {'status': 'PASS', 'exit_code': 0, 'shutdown_exit_code': 0, 'junit': 'evidence/native-postgres.xml'}],
        'code_digests': evidence_utils.code_digests(root)}))
    # An explicit NOT_RUN record needs no binding: it contributes nothing.
    (evidence / 'real-zabbix-run.json').write_text(json.dumps({'status': 'NOT_RUN', 'reason': 'synthetic'}))
    commands = []
    monkeypatch.setattr(update_acceptance, 'ROOT', root)
    monkeypatch.setattr(update_acceptance.subprocess, 'run',
                        lambda command, **kwargs: commands.append(command) or subprocess.CompletedProcess(command, 0))
    assert update_acceptance.main() == 0
    assert '--native-xml' in commands[0] and '--real-zabbix-xml' not in commands[0]


def test_reference_runner_binds_its_record_to_the_tree_before_running(tmp_path, monkeypatch):
    root = synthetic_tree(tmp_path)
    (root / 'build/evidence-raw').mkdir(parents=True)
    expected = evidence_utils.code_digests(root)

    def child(command, **kwargs):
        # A harness edit during the run must not be what the record is bound to.
        (root / 'tests/test_x.py').write_text('def test_x(): assert 1\n')
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(run_reference_suite, 'ROOT', root)
    monkeypatch.setattr(run_reference_suite.subprocess, 'run', child)
    monkeypatch.setattr(sys, 'argv', ['run_reference_suite.py'])
    assert run_reference_suite.main() == 1
    report = json.loads((root / 'evidence/reference-run.json').read_text())
    assert report['code_digests'] == expected
    assert report['code_digests'] != evidence_utils.code_digests(root)


def test_native_runner_binds_its_record_to_the_tree(tmp_path, monkeypatch):
    root = synthetic_tree(tmp_path)
    (root / 'build/evidence-raw').mkdir(parents=True)
    monkeypatch.setattr(native_ci, 'ROOT', root)
    monkeypatch.setattr(native_ci, 'ensure_local_docker', lambda: (_ for _ in ()).throw(RuntimeError('synthetic')))
    monkeypatch.setattr(sys, 'argv', ['native_ci.py', '--isolated-lab', '--phase', 'postgres'])
    assert native_ci.main() == 2
    report = json.loads((root / 'evidence/native-run.json').read_text())
    assert report['status'] == 'NOT_RUN' and report['code_digests'] == evidence_utils.code_digests(root)


# Execution-owned real-Zabbix recorder regressions are in test_real_zabbix_recorder.py.

@pytest.mark.parametrize('case', ['reference-missing', 'reference-unbound', 'native-stale', 'native-running',
                                  'real-invalid', 'acceptance-missing', 'acceptance-unbound',
                                  'acceptance-stale', 'bound'])
def test_public_audit_requires_final_records_bound_to_the_tree(tmp_path, case):
    root = synthetic_tree(tmp_path)
    digests = evidence_utils.code_digests(root)
    evidence = root / 'evidence'
    if case != 'reference-missing':
        (evidence / 'reference-run.json').write_text(json.dumps(
            {'phases': [], 'all_executed_phases_succeeded': True} if case == 'reference-unbound' else {'phases': [], 'all_executed_phases_succeeded': True, 'code_digests': digests}))
    native = {'status': 'PASS', 'phases': [], 'code_digests': digests}
    if case == 'native-stale':
        native['code_digests'] = {'source': digests['source'], 'harness': '2' * 64}
    if case == 'native-running':
        native['status'] = 'RUNNING'
    (evidence / 'native-run.json').write_text(json.dumps(native))
    (evidence / 'real-zabbix-run.json').write_text('<broken' if case == 'real-invalid' else json.dumps({'status': 'NOT_RUN'}))
    if case != 'acceptance-missing':
        acceptance = {'code_digests': digests}
        if case == 'acceptance-unbound':
            acceptance = {}
        elif case == 'acceptance-stale':
            acceptance = {'code_digests': {'source': '0' * 64, 'harness': '0' * 64}}
        (evidence / 'acceptance-results.json').write_text(json.dumps(acceptance))
    (evidence / 'quality-run.json').write_text(json.dumps({'status': 'PASS', 'code_digests': digests}))
    (evidence / 'mutation-run.json').write_text(json.dumps(passing_mutation_record(root)))
    rules = {finding['rule'] for finding in public_audit.evidence_binding_findings(root)}
    expected = {'reference-missing': {'evidence-record-missing'}, 'reference-unbound': {'evidence-not-bound-to-tree'},
                'native-stale': {'evidence-not-bound-to-tree'}, 'native-running': {'evidence-record-not-final'},
                'real-invalid': {'evidence-record-invalid'},
                'acceptance-missing': {'evidence-record-missing'},
                'acceptance-unbound': {'evidence-not-bound-to-tree'},
                'acceptance-stale': {'evidence-not-bound-to-tree'}, 'bound': set()}[case]
    assert rules == expected


def test_repository_root_helpers_import_under_plain_pytest(tmp_path):
    """`pytest` from any working directory collects the optional suites (pythonpath = ['.'])."""
    result = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider',
                             str(ROOT / 'tests/real_zabbix'), str(ROOT / 'tests/security/test_real_zabbix_lab_boundary.py')],
                            cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert 'test_end_to_end.py' in result.stdout and 'test_real_zabbix_lab_boundary.py' in result.stdout


@pytest.mark.parametrize('flaw', ['missing', 'survivor', 'stale', 'truncated'])
def test_export_requires_complete_current_behavioral_mutation_evidence(tmp_path, flaw):
    root = synthetic_tree(tmp_path)
    report = passing_mutation_record(root)
    path = root / 'evidence/mutation-run.json'
    if flaw == 'survivor':
        report['mutants'][0]['outcome'] = 'SURVIVED'
    elif flaw == 'stale':
        report['code_digests']['harness'] = 'f' * 64
    elif flaw == 'truncated':
        report['mutants'].pop()
    if flaw != 'missing':
        path.write_text(json.dumps(report))
    findings = [row for row in public_audit.evidence_binding_findings(root)
                if row['path'] == 'evidence/mutation-run.json']
    assert findings
    assert any(row['rule'] == ('evidence-record-missing' if flaw == 'missing' else 'mutation-gate-invalid')
               for row in findings)


def test_interrupted_reference_run_cannot_leave_prior_success(tmp_path, monkeypatch):
    root = synthetic_tree(tmp_path)
    record = root / 'evidence/reference-run.json'
    record.write_text(json.dumps({'status': 'PASS', 'all_executed_phases_succeeded': True,
                                 'code_digests': evidence_utils.code_digests(root)}))
    monkeypatch.setattr(run_reference_suite, 'ROOT', root)
    monkeypatch.setattr(sys, 'argv', ['run_reference_suite.py'])

    def child(*args, **kwargs):
        assert json.loads(record.read_text())['status'] == 'RUNNING'
        raise KeyboardInterrupt

    monkeypatch.setattr(run_reference_suite.subprocess, 'run', child)
    with pytest.raises(KeyboardInterrupt):
        run_reference_suite.main()
    findings = public_audit.evidence_binding_findings(root)
    assert {'path': 'evidence/reference-run.json', 'rule': 'evidence-record-not-final'} in findings
