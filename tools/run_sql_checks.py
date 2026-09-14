#!/usr/bin/env python3
"""Own a fresh, isolated supplemental SQL run; never import old PASS reports."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from evidence_utils import code_digests, public_text

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated-lab', required=True, action='store_true')
    parser.parse_args()
    output = ROOT / 'evidence/runtime_wasm_sql.json'
    output.parent.mkdir(exist_ok=True)
    raw_dir = ROOT / 'build/evidence-raw'
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = raw_dir / 'sql-groups.json'
    raw.unlink(missing_ok=True)
    report = {'status': 'RUNNING', 'results': [], 'code_digests': code_digests(ROOT),
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'deployment_gates': 'MANUAL_GATE_PENDING'}
    output.write_text(json.dumps(report, indent=2) + '\n')
    try:
        env = dict(os.environ, TZ='UTC', ACK_SQL_REPORT_PATH=str(raw))
        with (raw_dir / 'sql-groups.log').open('w') as log:
            code = subprocess.run(['node', 'lab/pglite_checks.mjs', '--isolated-lab'],
                                  cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600).returncode
        packet = json.loads(public_text(raw.read_text(), ROOT))
        if not isinstance(packet, dict) or not isinstance(packet.get('results'), list) or not packet['results']:
            raise ValueError('invalid_sql_report')
        # The executed child's diagnostics may be published after redaction,
        # but it cannot supply this runner's binding or result status.
        report.update({k: v for k, v in packet.items() if k not in report and k != 'status'})
        report['results'] = packet['results']
        report['exit_code'] = code
        report['tree_unchanged'] = report['code_digests'] == code_digests(ROOT)
        report['status'] = 'PASS' if code == 0 and report['tree_unchanged'] and all(
            isinstance(item, dict) and item.get('status') == 'PASS' for item in packet['results']) else 'FAIL'
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        report['status'] = 'FAIL'
        report['reason'] = type(error).__name__
    finally:
        report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'groups': len(report['results'])}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
