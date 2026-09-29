"""Mutation evidence cannot turn test infrastructure failure into a killed mutant."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import run_mutation_checks as runner
from evidence_utils import code_digests
from mutation_catalog import CATALOG, selected


def counts(**changes):
    result = {'tests': 2, 'failures': 0, 'errors': 0, 'skipped': 0,
              'assertion_failures': 0, 'case_digest': 'a' * 64}
    result.update(changes)
    return result


def passed(root):
    baseline = counts(outcome='BASELINE_PASS', exit_code=0)
    names = [item.name for item in selected('pr')]
    return {'schema_version': 1, 'status': 'PASS', 'profile': 'pr',
            'code_digests': code_digests(root), 'tree_unchanged': True,
            'baseline': baseline, 'selected_count': len(names),
            'mutants': [dict(counts(failures=1, assertion_failures=1), name=name,
                             outcome='KILLED', exit_code=1) for name in names],
            'counts': {key: len(names) if key == 'KILLED' else 0 for key in runner.OUTCOMES}}


@pytest.mark.parametrize('exit_code,changes,expected', [
    (0, {}, 'SURVIVED'),
    (1, {'failures': 1, 'assertion_failures': 1}, 'KILLED'),
    (1, {'failures': 1}, 'ERROR'),
    (1, {'failures': 1, 'assertion_failures': 1, 'errors': 1}, 'ERROR'),
    (1, {'failures': 1, 'assertion_failures': 1, 'skipped': 1}, 'ERROR'),
    (1, {'failures': 1, 'assertion_failures': 1, 'tests': 1}, 'ERROR'),
    (1, {'failures': 1, 'assertion_failures': 1, 'case_digest': 'b' * 64}, 'ERROR'),
    (2, {'failures': 1, 'assertion_failures': 1}, 'ERROR'),
    (3, {'failures': 1, 'assertion_failures': 1}, 'ERROR'),
    (5, {}, 'ERROR'),
    (0, {'failures': 1, 'assertion_failures': 1}, 'ERROR'),
])
def test_only_matching_assertion_failure_kills(exit_code, changes, expected):
    assert runner.classify(exit_code, counts(**changes), counts()) == expected


def test_failed_baseline_is_never_a_kill():
    assert runner.classify(1, counts(failures=1, assertion_failures=1)) == 'ERROR'


@pytest.mark.parametrize('before,after,error', [
    ('missing', 'False', ValueError), ('True', 'True', ValueError),
    ('', 'False', ValueError), ('True', 'if', SyntaxError),
])
def test_invalid_mutation_is_not_applied(before, after, error):
    with pytest.raises(error):
        runner.mutate('answer = True\n', before, after)


def test_ambiguous_anchor_requires_review():
    with pytest.raises(ValueError, match='mutation_anchor_not_unique'):
        runner.mutate('x = True\ny = True\n', 'True', 'False')


def test_catalogue_has_unique_reviewed_targets_and_expanded_nightly_profile():
    assert len({item.name for item in CATALOG}) == len(CATALOG)
    assert len(selected('nightly')) > len(selected('pr')) > 0
    for item in CATALOG:
        assert item.invariant.startswith('T')
        assert item.path.startswith('src/zabalack/') and item.path.endswith('.py')
        original = (ROOT / item.path).read_text()
        assert runner.mutate(original, item.before, item.after) != original


@pytest.mark.parametrize('xml,outcome', [
    ('<testcase classname="unit" name="check"/>', 'SURVIVED'),
    ('<testcase classname="unit" name="check"><failure type="AssertionError"/></testcase>', 'KILLED'),
    ('<testcase classname="unit" name="check"><failure type="Failed"/></testcase>', 'KILLED'),
    ('<testcase classname="unit" name="check"><failure type="KeyError"/></testcase>', 'ERROR'),
    ('<testcase classname="unit" name="check"><error type="ImportError"/></testcase>', 'ERROR'),
    ('<testcase classname="unit" name="check"><skipped/></testcase>', 'ERROR'),
])
def test_junit_distinguishes_assertion_runtime_and_setup_failures(tmp_path, xml, outcome):
    path = tmp_path / 'result.xml'
    path.write_text('<testsuite>' + xml + '</testsuite>')
    observed = runner._junit(path)
    baseline = dict(observed, failures=0, errors=0, skipped=0, assertion_failures=0)
    code = 0 if outcome == 'SURVIVED' else 1
    assert runner.classify(code, observed, baseline) == outcome


@pytest.mark.parametrize('xml', ['not xml', '<testsuite/>',
    '<testsuite><testcase name="missing-class"/></testsuite>',
    '<testsuite><testcase classname="c" name="x"/><testcase classname="c" name="x"/></testsuite>'])
def test_incomplete_or_ambiguous_junit_is_not_evidence(tmp_path, xml):
    path = tmp_path / 'result.xml'
    path.write_text(xml)
    with pytest.raises((ValueError, runner.ET.ParseError)):
        runner._junit(path)


def test_current_complete_report_validates(tmp_path):
    runner.validate_report(passed(tmp_path), tmp_path, expected_profile='pr')


@pytest.mark.parametrize('change', [
    lambda record: record.update(status='RUNNING'),
    lambda record: record.update(schema_version=True),
    lambda record: record.update(tree_unchanged=False),
    lambda record: record.update(profile='nightly'),
    lambda record: record.update(selected_count=1),
    lambda record: record.update(code_digests={'source': 'b' * 64, 'harness': 'b' * 64}),
    lambda record: record['baseline'].update(outcome='ERROR'),
    lambda record: record['baseline'].update(exit_code=False),
    lambda record: record['baseline'].update(tests=0),
    lambda record: record['baseline'].update(errors=1),
    lambda record: record['baseline'].update(case_digest='missing'),
    lambda record: record['mutants'].pop(),
    lambda record: record['mutants'].append(record['mutants'][0]),
    lambda record: record['mutants'][0].update(outcome='SURVIVED'),
    lambda record: record['mutants'][0].update(outcome='TIMEOUT'),
    lambda record: record['mutants'][0].update(outcome='INVALID'),
    lambda record: record['mutants'][0].update(outcome='NOT_RUN'),
    lambda record: record['mutants'][0].update(exit_code=2),
    lambda record: record['mutants'][0].update(assertion_failures=0),
    lambda record: record['mutants'][0].update(assertion_failures=True),
    lambda record: record['mutants'][0].update(failures=3, assertion_failures=3),
    lambda record: record['mutants'][0].update(skipped=1),
    lambda record: record['mutants'][0].update(tests=1),
    lambda record: record['mutants'][0].update(case_digest='b' * 64),
    lambda record: record['counts'].update(KILLED=0),
    lambda record: record['counts'].update(ERROR=False),
])
def test_rejects_incomplete_false_or_stale_mutation_evidence(tmp_path, change):
    record = passed(tmp_path)
    change(record)
    with pytest.raises(ValueError):
        runner.validate_report(record, tmp_path)


def test_required_profile_cannot_be_substituted(tmp_path):
    with pytest.raises(ValueError, match='mutation_evidence_profile_invalid'):
        runner.validate_report(passed(tmp_path), tmp_path, expected_profile='nightly')


def fixture_tree(tmp_path):
    root = tmp_path / 'root'
    for name in runner.COPY_DIRS:
        (root / name).mkdir(parents=True)
    module = root / 'src/example.py'
    module.write_text('value = True\n')
    return root, module


@pytest.mark.parametrize('mode', ['baseline_error', 'survived', 'timeout', 'interrupt', 'tree_edit', 'killed'])
def test_disposable_run_restores_source_and_rejects_false_success(tmp_path, monkeypatch, mode):
    root, module = fixture_tree(tmp_path)
    original = module.read_bytes()
    item = runner.selected('pr')[0]
    item = type(item)('example', 'src/example.py', 'True', 'False', 'T1: synthetic runner fixture')
    monkeypatch.setattr(runner, 'selected', lambda profile: (item,))
    record_path = root / 'evidence/mutation-run.json'
    record_path.parent.mkdir()
    record_path.write_text('{"status":"PASS"}')
    calls = []

    def execute(tree, raw, label, timeout, baseline=None):
        calls.append(label)
        assert json.loads(record_path.read_text())['status'] == 'RUNNING'
        assert module.read_bytes() == original
        if baseline is None:
            return counts(outcome='ERROR' if mode == 'baseline_error' else 'BASELINE_PASS', exit_code=0)
        assert (tree / 'src/example.py').read_text() == 'value = False\n'
        if mode == 'interrupt':
            raise KeyboardInterrupt
        if mode == 'tree_edit':
            (root / 'tests/changed.py').write_text('# concurrent change\n')
        outcome = {'survived': 'SURVIVED', 'timeout': 'TIMEOUT'}.get(mode, 'KILLED')
        return counts(outcome=outcome, exit_code=1, failures=1, assertion_failures=1)

    monkeypatch.setattr(runner, 'run_pytest', execute)
    assert runner.run(root) == (0 if mode == 'killed' else 1)
    report = json.loads(record_path.read_text())
    assert report['status'] == ('PASS' if mode == 'killed' else 'FAIL')
    assert module.read_bytes() == original
    assert not list((root / 'build/private/mutation').glob('run-*/tree'))
    if mode == 'baseline_error':
        assert calls == ['baseline'] and report['counts']['NOT_RUN'] == 1
    if mode == 'tree_edit':
        assert report['tree_unchanged'] is False


def test_copy_refuses_symlinked_sources(tmp_path):
    root, module = fixture_tree(tmp_path)
    (root / 'src/alias.py').symlink_to(module)
    with pytest.raises(ValueError, match='mutation_source_symlink'):
        runner._copy_tree(root, tmp_path / 'copy')


def test_report_never_follows_symlink(tmp_path):
    target = tmp_path / 'target'
    target.write_text('unchanged')
    link = tmp_path / 'report'
    link.symlink_to(target)
    with pytest.raises(ValueError, match='mutation_report_symlink'):
        runner._atomic_json(link, {'status': 'PASS'})
    assert target.read_text() == 'unchanged'


@pytest.mark.parametrize('change', [
    lambda data: data.update(tests=True),
    lambda data: data.update(tests=2),
    lambda data: data.update(assertion_failures=True),
    lambda data: data.update(assertion_failures=2),
    lambda data: data.update(case_digest='invalid'),
    lambda data: data.update(unexpected='field'),
])
def test_pytest_callback_and_junit_must_agree(tmp_path, change):
    junit = tmp_path / 'result.xml'
    junit.write_text('<testsuite><testcase classname="c" name="x"><failure message="redacted"/></testcase></testsuite>')
    events = {'tests': 1, 'assertion_failures': 1, 'case_digest': 'a' * 64}
    change(events)
    path = tmp_path / 'events.json'
    path.write_text(json.dumps(events))
    with pytest.raises(ValueError):
        runner._junit(junit, path)


def test_real_pytest_assertion_runtime_skip_and_import_provenance(tmp_path, monkeypatch):
    """Execute the pinned pytest hooks, rather than mocking their report shape."""
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    root, _ = fixture_tree(tmp_path)
    (root / 'src/zabalack').mkdir()
    (root / 'src/zabalack/__init__.py').write_text('value = True\n')
    test = root / 'tests/test_boundary.py'
    test.write_text('import zabalack\ndef test_boundary():\n    assert zabalack.value is True\n')
    tree = tmp_path / 'tree'
    runner._copy_tree(root, tree)
    raw = tmp_path / 'raw'
    raw.mkdir()
    monkeypatch.setattr(runner, 'TESTS', ('tests/test_boundary.py',))
    baseline = runner.run_pytest(tree, raw, 'baseline', 10)
    assert baseline['outcome'] == 'BASELINE_PASS'
    module = tree / 'src/zabalack/__init__.py'
    module.write_text('value = False\n')
    assert runner.run_pytest(tree, raw, 'assertion', 10, baseline)['outcome'] == 'KILLED'
    module.write_text('raise RuntimeError("synthetic import failure")\n')
    assert runner.run_pytest(tree, raw, 'import-error', 10, baseline)['outcome'] == 'ERROR'
    module.write_text('value = True\n')
    copied_test = tree / 'tests/test_boundary.py'
    copied_test.write_text('import zabalack\ndef test_boundary():\n    raise KeyError("synthetic runtime failure")\n')
    assert runner.run_pytest(tree, raw, 'runtime-error', 10, baseline)['outcome'] == 'ERROR'
    copied_test.write_text('import zabalack\nimport pytest\ndef test_boundary():\n    pytest.skip("synthetic unavailable service")\n')
    assert runner.run_pytest(tree, raw, 'skip', 10, baseline)['outcome'] == 'ERROR'
    copied_test.write_text('import zabalack\ndef test_boundary():\n    zabalack.__file__ = "/untrusted/zabalack/__init__.py"\n')
    assert runner.run_pytest(tree, raw, 'wrong-source', 10, baseline)['outcome'] == 'ERROR'
    copied_test.write_text('import zabalack\nimport time\ndef test_boundary():\n    time.sleep(30)\n')
    assert runner.run_pytest(tree, raw, 'timeout', 1, baseline)['outcome'] == 'TIMEOUT'


def test_profiles_and_prior_attempts_survive_new_invocations(tmp_path, monkeypatch):
    root, _ = fixture_tree(tmp_path)
    entry = type(runner.selected('pr')[0])('example', 'src/example.py', 'True', 'False', 'T1: synthetic')
    monkeypatch.setattr(runner, 'selected', lambda profile: (entry,))
    monkeypatch.setattr(runner, 'run_pytest', lambda tree, raw, label, timeout, baseline=None:
        counts(outcome='BASELINE_PASS' if baseline is None else 'KILLED',
               exit_code=0 if baseline is None else 1,
               failures=0 if baseline is None else 1, assertion_failures=0 if baseline is None else 1))
    assert runner.run(root, profile='pr') == 0
    original = (root / 'evidence/mutation-pr-run.json').read_bytes()
    first = next((root / 'build/private/mutation').glob('run-*/report.json'))
    old = first.read_bytes()
    assert runner.run(root, profile='nightly') == 0
    assert (root / 'evidence/mutation-pr-run.json').read_bytes() == original
    assert first.read_bytes() == old
    assert len(list((root / 'build/private/mutation').glob('run-*/report.json'))) == 2
    assert json.loads((root / 'evidence/mutation-run.json').read_text())['profile'] == 'nightly'


def test_published_records_are_readable_and_diagnostics_stay_private(tmp_path, monkeypatch):
    """A 0600 temporary file must not become the mode of an exported record."""
    root, _ = fixture_tree(tmp_path)
    entry = type(runner.selected('pr')[0])('example', 'src/example.py', 'True', 'False', 'T1: synthetic')
    monkeypatch.setattr(runner, 'selected', lambda profile: (entry,))
    monkeypatch.setattr(runner, 'run_pytest', lambda tree, raw, label, timeout, baseline=None:
        counts(outcome='BASELINE_PASS' if baseline is None else 'KILLED',
               exit_code=0 if baseline is None else 1,
               failures=0 if baseline is None else 1, assertion_failures=0 if baseline is None else 1))
    assert runner.run(root, profile='pr') == 0
    for name in ('evidence/mutation-run.json', 'evidence/mutation-pr-run.json'):
        assert (root / name).stat().st_mode & 0o777 == 0o644, name
    private = next((root / 'build/private/mutation').glob('run-*/report.json'))
    assert private.stat().st_mode & 0o777 == 0o600
    assert (root / 'build/private/mutation').stat().st_mode & 0o777 == 0o700


def test_baseline_tree_and_each_variant_are_retained_before_they_run(tmp_path, monkeypatch):
    root, module = fixture_tree(tmp_path)
    (root / 'tests/test_example.py').write_text('def test_example(): pass\n')
    entry = type(runner.selected('pr')[0])('example', 'src/example.py', 'True', 'False', 'T1: synthetic')
    monkeypatch.setattr(runner, 'selected', lambda profile: (entry,))
    seen = []

    def execute(tree, raw, label, timeout, baseline=None):
        # Both are already on disk when the child would start: no later reconstruction.
        assert (raw / 'tree-baseline/src/example.py').read_bytes() == b'value = True\n'
        assert (raw / 'tree-baseline/tests/test_example.py').is_file()
        if baseline is not None:
            assert (raw / 'variants' / label).read_bytes() == (tree / 'src/example.py').read_bytes() == b'value = False\n'
        seen.append(label)
        return counts(outcome='BASELINE_PASS' if baseline is None else 'KILLED', exit_code=0 if baseline is None else 1,
                      failures=0 if baseline is None else 1, assertion_failures=0 if baseline is None else 1)

    monkeypatch.setattr(runner, 'run_pytest', execute)
    assert runner.run(root) == 0 and seen == ['baseline', 'example']
    raw = next((root / 'build/private/mutation').glob('run-*'))
    assert json.loads((raw / 'variants/index.json').read_text()) == {'variants': [
        {'name': 'example', 'path': 'src/example.py', 'sha256': hashlib.sha256(b'value = False\n').hexdigest()}]}
    assert (raw / 'variants').stat().st_mode & 0o777 == 0o700
    # The working copy is still removed and the checkout never changes.
    assert not (raw / 'tree').exists() and module.read_bytes() == b'value = True\n'


def test_each_invocation_gets_an_owned_basetemp_that_is_retained(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path / 'system-temp'))
    (tmp_path / 'system-temp').mkdir()
    root, _ = fixture_tree(tmp_path)
    (root / 'src/zabalack').mkdir()
    (root / 'src/zabalack/__init__.py').write_text('value = True\n')
    (root / 'tests/test_scratch.py').write_text(
        'import zabalack\ndef test_scratch(tmp_path):\n    (tmp_path / "out.txt").write_text("kept")\n'
        '    assert zabalack.value is True\n')
    tree = tmp_path / 'tree'
    runner._copy_tree(root, tree)
    raw = tmp_path / 'raw'
    raw.mkdir()
    monkeypatch.setattr(runner, 'TESTS', ('tests/test_scratch.py',))
    assert runner.run_pytest(tree, raw, 'baseline', 10)['outcome'] == 'BASELINE_PASS'
    assert runner.run_pytest(tree, raw, 'again', 10)['outcome'] == 'BASELINE_PASS'
    roots = sorted((tmp_path / 'system-temp').glob('zab-*'))
    assert len(roots) == 2  # one owned root per invocation, never shared or rotated
    for label in ('baseline', 'again'):
        record = json.loads((raw / 'basetemp' / label / 'retention.json').read_text())
        assert record['status'] == 'COMPLETE' and json.loads(record['owner'])['label'] == 'mutation-' + label
        assert (raw / 'basetemp' / label / 'scratch/test_scratch0/out.txt').read_text() == 'kept'
    # The originals stay in place after the copy.
    assert all((path / 'b/test_scratch0/out.txt').is_file() for path in roots)
