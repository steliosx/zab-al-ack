"""The S2 harness keeps an attempt's evidence: no wipe without a complete collection, and every attempt recorded.

Review of 97925db (evidence lifecycle): a copy error still read COLLECTED and Lab.stop_and_wipe wiped even after
an explicit FAILED, destroying the only original; a refusal in isolate() left neither a result nor a bundle; and
gateway logs were collected while their writers still ran. The real collector and the real Lab sequencing run
here; only the lab's runuser commands, the gateway process and root-only setup are replaced.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def harness():
    spec = importlib.util.spec_from_file_location('s2_acceptance_evidence', ROOT / 'lab/webhook_transport_acceptance.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def failed_attempt(tmp_path):
    checkout = tmp_path / 'checkout'
    (checkout / '.zabbix-lab').mkdir(parents=True)
    original = checkout / '.zabbix-lab/zabbix_server.log'
    original.write_bytes(b'the only copy of a failed attempt\'s server log\n')
    return checkout, original


def lab_with_commands(harness, checkout, commands):
    lab = harness.Lab(checkout, 'SYNTHETIC')

    def command(*args, check=True):
        commands.append(args[0] + (' --wipe' if '--wipe' in args else ''))
        if '--wipe' in args:
            shutil.rmtree(checkout / '.zabbix-lab')
        return subprocess.CompletedProcess(list(args), 0, '{}', '')
    lab.command = command
    return lab


@pytest.mark.parametrize('failure', ['copy', 'destination'])
def test_a_failed_collection_is_never_followed_by_a_wipe_and_a_later_one_recovers(harness, tmp_path, monkeypatch, failure):
    checkout, original = failed_attempt(tmp_path)
    commands: list[str] = []
    lab = lab_with_commands(harness, checkout, commands)
    tool = harness.evidence_tool()
    bundle = tmp_path / 'private'
    if failure == 'copy':
        def failing_fsync(fd):
            raise OSError(5, 'synthetic destination I/O failure')
        monkeypatch.setattr(tool.os, 'fsync', failing_fsync)
    else:
        bundle.write_text('a regular file where the bundle should be\n')
    steps = lab.stop_and_wipe(harness.collector(bundle, tool.attempt_label('first'), [('checkout', 'checkout', checkout)], new=True))
    assert commands == ['stop'] and steps[1]['collect']['status'] == ('INCOMPLETE' if failure == 'copy' else 'FAILED')
    assert steps[2] == {'wipe': 'SKIPPED', 'reason': 'evidence not fully retained; the stopped lab state is left in place'}
    assert original.read_bytes() == b'the only copy of a failed attempt\'s server log\n'
    # Recovery: once the destination works, the same stopped lab is collected in full, and only then wiped.
    monkeypatch.undo()
    if failure == 'destination':
        bundle.unlink()
    label = tool.attempt_label('recovery')
    steps = lab.stop_and_wipe(harness.collector(bundle, label, [('checkout', 'checkout', checkout)], new=True))
    assert steps[1]['collect']['status'] == 'COLLECTED' and commands == ['stop', 'stop', 'reset --wipe'] and not original.exists()
    manifest = json.loads((bundle / label / 'MANIFEST.json').read_text())
    copy = bundle / next(entry['bundle_path'] for entry in manifest['files'] if entry['path'] == '.zabbix-lab/zabbix_server.log')
    assert copy.read_bytes() == b'the only copy of a failed attempt\'s server log\n'


class FakeProcess:
    def __init__(self, events):
        self.events, self.returncode = events, None

    def terminate(self):
        self.events.append('gateway.terminate')
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.events.append('gateway.kill')


def run_main(harness, monkeypatch, tmp_path, events, isolate=None, start=None):
    monkeypatch.setattr(harness, 'pki', lambda directory: events.append('pki'))
    def copytree(source, target, **unused):
        (Path(target) / 'zabbix/v1/fixtures').mkdir(parents=True)
        (Path(target) / 'zabbix/v1/fixtures/problem.params.json').write_text('{}\n')
    monkeypatch.setattr(harness.shutil, 'copytree', copytree)
    monkeypatch.setattr(harness, 'sh', lambda *argv, **unused: subprocess.CompletedProcess(argv, 0, '', ''))
    monkeypatch.setattr(harness, 'isolate', isolate or (lambda work: {'synthetic': True}))
    monkeypatch.setattr(harness, 'digest', lambda path: 'synthetic')

    class Receiver:
        def __init__(self, *unused, **unused_too):
            self.port = 1

        def shutdown(self):
            events.append('receiver.shutdown')
    monkeypatch.setattr(harness, 'Receiver', Receiver)

    def gateway(work, nginx, user, port, label='nginx'):
        logs = work / label / 'logs'
        logs.mkdir(parents=True)
        (logs / 'access.log').write_text('synthetic gateway access log\n')
        events.append('gateway.start')
        return FakeProcess(events), 2, work / label
    monkeypatch.setattr(harness, 'gateway', gateway)

    def command(self, *args, check=True):
        events.append('lab.' + args[0] + (' --wipe' if '--wipe' in args else ''))
        if args[0] == 'start':
            (self.checkout / '.zabbix-lab').mkdir(parents=True, exist_ok=True)
            (self.checkout / '.zabbix-lab/zabbix_server.log').write_text('synthetic server log\n')
            (start or (lambda: None))()
        return subprocess.CompletedProcess(list(args), 0, '{}', '')
    monkeypatch.setattr(harness.Lab, 'command', command)
    real = harness.collector

    def recording(bundle, attempt, sources, new=False):
        run = real(bundle, attempt, sources, new)
        return lambda: (events.append('collect'), run())[1]
    monkeypatch.setattr(harness, 'collector', recording)
    out = tmp_path / 'result.json'
    argv = ['--checkout', str(ROOT), '--work', str(tmp_path / 'work'), '--out', str(out), '--nginx', '/synthetic/nginx']
    return out, argv


def test_a_refusal_in_isolate_is_recorded_collected_and_still_raised(harness, monkeypatch, tmp_path):
    events: list[str] = []

    def refuse(work):
        raise SystemExit('synthetic: the lab-trust unit is active')
    out, argv = run_main(harness, monkeypatch, tmp_path, events, isolate=refuse)
    with pytest.raises(SystemExit, match='synthetic: the lab-trust unit is active'):
        harness.main(argv)
    result = json.loads(out.read_text())
    assert result['error'] == 'SystemExit: synthetic: the lab-trust unit is active' and 'error' in result['summary']['failed']
    assert not any(event.startswith(('lab.', 'gateway.')) for event in events)  # nothing started to obtain the record
    bundle = Path(result['summary']['private_evidence'])
    manifest = json.loads((bundle / 'MANIFEST.json').read_text())
    assert [entry['path'] for entry in manifest['files']] == ['result.json'] and manifest['complete'] is True


def test_owned_gateways_stop_before_collection_and_the_wipe_follows_it(harness, monkeypatch, tmp_path):
    events: list[str] = []

    def fail():
        raise RuntimeError('synthetic: the lab start failed')
    out, argv = run_main(harness, monkeypatch, tmp_path, events, start=fail)
    with pytest.raises(RuntimeError, match='synthetic: the lab start failed'):
        harness.main(argv)
    assert events.index('gateway.terminate') < events.index('lab.stop') < events.index('collect') < events.index('lab.reset --wipe')
    result = json.loads(out.read_text())
    manifest = json.loads((Path(result['summary']['private_evidence']) / 'MANIFEST.json').read_text())
    assert {entry['path'] for entry in manifest['files']} >= {'.zabbix-lab/zabbix_server.log', 'access.log', 'result.json'}
