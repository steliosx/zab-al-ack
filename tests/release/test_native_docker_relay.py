"""Synthetic TCP and inspection tests; no Docker or database evidence."""
from concurrent.futures import ThreadPoolExecutor
import errno
import json
import socket

import pytest

import native_docker_relay as relay_tool

PROJECT = "ack-alpha-0123456789-postgres"
NETWORK_ID = "c" * 64
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "lab/compose.postgres.yml", "--profile", "pooling"]


def inspections():
    name = PROJECT + "_isolated"
    containers = {}
    network = {"Id": NETWORK_ID, "Name": name, "Internal": True, "Driver": "bridge",
        "Labels": {"com.docker.compose.project": PROJECT, "com.docker.compose.network": "isolated"},
        "IPAM": {"Config": [{"Subnet": "172.24.0.0/16"}]}, "Containers": {}}
    for service, identifier, address in (("postgres", "a" * 64, "172.24.0.2"), ("pgbouncer", "b" * 64, "172.24.0.3")):
        containers[service] = {"Id": identifier, "State": {"Running": True},
            "Config": {"Labels": {"com.docker.compose.project": PROJECT, "com.docker.compose.service": service}},
            "HostConfig": {"NetworkMode": name},
            "NetworkSettings": {"Networks": {name: {"NetworkID": NETWORK_ID, "IPAddress": address}}}}
        network["Containers"][identifier] = {"IPv4Address": address + "/16"}
    return containers, network


def fake_inspection(monkeypatch, containers, network, *, bad_ps=False):
    commands = []
    def output(command, root):
        commands.append(command)
        if command[:len(COMPOSE)] == COMPOSE:
            assert command[-3:-1] == ["ps", "-q"]
            return "ambiguous\nids" if bad_ps else containers[command[-1]]["Id"]
        if command[:4] == ["docker", "inspect", "--type", "container"]:
            return json.dumps([next(item for item in containers.values() if item["Id"] == command[-1])])
        assert command == ["docker", "network", "inspect", NETWORK_ID]
        return json.dumps([network])
    monkeypatch.setattr(relay_tool, "_output", output)
    return commands


def test_only_exact_owned_running_internal_bridge_endpoints_are_resolved(monkeypatch, tmp_path):
    containers, network = inspections()
    commands = fake_inspection(monkeypatch, containers, network)
    assert relay_tool.owned_endpoints(COMPOSE, PROJECT, tmp_path) == {
        "postgres": ("172.24.0.2", 5432), "pgbouncer": ("172.24.0.3", 6432)}
    assert len(commands) == 6


@pytest.mark.parametrize("flaw", ["project", "service", "stopped", "extra-network", "external-network",
    "driver", "network-owner", "public-ip", "loopback", "wrong-member", "wrong-subnet", "ambiguous", "malformed"])
def test_unowned_or_noninternal_destinations_are_rejected_before_socket_access(monkeypatch, tmp_path, flaw):
    containers, network = inspections()
    # Fault the second service to ensure the first is not contacted early.
    item = containers["pgbouncer"]
    if flaw in {"project", "service"}:
        item["Config"]["Labels"]["com.docker.compose." + flaw] = "unowned"
    elif flaw == "stopped":
        item["State"]["Running"] = False
    elif flaw == "extra-network":
        item["NetworkSettings"]["Networks"]["another"] = {}
    elif flaw == "external-network":
        network["Internal"] = False
    elif flaw == "driver":
        network["Driver"] = "host"
    elif flaw == "network-owner":
        network["Labels"]["com.docker.compose.project"] = "unowned"
    elif flaw in {"public-ip", "loopback"}:
        item["NetworkSettings"]["Networks"][PROJECT + "_isolated"]["IPAddress"] = "8.8.8.8" if flaw == "public-ip" else "127.0.0.1"
    elif flaw == "wrong-member":
        network["Containers"][item["Id"]]["IPv4Address"] = "172.24.0.99/16"
    elif flaw == "wrong-subnet":
        network["IPAM"]["Config"][0]["Subnet"] = "192.168.0.0/24"
    elif flaw == "malformed":
        item["Config"]["Labels"] = None
    fake_inspection(monkeypatch, containers, network, bad_ps=flaw == "ambiguous")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("invalid endpoint contacted"))
    relays = relay_tool.DockerLabRelays()
    with pytest.raises(ValueError):
        relays.activate(COMPOSE, PROJECT, tmp_path)


