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
