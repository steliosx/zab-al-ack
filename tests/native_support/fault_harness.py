"""Real-process barriers and a durable, loopback-only synthetic JSON-RPC source.

The database observer forwards every production payload/result unchanged. Gates
run outside committed DB calls. No verifier, endpoint registry, HTTP transport,
clock, PostgreSQL privilege, or RPC response binding is replaced.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import multiprocessing
import os
from pathlib import Path
import signal
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from zabalack.config import load_settings, read_protected_file
from zabalack.db import Postgres
from zabalack.reconciler import Reconciler
from zabalack.worker import Worker


def protected_file(path: Path, content: bytes) -> Path:
    """Create only fresh synthetic files; never follow a supplied symlink."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return path.resolve()


def _facts(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"present": bool(value)}
    allowed = {"action_id", "worker_id", "lease_generation", "slot", "attempt_id",
               "allowed", "state", "reason", "uncertain", "completion_evidence"}
    return {"present": True, **{key: item for key, item in value.items() if key in allowed}}


class ObservedPostgres:
    """Barrier-only decorator: the production Postgres performs every DB call."""

    def __init__(self, database: Postgres, channel: Any, gates: tuple[str, ...]) -> None:
        self.database, self.channel, self.gates = database, channel, gates

    async def checkpoint(self, name: str, value: Any) -> None:
        self.channel.send({"event": name, **_facts(value)})
        if name in self.gates:
            if not await asyncio.to_thread(self.channel.poll, 55):
                raise TimeoutError("synthetic process barrier timed out")
            if self.channel.recv() != {"release": name}:
                raise RuntimeError("synthetic process barrier order mismatch")

    async def call(self, operation: str, payload: dict[str, Any] | None = None) -> Any:
        await self.checkpoint("before:" + operation, payload)
        result = await self.database.call(operation, payload)
        await self.checkpoint("after:" + operation, result)
        return result

    async def operator(self, operation: str, payload: dict[str, Any]) -> Any:
        return await self.database.operator(operation, payload)


def _worker_entry(config_path: str, worker_id: str, mode: str, count: int,
                  action_id: str | None, gates: tuple[str, ...], channel: Any) -> None:
    """Spawn target; no inherited connections, event loops, or monkeypatches."""
    import psycopg

    async def run() -> None:
        settings = load_settings(config_path)
        dsn = read_protected_file(settings.worker_dsn_file).decode().strip()
        # This is a direct LOGIN, not an administrator connection with SET ROLE.
        with psycopg.connect(dsn, autocommit=True, connect_timeout=3) as connection:
            assert connection.execute("SELECT session_user,current_user").fetchone() == (
                "zabalack_worker", "zabalack_worker")
        database = ObservedPostgres(Postgres(dsn), channel, gates)
        keys = {kid: read_protected_file(path) for kid, path in settings.signing_keys.items()}
        worker = Worker(settings, database, keys, worker_id=worker_id)
        assert await worker.check_health(), "synthetic worker health gate failed"
        channel.send({"event": "ready", "pid": os.getpid(), "worker_id": worker_id})
        results = []
        for _ in range(count):
            if mode == "verify":
                results.append(await worker.verify_once())
            elif mode == "delivery":
                results.append(await worker.delivery_once())
            elif mode == "reconcile":
                rows = await database.call("inspect", {"action_id": action_id})
                assert len(rows) == 1
                reconciler = Reconciler(settings, database, worker.registry, worker_id=worker_id)
                results.append(await reconciler.reconcile_action(rows[0]))
            else:
                raise ValueError("unsupported synthetic child operation")
        channel.send({"event": "done", "results": results})

    try:
        asyncio.run(run())
    except BaseException as error:
        # SQL exceptions can carry private capture material. Export class only.
        with suppress(BrokenPipeError, EOFError, OSError):
            channel.send({"event": "error", "error_class": type(error).__name__,
                          "sqlstate": getattr(error, "sqlstate", None)})
        raise SystemExit(1) from None
    finally:
        channel.close()


