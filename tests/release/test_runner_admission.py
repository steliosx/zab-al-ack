"""R32: the service-lab admission check admits only under a proven external whole-job exclusion."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import runner_admission as ra  # noqa: E402

UNIT = 'actions.runner.zabalack-attended.service'
INVOCATION = '0123456789abcdef0123456789abcdef'
GATED = ('{ path=/usr/local/sbin/shared-ci-window ; argv[]=/usr/local/sbin/shared-ci-window runner-may-start ; '
         'ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }')


def cgroup(tmp_path, line=f'0::/system.slice/{UNIT}'):
    path = tmp_path / 'cgroup'
    path.write_text(line + '\n')
    return path


def fake_systemctl(tmp_path, values, rc=0):
    """A stand-in for systemctl that prints values[prop] for `show UNIT -p PROP --value`."""
    script = tmp_path / 'systemctl'
    script.write_text('#!/usr/bin/env python3\nimport json, sys\n'
                      f'values = json.loads({json.dumps(json.dumps(values))})\n'
                      'prop = sys.argv[sys.argv.index("-p") + 1]\n'
                      f'print(values.get(prop, ""))\nsys.exit({rc})\n')
    script.chmod(0o755)
    return str(script)


def broker(tmp_path, reply, name='broker.sock'):
    """A real unix-socket status server answering one request with reply (a dict or raw bytes)."""
    path = tmp_path / name
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    seen = {}

    def serve():
        conn, _ = server.accept()
        with conn:
            buf = b''
            while not buf.endswith(b'\n'):
                buf += conn.recv(4096)
            seen['request'] = json.loads(buf)
            conn.sendall(reply if isinstance(reply, bytes) else (json.dumps(reply) + '\n').encode())
        server.close()

    threading.Thread(target=serve, daemon=True).start()
    return path, seen


def status(**override):
    value = {'ok': True, 'quarantined': False, 'quarantine_state': 'clear', 'paused': False, 'pause_requested': False,
             'active': {'unit': UNIT, 'invocation': INVOCATION, 'project': 'zabalack', 'phase': 'running'}}
    value.update(override)
    return value


def run(tmp_path, cg, broker_path, systemctl, capsys):
    rc = ra.main(['--cgroup-file', str(cg), '--broker-socket', str(broker_path), '--systemctl', systemctl,
                  '--timeout', '2'])
    return rc, json.loads(capsys.readouterr().out)


def test_phase_a_admits_the_persistent_window_gated_unit(tmp_path, capsys):
    sc = fake_systemctl(tmp_path, {'FragmentPath': f'/etc/systemd/system/{UNIT}', 'DropInPaths': '', 'ExecCondition': GATED})
    rc, out = run(tmp_path, cgroup(tmp_path), tmp_path / 'absent.sock', sc, capsys)
    assert rc == 0 and out == {'admitted': True, 'mode': 'phase-a', 'unit': UNIT}


@pytest.mark.parametrize('values, needle', [
    ({'FragmentPath': f'/run/systemd/transient/{UNIT}', 'ExecCondition': ''}, 'not the persistent runner unit'),
    ({'FragmentPath': f'/etc/systemd/system/{UNIT}', 'ExecCondition': ''}, 'not gated by the shared-ci window'),
    ({'FragmentPath': f'/etc/systemd/system/{UNIT}', 'ExecCondition': '{ argv[]=/bin/true }'}, 'not gated'),
])
def test_phase_a_refuses_the_historical_or_an_ungated_unit(tmp_path, capsys, values, needle):
    rc, out = run(tmp_path, cgroup(tmp_path), tmp_path / 'absent.sock', fake_systemctl(tmp_path, values), capsys)
    assert rc == 1 and out['admitted'] is False and needle in out['reason']


@pytest.mark.parametrize('line, needle', [
    ('0::/user.slice/user-1000.slice/session-1.scope', 'not inside exactly one runner service unit'),
    ('1:name=systemd:/x', 'no cgroup v2 entry'),
])
def test_refuses_outside_a_runner_unit(tmp_path, capsys, line, needle):
    sc = fake_systemctl(tmp_path, {'FragmentPath': f'/etc/systemd/system/{UNIT}', 'ExecCondition': GATED})
    rc, out = run(tmp_path, cgroup(tmp_path, line), tmp_path / 'absent.sock', sc, capsys)
    assert rc == 1 and needle in out['reason']


def test_refuses_an_unreadable_cgroup_and_a_failing_systemctl(tmp_path, capsys):
    sc = fake_systemctl(tmp_path, {}, rc=1)
    rc, out = run(tmp_path, tmp_path / 'missing', tmp_path / 'absent.sock', sc, capsys)
    assert rc == 1 and 'cgroup unreadable' in out['reason']
    rc, out = run(tmp_path, cgroup(tmp_path), tmp_path / 'absent.sock', sc, capsys)
    assert rc == 1 and 'systemctl show FragmentPath exited 1' in out['reason']


def test_phase_b_admits_only_its_own_running_lifecycle(tmp_path, capsys):
    path, seen = broker(tmp_path, status())
    rc, out = run(tmp_path, cgroup(tmp_path), path, fake_systemctl(tmp_path, {'InvocationID': INVOCATION}), capsys)
    assert rc == 0 and out == {'admitted': True, 'mode': 'phase-b', 'unit': UNIT, 'invocation': INVOCATION}
    assert seen['request'] == {'op': 'status'}


@pytest.mark.parametrize('override, needle', [
    ({'quarantined': None, 'quarantine_state': 'unknown'}, 'not explicitly clear'),
    ({'quarantined': True, 'quarantine_state': 'latched'}, 'not explicitly clear'),
    ({'quarantined': False, 'quarantine_state': 'set'}, 'not explicitly clear'),
    ({'ok': False}, 'not ok'),
    ({'paused': 'no'}, 'paused'),
    ({'paused': True}, 'paused'),
    ({'pause_requested': True}, 'paused'),
    ({'active': None}, 'no active broker lifecycle'),
    ({'active': {'unit': 'actions.runner.alrust.service', 'invocation': INVOCATION, 'project': 'zabalack', 'phase': 'running'}}, 'unit'),
    ({'active': {'unit': UNIT, 'invocation': 'f' * 32, 'project': 'zabalack', 'phase': 'running'}}, 'invocation'),
    ({'active': {'unit': UNIT, 'invocation': INVOCATION, 'project': 'zabalack', 'phase': 'starting'}}, 'phase'),
    ({'active': {'unit': UNIT, 'invocation': INVOCATION, 'project': 'alrust', 'phase': 'running'}}, 'project'),
])
def test_phase_b_refuses_every_unproven_state(tmp_path, capsys, override, needle):
    st = status(**override)
    if 'quarantined' in override and override['quarantined'] is None:
        st.pop('quarantined')   # missing counts the same as null
    path, _ = broker(tmp_path, st)
    rc, out = run(tmp_path, cgroup(tmp_path), path, fake_systemctl(tmp_path, {'InvocationID': INVOCATION}), capsys)
    assert rc == 1 and needle in out['reason']


def test_phase_b_refuses_a_malformed_reply_a_dead_socket_and_a_missing_invocation(tmp_path, capsys):
    sc = fake_systemctl(tmp_path, {'InvocationID': INVOCATION})
    path, _ = broker(tmp_path, b'not json\n')
    rc, out = run(tmp_path, cgroup(tmp_path), path, sc, capsys)
    assert rc == 1 and 'not JSON' in out['reason']
    dead = tmp_path / 'dead.sock'
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(dead))
    stale.close()   # the path exists, nothing listens
    rc, out = run(tmp_path, cgroup(tmp_path), dead, sc, capsys)
    assert rc == 1 and 'broker status unavailable' in out['reason']
    path, _ = broker(tmp_path, status(), name='second.sock')
    rc, out = run(tmp_path, cgroup(tmp_path), path, fake_systemctl(tmp_path, {'InvocationID': ''}), capsys)
    assert rc == 1 and 'own InvocationID unavailable' in out['reason']


ECHO_WRAPPED = ('{ path=/usr/bin/echo ; argv[]=/usr/bin/echo /usr/local/sbin/shared-ci-window runner-may-start ; '
                'ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }')
IGNORED = GATED.replace('ignore_errors=no', 'ignore_errors=yes')


@pytest.mark.parametrize('values, needle', [
    ({'ExecCondition': ECHO_WRAPPED}, 'not gated'),          # C6-ADMISSION-02: the helper text only as an argument
    ({'ExecCondition': IGNORED}, 'not gated'),               # a non-mandatory (ignore_errors=yes) condition
    ({'DropInPaths': '/etc/systemd/system/actions.runner.zabalack-attended.service.d/override.conf'}, 'drop-in overrides'),
])
def test_phase_a_requires_the_exact_mandatory_condition_and_no_override(tmp_path, capsys, values, needle):
    base = {'FragmentPath': f'/etc/systemd/system/{UNIT}', 'DropInPaths': '', 'ExecCondition': GATED}
    rc, out = run(tmp_path, cgroup(tmp_path), tmp_path / 'absent.sock', fake_systemctl(tmp_path, {**base, **values}), capsys)
    assert rc == 1 and needle in out['reason']


def test_an_unobservable_or_wrong_type_broker_path_refuses_instead_of_selecting_phase_a(tmp_path, capsys):
    """C6-ADMISSION-01: only ENOENT means no broker; EACCES, ENOTDIR or a non-socket refuse."""
    sc = fake_systemctl(tmp_path, {'FragmentPath': f'/etc/systemd/system/{UNIT}', 'DropInPaths': '', 'ExecCondition': GATED})
    locked = tmp_path / 'locked'
    locked.mkdir()
    locked.chmod(0)
    try:
        rc, out = run(tmp_path, cgroup(tmp_path), locked / 'broker.sock', sc, capsys)
    finally:
        locked.chmod(0o700)
    assert rc == 1 and 'unobservable: EACCES' in out['reason']
    plain = tmp_path / 'plainfile'
    plain.write_text('x')
    rc, out = run(tmp_path, cgroup(tmp_path), plain / 'broker.sock', sc, capsys)
    assert rc == 1 and 'unobservable: ENOTDIR' in out['reason']
    rc, out = run(tmp_path, cgroup(tmp_path), plain, sc, capsys)
    assert rc == 1 and 'not a socket' in out['reason']
