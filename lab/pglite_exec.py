#!/usr/bin/env python3
"""Run a sequential command with a disposable loopback PostgreSQL18 WASM socket.

Explicitly supplemental: not native SQL server, authentication, concurrency, TLS,
statement-cancellation, PgBouncer or HA evidence. Child and server share one
development process environment; nothing is connected to production.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-lab', action='store_true', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('Provide command after --')
    server = subprocess.Popen(['node', str(REPO / 'lab/pglite_socket.mjs'), '--isolated-lab'], cwd=REPO,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        selector = selectors.DefaultSelector()
        selector.register(server.stdout, selectors.EVENT_READ)
        if not selector.select(30):
            raise RuntimeError('WASM socket did not become ready within 30seconds')
        line = server.stdout.readline()
        if not line:
            raise RuntimeError('WASM socket failed before readiness: ' + server.stderr.read(1000))
        ready = json.loads(line)
        if ready.get('event') != 'isolated_wasm_socket_ready':
            raise RuntimeError('Unexpected readiness record')
        print(json.dumps(ready), flush=True)
        environment = dict(os.environ)
        environment['ACK_TEST_ADMIN_DSN'] = 'postgresql://postgres@127.0.0.1:55433/postgres?sslmode=disable'
        environment['ACK_TEST_EVIDENCE_LAYER'] = 'postgresql18_wasm_supplemental'
        environment['ACK_TEST_WASM'] = '1'
        environment['ACK_TEST_DESTRUCTIVE_LAB'] = 'YES_ISOLATED_LAB'
        environment['ACK_TEST_ALERTA_SCHEMA'] = str(REPO / 'lab/upstream/alerta-9.1.0-schema.sql')
        return subprocess.run(command, cwd=REPO, env=environment).returncode
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


if __name__ == '__main__':
    sys.exit(main())
