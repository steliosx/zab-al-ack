"""Private socket-recorder regressions; no PostgreSQL or Node service is used."""
import io
import json
import os
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from lab import pglite_exec as recorder

REAL_POPEN = subprocess.Popen
READY = b'{"event":"isolated_wasm_socket_ready","production_gates":"NOT_VERIFIED"}\n'


@pytest.fixture
def recording(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, 'REPO', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['pglite_exec.py', '--isolated-lab', '--', 'synthetic-child'])
    state = {'stdout': READY, 'stderr': b'synthetic private diagnostic\n', 'child_code': 0,
             'startup_exit': None, 'escalate': False, 'calls': [], 'child_error': None}

    class Server:
        pid = 12345

        def __init__(self, stdout, stderr):
            self.returncode = state['startup_exit']
            self.stdout, self.stderr = stdout, stderr
            stdout.write(state['stdout'])
            stderr.write(state['stderr'])
            stdout.flush()
            stderr.flush()

        def poll(self):
            return self.returncode

        def terminate(self):
            state['calls'].append('terminate')
            self.stderr.write(b'synthetic shutdown diagnostic\n')
            self.stderr.flush()

        def wait(self, timeout):
            state['calls'].append(('wait', timeout))
            if state.get('unkillable') or (state['escalate'] and 'kill' not in state['calls']):
                raise subprocess.TimeoutExpired('synthetic-server', timeout)
            if self.returncode is None:
                self.returncode = -15
            return self.returncode

        def kill(self):
            state['calls'].append('kill')

    def start(command, **kwargs):
        assert command == ['node', str(tmp_path / 'lab/pglite_socket.mjs'), '--isolated-lab']
        assert kwargs['stdout'] != subprocess.PIPE and kwargs['stderr'] != subprocess.PIPE
        if state.get('spawn_error'):
            raise FileNotFoundError('synthetic missing Node')
        return Server(kwargs['stdout'], kwargs['stderr'])

    def child(command, **kwargs):
        assert command == ['synthetic-child'] and kwargs['cwd'] == tmp_path
        assert kwargs['env']['ACK_TEST_WASM'] == '1'
        assert kwargs['env']['ACK_TEST_DESTRUCTIVE_LAB'] == 'YES_ISOLATED_LAB'
        assert kwargs['env']['ACK_TEST_EVIDENCE_LAYER'] == 'postgresql18_wasm_supplemental'
        assert kwargs['env']['ACK_TEST_ADMIN_DSN'].startswith('postgresql://postgres@127.0.0.1:55433/')
        attempt = sorted((tmp_path / 'build/evidence-raw').iterdir())[-1]
        if os.name == 'posix':
            assert stat.S_IMODE(attempt.stat().st_mode) == 0o700
            for path in attempt.iterdir():
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
        state['calls'].append('child')
        if state['child_error']:
            raise state['child_error']
        return SimpleNamespace(returncode=state['child_code'])

    monkeypatch.setattr(recorder.subprocess, 'Popen', start)
    monkeypatch.setattr(recorder.subprocess, 'run', child)
    return tmp_path, state


def records(root):
    attempts = list((root / 'build/evidence-raw').iterdir())
    assert len(attempts) == 1
    attempt = attempts[0]
    return attempt, json.loads((attempt / 'run.json').read_text())


def test_nonzero_child_preserved_with_private_streams_and_cleanup(recording, capsys):
    root, state = recording
    state['child_code'] = 7
    assert recorder.main() == 7
    attempt, result = records(root)
    assert (attempt / 'server.stdout').read_bytes() == READY
    assert (attempt / 'server.stderr').read_bytes() == state['stderr'] + b'synthetic shutdown diagnostic\n'
    assert result['child_exit_code'] == 7 and result['status'] == 'CHILD_EXITED'
    assert result['cleanup'] == 'EXIT_CONFIRMED' and result['server_exit_code'] == -15
    assert state['calls'] == ['child', 'terminate', ('wait', 5)]
    assert 'synthetic private' not in capsys.readouterr().out


def test_startup_failure_retains_stderr_without_echo_or_child(recording, capsys):
    root, state = recording
    state.update(stdout=b'', startup_exit=3)
    with pytest.raises(RuntimeError, match='failed before readiness'):
        recorder.main()
    attempt, result = records(root)
    assert (attempt / 'server.stderr').read_bytes() == state['stderr']
    assert result['server_exit_code'] == 3 and result['child_exit_code'] is None
    assert result['status'] == 'WRAPPER_ERROR' and result['cleanup'] == 'EXIT_CONFIRMED'
    assert state['calls'] == [('wait', 5)]
    assert 'synthetic private' not in capsys.readouterr().out


