"""Offline Docker inventory controls; these tests never start native services."""
from copy import deepcopy
import hashlib
import json
import os
import stat
import subprocess
import sys

import pytest

import native_network_retention as retention

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX private retention contract')
PROJECT = 'ack-alpha-0123456789-postgres'
NETWORK = 'a' * 64
IDS = ['b' * 64, 'c' * 64]
VOLUME = PROJECT + '_ack-lab-pg18'
BINDING = {'code_digests': {'source': 'synthetic-source', 'harness': 'synthetic-harness'}}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    root = tmp_path / 'checkout'
    root.mkdir()
    store = tmp_path / 'private'
    retention.prepare_store(store, root)
    prefix = 'com.docker.compose.'
    network = {'Id': NETWORK, 'Name': PROJECT + '_isolated', 'Driver': 'bridge', 'Scope': 'local',
               'Internal': True, 'Ingress': False, 'ConfigOnly': False, 'Containers': {},
               'Labels': {prefix + 'project': PROJECT, prefix + 'network': 'isolated'}}
    volume = {'Name': VOLUME, 'CreatedAt': 'original', 'Driver': 'local', 'Scope': 'local',
              'Labels': {prefix + 'project': PROJECT, prefix + 'volume': 'ack-lab-pg18'}}
    rows = {'networks': {NETWORK: network}, 'volumes': {VOLUME: volume}, 'containers': {}}
    for identifier, service in zip(IDS, ['postgres', 'pgbouncer'], strict=True):
        rows['containers'][identifier] = {'Id': identifier,
            'Config': {'Labels': {prefix + 'project': PROJECT, prefix + 'service': service}},
            'State': {'Status': 'running', 'Running': True, 'Paused': False, 'Restarting': False, 'Dead': False},
            'HostConfig': {'AutoRemove': False},
            'NetworkSettings': {'Networks': {PROJECT + '_isolated': {'NetworkID': NETWORK}}},
            'Mounts': [{'Type': 'volume', 'Name': VOLUME, 'Destination': '/var/lib/postgresql', 'RW': True}]
                      if service == 'postgres' else []}
    # Unrelated objects must survive byte-for-byte in inspect records.
    rows['networks']['d' * 64] = {'Id': 'd' * 64, 'Name': 'foreign', 'Labels': {}}
    rows['volumes']['foreign-volume'] = {'Name': 'foreign-volume', 'Labels': {}}
    calls = []

    def output(command, cwd, *, combine_stderr=False):
        calls.append(command)
        assert cwd == root
        if command == ['compose', 'ps', '--all', '--quiet']:
            return '\n'.join(IDS).encode()
        if command[:3] == ['docker', 'network', 'ls']:
            return '\n'.join(rows['networks']).encode()
        if command[:2] == ['docker', 'ps']:
            return '\n'.join(rows['containers']).encode()
        if command[:3] == ['docker', 'volume', 'ls']:
            return '\n'.join(rows['volumes']).encode()
        if command[2] == 'inspect':
            key = {'network': 'networks', 'container': 'containers', 'volume': 'volumes'}[command[1]]
            return json.dumps([rows[key][i] for i in command[3:]]).encode()
        if command[:2] == ['docker', 'logs']:
            assert combine_stderr
            return b'synthetic-private-stdout-and-stderr\n'
        if command[:3] == ['docker', 'network', 'rm']:
            assert command == ['docker', 'network', 'rm', NETWORK]
            attempts = list(store.glob(PROJECT + '-*'))
            assert len(attempts) == 1
            receipt = json.loads((attempts[0] / 'pre-removal.json').read_text())
            assert receipt['status'] == 'RETAINED'
            for name, digest in receipt['files'].items():
                assert retention.digest_file(attempts[0] / name) == digest
            assert json.loads((attempts[0] / 'removal-requested.json').read_text())['removal_requested']
            del rows['networks'][NETWORK]
            return (NETWORK + '\n').encode()
        pytest.fail('Unexpected fake Docker command')

    monkeypatch.setattr(retention, 'output', output)
    monkeypatch.setattr(retention, 'code_digests', lambda root: BINDING['code_digests'])
    identity = retention.capture_identity(['compose'], PROJECT, root)
    for row in rows['containers'].values():
        row['State'].update(Status='exited', Running=False)
    log = root / 'raw.log'
    log.write_text('synthetic private phase details')
    return root, store, rows, calls, identity, log


