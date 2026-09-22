"""Recorder failure regressions use a fake pytest process, never real services."""
import json
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

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
        assert raw.is_file() and raw.read_bytes() == b''
        if os.name == 'posix':
            assert raw.parent.stat().st_mode & 0o077 == 0
            assert stat.S_IMODE(raw.stat().st_mode) == 0o600
        state['calls'].append(command)
        kwargs['stdout'].write(b'private synthetic console output\n')
        kwargs['stdout'].flush()
        if state['xml'] is not None:
            raw.write_text(state['xml'])
        if state['callback']:
            state['callback']()
        if state['exception']:
            raise state['exception']
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


@pytest.mark.parametrize('outcome', ['pass', 'assertion-failure', 'timeout', 'interrupt', 'leak'])
def test_raw_files_survive_owned_attempt(run_lab, outcome):
    """Also a test-only baseline: old TemporaryDirectory loses these exact bytes."""
    root, state, run, *_ = run_lab
    if outcome == 'assertion-failure':
        state['xml'] = PASS_XML.replace('</testcase>', '<failure>synthetic failure</failure></testcase>')
        state['returncode'] = 1
    elif outcome == 'timeout':
        state['exception'] = subprocess.TimeoutExpired(['synthetic'], 300)
    elif outcome == 'interrupt':
        state['exception'] = KeyboardInterrupt()
    elif outcome == 'leak':
        state['xml'] = PASS_XML.replace('</testcase>', '<system-out>synthetic-main-token-value</system-out></testcase>')
    code, public = run()
    raw = Path(next(arg.split('=', 1)[1] for arg in state['calls'][0] if arg.startswith('--junitxml=')))
    assert raw.exists(), 'owned raw JUnit was deleted after execution'
    assert raw.read_text() == state['xml']
    assert (raw.parent / 'console.log').read_bytes() == b'private synthetic console output\n'
    manifest = json.loads((raw.parent / 'manifest.json').read_text())
    assert manifest['status'] == public['status'] == ('PASS' if outcome == 'pass' else 'FAIL')
    assert manifest['command'] == state['calls'][0]
    assert manifest['pytest_exit_code'] == public.get('pytest_exit_code')
    assert manifest['code_digests_before'] == manifest['code_digests_after'] == evidence_utils.code_digests(root)
    published = outcome in {'pass', 'assertion-failure'}
    assert manifest['published'] is published
    assert [event['phase'] for event in manifest['events']] == [
        'PREPARING', 'EXECUTING', 'FINISHED', *(['PUBLISHED'] if published else [])]
    assert manifest['started_at'] <= manifest['finished_at']
    for name, binding in manifest['files'].items():
        data = (raw.parent / name).read_bytes()
        assert binding == {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    assert (code == 0) == (outcome == 'pass')
    if outcome in {'timeout', 'interrupt', 'leak'}:
        assert not (root / recorder.PUBLIC).exists()


@pytest.mark.parametrize('mask', [0o000, 0o077])
def test_attempts_are_unique_private_and_leave_existing_ancestors_unchanged(run_lab, mask):
    root, state, run, *_ = run_lab
    parent = root / 'build/evidence-raw'
    parent.mkdir(parents=True)
    parent.chmod(0o755)
    old_modes = {path: path.stat().st_mode for path in (root, root / 'build', parent)}
    previous = os.umask(mask)
    try:
        assert run()[0] == 0
        first = next(parent.iterdir())
        snapshot = {path.name: path.read_bytes() for path in first.iterdir()}
        assert run()[0] == 0
    finally:
        os.umask(previous)
    assert len(list(parent.iterdir())) == 2 and len(state['calls']) == 2
    assert {path.name: path.read_bytes() for path in first.iterdir()} == snapshot
    assert {path: path.stat().st_mode for path in old_modes} == old_modes
    for attempt in parent.iterdir():
        assert attempt.is_dir()
        if os.name == 'posix':
            assert stat.S_IMODE(attempt.stat().st_mode) == 0o700
            assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in attempt.iterdir())
    assert (first / 'public.xml').read_bytes() == (root / recorder.PUBLIC).read_bytes()
    if os.name == 'posix':
        assert stat.S_IMODE((root / recorder.PUBLIC).stat().st_mode) == 0o644


