#!/usr/bin/env python3
"""Fail-closed admission check for service-lab jobs on the shared Linux x64 runner (R32).

The in-job flock on the legacy lab lock is retired (shared-CI Phase B prerequisite). A job that starts services runs
only under an external whole-job exclusion; this step verifies that exclusion before any service starts. It never
grants, extends or releases anything, and it trusts nothing the job could have written:

  phase-b   the broker status socket exists: its read-only status must answer ok, with quarantined exactly false (a
            null, missing or latched value refuses), not paused or pausing, and an active lifecycle whose unit and
            InvocationID are this job's own unit and invocation (from the manager), in phase "running", for this
            project.
  phase-a   no broker socket: this job's own unit must be the persistent runner unit gated by the shared-ci window,
            with its FragmentPath under /etc/systemd/system and an ExecCondition that runs
            `shared-ci-window runner-may-start`.
  anything else refuses: no unit cgroup, a transient or ungated unit, an unreadable observation, a malformed reply.

Exit 0 when admitted, 1 when refused, 2 for usage errors. It prints one JSON object. It reads only /proc/self/cgroup,
`systemctl show` for its own unit and the broker status socket.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

BROKER_SOCKET = Path('/run/shared-ci/broker.sock')
UNIT_DIR = '/etc/systemd/system/'
WINDOW_CONDITION = re.compile(r'/usr/local/sbin/shared-ci-window\s.*\brunner-may-start\b|argv\[\]=/usr/local/sbin/shared-ci-window runner-may-start')
UNIT_NAME = re.compile(r'actions\.runner\.[A-Za-z0-9_.-]+\.service')
MAX_REPLY = 65536


class Refused(Exception):
    """The exclusion is not proven."""


def own_unit(cgroup_file: Path) -> str:
    try:
        text = cgroup_file.read_text()
    except OSError as error:
        raise Refused(f'cgroup unreadable: {error.strerror}') from None
    for line in text.splitlines():
        if line.startswith('0::'):
            units = [part for part in line[3:].split('/') if UNIT_NAME.fullmatch(part)]
            if len(units) == 1:
                return units[0]
            raise Refused('this job is not inside exactly one runner service unit')
    raise Refused('no cgroup v2 entry')


def systemctl_show(unit: str, prop: str, systemctl: str) -> str:
    try:
        result = subprocess.run([systemctl, 'show', unit, '-p', prop, '--value'], capture_output=True, text=True,
                                timeout=20, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    except (OSError, subprocess.SubprocessError) as error:
        raise Refused(f'systemctl show {prop} failed: {type(error).__name__}') from None
    if result.returncode != 0:
        raise Refused(f'systemctl show {prop} exited {result.returncode}')
    return result.stdout.strip()


def broker_status(path: Path, timeout: float) -> dict[str, object]:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout)
            conn.connect(str(path))
            conn.sendall(b'{"op": "status"}\n')
            buf = b''
            while not buf.endswith(b'\n'):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_REPLY:
                    raise Refused('broker reply too long')
    except OSError as error:
        raise Refused(f'broker status unavailable: {type(error).__name__}') from None
    try:
        reply = json.loads(buf)
    except ValueError:
        raise Refused('broker reply is not JSON') from None
    if not isinstance(reply, dict):
        raise Refused('broker reply is not an object')
    return reply


def check_phase_b(unit: str, invocation: str, status: dict[str, object], project: str) -> dict[str, object]:
    if status.get('ok') is not True:
        raise Refused('broker status not ok')
    if status.get('quarantined') is not False or status.get('quarantine_state') != 'clear':
        raise Refused(f"broker quarantine is not explicitly clear ({status.get('quarantine_state')!r})")
    # Deliberate: a requested pause also refuses. Maintenance has been asked for, so a job must not start services
    # inside the remaining lifecycle; the refusal fails the job, which is not validation.
    if status.get('paused') is not False or status.get('pause_requested') is not False:
        raise Refused('broker is paused or a pause is requested')
    active = status.get('active')
    if not isinstance(active, dict):
        raise Refused('no active broker lifecycle')
    expected = {'unit': unit, 'invocation': invocation, 'phase': 'running', 'project': project}
    for key, value in expected.items():
        if active.get(key) != value:
            raise Refused(f'active lifecycle {key} is {active.get(key)!r}, not {value!r}')
    return {'mode': 'phase-b', 'unit': unit, 'invocation': invocation}


def check_phase_a(unit: str, fragment: str, condition: str) -> dict[str, object]:
    if fragment != UNIT_DIR + unit:
        raise Refused(f'unit {unit} is not the persistent runner unit (FragmentPath {fragment!r})')
    if not WINDOW_CONDITION.search(condition):
        raise Refused(f'unit {unit} is not gated by the shared-ci window (ExecCondition {condition!r})')
    return {'mode': 'phase-a', 'unit': unit}


def admit(cgroup_file: Path, broker: Path, systemctl: str, project: str, timeout: float) -> dict[str, object]:
    unit = own_unit(cgroup_file)
    if os.path.lexists(broker):
        invocation = systemctl_show(unit, 'InvocationID', systemctl)
        if not re.fullmatch(r'[0-9a-f]{32}', invocation):
            raise Refused('own InvocationID unavailable')
        return check_phase_b(unit, invocation, broker_status(broker, timeout), project)
    return check_phase_a(unit, systemctl_show(unit, 'FragmentPath', systemctl),
                         systemctl_show(unit, 'ExecCondition', systemctl))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', default='zabalack')
    parser.add_argument('--cgroup-file', type=Path, default=Path('/proc/self/cgroup'), help=argparse.SUPPRESS)
    parser.add_argument('--broker-socket', type=Path, default=BROKER_SOCKET, help=argparse.SUPPRESS)
    parser.add_argument('--systemctl', default='systemctl', help=argparse.SUPPRESS)
    parser.add_argument('--timeout', type=float, default=5.0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        result = admit(args.cgroup_file, args.broker_socket, args.systemctl, args.project, args.timeout)
    except Refused as error:
        print(json.dumps({'admitted': False, 'reason': str(error)}))
        return 1
    print(json.dumps({'admitted': True, **result}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
