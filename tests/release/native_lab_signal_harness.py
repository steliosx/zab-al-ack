"""Run `lab/deployment_native_lab.py cycle` as its CLI does and deliver a real signal at one publication boundary.

python tests/release/native_lab_signal_harness.py <empty directory> <scenario>

The cycle command runs through run_lab into the real tools/run_deployment_e2e_local.main and the real final
validation (tests/release/test_native_lab_binding.command_path); only host effects are replaced (service
processes, the runner's layout and installed-environment probes, the inner pytest). The process ends exactly as
`python lab/deployment_native_lab.py cycle ...` would: sys.exit(main(argv)), or the interpreter's own handling of
an exception that escapes main. The caller reads the exit status, the output and <directory>/evidence/.

Scenarios (a signal sent by this process to itself with os.kill):
  none                       no signal
  term-after-cleanup         SIGTERM as cycle_lab returns, its cleanup recorded (before publication)
  term-entering-publication  SIGTERM as the command enters its final publication: publish() where it exists,
                             else (e8ef82b) the final record's critical section
  term-in-validation         SIGTERM while the final record is bound, inside its validation (before publication)
  int-in-validation          SIGINT there (the first signal of a GitHub cancellation)
  kill-in-validation         SIGKILL there (a death before publication)
  term-at-replace            SIGTERM as the final record's write begins, after publish()'s last check
  term-after-publication     SIGTERM right after the final record was published
  kill-after-publication     SIGKILL right after the final record was published (a death after publication)
"""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools'), str(ROOT / 'tests/release')]

import pytest  # noqa: E402 (MonkeyPatch outside a test)

from tests.release.test_deployment_native_lab import CYCLE  # noqa: E402
from tests.release.test_native_lab_binding import command_path  # noqa: E402

SCENARIOS = ('none', 'term-after-cleanup', 'term-entering-publication', 'term-in-validation', 'int-in-validation', 'kill-in-validation',
             'term-at-replace', 'term-after-publication',
             'kill-after-publication')


def make_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The `lab` fixture of tests/release/test_deployment_native_lab.py, outside pytest."""
    spec = importlib.util.spec_from_file_location('signal_harness_native_lab', ROOT / 'lab/deployment_native_lab.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = tmp_path / '.deployment-lab'
    for key, value in [('REPO', tmp_path), ('STATE', state), ('MARKER', state / 'ISOLATED_DEPLOYMENT_LAB'), ('PGDATA', state / 'pgdata'),
                       ('PRIVATE', tmp_path / 'private'), ('LINK', tmp_path / 'link'), ('RECORD', tmp_path / 'evidence/deployment-e2e-run.json')]:
        monkeypatch.setattr(module, key, value)
    monkeypatch.setattr(module, 'port_listening', lambda port: False)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 1001)
    monkeypatch.setattr(module, 'unprivileged_helper_available', lambda: True)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    for name in ('pg_ctl', 'initdb', 'psql', 'postgres'):
        (binaries / name).write_text('')
    monkeypatch.setenv('ACK_LAB_PG_BIN', str(binaries))
    zabbix = SimpleNamespace(state='absent', calls=[])
    monkeypatch.setattr(module, 'zabbix_module', lambda: SimpleNamespace(
        WEB_PORT=8080, PORTS={'php': 58080}, API_URL='', inspect=lambda: {'state': zabbix.state, 'detail': 'synthetic ' + zabbix.state},
        package_identity=lambda binary: {'kind': 'unpackaged', 'sha256': 'a' * 64}))

    def zabbix_command(command, *args, check=True):
        zabbix.calls.append(command)
        if command == 'status':
            return subprocess.CompletedProcess(command, 0, json.dumps({'state': {'state': 'running'}, 'services': {
                'reference': '7.0.31', 'observed_api_version': '7.0.31', 'reference_match': True,
                'zabbix_server': {'version_line': 'zabbix_server (Zabbix) 7.0.31', 'identity': {'kind': 'dpkg', 'package': 'zabbix-server-pgsql', 'version': '1:7.0.31-1+ubuntu24.04', 'architecture': 'amd64'}},
                'frontend': {'ZABBIX_VERSION': '7.0.31', 'identity': {'kind': 'unpackaged', 'sha256': 'b' * 64}, 'served_by': 'PHP built-in server'},
                'postgres': {'version': 'postgres (PostgreSQL) 18.3', 'identity': {'kind': 'unpackaged', 'sha256': 'c' * 64}},
                'php': {'version_line': 'PHP 8.3.6 (cli)', 'identity': {'kind': 'dpkg', 'package': 'php8.3-cli', 'version': '8.3.6', 'architecture': 'amd64'}}}}), '')
        if command == 'start':
            zabbix.state = 'running'
        if command == 'stop':
            zabbix.state = 'stopped'
        if command == 'reset' and '--wipe' in args:
            zabbix.state = 'absent'
        return subprocess.CompletedProcess(command, 0, json.dumps({'cleanup': {'php': {'status': 'STOPPED'}}}), '')
    monkeypatch.setattr(module, 'zabbix', zabbix_command)
    return SimpleNamespace(module=module, tmp=tmp_path, binaries=binaries, zabbix=zabbix)


def install(lab: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, scenario: str) -> None:
    module = lab.module
    signum = {'kill': signal.SIGKILL, 'int': signal.SIGINT}.get(scenario.split('-')[0], signal.SIGTERM)

    def send():
        os.kill(os.getpid(), signum)
    if scenario.endswith('in-validation'):
        real = module.validate_final

        def validate_final(record):
            send()
            return real(record)
        monkeypatch.setattr(module, 'validate_final', validate_final)
    elif scenario.endswith('after-publication') or scenario == 'term-at-replace':
        real_write = module.write_record
        writes = []

        def write_record(record):
            writes.append(record.get('status'))
            final = len(writes) == 2  # the first write is the fresh NOT_RUN record; the second is the final one
            if final and scenario == 'term-at-replace':
                send()
            real_write(record)
            if final and scenario != 'term-at-replace':
                send()
        monkeypatch.setattr(module, 'write_record', write_record)
    elif scenario == 'term-after-cleanup':
        real_cycle = module.cycle_lab

        def cycle_lab(*args, **kwargs):
            result = real_cycle(*args, **kwargs)
            send()
            return result
        monkeypatch.setattr(module, 'cycle_lab', cycle_lab)
    elif scenario == 'term-entering-publication' and hasattr(module, 'publish'):
        real_publish, calls = module.publish, []

        def publish(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                send()
            return real_publish(*args, **kwargs)
        monkeypatch.setattr(module, 'publish', publish)
    elif scenario == 'term-entering-publication':
        base = module.Cancellation
        entered = []

        class Boundary(base):
            @contextmanager
            def critical(self):
                entered.append(1)
                if len(entered) == 2:  # the first critical section is the cycle's cleanup; the second publishes
                    send()
                with super().critical():
                    yield
        monkeypatch.setattr(module, 'Cancellation', Boundary)


if __name__ == '__main__':
    directory, scenario = Path(sys.argv[1]), sys.argv[2]
    assert scenario in SCENARIOS, scenario
    patch = pytest.MonkeyPatch()
    lab = make_lab(directory, patch)
    command_path(lab, patch)
    install(lab, patch, scenario)
    sys.exit(lab.module.main(CYCLE))
