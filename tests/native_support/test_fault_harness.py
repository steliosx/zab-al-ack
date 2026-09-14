"""Offline helper smoke checks, explicitly NOT native PostgreSQL evidence."""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import shutil
import socket

import pytest

from zabalack.rpc import Endpoint, RPCError, ZabbixClient, neutral_comment, observed_reference
from native_support import fault_harness
from native_support.fault_harness import ObservedPostgres, SyntheticSource


@pytest.fixture
def source(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("offline HTTPS helper requires openssl")
    server = SyntheticSource(tmp_path, trigger_id="87200", host_id="87100", group_id="87300")
    try:
        yield server
    finally:
        server.close()
        assert server.errors == []


def client_for(source):
    return ZabbixClient(Endpoint("offline-synthetic", **source.endpoint, lab=True))


COHORT = {"group_ids": ["87300"], "host_ids": ["87100"]}
REFERENCE = "synthetic_native_reference_12345"
ISSUED_AT = "2026-01-01T00:00:00Z"


async def test_offline_real_https_client_and_durable_synthetic_effect(source):
    source.add("87400001")
    client = client_for(source)
    assert (await client.event_get("87400001", COHORT))[0]["acknowledged"] == "0"
    await client.acknowledge("87400001", comment=True, public_reference=REFERENCE, issued_at=ISSUED_AT)
    row = (await client.event_get("87400001", COHORT, history=True))[0]
    assert row["acknowledged"] == "1"
    assert observed_reference(row, public_reference=REFERENCE, issued_at=ISSUED_AT)
    ledger = json.loads(source.ledger_path.read_text())
    assert ledger["events"]["87400001"] == {
        "commits": 1, "messages": [neutral_comment(ISSUED_AT, REFERENCE)]}
    assert [request["method"] for request in ledger["requests"]] == [
        "event.get", "event.acknowledge", "event.get"]
    assert source.token not in source.ledger_path.read_text()


async def test_offline_commit_precedes_dropped_response_and_read_recovers_evidence(source):
    source.add("87400002", behavior="drop_after_commit")
    client = client_for(source)
    with pytest.raises(RPCError) as raised:
        await client.acknowledge("87400002", comment=True, public_reference=REFERENCE, issued_at=ISSUED_AT)
    assert (raised.value.reason, raised.value.uncertain, raised.value.transient) == (
        "outcome_unknown", True, True)
    source.wait_committed("87400002")
    row = (await client.event_get("87400002", COHORT, history=True))[0]
    assert observed_reference(row, public_reference=REFERENCE, issued_at=ISSUED_AT)
    assert source.events["87400002"]["commits"] == 1


async def test_offline_remote_commit_gate_survives_client_cancellation(source):
    source.add("87400003", behavior="hold_after_commit")
    task = asyncio.create_task(client_for(source).acknowledge("87400003", comment=True,
        public_reference=REFERENCE, issued_at=ISSUED_AT))
    await asyncio.to_thread(source.wait_committed, "87400003")
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    source.release_commit("87400003")
    assert json.loads(source.ledger_path.read_text())["events"]["87400003"]["commits"] == 1


async def test_offline_database_barrier_forwards_exact_payload_and_result():
    payload = {"action_id": "synthetic-id", "private_capture": "must-not-enter-observation"}
    response = {"allowed": True, "attempt_id": "synthetic-attempt", "private_capture": "do-not-export"}

    class RecordingDatabase:
        async def call(self, operation, value):
            assert operation == "start_attempt" and value is payload
            return response

    parent, child = multiprocessing.get_context("spawn").Pipe()
    try:
        observer = ObservedPostgres(RecordingDatabase(), child, ("after:start_attempt",))
        task = asyncio.create_task(observer.call("start_attempt", payload))
        assert await asyncio.to_thread(parent.poll, 3)
        before = parent.recv()
        assert await asyncio.to_thread(parent.poll, 3)
        after = parent.recv()
        assert before == {"event": "before:start_attempt", "present": True, "action_id": "synthetic-id"}
        assert after == {"event": "after:start_attempt", "present": True,
                         "allowed": True, "attempt_id": "synthetic-attempt"}
        assert not task.done()
        parent.send({"release": "after:start_attempt"})
        assert await asyncio.wait_for(task, 3) is response
    finally:
        parent.close()
        child.close()


def test_offline_source_cleanup_also_runs_when_later_setup_fails(tmp_path):
    if not shutil.which("openssl"):
        pytest.skip("offline HTTPS helper requires openssl")
    with pytest.raises(RuntimeError, match="synthetic later setup failure"):
        with SyntheticSource(tmp_path, trigger_id="87200", host_id="87100", group_id="87300") as server:
            port = server.server.server_port
            raise RuntimeError("synthetic later setup failure")
    assert not server.thread.is_alive()
    assert server.active_handlers == 0
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1)
    assert server.ledger_path.is_file(), "failure cleanup must preserve source evidence"


def test_offline_partial_source_start_closes_its_bound_socket(tmp_path, monkeypatch):
    if not shutil.which("openssl"):
        pytest.skip("offline HTTPS helper requires openssl")
    servers = []
    original = fault_harness.ThreadingHTTPServer

    def capture_server(*args, **kwargs):
        server = original(*args, **kwargs)
        servers.append(server)
        return server

    def reject_start(_thread):
        raise RuntimeError("synthetic thread start failure")

    monkeypatch.setattr(fault_harness, "ThreadingHTTPServer", capture_server)
    monkeypatch.setattr(fault_harness.threading.Thread, "start", reject_start)
    with pytest.raises(RuntimeError, match="synthetic thread start failure"):
        SyntheticSource(tmp_path, trigger_id="87200", host_id="87100", group_id="87300")
    assert len(servers) == 1
    assert servers[0].socket.fileno() == -1
