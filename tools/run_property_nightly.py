#!/usr/bin/env python3
"""Record the expanded generated-test profile (docs/stateful-testing.md).

Runs tests/property and the sequential PostgreSQL WASM ledger sequences with
ACK_HYPOTHESIS_PROFILE=nightly and a fixed seed through lab/pglite_exec.py,
redacts the JUnit into evidence/property-nightly.xml and writes
evidence/property-nightly-run.json bound to the tree's source/harness digests.
Supplemental evidence only: PostgreSQL WASM, not native concurrency, permissions
or any service path. The record is invalidated before execution starts.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from evidence_utils import cases, code_digests, redact_junit
from retained_temp import FAILED_RETENTION, fresh_basetemp, retain_status

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ['tests/property', 'tests/postgres/test_stateful_ledger.py']
SCOPE = 'Additional overlapping generated tests; PostgreSQL WASM, not native concurrency or permissions'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--isolated-lab', action='store_true', required=True)
    parser.add_argument('--seed', type=int, default=20260912)
    args = parser.parse_args(argv)
    evidence = ROOT / 'evidence'
    evidence.mkdir(exist_ok=True)
    raw = ROOT / 'build/evidence-raw'
    raw.mkdir(parents=True, exist_ok=True)
    record_path = evidence / 'property-nightly-run.json'
    public_xml = evidence / 'property-nightly.xml'
    raw_xml = raw / 'property-nightly.xml'
    for stale in (record_path, public_xml, raw_xml):
        stale.unlink(missing_ok=True)
    record: dict = {'status': 'RUNNING', 'profile': 'nightly', 'seed': args.seed, 'code_digests': code_digests(ROOT),
                    'started_at_utc': datetime.now(timezone.utc).isoformat(), 'scope': SCOPE}
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    env = {key: value for key, value in os.environ.items() if not key.startswith('ACK_TEST_')}
    env.update(TZ='UTC', ACK_HYPOTHESIS_PROFILE='nightly', ACK_HYPOTHESIS_SEED=str(args.seed),
               HYPOTHESIS_STORAGE_DIRECTORY=str(ROOT / 'build/private/hypothesis'))
    basetemp = fresh_basetemp('property-nightly')
    with (raw / 'property-nightly.log').open('w') as log:
        code = subprocess.run([sys.executable, 'lab/pglite_exec.py', '--isolated-lab', '--', sys.executable, '-m', 'pytest',
                               *TARGETS, '-q', '-p', 'no:cacheprovider', '--junitxml=' + str(raw_xml),
                               '--basetemp=' + str(basetemp)],
                              cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=3600).returncode
    record['retention'] = retain_status(basetemp, raw / 'basetemp' / ('property-nightly-' + basetemp.parent.name))
    record['exit_code'] = code
    if raw_xml.is_file():
        redact_junit(raw_xml, public_xml, ROOT)
        rows = cases(public_xml)
        record['junit'] = 'evidence/property-nightly.xml'
        record['counts'] = {status: sum(row['status'] == status for row in rows) for status in ('passed', 'skipped', 'failed')}
        passed = bool(rows) and all(row['status'] == 'passed' for row in rows)
    else:
        passed = False
    record['tree_unchanged'] = record['code_digests'] == code_digests(ROOT)
    record['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    retained = record['retention'] not in FAILED_RETENTION
    record['status'] = 'PASS' if code == 0 and passed and record['tree_unchanged'] and retained else 'FAIL'
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({key: record.get(key) for key in ('status', 'counts', 'seed', 'tree_unchanged')}))
    return 0 if record['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
