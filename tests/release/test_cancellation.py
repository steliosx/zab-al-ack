"""tools/cancellation.py: SIGTERM, SIGHUP and SIGINT as one interrupt, deferral inside critical sections, group termination."""
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
