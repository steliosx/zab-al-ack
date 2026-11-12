"""T15/T34 after-fork acquisition regression; no DB/network connection is made."""
import os
import select
import signal

import pytest

from zabalack import health


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='requires POSIX fork')
def test_T15_T34_probe_registry_does_not_inherit_locked_parent_mutex():
    read_fd, write_fd = os.pipe()
    child = None
    health._registry_lock.acquire()
    try:
        child = os.fork()
        if child == 0:
            os.close(read_fd)
            try:
                config = health.ProbeConfig('unused-no-connect', 'lab', 'lab', 'lab')
                health.get_probe(config)  # construction only, never check/connect
                os.write(write_fd, b'constructed')
            finally:
                os._exit(0)
        os.close(write_fd)
        readable, _, _ = select.select([read_fd], [], [], 1.5)
        assert readable, 'child blocked on inherited registry lock before bounded acquisition'
        assert os.read(read_fd, 64) == b'constructed'
    finally:
        health._registry_lock.release()
        os.close(read_fd)
        if child:
            exited, _ = os.waitpid(child, os.WNOHANG)
            if not exited:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)


import threading

# Bounds only; nothing in this test waits for a wall-clock duration on the
# correct path except the deliberate overlap window below.
PARKED = 2.0
OVERLAP = 0.2
JOIN = 10.0
HEALTHY = dict(in_recovery=False, database_name='lab', deployment_id='lab-only',
               zabalack_schema_version=4, config_revision='synthetic-1', capture_disabled=False,
               trigger_ok=True, functions_ok=True, checked_at='2026-01-01T00:00:00Z')


async def _never_connect(dsn):
    raise AssertionError('this probe must not open a connection')


class RacingProbe(health.CaptureHealthProbe):
    """Injects delays only; no branch of `check()` is altered.

    The window is the non-atomic sequence `check()` runs when it sees the stale
    pid a fork leaves behind: it replaces `self._lock`, so two threads that both
    observed the stale pid acquire *different* mutexes and enter the section
    documented as mutually exclusive together, after which one releases a lock
    it never held.
    """

    def __init__(self, config):
        self.entered = []
        self._entered_lock = threading.Lock()
        self.late_committed = threading.Event()
        self.refresh_entered = threading.Event()
        self.second_refresh = threading.Event()
        super().__init__(config, connector=_never_connect)

    def _after_fork(self):
        if threading.current_thread().name == 'late':
            # Park here until another thread is inside the guarded section, so
            # the two threads' fork branches genuinely overlap.
            self.late_committed.set()
            self.refresh_entered.wait(PARKED)
        super()._after_fork()

    async def _refresh(self):
        with self._entered_lock:
            self.entered.append(threading.current_thread().name)
            first = len(self.entered) == 1
        self.late_committed.set()
        self.refresh_entered.set()
        if first:
            # Stay inside long enough for a second thread to arrive, if the
            # fork branch let one through; the correct path waits this out once.
            self.second_refresh.wait(OVERLAP)
        else:
            self.second_refresh.set()
        return dict(HEALTHY)


def test_T15_T34_two_threads_racing_the_post_fork_branch_stay_mutually_exclusive():
    """`check()` must not let a second thread into the refresh after a fork.

    `os.register_at_fork(after_in_child=self._after_fork)` leaves `_pid` None in
    the child, so in a threaded worker two request threads can both take the pid
    branch. Fork detection therefore runs under a lock `check()` never replaces,
    and the working lock is bound to a local before it is acquired and released.
    """
    config = health.ProbeConfig('synthetic-unused-no-connect', 'lab', 'lab-only', 'synthetic-1',
                                cache_seconds=5, acquisition_timeout=1, overall_timeout=2)
    probe = RacingProbe(config)
    probe._pid = None  # exactly the state os.register_at_fork leaves in a child
    failures = []

    def run():
        try:
            probe.check()
        except BaseException as exc:  # noqa: BLE001 - the failure is what is asserted
            failures.append(f'{threading.current_thread().name}: {type(exc).__name__}: {exc}')

    late = threading.Thread(target=run, name='late')
    early = threading.Thread(target=run, name='early')
    late.start()
    # The early thread only starts once the late one has committed to the branch.
    assert probe.late_committed.wait(PARKED), 'the late thread never reached the fork branch'
    early.start()
    for thread in (late, early):
        thread.join(JOIN)
    assert not late.is_alive() and not early.is_alive(), 'a thread never left check()'

    assert len(probe.entered) == 1, f'two threads entered the mutually exclusive refresh: {probe.entered}'
    assert failures == [], f'check() raised out of the post-fork branch: {failures}'