def retire(lab):
    root, store, _, _, identity, log = lab
    binding = {**BINDING, 'phase_result': {'raw_junit_sha256': hashlib.sha256(b'synthetic private phase details').hexdigest()}}
    return retention.retire_network(root, store, identity, binding, [log, log])


def test_owned_success_retains_verified_diagnostics_before_exact_network_removal(lab):
    _, store, rows, calls, _, _ = lab
    before = deepcopy(rows)
    result = retire(lab)
    assert result['status'] == 'RETIRED'
    assert rows['containers'] == before['containers'] and rows['volumes'] == before['volumes']
    assert rows['networks'] == {'d' * 64: before['networks']['d' * 64]}
    assert sum(command[:3] == ['docker', 'network', 'rm'] for command in calls) == 1
    attempt, = store.glob(PROJECT + '-*')
    manifest = attempt / 'manifest.json'
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == result['manifest_sha256']
    listed = json.loads(manifest.read_text())
    assert {'inventory-after.json', 'result.json', 'removal-output.log'} <= set(listed)
    assert all(retention.digest_file(attempt / name) == digest for name, digest in listed.items())
    assert stat.S_IMODE(attempt.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in attempt.iterdir())
    assert 'synthetic-private' not in json.dumps(result)


@pytest.mark.parametrize('mutation', [
    'running', 'active-endpoint', 'foreign-stopped', 'replaced-container', 'replaced-volume',
    'foreign-network', 'extra-network', 'extra-volume', 'extra-mount', 'missing-mount',
    'not-internal', 'null-state', 'null-config', 'null-endpoints', 'changed-source', 'changed-junit',
])
def test_ownership_or_revision_uncertainty_never_removes_network(lab, monkeypatch, mutation):
    _, _, rows, calls, _, log = lab
    container = rows['containers'][IDS[0]]
    if mutation == 'running':
        container['State'].update(Status='running', Running=True)
    elif mutation == 'active-endpoint':
        rows['networks'][NETWORK]['Containers'] = {'unexpected': {}}
    elif mutation == 'foreign-stopped':
        foreign = deepcopy(container)
        foreign.update(Id='e' * 64, Config={'Labels': {}})
        rows['containers']['e' * 64] = foreign
    elif mutation == 'replaced-container':
        container['Id'] = 'f' * 64
        rows['containers']['f' * 64] = rows['containers'].pop(IDS[0])
    elif mutation == 'replaced-volume':
        rows['volumes'][VOLUME]['CreatedAt'] = 'replacement'
    elif mutation == 'foreign-network':
        container['NetworkSettings']['Networks']['foreign'] = {'NetworkID': 'd' * 64}
    elif mutation == 'extra-network':
        rows['networks']['d' * 64]['Labels'] = {'com.docker.compose.project': PROJECT}
    elif mutation == 'extra-volume':
        rows['volumes']['foreign-volume']['Labels'] = {'com.docker.compose.project': PROJECT}
    elif mutation == 'extra-mount':
        container['Mounts'].append({'Type': 'bind'})
    elif mutation == 'missing-mount':
        container['Mounts'] = []
    elif mutation == 'not-internal':
        rows['networks'][NETWORK]['Internal'] = False
    elif mutation == 'null-state':
        container['State'] = None
    elif mutation == 'null-config':
        container['Config'] = None
    elif mutation == 'null-endpoints':
        container['NetworkSettings']['Networks'] = None
    elif mutation == 'changed-source':
        monkeypatch.setattr(retention, 'code_digests', lambda root: {})
    elif mutation == 'changed-junit':
        log.write_text('Different JUnit after PASS assessment')
    with pytest.raises((ValueError, KeyError, TypeError, AttributeError)):
        retire(lab)
    assert not any(command[:3] == ['docker', 'network', 'rm'] for command in calls)