class WorkerProcess:
    def __init__(self, config_path: Path, worker_id: str, *, mode: str = "delivery",
                 count: int = 1, action_id: str | None = None,
                 gates: tuple[str, ...] = (), evidence_dir: Path) -> None:
        context = multiprocessing.get_context("spawn")
        self.channel, child_channel = context.Pipe()
        self.process = context.Process(target=_worker_entry,
            args=(str(config_path), worker_id, mode, count, action_id, gates, child_channel))
        self.events: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.evidence_path = evidence_dir / (worker_id + ".json")
        self.process.start()
        child_channel.close()

    def wait(self, event: str, *, timeout: float = 12) -> dict[str, Any]:
        for index, message in enumerate(self.pending):
            if message["event"] == event:
                return self.pending.pop(index)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.channel.poll(max(0, deadline - time.monotonic())):
                break
            try:
                message = self.channel.recv()
            except EOFError:
                break
            self.events.append(message)
            if message["event"] == event:
                return message
            if message["event"] == "error":
                raise AssertionError(f"child failed: {message['error_class']}")
            self.pending.append(message)
        raise AssertionError(f"child did not reach {event}; observed {[m['event'] for m in self.events]}")

    def release(self, event: str) -> None:
        self.channel.send({"release": event})

    def finish(self, *, timeout: float = 12) -> dict[str, Any]:
        result = self.wait("done", timeout=timeout)
        self.process.join(timeout=3)
        assert self.process.exitcode == 0, "child failed to exit successfully"
        return result

    def kill(self) -> None:
        assert self.process.is_alive(), "fault injection requires a live process"
        self.process.kill()
        self.process.join(timeout=3)
        assert self.process.exitcode == -signal.SIGKILL

    def close(self) -> None:
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=3)
        while self.channel.poll():
            try:
                self.events.append(self.channel.recv())
            except (EOFError, OSError):
                break
        self.channel.close()
        protected_file(self.evidence_path, json.dumps({"exitcode": self.process.exitcode,
            "events": self.events}, indent=2).encode())