@pytest.mark.parametrize('failure', ['retention', 'xml-publication', 'json-publication', 'publication-checkpoint'])
def test_finalization_failure_retains_attempt_and_never_leaves_public_pass(run_lab, monkeypatch, failure):
    root, state, run, *_ = run_lab
    if failure in {'retention', 'publication-checkpoint'}:
        original = recorder.PrivateAttempt.checkpoint

        def checkpoint(attempt, phase):
            if phase == ('FINISHED' if failure == 'retention' else 'PUBLISHED'):
                raise OSError('private finalization failure')
            original(attempt, phase)

        monkeypatch.setattr(recorder.PrivateAttempt, 'checkpoint', checkpoint)
    elif failure == 'xml-publication':
        def publish(*args):
            raise OSError('private publication failure')

        monkeypatch.setattr(recorder, 'publish_xml', publish)
    else:
        original = recorder.write_record

        def write(path, record):
            if record['status'] == 'PASS':
                raise OSError('private publication failure')
            original(path, record)

        monkeypatch.setattr(recorder, 'write_record', write)
    code, public = run()
    assert code == 1 and public['status'] == 'FAIL' and not (root / recorder.PUBLIC).exists()
    attempt = next((root / 'build/evidence-raw').iterdir())
    assert (attempt / 'run.xml').read_text() == state['xml']
    assert (attempt / 'console.log').read_bytes() == b'private synthetic console output\n'
    manifest = json.loads((attempt / 'manifest.json').read_text())
    assert manifest['published'] is False
    assert manifest['status'] == ('RUNNING' if failure == 'retention' else 'FAIL')
    if failure == 'retention':
        assert public['reason'] == 'private_retention_failed'


@pytest.mark.parametrize('boundary', ['before-xml', 'before-json', 'after-json'])
def test_interrupted_publication_is_not_recorded_as_published(run_lab, monkeypatch, boundary):
    root, state, run, *_ = run_lab
    if boundary == 'before-xml':
        def publish(*args):
            raise SystemExit(143)

        monkeypatch.setattr(recorder, 'publish_xml', publish)
    else:
        original = recorder.write_record

        def write(path, record):
            if record['status'] == 'PASS' and boundary == 'before-json':
                raise SystemExit(143)
            original(path, record)
            if record['status'] == 'PASS':
                raise SystemExit(143)

        monkeypatch.setattr(recorder, 'write_record', write)
    with pytest.raises(SystemExit):
        run()
    attempt = next((root / 'build/evidence-raw').iterdir())
    manifest = json.loads((attempt / 'manifest.json').read_text())
    assert manifest['status'] == 'PASS' and manifest['published'] is False
    assert manifest['events'][-1]['phase'] == 'FINISHED'
    assert (attempt / 'run.xml').read_text() == state['xml']
    public = json.loads((root / recorder.RECORD).read_text())
    assert public['status'] == ('PASS' if boundary == 'after-json' else 'RUNNING')
    assert (root / recorder.PUBLIC).exists() == (boundary != 'before-xml')


def test_unfinished_attempt_is_preserved_without_inferring_pass_from_xml(run_lab, monkeypatch):
    root, state, run, *_ = run_lab
    # Simulate an unhandled termination after child output. Unlike SIGKILL this
    # remains catchable by the test, without adding it to the recorder's handlers.
    state['exception'] = SystemExit(143)
    with pytest.raises(SystemExit):
        run()
    attempt = next((root / 'build/evidence-raw').iterdir())
    assert (attempt / 'run.xml').read_text() == state['xml']
    assert json.loads((attempt / 'manifest.json').read_text())['status'] == 'RUNNING'
    assert json.loads((root / recorder.RECORD).read_text())['status'] == 'RUNNING'
    assert not (root / recorder.PUBLIC).exists()


@pytest.mark.parametrize('part', ['build', 'evidence-raw'])
def test_symlink_ancestor_rejected_without_changing_external_permissions(run_lab, tmp_path, part):
    root, state, run, *_ = run_lab
    outside = tmp_path / 'outside'
    outside.mkdir()
    mode = outside.stat().st_mode
    path = root / 'build'
    if part == 'evidence-raw':
        path.mkdir()
        path = path / part
    try:
        path.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('host cannot create directory symlinks')
    code, record = run()
    assert code == 1 and record['status'] == 'FAIL' and state['calls'] == []
    assert list(outside.iterdir()) == [] and outside.stat().st_mode == mode
    assert not (root / recorder.PUBLIC).exists()


def test_private_root_traversal_rejected_without_creating_storage(tmp_path):
    with pytest.raises(ValueError, match='private_path_traversal'):
        recorder.PrivateAttempt(tmp_path / 'uncreated/../outside', {'source': 'synthetic'})
    assert list(tmp_path.iterdir()) == []


