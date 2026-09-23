"""Cooperative cancellation for the lab runners: SIGTERM, SIGHUP and SIGINT become one exception at a safe point.

A hosted job cancel escalates to SIGTERM; an operator's Ctrl-C is SIGINT; a
lost terminal is SIGHUP. Each is routed into `Cancelled`, a KeyboardInterrupt
subclass, so a runner's existing interrupt path handles all three: the phase
in flight is recorded, its services are stopped and its diagnostics
preserved, and the run ends with a FAIL record. Inside a critical section
(cleanup and the record write) a further signal is deferred and raised when
the section ends, so cleanup is never cut short by a second signal.

`run_child` runs a child in its own session and, on cancellation, terminates
the whole process group (the child and everything it spawned), waits a
bounded grace period, then kills what is left, before re-raising.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import signal
import subprocess
import threading
import time

SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


class Cancelled(KeyboardInterrupt):
    """A termination signal received at a safe point; str() names it."""

    def __init__(self, signum: int) -> None:
        self.signum = signum
        super().__init__('interrupted (' + signal_name(signum) + ')')


def signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return 'signal %d' % signum


def describe(error: BaseException) -> str:
    """The public reason for an interruption: which signal, or a plain interrupt."""
    if isinstance(error, Cancelled):
        return str(error)
    return 'interrupted'


class Cancellation:
    """Installs the handlers (main thread only) and owns the deferral state."""

    def __init__(self) -> None:
        self.deferring = False
        self.pending: int | None = None
        self.received: list[int] = []
        self._previous: dict[int, object] = {}

    def install(self) -> Cancellation:
        if threading.current_thread() is not threading.main_thread():
            return self
        for signum in SIGNALS:
            self._previous[signum] = signal.signal(signum, self._handler)
        return self

    def restore(self) -> None:
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)
        self._previous.clear()

    def _handler(self, signum, frame) -> None:
        self.received.append(signum)
        if self.deferring:
            if self.pending is None:
                self.pending = signum
            return
        raise Cancelled(signum)

    @contextmanager
    def critical(self):
        """Defer termination signals until the block ends; then raise the first one deferred."""
        outer = self.deferring
        self.deferring = True
        try:
            yield
        finally:
            self.deferring = outer
            if not outer and self.pending is not None:
                signum, self.pending = self.pending, None
                raise Cancelled(signum)


def leader_state(pid: int) -> str:
    """'running', 'exited' (a zombie, not reaped) or 'reaped', observed without reaping.

    waitid(WNOWAIT) leaves an exited leader as a zombie: its pid stays
    allocated, and the kernel does not reuse a pid while any process, zombie
    included, holds it.
    """
    try:
        info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return 'reaped'
    return 'running' if info is None else 'exited'


def terminate_group(process: subprocess.Popen, grace: float, *, reraise: bool = True) -> str:
    """SIGTERM the child's process group, wait up to grace seconds, then SIGKILL what is left.

    Group identity without a reuse race: the child leads its own session
    (every caller starts it with start_new_session=True), so the group id is
    the leader's pid. The leader is never reaped here before the final
    SIGKILL; its exit is observed with waitid(WNOWAIT), so up to the reap at
    the end it is alive or a zombie, its pid (the group id) cannot belong to
    any other process, and every killpg reaches only this group. The group is
    signalled only after leader_state() confirms the leader is still ours and
    unreaped; nothing in this process reaps it in between. If the leader had
    already been reaped before or during the call (outside this function),
    the group id is no longer provable and the group is not signalled: the
    returned outcome says so.

    The grace deadline is fixed once. A further signal during the escalation
    (GitHub escalates a cancel from SIGINT to SIGTERM) neither restarts nor
    extends it: the interrupted step resumes, and the SIGKILL step always
    runs. The first such interrupt is re-raised at the end when `reraise` is
    set; a caller that re-raises its own original cause passes reraise=False,
    so the original remains the recorded reason.
    """
    pgid = process.pid
    held: BaseException | None = None
    end = time.monotonic() + grace

    def step(action):
        nonlocal held
        while True:
            try:
                return action()
            except KeyboardInterrupt as error:  # a further cancellation: finish first, keep the first one
                held = held or error

    def signal_group(signum) -> bool:
        if process.returncode is not None or leader_state(pgid) == 'reaped':
            return False  # the group id can no longer be proven; never signal it
        try:
            os.killpg(pgid, signum)
        except ProcessLookupError:
            pass
        return True

    def wait_for_leader():
        while time.monotonic() < end and leader_state(pgid) == 'running':
            time.sleep(0.2)

    def reap():
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            pass

    outcome = 'terminated'
    if not step(lambda: signal_group(signal.SIGTERM)):
        outcome = 'leader_already_reaped'
    else:
        step(wait_for_leader)
        if not step(lambda: signal_group(signal.SIGKILL)):  # the leader if still alive, and any member left behind
            outcome = 'leader_reaped_elsewhere'
        step(reap)
    if held is not None and reraise:
        raise held
    return outcome


def run_child(command: list[str], *, grace: float = 20.0, timeout: float | None = None, **popen_kwargs) -> int:
    """Run a child in its own session; on cancellation or timeout stop its whole group before raising."""
    process = subprocess.Popen(command, start_new_session=True, **popen_kwargs)
    try:
        return process.wait(timeout=timeout)
    except BaseException:
        # The original cause (the first signal, or the timeout) is what the caller
        # records; a further signal during the escalation does not replace it.
        terminate_group(process, grace, reraise=False)
        raise