class SyntheticSource:
    """HTTPS stub with fsynced effect evidence before any reply/drop/commit gate."""

    def __init__(self, directory: Path, *, trigger_id: str, host_id: str, group_id: str) -> None:
        self.directory = directory
        self.trigger_id, self.host_id, self.group_id = trigger_id, host_id, group_id
        self.token = "synthetic-native-fault-token"
        self.token_file = protected_file(directory / "source-token", self.token.encode())
        self.ca_file = directory / "source-ca.pem"
        self.key_file = directory / "source-key.pem"
        # Ephemeral CA/server certificate trusted explicitly by the real RPC client.
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(self.key_file), "-out", str(self.ca_file), "-days", "2",
            "-subj", "/CN=synthetic-zabalack-loopback",
            "-addext", "subjectAltName=IP:127.0.0.1",
            "-addext", "basicConstraints=critical,CA:TRUE"],
            check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.key_file.chmod(0o600)
        self.ca_file.chmod(0o600)
        self.condition = threading.Condition()
        self.events: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, str]] = []
        self.errors: list[str] = []
        self.active_handlers = 0
        self.behaviors: dict[str, str] = {}
        self.commit_gates: dict[str, threading.Event] = {}
        self.ledger_path = directory / "source-effects.json"
        self._persist()
        source = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(2)
                with source.condition:
                    source.active_handlers += 1

            def finish(self) -> None:
                try:
                    super().finish()
                finally:
                    with source.condition:
                        source.active_handlers -= 1
                        source.condition.notify_all()

            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:
                try:
                    assert self.path == "/api_jsonrpc.php"
                    assert self.headers.get("Authorization") == "Bearer " + source.token
                    size = int(self.headers.get("Content-Length", "0"))
                    assert 0 < size < 16384
                    request = json.loads(self.rfile.read(size))
                    assert request["jsonrpc"] == "2.0" and isinstance(request["id"], str)
                    params = request["params"]
                    assert len(params["eventids"]) == 1
                    event_id = params["eventids"][0]
                    with source.condition:
                        assert event_id in source.events
                        source.requests.append({"method": request["method"], "event_id": event_id})
                        event = source.events[event_id]
                        if request["method"] == "event.get":
                            assert params["groupids"] == [source.group_id]
                            assert (params["source"], params["object"], params["value"]) == (0, 0, 1)
                            row = {"eventid": event_id, "objectid": source.trigger_id, "source": "0",
                                "object": "0", "value": "1", "clock": "1700000000", "r_eventid": "0",
                                "acknowledged": "1" if event["commits"] else "0",
                                "hosts": [{"hostid": source.host_id}]}
                            if "selectAcknowledges" in params:
                                row["acknowledges"] = [{"action": "6", "message": message}
                                    for message in event["messages"]]
                            result = [row]
                        else:
                            assert request["method"] == "event.acknowledge"
                            assert type(params["action"]) is int and params["action"] in (2, 6)
                            assert set(params) == ({"eventids", "action", "message"}
                                                  if params["action"] == 6 else {"eventids", "action"})
                            event["commits"] += 1
                            if "message" in params:
                                assert params["message"].startswith("[zabalack] Acknowledged by the monitoring operations team.")
                                event["messages"].append(params["message"])
                            result = {"eventids": [event_id]}
                        source._persist()
                        source.condition.notify_all()
                    if request["method"] == "event.acknowledge":
                        behavior = source.behaviors.get(event_id)
                        if behavior == "hold_after_commit":
                            if not source.commit_gates[event_id].wait(12):
                                raise TimeoutError("synthetic remote commit gate timed out")
                        if behavior == "drop_after_commit":
                            self.close_connection = True
                            with suppress(OSError):
                                self.connection.shutdown(socket.SHUT_RDWR)
                            self.connection.close()
                            return
                    body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
                    with suppress(BrokenPipeError, ConnectionResetError, ssl.SSLError):
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                except Exception as error:
                    with source.condition:
                        source.errors.append(type(error).__name__)
                        source._persist()
                        source.condition.notify_all()
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(self.ca_file, self.key_file)
            self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
        except BaseException:
            # Do not call shutdown before serve_forever starts: it would block.
            self.server.server_close()
            raise

    @property
    def endpoint(self) -> dict[str, str]:
        return {"url": f"https://127.0.0.1:{self.server.server_port}/api_jsonrpc.php",
            "token_file": str(self.token_file), "ca_file": str(self.ca_file), "api_version": "7.0.0"}

    def _persist(self) -> None:
        pending = self.directory / "source-effects.next"
        protected_file(pending, json.dumps({"events": self.events,
            "requests": self.requests, "errors": self.errors}, indent=2).encode())
        os.replace(pending, self.ledger_path)
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def add(self, event_id: str, *, behavior: str = "normal") -> None:
        assert behavior in {"normal", "drop_after_commit", "hold_after_commit"}
        with self.condition:
            assert event_id not in self.events
            self.events[event_id] = {"commits": 0, "messages": []}
            self.behaviors[event_id] = behavior
            self.commit_gates[event_id] = threading.Event()
            self._persist()

    def wait_committed(self, event_id: str, *, timeout: float = 8) -> None:
        with self.condition:
            assert self.condition.wait_for(lambda: self.events[event_id]["commits"] > 0 or self.errors,
                                           timeout=timeout), "synthetic effect was not committed"
            assert not self.errors, "synthetic HTTPS source failed"
            # Read back the durable file, not only an in-memory success flag.
            assert json.loads(self.ledger_path.read_text())["events"][event_id]["commits"] == 1

    def release_commit(self, event_id: str) -> None:
        self.commit_gates[event_id].set()

    def __enter__(self) -> SyntheticSource:
        return self

    def __exit__(self, *_exception: Any) -> None:
        self.close()

    def close(self) -> None:
        for gate in self.commit_gates.values():
            gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        assert not self.thread.is_alive(), "synthetic HTTPS source failed to stop"
        with self.condition:
            assert self.condition.wait_for(lambda: self.active_handlers == 0, timeout=3), (
                "synthetic HTTPS request handlers failed to stop")
