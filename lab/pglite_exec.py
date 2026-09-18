#!/usr/bin/env python3
"""Run a sequential command with a disposable loopback PostgreSQL18 WASM socket.

Explicitly supplemental: not native SQL server, authentication, concurrency, TLS,
statement-cancellation, PgBouncer or HA evidence. Child and server share one
development process environment; nothing is connected to production.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]


def private_file(path: Path, mode: str):
    """Create raw records privately before the child can write to them."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), mode)


def wait_ready(server, output, *, timeout: float = 30) -> dict:
    """Read the first complete record from a regular file, never an unread pipe."""
    deadline = time.monotonic() + timeout
    line = b''
    while time.monotonic() < deadline:
        line += output.readline(65537 - len(line))
        if len(line) > 65536:
            raise RuntimeError('WASM socket readiness record exceeds 64KiB')
        if line.endswith(b'\n'):
            ready = json.loads(line)
            if not isinstance(ready, dict) or ready.get('event') != 'isolated_wasm_socket_ready':
                raise RuntimeError('Unexpected readiness record')
            return ready
        if server.poll() is not None:
            # Full stderr remains private; do not echo arbitrary upstream data.
            raise RuntimeError('WASM socket failed before readiness; see private server logs')
        time.sleep(0.05)
    raise RuntimeError('WASM socket did not become ready within 30seconds')


def stop_server(server, record: dict) -> None:
    record['cleanup'] = 'WAITING'
    if server.poll() is None:
        record['cleanup'] = 'TERMINATING'
        server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        record['cleanup'] = 'KILLING'
        server.kill()
        server.wait(timeout=5)
    record['server_exit_code'] = server.returncode
    record['cleanup'] = 'EXIT_CONFIRMED'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-lab', action='store_true', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('Provide command after --')
    raw = REPO / 'build/evidence-raw'
    raw.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix='pglite-socket-', dir=raw))
    # mkdtemp creates mode 0700; child streams and receipt are mode 0600 on POSIX.
    # These full raw records are private and never copied into public evidence.
    record = {'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'STARTING',
              'child_exit_code': None, 'server_exit_code': None, 'cleanup': 'NOT_STARTED'}
    with private_file(attempt / 'server.stdout', 'wb') as stdout, \
            private_file(attempt / 'server.stderr', 'wb') as stderr, \
            private_file(attempt / 'run.json', 'w') as receipt:
        json.dump(record, receipt)
        receipt.flush()
        server = None
        try:
            server = subprocess.Popen(['node', str(REPO / 'lab/pglite_socket.mjs'), '--isolated-lab'],
                                      cwd=REPO, stdout=stdout, stderr=stderr)
            record['server_pid'] = server.pid
            with (attempt / 'server.stdout').open('rb') as output:
                ready = wait_ready(server, output)
            print(json.dumps(ready), flush=True)
            record['status'] = 'READY'
            environment = dict(os.environ)
            environment['ACK_TEST_ADMIN_DSN'] = 'postgresql://postgres@127.0.0.1:55433/postgres?sslmode=disable'
            environment['ACK_TEST_EVIDENCE_LAYER'] = 'postgresql18_wasm_supplemental'
            environment['ACK_TEST_WASM'] = '1'
            environment['ACK_TEST_DESTRUCTIVE_LAB'] = 'YES_ISOLATED_LAB'
            environment['ACK_TEST_ALERTA_SCHEMA'] = str(REPO / 'lab/upstream/alerta-9.1.0-schema.sql')
            record['child_exit_code'] = subprocess.run(command, cwd=REPO, env=environment).returncode
            record['status'] = 'CHILD_EXITED'
            return record['child_exit_code']
        except BaseException as error:
            record['status'] = 'WRAPPER_ERROR'
            record['error_type'] = type(error).__name__
            raise
        finally:
            try:
                if server is not None:
                    stop_server(server, record)
            except BaseException as error:
                record['status'] = 'WRAPPER_ERROR'
                record['cleanup_error_type'] = type(error).__name__
                raise
            finally:
                record['finished_at'] = datetime.now(timezone.utc).isoformat()
                receipt.seek(0)
                receipt.truncate()
                json.dump(record, receipt, indent=2)
                receipt.write('\n')
                for stream in (stdout, stderr, receipt):
                    stream.flush()
                    os.fsync(stream.fileno())


if __name__ == '__main__':
    sys.exit(main())
