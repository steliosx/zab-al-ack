#!/usr/bin/env python3
"""Non-destructive hold plan; applies only with explicit target/change selection."""
from __future__ import annotations
import argparse
import json
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--instance', action='append', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--change-ref', required=True)
    parser.add_argument('--cli', default='zabalack')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--stop-worker-service', action='store_true')
    args = parser.parse_args()
    if not args.owner.strip() or not args.change_ref.strip() or any(not i.strip() for i in args.instance):
        parser.error('owner, change reference, and instance IDs must be nonempty')
    commands = [[args.cli, '--config', args.config, 'pause', '--instance', instance,
                 '--reason', 'non-destructive rollback hold', '--owner', args.owner,
                 '--change-ref', args.change_ref] for instance in dict.fromkeys(args.instance)]
    if args.stop_worker_service:
        commands.append(['systemctl', 'stop', 'zabalack-worker.service'])
    if not args.apply:
        print(json.dumps({'mode': 'plan_only', 'commands': commands,
                          'after_hold': ['reconcile in-flight outcomes', 'remove only reviewed integration hooks/config with rolling drain', 'retain ledger/capture evidence', 'retain source-read-only reconciliation'],
                          'deletes_evidence': False, 'disables_trigger': False, 'unacknowledges_source': False}, indent=2))
        return 0
    for command in commands:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode:
            print(json.dumps({'status': 'stopped_on_failure', 'command': command[0], 'exit_code': result.returncode}))
            return result.returncode
    print(json.dumps({'status': 'held', 'evidence_preserved': True, 'in_flight_review_required': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
