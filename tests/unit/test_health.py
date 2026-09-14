"""T15/T16/T34 unit evidence; these mocks are not PgBouncer acceptance."""
import asyncio
import copy
import threading

import pytest

from zabalack.health import CaptureHealthProbe, CaptureUnhealthy, ProbeConfig


def healthy():
    return dict(in_recovery=False, database_name="lab", deployment_id="lab-only",
                zabalack_schema_version=8, config_revision="synthetic-1", capture_disabled=False,
                trigger_ok=True, functions_ok=True, checked_at="2026-01-01T00:00:00Z")


class Connection:
    def __init__(self):
        self.commands = []
        self.closed = False
        self.result = healthy()
        self.fail = False
        self.delay = 0

    async def execute(self, command):
        self.commands.append(command)
        if command == "SELECT zabalack.capture_health()":
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                raise OSError("synthetic transport failure; do not expose this text")
        return self

    async def fetchone(self):
        return [self.result]

    async def close(self):
        self.closed = True


@pytest.fixture
def setup_probe():
    connections = []
    now = [1.0]

    async def connect(dsn):
        connection = Connection()
        connections.append(connection)
        return connection

    probe = CaptureHealthProbe(ProbeConfig("synthetic-unused", "lab", "lab-only", "synthetic-1"),
                               connector=connect, clock=lambda: now[0])
    yield probe, connections, now
    probe.close()


def test_T15_separate_commands_cache_and_failed_refresh_no_stale(setup_probe):
    probe, connections, now = setup_probe
    assert not connections  # lazy after-fork construction
    assert probe.check()["trigger_ok"]
    assert connections[0].commands == ["BEGIN READ ONLY", "SET LOCAL statement_timeout = '500ms'",
                                       "SELECT zabalack.capture_health()", "COMMIT"]
    now[0] = 5.9
    assert probe.check()["trigger_ok"]
    assert len(connections[0].commands) == 4
    connections[0].fail = True
    now[0] = 6.0
    with pytest.raises(CaptureUnhealthy, match="capture_health_unavailable"):
        probe.check()
    assert probe._healthy_at is None
    assert connections[0].closed
    assert connections[0].commands[-1] == "ROLLBACK"
    probe.check()
    assert len(connections) == 2


@pytest.mark.parametrize("field,value", [("in_recovery", True), ("deployment_id", "unrelated"),
                                         ("database_name", "other"), ("zabalack_schema_version", 7),
                                         ("capture_disabled", True), ("config_revision", "stale"),
                                         ("trigger_ok", False), ("functions_ok", False)])
def test_T16_T21_wrong_identity_or_missing_capture_is_unhealthy(setup_probe, field, value):
    probe, connections, now = setup_probe
    probe.check()
    connections[0].result[field] = value
    with pytest.raises(CaptureUnhealthy):
        probe.check(force=True)
    assert probe._healthy_at is None
    assert connections[0].closed


def test_T34_mock_transport_total_deadline_discards_connection(setup_probe):
    probe, connections, now = setup_probe
    probe.check()
    connections[0].delay = 5
    probe.config = ProbeConfig("synthetic-unused", "lab", "lab-only", "synthetic-1", overall_timeout=.03)
    with pytest.raises(CaptureUnhealthy):
        probe.check(force=True)
    assert connections[0].closed
    assert "ROLLBACK" in connections[0].commands


def test_T34_probe_acquisition_is_bounded(setup_probe):
    probe, connections, now = setup_probe
    probe.check()
    probe.config = ProbeConfig("synthetic-unused", "lab", "lab-only", "synthetic-1", acquisition_timeout=.01)
    probe._lock.acquire()
    try:
        with pytest.raises(CaptureUnhealthy, match="acquisition_timeout"):
            probe.check()
    finally:
        probe._lock.release()
    assert probe._healthy_at is None


def test_probe_config_limits_and_afterfork_reset(setup_probe):
    with pytest.raises(ValueError):
        ProbeConfig("unused", "lab", "lab", "r", cache_seconds=6)
    with pytest.raises(ValueError):
        ProbeConfig("unused", "lab", "lab", "r", statement_timeout_ms=501)
    probe, connections, now = setup_probe
    probe.check()
    probe.close()
    probe._after_fork()
    assert probe._connection is None and probe._healthy_at is None and probe._loop is None
