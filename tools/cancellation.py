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


def terminate_group(process: subprocess.Popen, grace: float) -> None:
    """SIGTERM the child's process group, wait up to grace seconds, then SIGKILL what is left.

    The child leads its own session (run_child), so its process group id is
    its pid, valid even after the leader has been reaped while members remain.
    A second signal during the escalation (GitHub escalates a cancel from
    SIGINT to SIGTERM) is held until the escalation has finished, then
    re-raised, so the SIGKILL step is never skipped. Members that outlive the
    leader are killed too.
    """
    pgid = process.pid
    held: BaseException | None = None

    def step(action):
        nonlocal held
        while True:
            try:
                return action()
            except KeyboardInterrupt as error:  # a further cancellation: finish first
                held = held or error

    def signal_group(signum):
        try:
            os.killpg(pgid, signum)
        except ProcessLookupError:
            pass

    def wait_for_leader():
        end = time.monotonic() + grace
        while time.monotonic() < end and process.poll() is None:
            time.sleep(0.2)

    def reap():
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            pass

    step(lambda: signal_group(signal.SIGTERM))
    step(wait_for_leader)
    step(lambda: signal_group(signal.SIGKILL))  # the leader if still alive, and any member left behind
    step(reap)
    if held is not None:
        raise held


def run_child(command: list[str], *, grace: float = 20.0, timeout: float | None = None, **popen_kwargs) -> int:
    """Run a child in its own session; on cancellation or timeout stop its whole group before raising."""
    process = subprocess.Popen(command, start_new_session=True, **popen_kwargs)
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_group(process, grace)
        raise
    except BaseException:
        terminate_group(process, grace)
        raise