def test_spawn_failure_still_has_private_attempt_receipt(recording):
    root, state = recording
    state['spawn_error'] = True
    with pytest.raises(FileNotFoundError):
        recorder.main()
    attempt, result = records(root)
    assert result['error_type'] == 'FileNotFoundError' and result['cleanup'] == 'NOT_STARTED'
    assert result['child_exit_code'] is None and not state['calls']
    assert (attempt / 'server.stdout').read_bytes() == b''
    assert (attempt / 'server.stderr').read_bytes() == b''


@pytest.mark.parametrize('payload', [b'not-json\n', b'[]\n', b'{"event":"wrong"}\n'])
def test_invalid_readiness_retained_and_server_stopped(recording, payload):
    root, state = recording
    state['stdout'] = payload
    with pytest.raises((ValueError, RuntimeError)):
        recorder.main()
    attempt, result = records(root)
    assert (attempt / 'server.stdout').read_bytes() == payload
    assert result['cleanup'] == 'EXIT_CONFIRMED' and 'child' not in state['calls']


def test_cleanup_escalates_and_retains_interruption(recording):
    root, state = recording
    state.update(escalate=True, child_error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        recorder.main()
    _, result = records(root)
    assert result['error_type'] == 'KeyboardInterrupt' and result['cleanup'] == 'EXIT_CONFIRMED'
    assert state['calls'] == ['child', 'terminate', ('wait', 5), 'kill', ('wait', 5)]


def test_unconfirmed_cleanup_is_retained_as_error(recording):
    root, state = recording
    state['unkillable'] = True
    with pytest.raises(subprocess.TimeoutExpired):
        recorder.main()
    _, result = records(root)
    assert result['status'] == 'WRAPPER_ERROR' and result['cleanup'] == 'KILLING'
    assert result['cleanup_error_type'] == 'TimeoutExpired' and result['server_exit_code'] is None
    assert result['child_exit_code'] == 0  # Child success cannot hide unconfirmed cleanup.


def test_partial_readiness_observes_deadline(monkeypatch):
    ticks = iter([0, 0, 31])
    monkeypatch.setattr(recorder.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(recorder.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError, match='within 30seconds'):
        recorder.wait_ready(SimpleNamespace(poll=lambda: None), io.BytesIO(b'{"event":'))


def test_new_attempt_does_not_replace_previous_failure(recording):
    root, state = recording
    state.update(stdout=b'', startup_exit=2)
    with pytest.raises(RuntimeError):
        recorder.main()
    first, _ = records(root)
    original = {p.name: p.read_bytes() for p in first.iterdir()}
    state.update(stdout=READY, startup_exit=None)
    assert recorder.main() == 0
    assert len(list(first.parent.iterdir())) == 2
    assert {p.name: p.read_bytes() for p in first.iterdir()} == original


@pytest.mark.parametrize('argv', [['pglite_exec.py', '--', 'child'], ['pglite_exec.py', '--isolated-lab']])
def test_explicit_isolation_and_child_required_before_recording(recording, monkeypatch, argv):
    root, _ = recording
    monkeypatch.setattr(sys, 'argv', argv)
    with pytest.raises(SystemExit):
        recorder.main()
    assert not (root / 'build').exists()


def test_streams_larger_than_pipe_capacity_are_retained(recording, monkeypatch):
    root, _ = recording
    server_script = (
        'import sys,time\n'
        'sys.stderr.buffer.write(b"e"*1048576);sys.stderr.flush()\n'
        f'sys.stdout.buffer.write({READY!r});sys.stdout.flush()\n'
        'sys.stdout.buffer.write(b"o"*1048576);sys.stdout.flush()\n'
        'sys.stderr.buffer.write(b"tail");sys.stderr.flush()\n'
        'time.sleep(60)\n'
    )
    processes = []

    def start(_command, **kwargs):
        process = REAL_POPEN([sys.executable, '-u', '-c', server_script], **kwargs)
        processes.append(process)
        return process

    def child(*_args, **_kwargs):
        attempt, _ = records(root)
        deadline = time.monotonic() + 10
        while (attempt / 'server.stderr').stat().st_size < 1048580:
            assert time.monotonic() < deadline, 'server output blocked before child execution'
            time.sleep(0.01)
        return SimpleNamespace(returncode=9)

    monkeypatch.setattr(recorder.subprocess, 'Popen', start)
    monkeypatch.setattr(recorder.subprocess, 'run', child)
    assert recorder.main() == 9
    attempt, result = records(root)
    assert (attempt / 'server.stderr').read_bytes() == b'e' * 1048576 + b'tail'
    assert (attempt / 'server.stdout').read_bytes() == READY + b'o' * 1048576
    assert result['cleanup'] == 'EXIT_CONFIRMED' and processes[0].poll() is not None
