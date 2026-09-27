"""The WASM lab's socket adapter (lab/pglite_socket.mjs) releases the slot of a client connection that ends in a reset.

pglite-socket 0.2.11 counted such a connection as open forever. After its 16 connection slots filled (15 while the
session fixture holds its admin connection), every new client was refused with a plain-text "Too many connections",
whose first byte psycopg reports as "expected authentication request from server, but received T". Every later test
in the postgres-wasm phase then errored in setup (B1 build, 5ab8fef). Runs only against the supplemental WASM backend.
"""
import os
import socket
import struct

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

pytestmark = pytest.mark.postgres

CLIENTS = 24  # more than the adapter's 16 connection slots
STARTUP = b'user\x00postgres\x00database\x00postgres\x00\x00'


def admitted_then_reset(host: str, port: int) -> bytes:
    """Send a startup message and read the first response byte, as a client does.

    An admitted connection answers with an authentication message ('R') and is then ended with a TCP reset
    (SO_LINGER 0). A refused one answers with the adapter's plain-text "Too many connections" ('T') and is closed
    normally, exactly as psycopg closes it; b'' records a connection that failed outright (0.2.11 can also drop,
    or crash on, an over-limit client).
    """
    try:
        raw = socket.create_connection((host, port), timeout=5)
        raw.sendall(struct.pack('!ii', 8 + len(STARTUP), 196608) + STARTUP)
        first = raw.recv(1)
    except OSError:
        return b''
    if first == b'R':
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
    raw.close()
    return first


def test_reset_client_connections_never_exhaust_the_adapters_connection_slots(pg_lab):
    if os.environ.get('ACK_TEST_WASM') != '1':
        pytest.skip('supplemental WASM socket adapter check; native PostgreSQL has no such adapter')
    target = conninfo_to_dict(pg_lab['admin_dsn'])
    answers = [admitted_then_reset(target['host'], int(target['port'])) for _ in range(CLIENTS)]
    admitted = next((index for index, answer in enumerate(answers) if answer != b'R'), CLIENTS)
    assert admitted == CLIENTS, f'the adapter stopped admitting clients after {admitted} reset connections: {answers[admitted:admitted + 3]}'
    with psycopg.connect(pg_lab['admin_dsn'], autocommit=True, connect_timeout=5) as conn:
        assert conn.execute('SELECT 1').fetchone()[0] == 1
