"""tools/cancellation.py: SIGTERM, SIGHUP and SIGINT as one interrupt, deferral inside critical sections, group termination."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import cancellation  # noqa: E402


def test_cancelled_is_a_keyboard_interrupt_that_names_its_signal():
    error = cancellation.Cancelled(signal.SIGTERM)
    assert isinstance(error, KeyboardInterrupt) and str(error) == 'interrupted (SIGTERM)' and error.signum == signal.SIGTERM
    assert cancellation.describe(error) == 'interrupted (SIGTERM)' and cancellation.describe(KeyboardInterrupt()) == 'interrupted'
    assert cancellation.describe(cancellation.Cancelled(signal.SIGHUP)) == 'interrupted (SIGHUP)'


def test_a_signal_inside_a_critical_section_is_deferred_and_raised_when_it_ends():
    control = cancellation.Cancellation()
    with pytest.raises(cancellation.Cancelled):
        control._handler(signal.SIGTERM, None)    # outside a critical section: immediate
    steps = []
    with pytest.raises(cancellation.Cancelled, match='SIGHUP'):
        with control.critical():
            control._handler(signal.SIGHUP, None)  # deferred
            control._handler(signal.SIGTERM, None)  # a second one does not replace the first
            steps.append('cleanup finished')
    assert steps == ['cleanup finished'] and control.pending is None and control.received == [signal.SIGTERM, signal.SIGHUP, signal.SIGTERM]
    # Nested sections defer to the outermost one.
    with pytest.raises(cancellation.Cancelled):
        with control.critical():
            with control.critical():
                control._handler(signal.SIGINT, None)
            steps.append('inner done')
    assert steps[-1] == 'inner done'


@pytest.mark.parametrize('signum', [signal.SIGTERM, signal.SIGHUP, signal.SIGINT], ids=['SIGTERM', 'SIGHUP', 'SIGINT'])
def test_installed_handlers_route_every_termination_signal_into_the_interrupt_path(signum):
    control = cancellation.Cancellation().install()
    try:
        with pytest.raises(cancellation.Cancelled) as raised:
            os.kill(os.getpid(), signum)
            time.sleep(1)
        assert raised.value.signum == signum
    finally:
        control.restore()
    assert signal.getsignal(signal.SIGTERM) is not control._handler


def terminated(pid: int) -> bool:
    """Dead or a zombie (an init that does not reap leaves zombies behind in a container)."""
    try:
        status = Path(f'/proc/{pid}/status').read_text()
    except OSError:
        return True
    state = status.split('State:')[1].split('\n')[0].strip()
    return state.startswith('Z')


def wait_terminated(pid: int, timeout: float = 5) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if terminated(pid):
            return True
        time.sleep(0.1)
    return terminated(pid)


def test_run_child_terminates_the_whole_process_group_on_cancellation(tmp_path):
    """R8/R20: a cancellation stops the child and everything it spawned, then re-raises."""
    pidfile = tmp_path / 'grandchild.pid'
    control = cancellation.Cancellation().install()
    try:
        timer = threading.Timer(0.8, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        started = time.monotonic()
        with pytest.raises(cancellation.Cancelled):
            cancellation.run_child(['sh', '-c', f'sleep 60 & echo $! > {pidfile}; wait'], grace=3)
        elapsed = time.monotonic() - started
    finally:
        control.restore()
    assert elapsed < 20
    assert wait_terminated(int(pidfile.read_text().strip()))


def test_run_child_returns_the_exit_code_and_stops_the_group_on_timeout(tmp_path):
    assert cancellation.run_child(['sh', '-c', 'exit 7']) == 7
    pidfile = tmp_path / 'grandchild.pid'
    with pytest.raises(subprocess.TimeoutExpired):
        cancellation.run_child(['sh', '-c', f'sleep 60 & echo $! > {pidfile}; wait'], timeout=0.8, grace=2)
    assert wait_terminated(int(pidfile.read_text().strip()))


def test_a_second_signal_during_escalation_does_not_skip_the_kill_step(tmp_path):
    """Re-review New-1: SIGINT then SIGTERM 1 s later (GitHub's escalation) aborted the grace wait and the
    SIGKILL was never sent; a child and a grandchild that ignore SIGTERM stayed alive."""
    pids = tmp_path / 'pids'
    script = f"trap '' TERM INT; (trap '' TERM INT; sleep 60) & echo $$ $! > {pids}; while :; do sleep 0.2; done"
    control = cancellation.Cancellation().install()
    try:
        threading.Timer(0.8, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
        threading.Timer(1.8, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
        with pytest.raises(KeyboardInterrupt):
            cancellation.run_child(['sh', '-c', script], grace=3)
    finally:
        control.restore()
    child, grandchild = (int(pid) for pid in pids.read_text().split())
    assert wait_terminated(child) and wait_terminated(grandchild)


def test_group_members_that_outlive_the_leader_are_killed(tmp_path):
    """Re-review New-2: terminate_group returned once the leader exited; a member ignoring SIGTERM survived."""
    pidfile = tmp_path / 'member.pid'
    process = subprocess.Popen(['sh', '-c', f"(trap '' TERM; sleep 60) & echo $! > {pidfile}; sleep 60"], start_new_session=True)
    deadline = time.monotonic() + 5
    while not pidfile.exists() or not pidfile.read_text().strip():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    cancellation.terminate_group(process, grace=2)
    assert process.poll() is not None
    assert wait_terminated(int(pidfile.read_text().strip()))


BOUNDED_STREAM = r"""
import json, os, signal, sys, threading, time
sys.path.insert(0, sys.argv[1])
import cancellation
control = cancellation.Cancellation().install()
first = {}
def stream():
    time.sleep(0.8)
    first['at'] = time.monotonic()
    os.kill(os.getpid(), signal.SIGINT)
    end = time.monotonic() + 3.5
    while time.monotonic() < end:
        time.sleep(0.25)
        os.kill(os.getpid(), signal.SIGTERM)
thread = threading.Thread(target=stream, daemon=True)
thread.start()
cause = None
try:
    cancellation.run_child(['sh', '-c', "trap '' TERM INT; echo $$ > " + sys.argv[2] + "; while :; do sleep 0.2; done"], grace=1)
except KeyboardInterrupt as error:
    cause = str(error)
returned = time.monotonic()
while thread.is_alive():
    try:
        thread.join()
    except KeyboardInterrupt:
        pass
print(json.dumps({'elapsed_after_first_signal': returned - first['at'], 'cause': cause}))
"""


def test_repeated_signals_do_not_move_the_original_deadline(tmp_path):
    """Review of O7: each signal restarted the grace wait, so a stream of signals delayed SIGKILL indefinitely.

    Bounded, in its own interpreter (no signal reaches pytest): the stream stops by itself 3.5 s after the
    first signal. With the deadline fixed at the first signal plus grace=1, run_child returns about 1 s after
    it; a restarting wait needs more than 4 s. The whole test is bounded by a 30 s timeout.
    """
    pidfile = tmp_path / 'child.pid'
    result = subprocess.run([sys.executable, '-c', BOUNDED_STREAM, str(Path(cancellation.__file__).parent), str(pidfile)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome['cause'] is not None
    assert outcome['elapsed_after_first_signal'] < 2.5, outcome
    assert wait_terminated(int(pidfile.read_text().strip()))


def test_repeated_signals_do_not_move_the_original_deadline_timing(tmp_path, monkeypatch):
    """Deterministic: a fake clock and a signal injected into every wait step; SIGKILL follows at the fixed deadline."""
    ready = tmp_path / 'ready'
    process = subprocess.Popen(['sh', '-c', f"trap '' TERM; touch {ready}; while :; do sleep 0.2; done"], start_new_session=True)
    deadline = time.monotonic() + 5
    while not ready.exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    now = [100.0]
    monkeypatch.setattr(cancellation.time, 'monotonic', lambda: now[0])
    signals = []
    try:
        real_killpg = os.killpg
        interrupts = {'count': 0}

        def sleep(seconds):
            now[0] += seconds
            if interrupts['count'] < 50:  # a later signal on every step of the wait
                interrupts['count'] += 1
                raise cancellation.Cancelled(signal.SIGTERM)
        monkeypatch.setattr(cancellation.time, 'sleep', sleep)

        def killpg(pgid, signum):
            signals.append((now[0], signum))
            return real_killpg(pgid, signum)
        monkeypatch.setattr(cancellation.os, 'killpg', killpg)
        outcome = cancellation.terminate_group(process, grace=2, reraise=False)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert outcome == 'terminated' and interrupts['count'] > 5
    assert [signum for _, signum in signals] == [signal.SIGTERM, signal.SIGKILL]
    term_at, kill_at = signals[0][0], signals[1][0]
    assert 2.0 <= kill_at - term_at <= 2.4, (term_at, kill_at)
    assert process.returncode == -signal.SIGKILL


def test_the_leader_is_not_reaped_before_the_final_kill_so_the_group_id_cannot_be_reused(tmp_path, monkeypatch):
    """Review of O7 (pid/pgid reuse): every killpg happens while the leader's pid is still allocated
    (alive or a zombie), so the group id cannot belong to another process; members are killed; the
    leader is reaped only afterwards."""
    member = tmp_path / 'member.pid'
    process = subprocess.Popen(['sh', '-c', f"(trap '' TERM; while :; do sleep 0.2; done) & echo $! > {member}; exit 0"],
                               start_new_session=True)
    deadline = time.monotonic() + 5
    while not (member.exists() and member.read_text().strip()):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    time.sleep(0.3)  # the leader has exited: a zombie, not reaped
    observed = []
    real_killpg = os.killpg

    def killpg(pgid, signum):
        stat = Path(f'/proc/{pgid}/stat').read_text()
        observed.append((signum, stat.rpartition(')')[2].split()[0], process.returncode))
        return real_killpg(pgid, signum)
    monkeypatch.setattr(cancellation.os, 'killpg', killpg)
    assert cancellation.terminate_group(process, grace=1) == 'terminated'
    assert [signum for signum, _, _ in observed] == [signal.SIGTERM, signal.SIGKILL]
    assert all(state == 'Z' and returncode is None for _, state, returncode in observed), observed
    assert process.returncode == 0 and wait_terminated(int(member.read_text().strip()))


def test_an_already_reaped_leader_is_never_signalled(monkeypatch):
    """The group id of a reaped leader can no longer be proven: nothing is signalled."""
    process = subprocess.Popen(['sh', '-c', 'exit 0'], start_new_session=True)
    process.wait()
    monkeypatch.setattr(cancellation.os, 'killpg', lambda pgid, signum: pytest.fail('a reaped leader\'s group was signalled'))
    assert cancellation.terminate_group(process, grace=1) == 'leader_already_reaped'


def test_run_child_keeps_the_first_interrupt_as_the_cause_when_later_ones_arrive_during_escalation(tmp_path, monkeypatch):
    """The original cause (here SIGINT) is re-raised; SIGTERMs arriving during the escalation do not replace it."""
    ready = tmp_path / 'ready'
    real_popen = subprocess.Popen
    started = {}

    def popen(command, **kwargs):
        process = real_popen(command, **kwargs)
        started['process'] = process
        deadline = time.monotonic() + 5
        while not ready.exists():  # the child ignores SIGTERM only once its trap is installed
            assert time.monotonic() < deadline
            real_sleep(0.05)
        real_wait = process.wait
        waits = {'count': 0}

        def wait(timeout=None):
            waits['count'] += 1
            if waits['count'] == 1:
                raise cancellation.Cancelled(signal.SIGINT)  # the first interrupt arrives while run_child waits
            return real_wait(timeout)
        process.wait = wait
        return process
    real_sleep = time.sleep
    monkeypatch.setattr(cancellation.subprocess, 'Popen', popen)
    calls = {'count': 0}

    def sleep(seconds):
        real_sleep(seconds)
        if calls['count'] < 3:
            calls['count'] += 1
            raise cancellation.Cancelled(signal.SIGTERM)
    monkeypatch.setattr(cancellation.time, 'sleep', sleep)
    command = ['sh', '-c', f"trap '' TERM; touch {ready}; while :; do /bin/sleep 0.2; done"]
    with pytest.raises(cancellation.Cancelled) as raised:
        cancellation.run_child(command, grace=1)
    process = started['process']
    assert raised.value.signum == signal.SIGINT and calls['count'] == 3
    assert process.returncode == -signal.SIGKILL