def test_link_substitution_rejected_without_reading_or_chmod_external_file(run_lab, tmp_path):
    root, state, run, *_ = run_lab
    outside = tmp_path / 'outside.txt'
    outside.write_text(PASS_XML)
    mode = outside.stat().st_mode

    def substitute():
        raw = next((root / 'build/evidence-raw').glob('*/run.xml'))
        raw.unlink()
        os.link(outside, raw)

    state['callback'] = substitute
    code, record = run()
    assert code == 1 and record['status'] == 'FAIL' and not (root / recorder.PUBLIC).exists()
    assert outside.read_text() == PASS_XML and outside.stat().st_mode == mode


def lab_status(reference='7.0.31', observed='7.0.31', match=True, **overrides):
    """A lab/zabbix_native_lab.py status record with synthetic identities."""
    identity = {'kind': 'dpkg', 'package': 'synthetic', 'version': '1:7.0.31-1+ubuntu24.04', 'architecture': 'amd64', 'sha256': 'a' * 64}
    services = {'reference': reference, 'observed_api_version': observed, 'reference_match': match,
                'zabbix_server': {'version_line': 'zabbix_server (Zabbix) 7.0.31', 'identity': dict(identity)},
                'frontend': {'ZABBIX_VERSION': '7.0.31', 'identity': dict(identity)},
                'schema': {'source': 'server.sql.gz', 'identity': dict(identity)},
                'postgres': {'version': 'postgres (PostgreSQL) 18.3', 'identity': {'kind': 'unpackaged', 'sha256': 'b' * 64}},
                'php': {'version_line': 'PHP 8.3.6 (cli)', 'identity': dict(identity)}}
    services.update(overrides)
    return {'state': {'state': 'running'}, 'services': services}


def test_lab_services_file_is_recorded_as_bounded_identities(run_lab, tmp_path):
    root, state, run, *_ = run_lab
    state['xml'] = PASS_XML.replace('7.0.10', '7.0.31')
    services = tmp_path / 'lab-status.json'
    services.write_text(json.dumps(lab_status()))
    code, record = run('--run', '--services-file', str(services))
    assert code == 0 and record['status'] == 'PASS'
    assert record['services']['reference'] == '7.0.31' and record['services']['observed_api_version'] == '7.0.31'
    assert set(record['services']['components']) == set(recorder.SERVICE_COMPONENTS)
    assert record['services']['components']['zabbix_server'] == {
        'kind': 'dpkg', 'package': 'synthetic', 'version': '1:7.0.31-1+ubuntu24.04', 'architecture': 'amd64',
        'sha256': 'a' * 64, 'version_line': 'zabbix_server (Zabbix) 7.0.31'}
    assert record['services']['components']['frontend']['ZABBIX_VERSION'] == '7.0.31'
    assert 'state' not in record['services'] and 'served_by' not in json.dumps(record['services'])


@pytest.mark.parametrize('flaw', ['unmatched', 'other-observed', 'other-reference', 'missing-component',
                                  'no-identity', 'incomplete-package', 'bad-digest', 'not-json'])
def test_invalid_lab_services_file_fails_before_any_execution(run_lab, tmp_path, flaw):
    root, state, run, *_ = run_lab
    status = lab_status()
    services = status['services']
    if flaw == 'unmatched':
        services['reference_match'] = False
    elif flaw == 'other-observed':
        services['observed_api_version'] = '7.0.30'
    elif flaw == 'other-reference':
        services['reference'] = '7.0.30'
    elif flaw == 'missing-component':
        del services['php']
    elif flaw == 'no-identity':
        services['schema'] = {'source': 'server.sql.gz'}
    elif flaw == 'incomplete-package':
        del services['frontend']['identity']['version']
    elif flaw == 'bad-digest':
        services['postgres']['identity']['sha256'] = 'not-a-digest'
    path = tmp_path / 'lab-status.json'
    path.write_text('{not json' if flaw == 'not-json' else json.dumps(status))
    code, record = run('--run', '--services-file', str(path))
    assert code == 1 and record['status'] == 'FAIL' and record['reason'] == 'lab_services_file_invalid'
    assert state['calls'] == [] and 'services' not in record and not (root / recorder.PUBLIC).exists()


def test_observed_version_must_agree_with_the_lab_services_record(run_lab, tmp_path):
    root, state, run, *_ = run_lab
    services = tmp_path / 'lab-status.json'
    services.write_text(json.dumps(lab_status()))
    code, record = run('--run', '--services-file', str(services))   # PASS_XML observed 7.0.10
    assert code == 1 and record['status'] == 'FAIL' and record['reason'] == 'observed_version_differs_from_lab_services'
    assert not (root / recorder.PUBLIC).exists()
