#!/usr/bin/env python3
"""Small isolated demonstration using actual Alerta/PG WASM and a mocked source."""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

from retained_temp import FAILED_RETENTION, fresh_basetemp, retain_status

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-lab', action='store_true', required=True)
    parser.parse_args()
    commands = [
        ('Inert CLI configuration; no network access',
         [sys.executable, '-m', 'zabalack.cli', '--config',
          'config/production.example.json', 'config-check']),
        ('Synthetic media contracts; no Zabbix server', ['node', 'zabbix/v1/test_webhook.cjs']),
        ('Actual PostgreSQL WASM ledger/worker; mocked source requests',
         [sys.executable, 'lab/pglite_exec.py', '--isolated-lab', '--', sys.executable,
          '-m', 'pytest', 'tests/postgres/test_worker_path.py', '-q']),
        ('Actual Alerta HTTP and PostgreSQL WASM persistence; no live source',
         [sys.executable, 'lab/pglite_exec.py', '--isolated-lab', '--', sys.executable,
          '-m', 'pytest', 'tests/alerta/test_postgres_http.py', '-q']),
    ]
    unretained = []
    for number, (description, command) in enumerate(commands):
        print(description, flush=True)
        basetemp = None
        if 'pytest' in command:
            # An owned, explicit basetemp, retained privately after the run.
            basetemp = fresh_basetemp(f'quickstart-{number}')
            command = [*command, '--basetemp=' + str(basetemp)]
        result = subprocess.run(command, cwd=ROOT, timeout=180)
        if basetemp is not None:
            status = retain_status(basetemp, ROOT / 'build/evidence-raw/basetemp' / f'quickstart-{number}-{basetemp.parent.name}')
            if status in FAILED_RETENTION:
                unretained.append(f'quickstart-{number}: {status}')
        if result.returncode:
            return result.returncode
    if unretained:
        # The demonstration itself succeeded; its scratch was not fully retained.
        print('Scratch retention incomplete: ' + ', '.join(unretained), flush=True)
        return 3
    print('Demonstration complete. Native concurrency, real Zabbix and G1–G8 remain unverified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
