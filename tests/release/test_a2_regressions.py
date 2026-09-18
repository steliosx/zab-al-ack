"""Release regressions; mocked runners do not establish native/hosted execution."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile
from unittest.mock import MagicMock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
from build_acceptance import aggregate
from build_release import source_zip
from evidence_utils import utc_timestamp


def test_rebuilding_handoff_does_not_embed_stale_commit_or_checksums(tmp_path):
    source = tmp_path / 'source'
    (source / 'docs').mkdir(parents=True)
    for name in ['README.md', 'docs/START_HERE.md', 'RELEASE_MANIFEST.json',
                 'RELEASE_SHA256SUMS', 'START_HERE.md']:
        (source / name).write_text('old handoff metadata or public documentation')
    output = tmp_path / 'source.zip'
    source_zip(source, output, '0.1.0a6')
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            'zabalack-0.1.0a6/README.md',
            'zabalack-0.1.0a6/docs/START_HERE.md',
        }


@pytest.mark.parametrize('observations,expected', [
    ([('same', 'native', 'NOT_RUN'), ('same', 'native', 'PASS')], 'PASS'),
    ([('same', 'native', 'BLOCKED_ENVIRONMENT'), ('same', 'native', 'PASS')], 'PASS'),
    ([('same', 'native', 'FAIL'), ('same', 'native', 'PASS')], 'FAIL'),
    ([('unrun', 'native', 'NOT_RUN'), ('ran', 'native', 'PASS')], 'NOT_RUN'),
    ([('same', 'native', 'NOT_RUN'), ('same', 'wasm', 'PASS')], 'NOT_RUN'),
    ([], 'NOT_RUN'),
], ids=['executed-duplicate', 'blocked-duplicate', 'failure-preserved',
        'distinct-test-still-unrun', 'no-cross-layer-promotion', 'empty-evidence'])
def test_acceptance_resolves_only_same_test_and_layer(observations, expected):
    rows = [{'nodeid': node, 'layer': layer, 'status': status} for node, layer, status in observations]
    assert aggregate(rows) == expected


@pytest.mark.parametrize('phase', [
    {'junit': 'evidence/missing.xml', 'exit_code': 0},
    {'exit_code': 1},
    {'junit': 'evidence/unit.xml', 'exit_code': 1},
], ids=['missing-recorded-xml', 'no-native-report', 'failed-native-command'])
def test_acceptance_rejects_incomplete_native_run(tmp_path, monkeypatch, phase):
    import update_acceptance
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    for name in ('unit', 'wasm'):
        (evidence / (name + '.xml')).write_text('<testsuites><testsuite /></testsuites>')
    from evidence_utils import code_digests
    (evidence / 'reference-run.json').write_text(json.dumps({'phases': [
        {'layer': 'unit_mocked', 'junit': 'evidence/unit.xml'},
        {'layer': 'postgres_wasm', 'junit': 'evidence/wasm.xml'},
    ], 'code_digests': code_digests(tmp_path)}))
    (evidence / 'native-run.json').write_text(json.dumps({'status': 'PASS', 'phases': [phase],
                                                          'code_digests': code_digests(tmp_path)}))
    monkeypatch.setattr(update_acceptance, 'ROOT', tmp_path)
    monkeypatch.setattr(update_acceptance.subprocess, 'run',
                        lambda *a, **k: pytest.fail('Incomplete evidence must stop mapping'))
    with pytest.raises(RuntimeError, match='Native phase|native report'):
        update_acceptance.main()


@pytest.mark.parametrize('exit_code', [0, 1, 124], ids=['no-report', 'crashed-child', 'timeout'])
def test_reference_runner_never_reuses_previous_junit(tmp_path, monkeypatch, exit_code):
    import run_reference_suite
    raw = tmp_path / 'build/evidence-raw'
    public = tmp_path / 'evidence'
    raw.mkdir(parents=True)
    public.mkdir()
    for directory in (raw, public):
        for name in ('unit-mocked.xml', 'optional-services.xml'):
            (directory / name).write_text('<testsuite><testcase classname="old" name="stale_pass" /></testsuite>')

    def child(command, **kwargs):
        assert kwargs['env']['TZ'] == 'UTC'
        assert not any(key.startswith('ACK_TEST_') for key in kwargs['env'])
        if exit_code == 124:
            raise subprocess.TimeoutExpired(command, 600)
        return subprocess.CompletedProcess(command, exit_code)

    monkeypatch.setattr(run_reference_suite, 'ROOT', tmp_path)
    monkeypatch.setattr(run_reference_suite.subprocess, 'run', child)
    monkeypatch.setattr(sys, 'argv', ['run_reference_suite.py'])
    monkeypatch.setenv('TZ', 'Pacific/Auckland')
    monkeypatch.setenv('ACK_TEST_ADMIN_DSN', 'synthetic-inherited-value-must-be-removed')
    assert run_reference_suite.main() == 1
    report = json.loads((public / 'reference-run.json').read_text())
    assert report['counts']['passed'] == 0
    assert not report['all_executed_phases_succeeded']
    assert all(phase['junit'] is None for phase in report['phases'])


def test_native_runner_never_reuses_previous_junit(tmp_path, monkeypatch):
    import native_ci
    relay = MagicMock()
    relay.__enter__.return_value = relay
    relay.ports = {'postgres': 43123, 'pgbouncer': 43124}
    monkeypatch.setattr(native_ci, 'DockerLabRelays', lambda: relay)
    raw = tmp_path / 'build/evidence-raw'
    public = tmp_path / 'evidence'
    raw.mkdir(parents=True)
    public.mkdir()
    for directory in (raw, public):
        (directory / 'native-postgres.xml').write_text(
            '<testsuite><testcase classname="old" name="stale_pass" /></testsuite>')

    def child(command, **kwargs):
        if '-m' in command and 'pytest' in command:
            assert kwargs['env']['TZ'] == 'UTC'
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(native_ci, 'ROOT', tmp_path)
    monkeypatch.setattr(native_ci, 'ensure_local_docker', lambda: None)
    monkeypatch.setattr(native_ci.subprocess, 'run', child)
    monkeypatch.setattr(sys, 'argv', ['native_ci.py', '--isolated-lab', '--phase', 'postgres'])
    assert native_ci.main() == 1
    report = json.loads((public / 'native-run.json').read_text())
    assert report['status'] == 'FAIL'
    assert 'junit' not in report['phases'][0]
    assert not (public / 'native-postgres.xml').exists()


@pytest.mark.parametrize('source,expected', [
    ('2026-09-10T08:00:00.123456+05:00', '2026-09-10T03:00:00Z'),
    ('2026-09-09T23:00:00-04:00', '2026-09-10T03:00:00Z'),
    ('2026-09-10T03:00:00.123456', '2026-09-10T03:00:00'),
], ids=['positive-offset', 'negative-offset', 'naive-zone-not-invented'])
def test_utc_evidence_timestamp(source, expected):
    assert utc_timestamp(source) == expected


def test_gitleaks_arm64_binary_is_hash_verified_before_extraction_and_scanning():
    """Static release-pin check; this does not claim a scanner execution."""
    data = yaml.safe_load((ROOT / '.github/workflows/security.yml').read_text())
    scripts = [step['run'] for step in data['jobs']['scan']['steps'] if 'run' in step]
    manifest = json.loads((ROOT / 'docs/gitleaks_reference_manifest.json').read_text())
    install = next(script for script in scripts if 'curl --fail' in script)
    version = manifest['tag'][1:]
    assert f'https://github.com/{manifest["repository"]}/releases/download/{manifest["tag"]}/gitleaks_{version}_linux_arm64.tar.gz' in install
    assert 'test "$(uname -m)" = aarch64' in install
    assert '5f2edbe1f49f7b920f9e06e90759947d3c5dfc16f752fb93aaafc17e9d14cf07' in install
    assert install.index('sha256sum --check --strict') < install.index('tar --extract')
    assert install.rstrip().endswith('--no-same-owner gitleaks')
    assert not any('go install' in script for script in scripts)
    assert any('version | grep -x ' + manifest['tag'][1:] in script for script in scripts)
    scan = next(script for script in scripts if 'tools/security_scan.py' in script)
    assert '--gitleaks "$RUNNER_TEMP/gitleaks/gitleaks"' in scan
    assert scripts.index(install) < scripts.index(scan)


@pytest.mark.parametrize('root_user,existing', [(False, False), (True, False), (False, True)],
                         ids=['deep-checkout', 'root-no-shared-chown', 'existing-cluster-preserved'])
def test_lab_start_disables_unused_unix_socket_without_shared_temp_path(tmp_path, monkeypatch, root_user, existing):
    """Mocked OS/PostgreSQL commands; native startup remains a separate lab check."""
    spec = importlib.util.spec_from_file_location('review_local_runtime', ROOT / 'lab/local_runtime.py')
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    checkout = tmp_path / ('deep-checkout-' + 'x' * 100)
    checkout.mkdir()
    state = checkout / '.lab'
    pgdata = state / 'pgdata'
    marker = state / 'ISOLATED_ACK_LAB'
    shared = tmp_path / ('long-temp-' + 't' * 100)
    shared.mkdir()
    monkeypatch.setenv('TMPDIR', str(shared))
    for key, value in [('REPO', checkout), ('STATE', state), ('PGDATA', pgdata), ('MARKER', marker)]:
        monkeypatch.setattr(runtime, key, value)
    monkeypatch.setattr(runtime, 'binaries', lambda: tmp_path / 'bin')
    monkeypatch.setattr(runtime.os, 'geteuid', lambda: 0 if root_user else 1001)
    monkeypatch.setattr(runtime.pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=65534, pw_gid=65534))
    ownership_changes = []
    monkeypatch.setattr(runtime.os, 'chown', lambda path, *args: ownership_changes.append(Path(path)))
    monkeypatch.setattr(runtime.subprocess, 'run', lambda command, **kw: subprocess.CompletedProcess(command, 0, '65534', ''))
    commands = []

    def invoke(command, **kwargs):
        commands.append(command)
        if Path(command[0]).name == 'initdb':
            pgdata.mkdir(parents=True)
            (pgdata / 'PG_VERSION').write_text('18')
            (pgdata / 'postgresql.conf').write_text('# synthetic initdb output\n')
        code = 3 if command[-1] == 'status' else 0
        return subprocess.CompletedProcess(command, code, '', '')

    if existing:
        pgdata.mkdir(parents=True)
        marker.write_text('Disposable lab')
        (pgdata / 'PG_VERSION').write_text('18')
        (pgdata / 'postgresql.conf').write_text("unix_socket_directories='/old/deep/path'\n")
        sentinel = pgdata / 'preserved-record'
        sentinel.write_bytes(b'keep existing lab data')
    monkeypatch.setattr(runtime, 'run', invoke)
    monkeypatch.setattr(sys, 'argv', ['local_runtime.py', 'start', '--isolated-lab'])
    assert runtime.main() == 0
    started = next(command for command in commands if command[-1] == 'start')
    assert started[started.index('-o') + 1] == '-c unix_socket_directories='
    assert list(shared.iterdir()) == []
    assert set(ownership_changes) <= {state, marker}
    if existing:
        assert sentinel.read_bytes() == b'keep existing lab data'
        assert not any(Path(command[0]).name == 'initdb' for command in commands)
    else:
        config = (pgdata / 'postgresql.conf').read_text()
        assert "listen_addresses='127.0.0.1'" in config
        assert "unix_socket_directories=''" in config