def test_simultaneous_lab_reservations_have_distinct_kernel_assigned_ports():
    with relay_tool.DockerLabRelays() as first, relay_tool.DockerLabRelays() as second:
        ports = [*first.ports.values(), *second.ports.values()]
        assert len(ports) == len(set(ports)) == 4
        assert all(1 <= port <= 65535 for port in ports)
        for group in (first, second):
            for service, relay in group.relays.items():
                assert relay.listener.getsockname() == ("127.0.0.1", group.ports[service])


def test_reservations_are_exclusive_before_activation_and_released_on_exit():
    with relay_tool.DockerLabRelays() as group:
        addresses = [relay.listener.getsockname() for relay in group.relays.values()]
        assert all(relay.thread is None for relay in group.relays.values())
        for address in addresses:
            with socket.socket() as competitor:
                competitor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                with pytest.raises(OSError) as failure:
                    competitor.bind(address)
                assert failure.value.errno == errno.EADDRINUSE
    for address in addresses:
        with socket.socket() as available:
            available.bind(address)


def test_second_listener_collision_releases_first_without_touching_occupant(monkeypatch):
    with socket.socket() as occupied, socket.socket() as probe:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        occupied_port = occupied.getsockname()[1]
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
        probe.close()
        monkeypatch.setattr(relay_tool, "SERVICES", {"postgres": (free_port, 5432), "pgbouncer": (occupied_port, 6432)})
        with pytest.raises(OSError):
            with relay_tool.DockerLabRelays():
                pytest.fail("occupied listener accepted")
        with socket.socket() as available:
            available.bind(("127.0.0.1", free_port))
        with socket.create_connection(("127.0.0.1", occupied_port), timeout=1):
            client, _ = occupied.accept()
            client.close()


def read_all(stream):
    chunks = []
    while chunk := stream.recv(65536):
        chunks.append(chunk)
    return b"".join(chunks)


def test_concurrent_binary_streams_preserve_half_close_response_and_cleanup():
    count = 4
    with socket.socket() as backend:
        backend.bind(("127.0.0.1", 0))
        backend.listen(count)
        target = backend.getsockname()
        def respond():
            client, _ = backend.accept()
            with client:
                client.settimeout(3)
                data = read_all(client)
                client.sendall(data[::-1] + b"response-after-client-eof")
        with ThreadPoolExecutor(max_workers=count * 2) as executor:
            responses = [executor.submit(respond) for _ in range(count)]
            relay = relay_tool.LoopbackRelay(0)
            relay.start(target)
            def request(index):
                payload = bytes(range(256)) * (1024 + index)
                with socket.create_connection(("127.0.0.1", relay.port), timeout=3) as client:
                    client.sendall(payload)
                    client.shutdown(socket.SHUT_WR)
                    assert read_all(client) == payload[::-1] + b"response-after-client-eof"
            try:
                futures = [executor.submit(request, index) for index in range(count)]
                for future in futures + responses:
                    future.result(timeout=5)
            finally:
                relay.close()
            assert not relay.workers and not relay.connections and not relay.thread.is_alive()
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", relay.port), timeout=0.2)


def test_cleanup_closes_idle_active_connections_without_short_stream_timeout():
    with socket.socket() as backend:
        backend.bind(("127.0.0.1", 0))
        backend.listen()
        relay = relay_tool.LoopbackRelay(0)
        relay.start(backend.getsockname())
        with socket.create_connection(("127.0.0.1", relay.port), timeout=2) as client:
            upstream, _ = backend.accept()
            with upstream:
                upstream.settimeout(2)
                client.sendall(b"still-open")
                assert upstream.recv(10) == b"still-open"
                relay.close()
                assert client.recv(1) == b"" and upstream.recv(1) == b""
        assert not relay.workers and not relay.connections and not relay.thread.is_alive()
