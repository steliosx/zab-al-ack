"""Recorder failure regressions use a fake pytest process, never real services."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import evidence_utils
import record_real_zabbix as recorder

_REAL_SUBPROCESS_RUN = subprocess.run

PASS_XML = ('<testsuites><testsuite hostname="synthetic-host"><testcase '
            'classname="tests.real_zabbix.test_contract" name="test_T13_real_exact_original_event_read">'
            '<properties><property name="zabbix_api_version" value="7.0.10"/></properties>'
            '</testcase></testsuite></testsuites>')


@pytest.fixture
def run_lab(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    (root / 'src').mkdir(parents=True)
    (root / 'src/module.py').write_text('VALUE = 1\n')
    (root / 'tests').mkdir()
    (root / 'tests/test_x.py').write_text('def test_x(): pass\n')
    (root / 'evidence').mkdir()
    old = {'status': 'PASS', 'zabbix': {'version': '7.0.0'}, 'fixture': {'private': 'old-secret'},
           'executed': ['old claim'], 'code_under_test': 'old-private-description'}
    (root / recorder.RECORD).write_text(json.dumps(old))
    (root / recorder.PUBLIC).write_text(PASS_XML)
    token = tmp_path / 'private-lab-token'
    witness = tmp_path / 'private-witness-token'
    token.write_text('synthetic-main-token-value\n')
    witness.write_text('synthetic-witness-token-value\n')
    fixture = tmp_path / 'private-fixture.json'
    data = {'isolated_lab': True, 'fresh_instance_no_notifications': True,
            'url': 'http://127.0.0.1:58080/api_jsonrpc.php', 'token_file': str(token),
            'outside_scope_source': {'witness_token_file': str(witness)}}
    fixture.write_text(json.dumps(data))
    monkeypatch.setenv('ACK_TEST_REAL_ZABBIX_LAB', 'YES_ISOLATED_LAB')
    monkeypatch.setenv('ACK_TEST_REAL_ZABBIX_FIXTURE', str(fixture))
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k nothing')
    monkeypatch.setenv('PYTEST_PLUGINS', 'unapproved_plugin')
    monkeypatch.setattr(recorder, 'ROOT', root)
    state = {'xml': PASS_XML, 'returncode': 0, 'calls': [], 'callback': None, 'exception': None}

    def child(command, **kwargs):
        current = json.loads((root / recorder.RECORD).read_text())
        assert current['status'] == 'RUNNING' and not (root / recorder.PUBLIC).exists()
        assert current['code_digests'] == evidence_utils.code_digests(root)
        assert kwargs['cwd'] == root and kwargs['timeout'] == 300 and kwargs['check'] is False
        assert kwargs['env']['TZ'] == 'UTC'
        assert 'PYTEST_ADDOPTS' not in kwargs['env'] and 'PYTEST_PLUGINS' not in kwargs['env']
        assert kwargs['env']['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] == '1'
        assert kwargs['env']['PYTHONPATH'] == str(root / 'src')
        if state.get('probe_import'):
            probe = _REAL_SUBPROCESS_RUN([sys.executable, '-c', 'import zabalack; print(zabalack.__file__)'],
                                        cwd=root, env=kwargs['env'], capture_output=True, text=True, timeout=10)
            assert probe.returncode == 0, probe.stderr
            assert Path(probe.stdout.strip()) == root / 'src/zabalack/__init__.py'
        assert 'tests/real_zabbix' in command and 'pytest_asyncio.plugin' in command
        raw = Path(next(arg.split('=', 1)[1] for arg in command if arg.startswith('--junitxml=')))
        assert not raw.exists() and raw.parent.stat().st_mode & 0o077 == 0
        state['calls'].append(command)
        if state['callback']:
            state['callback']()
        if state['exception']:
            raise state['exception']
        if state['xml'] is not None:
            raw.write_text(state['xml'])
        return subprocess.CompletedProcess(command, state['returncode'])

    monkeypatch.setattr(recorder.subprocess, 'run', child)

    def run(*args):
        monkeypatch.setattr(sys, 'argv', ['record_real_zabbix.py', *(args or ('--run',))])
        result = recorder.main()
        return result, json.loads((root / recorder.RECORD).read_text())

    return root, state, run, fixture, token, witness


def test_recorder_owns_execution_uses_preexecution_digests_and_drops_old_claims(run_lab):
    root, state, run, *_ = run_lab
    expected = evidence_utils.code_digests(root)
    code, record = run()
    assert code == 0 and record['status'] == 'PASS' and record['pytest_exit_code'] == 0
    assert len(state['calls']) == 1 and record['code_digests'] == expected
    assert record['counts'] == {'passed': 1, 'failed': 0, 'skipped': 0}
    assert record['observed_api_versions'] == ['7.0.10']
    assert not {'zabbix', 'fixture', 'executed', 'code_under_test'} & record.keys()
    assert 'hostname' not in (root / recorder.PUBLIC).read_text()


def test_arbitrary_old_junit_cannot_be_rebound_to_new_tree(run_lab):
    root, state, run, *_ = run_lab
    old = root / 'old.xml'
    old.write_text(PASS_XML)
    (root / 'src/module.py').write_text('VALUE = 2\n')
    code, record = run('--junit', str(old))
    assert code != 0 and record['status'] == 'FAIL' and record['reason'] == 'unowned_junit_import_disabled'
    assert state['calls'] == [] and not (root / recorder.PUBLIC).exists() and 'junit' not in record


@pytest.mark.parametrize('which', ['source', 'harness'])
def test_recorder_refuses_edit_during_execution(run_lab, which):
    root, state, run, *_ = run_lab
    before = evidence_utils.code_digests(root)
    file = root / ('src/module.py' if which == 'source' else 'tests/test_x.py')
    state['callback'] = lambda: file.write_text('CHANGED = 1\n')
    code, record = run()
    assert code == 1 and record['status'] == 'FAIL' and record['reason'] == 'tree_changed_during_run'
    assert record['code_digests'] == before and not (root / recorder.PUBLIC).exists()


@pytest.mark.parametrize('which', ['token', 'fixture'])
def test_recorder_refuses_changed_private_inputs(run_lab, which):
    root, state, run, fixture, token, _ = run_lab
    if which == 'token':
        state['callback'] = lambda: token.write_text('changed-private-token\n')
    else:
        state['callback'] = lambda: fixture.write_text('{}')
    code, record = run()
    assert code != 0 and record['status'] == 'FAIL' and not (root / recorder.PUBLIC).exists()


@pytest.mark.parametrize('which', ['fixture', 'main-token', 'witness-token', 'forbid-file'])
def test_unreadable_private_input_cannot_leave_unchecked_xml_or_old_pass(run_lab, which):
    root, state, run, fixture, token, witness = run_lab
    args = ['--run']
    if which == 'forbid-file':
        args += ['--forbid-file', str(root / 'missing-private-forbid-file')]
    else:
        {'fixture': fixture, 'main-token': token, 'witness-token': witness}[which].unlink()
    code, record = run(*args)
    assert code == 1 and record['status'] == 'FAIL'
    assert state['calls'] == [] and not (root / recorder.PUBLIC).exists() and 'junit' not in record


@pytest.mark.parametrize('which', ['fixture-path', 'token-path', 'token-value', 'witness-value', 'literal', 'json-version'])
def test_no_xml_or_json_export_on_forbidden_material(run_lab, which):
    root, state, run, fixture, token, witness = run_lab
    value = {'fixture-path': fixture.name, 'token-path': token.name,
             'token-value': token.read_text().strip(), 'witness-value': witness.read_text().strip(),
             'literal': 'forbidden-test-string', 'json-version': '7.0.10'}[which]
    # The version lives in both the staged XML and the JSON metadata derived from it.
    if which != 'json-version':
        state['xml'] = PASS_XML.replace('</testcase>', '<system-out>' + value + '</system-out></testcase>')
    code, record = run('--run', '--forbid', 'forbidden-test-string', *(['--forbid', value] if which == 'json-version' else []))
    assert code == 1 and record['status'] == 'FAIL' and record['reason'] == 'forbidden_material_in_public_evidence'
    assert value not in json.dumps(record) and not (root / recorder.PUBLIC).exists()


@pytest.mark.parametrize('problem', ['missing', 'malformed', 'empty', 'foreign-scope'])
def test_unusable_fresh_junit_fails_closed(run_lab, problem):
    root, state, run, *_ = run_lab
    state['xml'] = {'missing': None, 'malformed': '<bad', 'empty': '<testsuites/>',
                    'foreign-scope': PASS_XML.replace('tests.real_zabbix.', 'tests.other.')}[problem]
    code, record = run()
    assert code == 1 and record['status'] == 'FAIL' and not (root / recorder.PUBLIC).exists()


@pytest.mark.parametrize('exit_code', [1, 2, 3, 4, 5])
def test_pytest_nonzero_exit_cannot_be_promoted_even_with_passing_xml(run_lab, exit_code):
    _, state, run, *_ = run_lab
    state['returncode'] = exit_code
    code, record = run()
    assert code == exit_code and record['pytest_exit_code'] == exit_code and record['status'] == 'FAIL'


@pytest.mark.parametrize('tag', ['failure', 'error', 'skipped'])
def test_nonpassing_cases_are_preserved_even_when_process_returns_zero(run_lab, tag):
    _, state, run, *_ = run_lab
    state['xml'] = PASS_XML.replace('</testcase>', '<' + tag + '/></testcase>')
    code, record = run()
    assert code == 1 and record['status'] == 'FAIL' and record['pytest_exit_code'] == 0
    assert record['counts']['skipped' if tag == 'skipped' else 'failed'] == 1


@pytest.mark.parametrize('exception', [subprocess.TimeoutExpired(['private-command'], 300, output=b'private-token'),
                                      OSError('private-exception'), RuntimeError('private-exception'), KeyboardInterrupt()])
def test_execution_failures_do_not_retain_old_pass_or_expose_exception(run_lab, exception, capsys):
    root, state, run, *_ = run_lab
    state['exception'] = exception
    code, record = run()
    assert code != 0 and record['status'] == 'FAIL' and not (root / recorder.PUBLIC).exists()
    assert 'private-' not in capsys.readouterr().out and 'private-' not in json.dumps(record)


def test_not_run_exports_fixed_reason_and_drops_private_prior_metadata(run_lab, capsys):
    root, state, run, *_ = run_lab
    code, record = run('--not-run', 'token=private-token-fixture=/private/path')
    assert code == 0 and record['status'] == 'NOT_RUN' and record['reason'] == 'operator_declared_unavailable'
    assert record['counts'] == {'passed': 0, 'failed': 0, 'skipped': 0} and 'junit' not in record
    assert 'private' not in capsys.readouterr().out and 'private' not in json.dumps(record)
    assert state['calls'] == [] and not (root / recorder.PUBLIC).exists()


def test_lab_guard_precedes_reading_tokens_and_starting_pytest(run_lab):
    root, state, run, fixture, token, _ = run_lab
    data = json.loads(fixture.read_text())
    data['url'] = 'https://example.invalid/api_jsonrpc.php'
    fixture.write_text(json.dumps(data))
    token.unlink()
    code, record = run()
    assert code == 1 and record['status'] == 'FAIL' and state['calls'] == []
    assert not (root / recorder.PUBLIC).exists()


def test_owned_child_imports_current_src_despite_foreign_pythonpath(run_lab, monkeypatch, tmp_path):
    root, state, run, *_ = run_lab
    (root / 'src/zabalack').mkdir()
    (root / 'src/zabalack/__init__.py').write_text('VERSION = "synthetic-owned-tree"\n')
    foreign = tmp_path / 'foreign'
    (foreign / 'zabalack').mkdir(parents=True)
    (foreign / 'zabalack/__init__.py').write_text('raise RuntimeError("foreign tree imported")\n')
    monkeypatch.setenv('PYTHONPATH', str(foreign))
    state['probe_import'] = True
    code, record = run()
    assert code == 0 and record['status'] == 'PASS'
