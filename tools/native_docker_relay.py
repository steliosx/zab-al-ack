"""Owned Linux Docker bridge endpoints exposed only during a synthetic lab run."""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path
import re
import select
import socket
import subprocess
import threading
import time

# Let the kernel select unused host ports; bridge service ports remain fixed.
SERVICES = {"postgres": (0, 5432), "pgbouncer": (0, 6432)}
MAX_CONNECTIONS = 128
MAX_BUFFER = 1024 * 1024


def _output(command, root):
    result = subprocess.run(command, cwd=root, check=True, capture_output=True,
                            text=True, timeout=10)
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("oversized Docker response")
    return result.stdout


def _object(command, root):
    data = json.loads(_output(command, root))
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("invalid Docker inspection")
    return data[0]


def _owned_endpoints(compose: list[str], project: str, root: Path) -> dict:
    """Resolve only the exact fresh project; never accept configured addresses."""
    if not re.fullmatch(r"ack-alpha-[0-9a-f]{10}-(postgres|delivery_faults|worker|alerta|performance)", project):
        raise ValueError("invalid synthetic project")
    endpoints = {}
    for service, (_, port) in SERVICES.items():
        identifier = _output([*compose, "ps", "-q", service], root).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise ValueError("expected one owned container")
        container = _object(["docker", "inspect", "--type", "container", identifier], root)
        labels = container.get("Config", {}).get("Labels", {})
        network_name = project + "_isolated"
        networks = container.get("NetworkSettings", {}).get("Networks", {})
        if (container.get("Id") != identifier or container.get("State", {}).get("Running") is not True
                or labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.service") != service
                or container.get("HostConfig", {}).get("NetworkMode") != network_name
                or set(networks) != {network_name}):
            raise ValueError("unowned container or network")
        endpoint = networks[network_name]
        network_id = endpoint.get("NetworkID", "")
        if not re.fullmatch(r"[0-9a-f]{64}", network_id):
            raise ValueError("invalid network identity")
        network = _object(["docker", "network", "inspect", network_id], root)
        network_labels = network.get("Labels", {})
        if (network.get("Id") != network_id or network.get("Name") != network_name
                or network.get("Internal") is not True or network.get("Driver") != "bridge"
                or network_labels.get("com.docker.compose.project") != project
                or network_labels.get("com.docker.compose.network") != "isolated"):
            raise ValueError("unowned or noninternal network")
        address = ipaddress.IPv4Address(endpoint.get("IPAddress", ""))
        private_ranges = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
                          ipaddress.ip_network("192.168.0.0/16"))
        if not any(address in subnet for subnet in private_ranges):
            raise ValueError("nonprivate bridge endpoint")
        member = network.get("Containers", {}).get(identifier, {})
        interface = ipaddress.IPv4Interface(member.get("IPv4Address", ""))
        subnets = [ipaddress.ip_network(item.get("Subnet", ""))
                   for item in network.get("IPAM", {}).get("Config", []) if item.get("Subnet")]
        if (interface.ip != address or not any(address in subnet and address not in {
                subnet.network_address, subnet.broadcast_address} for subnet in subnets if subnet.version == 4)):
            raise ValueError("bridge endpoint membership mismatch")
        endpoints[service] = (str(address), port)
    return endpoints


def owned_endpoints(compose: list[str], project: str, root: Path) -> dict:
    try:
        return _owned_endpoints(compose, project, root)
    except (AttributeError, KeyError, TypeError) as error:
        raise ValueError("malformed Docker inspection") from error