@pytest.mark.parametrize('failure', ['write', 'fsync', 'hardlink', 'symlink', 'changed-inventory', 'corrupt-copy'])
def test_retention_failure_prevents_mutation(lab, monkeypatch, failure):
    _, _, rows, calls, _, log = lab
    if failure == 'write':
        monkeypatch.setattr(retention, 'write_file', lambda *a: (_ for _ in ()).throw(OSError('private write failure')))
    elif failure == 'fsync':
        monkeypatch.setattr(retention.os, 'fsync', lambda *a: (_ for _ in ()).throw(OSError('private fsync failure')))
    elif failure == 'hardlink':
        os.link(log, log.with_suffix('.alias'))
    elif failure == 'symlink':
        original = log.with_suffix('.original')
        log.rename(original)
        log.symlink_to(original)
    elif failure == 'changed-inventory':
        original = retention.inventory
        reads = []
        def changed(root):
            if reads:
                rows['networks']['d' * 64]['Name'] = 'changed'
            reads.append(True)
            return original(root)
        monkeypatch.setattr(retention, 'inventory', changed)
    elif failure == 'corrupt-copy':
        original = retention.sync_dir
        def corrupt(path):
            original(path)
            if (path / 'pre-removal.json').exists():
                (path / 'phase-0.raw').write_text('corrupted')
        monkeypatch.setattr(retention, 'sync_dir', corrupt)
    with pytest.raises((OSError, ValueError)):
        retire(lab)
    assert not any(command[:3] == ['docker', 'network', 'rm'] for command in calls)


@pytest.mark.parametrize('failure', ['timeout', 'command-error', 'after-change'])
def test_uncertain_removal_is_preserved_failed_and_never_retried(lab, monkeypatch, failure):
    _, store, rows, calls, _, _ = lab
    original = retention.output
    def uncertain(command, *args, **kwargs):
        result = original(command, *args, **kwargs)
        if command[:3] == ['docker', 'network', 'rm']:
            if failure == 'timeout':
                raise subprocess.TimeoutExpired(command, 30, output=b'private partial output')
            if failure == 'command-error':
                raise subprocess.CalledProcessError(1, command, stderr=b'private command error')
            rows['volumes']['foreign-volume']['changed'] = True
        return result
    monkeypatch.setattr(retention, 'output', uncertain)
    with pytest.raises((ValueError, subprocess.SubprocessError)):
        retire(lab)
    assert sum(command[:3] == ['docker', 'network', 'rm'] for command in calls) == 1
    attempt, = store.glob(PROJECT + '-*')
    failed = json.loads((attempt / 'failure.json').read_text())
    assert failed['outcome'] == 'UNKNOWN' and failed['removal_requested']
    assert not (attempt / 'manifest.json').exists()


@pytest.mark.parametrize('unsafe', ['checkout', 'runner-temp', 'runner-sibling', 'mode', 'link'])
def test_preflight_refuses_unsafe_roots(tmp_path, monkeypatch, unsafe):
    root = tmp_path / '_work/repo/repo'
    root.mkdir(parents=True)
    store = tmp_path / 'private'
    if unsafe == 'checkout':
        store = root / 'private'
    elif unsafe == 'runner-temp':
        monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    elif unsafe == 'runner-sibling':
        store = tmp_path / '_work/private'
    elif unsafe == 'mode':
        store.mkdir(mode=0o755)
        store.chmod(0o755)  # Deliberately unsafe even under the private controller umask.
    elif unsafe == 'link':
        store.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        retention.prepare_store(store, root)


def test_service_logs_include_stderr(tmp_path):
    result = retention.output([sys.executable, '-c',
        'import sys; sys.stdout.write("synthetic-out"); sys.stderr.write("synthetic-err")'], tmp_path, combine_stderr=True)
    assert b'synthetic-out' in result and b'synthetic-err' in result


def test_capture_preserves_both_failed_streams(tmp_path):
    with pytest.raises(subprocess.CalledProcessError) as raised:
        retention.output([sys.executable, '-c',
            'import sys; sys.stdout.write("out"); sys.stderr.write("err"); sys.exit(1)'], tmp_path)
    assert raised.value.stdout == b'out' and raised.value.stderr == b'err'


def test_capture_enforces_bound_while_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(retention, 'LIMIT', 32)
    with pytest.raises(retention.CaptureError) as raised:
        retention.output([sys.executable, '-c', 'import sys; sys.stdout.write("x"*100000)'], tmp_path)
    assert len(raised.value.stdout) + len(raised.value.stderr) == 32


def test_fifo_diagnostic_is_refused_without_waiting_for_writer(tmp_path):
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo, 0o600)
    try:
        with pytest.raises(ValueError, match='Unsafe'):
            retention.digest_file(fifo)
    finally:
        fifo.unlink()  # Disposable special-file fixture, never a lab/evidence resource.