class LoopbackRelay:
    """Bounded concurrent byte streams; EOF in one direction preserves the other."""

    def __init__(self, port: int):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # On Linux, before listen(), two reuse-enabled sockets may bind the
        # same address. Keep reuse disabled throughout this held reservation.
        try:
            self.listener.bind(("127.0.0.1", port))
        except BaseException:
            self.listener.close()
            raise
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.connections = set()
        self.workers = set()
        self.thread = None
        self.failed = False

    def start(self, target):
        self.target = target
        self.listener.listen(MAX_CONNECTIONS)
        self.listener.settimeout(0.2)
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            while not self.stop.is_set():
                try:
                    client, _ = self.listener.accept()
                except TimeoutError:
                    continue
                with self.lock:
                    if self.stop.is_set() or len(self.workers) >= MAX_CONNECTIONS:
                        client.close()
                        continue
                    self.connections.add(client)
                    worker = threading.Thread(target=self._forward, args=(client,), daemon=True)
                    self.workers.add(worker)
                    worker.start()
        except OSError:
            if not self.stop.is_set():
                self.failed = True

    def _forward(self, client):
        upstream = None
        try:
            upstream = socket.create_connection(self.target, timeout=2)
            with self.lock:
                if self.stop.is_set():
                    return
                self.connections.add(upstream)
            client.setblocking(False)
            upstream.setblocking(False)
            peers = {client: upstream, upstream: client}
            pending = {client: bytearray(), upstream: bytearray()}
            reading = {client, upstream}
            while not self.stop.is_set() and (reading or any(pending.values())):
                readers = [stream for stream in reading if len(pending[peers[stream]]) < MAX_BUFFER]
                writers = [stream for stream, buffer in pending.items() if buffer]
                readable, writable, _ = select.select(readers, writers, [], 0.2)
                for stream in readable:
                    try:
                        data = stream.recv(min(65536, MAX_BUFFER - len(pending[peers[stream]])))
                    except BlockingIOError:
                        continue
                    if data:
                        pending[peers[stream]].extend(data)
                    else:
                        reading.remove(stream)
                        if not pending[peers[stream]]:
                            peers[stream].shutdown(socket.SHUT_WR)
                for stream in writable:
                    try:
                        sent = stream.send(pending[stream])
                    except BlockingIOError:
                        continue
                    if not sent:
                        return
                    del pending[stream][:sent]
                    if not pending[stream] and peers[stream] not in reading:
                        stream.shutdown(socket.SHUT_WR)
        except (OSError, ValueError):
            # A cancelled/reset client is normal; the pytest client observes
            # failed upstream operations. No payload or exception text is logged.
            pass
        finally:
            with self.lock:
                for stream in (client, upstream):
                    if stream is not None:
                        self.connections.discard(stream)
                        stream.close()
                self.workers.discard(threading.current_thread())

    def close(self):
        self.stop.set()
        self.listener.close()
        with self.lock:
            for stream in self.connections:
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            workers = list(self.workers)
        deadline = time.monotonic() + 4
        for thread in ([self.thread] if self.thread else []) + workers:
            thread.join(max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in ([self.thread] if self.thread else []) + workers):
            raise RuntimeError("native relay cleanup incomplete")
        if self.failed:
            raise RuntimeError("native relay listener failed")


class DockerLabRelays:
    """Reserve loopback before Compose startup; release before Compose shutdown."""

    def __enter__(self):
        self.relays = {}
        try:
            for service, (port, _) in SERVICES.items():
                self.relays[service] = LoopbackRelay(port)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    @property
    def ports(self):
        """Actual host ports remain reserved by these same open sockets."""
        return {service: relay.port for service, relay in self.relays.items()}

    def activate(self, compose, project, root):
        endpoints = owned_endpoints(compose, project, root)
        # Both ownership checks finish before either service is contacted.
        # Host Linux routing is required; Desktop/rootless bridge isolation is
        # not treated as an available native route merely due to a Unix socket.
        deadline = time.monotonic() + 10
        for target in endpoints.values():
            while True:
                try:
                    with socket.create_connection(target, timeout=1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("native Docker bridge unreachable") from None
                    time.sleep(0.1)
        for service, target in endpoints.items():
            self.relays[service].start(target)

    def __exit__(self, exc_type, exc, traceback):
        failed = False
        for relay in self.relays.values():
            try:
                relay.close()
            except RuntimeError:
                failed = True
        if failed:
            raise RuntimeError("native relay cleanup failed")
