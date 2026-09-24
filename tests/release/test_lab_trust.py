"""The lab-trust unit (deploy/lab-trust, docs/lab-trust.md): its privilege boundary, its CA and the lab's use of it.

Policy tests read the shipped unit, sudoers, tmpfiles and scripts; nothing is installed. The CA tests generate a
name-constrained CA exactly as install.sh does and verify real certificates with OpenSSL. The lab tests replace
only the privileged `sudo systemctl` call. Execution on the lab host is recorded separately (docs/lab-trust.md).
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
TRUST = ROOT / 'deploy/lab-trust'
FIXED_CONF = '/run/zabalack-lab-zabbix/zabbix_server.conf'


def unit():
    values: dict[str, list[str]] = {}
    for line in (TRUST / 'zabalack-lab-zabbix.service').read_text().splitlines():
        if line and not line.startswith(('#', '[')):
            key, _, value = line.partition('=')
            values.setdefault(key, []).append(value)
    return values


def test_the_unit_runs_only_setpriv_then_the_fixed_packaged_daemon():
    values = unit()
    (start,) = values['ExecStart']
    argv = shlex.split(start)
    separator = argv.index('--')
    assert argv[0] == '/usr/bin/setpriv' and argv[separator + 1:] == ['/usr/sbin/zabbix_server', '--foreground', '--config', FIXED_CONF]
    drop = set(argv[1:separator])
    assert drop == {'--reuid=zabalack-runner', '--regid=zabalack-runner', '--clear-groups', '--inh-caps=-all', '--ambient-caps=-all',
                    '--bounding-set=-all', '--no-new-privs', '--reset-env'}
    # Nothing else runs in the unit, as root or otherwise; nothing comes from a caller or a runner-owned file.
    for forbidden in ('ExecStartPre', 'ExecStartPost', 'ExecStop', 'User', 'Group', 'EnvironmentFile', 'ReadWritePaths', 'BindPaths',
                      'WorkingDirectory', 'RootDirectory', 'Conditions'):
        assert forbidden not in values
    assert '%' not in (TRUST / 'zabalack-lab-zabbix.service').read_text().replace('%s', '')  # not a template, no specifiers


def test_the_only_mount_is_the_fixed_root_owned_trust_over_the_libcurl_ca_paths():
    values = unit()
    assert values['BindReadOnlyPaths'] == ['/etc/zabalack-lab/trust:/etc/ssl/certs']
    assert values['PrivateMounts'] == ['yes'] and values['NoNewPrivileges'] == ['yes']
    assert values['CapabilityBoundingSet'] == ['CAP_SETUID CAP_SETGID CAP_SETPCAP']  # only what setpriv needs to drop
    # The runner-owned runtime directory appears only as the daemon's configuration, read after the drop.
    text = (TRUST / 'zabalack-lab-zabbix.service').read_text()
    body = '\n'.join(line for line in text.splitlines() if not line.startswith('#'))
    assert body.count('/run/zabalack-lab-zabbix') == 1 and shlex.split(values['ExecStart'][0])[-1] == FIXED_CONF


def test_the_sudoers_rule_allows_exactly_start_and_stop_of_that_unit():
    text = (TRUST / 'zabalack-lab-zabbix.sudoers').read_text()
    rules = [line for line in text.splitlines() if line and not line.startswith('#')]
    assert rules == ['Cmnd_Alias ZABALACK_LAB_ZABBIX = /usr/bin/systemctl start zabalack-lab-zabbix.service, '
                     '/usr/bin/systemctl stop zabalack-lab-zabbix.service',
                     'Defaults!ZABALACK_LAB_ZABBIX env_reset, !setenv',
                     'zabalack-runner ALL=(root) NOPASSWD: ZABALACK_LAB_ZABBIX']
    assert '*' not in text and '?' not in text.replace('#', '')
    if shutil.which('visudo'):
        checked = subprocess.run(['visudo', '-cf', str(TRUST / 'zabalack-lab-zabbix.sudoers')], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stdout + checked.stderr


def test_the_tmpfiles_rule_and_the_scripts():
    assert [line for line in (TRUST / 'zabalack-lab-zabbix.tmpfiles').read_text().splitlines() if line and not line.startswith('#')] == [
        'd /run/zabalack-lab-zabbix 0700 zabalack-runner zabalack-runner -']
    install = (TRUST / 'install.sh').read_text()
    assert 'nameConstraints=critical,permitted;DNS:runner,permitted;IP:127.0.0.1/255.255.255.255' in install
    assert 'basicConstraints=critical,CA:TRUE,pathlen:0' in install and 'visudo -cf' in install
    assert 'refusing,' in install  # an existing path is never overwritten
    for script in ('install.sh', 'uninstall.sh', 'verify.sh', 'check-ca.sh', 'snapshot-check.sh'):
        text = (TRUST / script).read_text()
        body = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
        for global_trust in ('update-ca-certificates', '/usr/local/share/ca-certificates', 'apparmor', '/etc/ssl/certs/', 'usermod', 'gpasswd'):
            assert global_trust not in body, (script, global_trust)
        assert subprocess.run(['sh', '-n', str(TRUST / script)]).returncode == 0
    uninstall = (TRUST / 'uninstall.sh').read_text()
    removed = set(re.findall(r'(/(?:etc|run)/[\w./-]+)', '\n'.join(line for line in uninstall.splitlines() if line.startswith('rm '))))
    assert removed == {'/etc/systemd/system/zabalack-lab-zabbix.service', '/etc/sudoers.d/zabalack-lab-zabbix',
                       '/etc/tmpfiles.d/zabalack-lab-zabbix.conf', '/etc/zabalack-lab', '/run/zabalack-lab-zabbix'}


# ---------------------------------------------------------------- the CA and the lab certificate

@pytest.fixture(scope='module')
def lab_ca(tmp_path_factory):
    if not shutil.which('openssl'):
        pytest.skip('openssl CLI unavailable')
    d = tmp_path_factory.mktemp('ca')
    command = re.search(r'/usr/bin/openssl req -x509 (.*?) 2>/dev/null', (TRUST / 'install.sh').read_text(), re.S)[1]
    arguments = shlex.split(command.replace('\\\n', ' ').replace('"$stage/', '"' + str(d) + '/'))
    subprocess.run(['openssl', 'req', '-x509', *arguments], check=True, capture_output=True)
    return d / 'lab-ca.pem', d / 'lab-ca.key'


@pytest.fixture
def e2e(monkeypatch):
    spec = importlib.util.spec_from_file_location('lab_trust_run_deployment_e2e', ROOT / 'tools/run_deployment_e2e.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def serve(chain, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(chain, key)
    listener = socket.create_server(('127.0.0.1', 0))

    def accept():
        try:
            connection, _ = listener.accept()
            with context.wrap_socket(connection, server_side=True) as tls:
                tls.recv(1)
        except OSError:
            pass
    threading.Thread(target=accept, daemon=True).start()
    return listener


def handshake(cafile, port, name):
    context = ssl.create_default_context(cafile=str(cafile))
    with socket.create_connection(('127.0.0.1', port), timeout=5) as raw, context.wrap_socket(raw, server_hostname=name):
        return True


def test_the_lab_certificate_is_signed_by_the_lab_ca_and_verifies_as_runner(tmp_path, lab_ca, e2e):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    e2e.certificates(private, ca=lab_ca)
    assert (private / 'ca/lab.crt').read_bytes() == lab_ca[0].read_bytes()
    assert (private / 'server.crt').read_text().count('BEGIN CERTIFICATE') == 2  # leaf, then the CA
    listener = serve(private / 'server.crt', private / 'server.key')
    try:
        assert handshake(lab_ca[0], listener.getsockname()[1], 'runner')  # what the lab zabbix_server trusts
    finally:
        listener.close()
    listener = serve(private / 'server.crt', private / 'server.key')
    try:
        assert handshake(private / 'server.crt', listener.getsockname()[1], 'runner')  # the lab's own clients
    finally:
        listener.close()
    listener = serve(private / 'server.crt', private / 'server.key')
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            handshake(lab_ca[0], listener.getsockname()[1], 'elsewhere')
    finally:
        listener.close()


def test_the_lab_ca_cannot_vouch_for_any_other_name(tmp_path, lab_ca):
    """nameConstraints: a certificate the CA signs for another name does not verify, whoever holds the key."""
    key, csr, cert = tmp_path / 'o.key', tmp_path / 'o.csr', tmp_path / 'o.pem'
    subprocess.run(['openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=elsewhere.example', '-keyout', str(key), '-out', str(csr),
                    '-addext', 'subjectAltName=DNS:elsewhere.example'], check=True, capture_output=True)
    subprocess.run(['openssl', 'x509', '-req', '-in', str(csr), '-CA', str(lab_ca[0]), '-CAkey', str(lab_ca[1]), '-CAcreateserial',
                    '-days', '1', '-copy_extensions', 'copy', '-out', str(cert)], check=True, capture_output=True)
    verified = subprocess.run(['openssl', 'verify', '-CAfile', str(lab_ca[0]), str(cert)], capture_output=True, text=True)
    assert verified.returncode != 0 and 'permitted subtree violation' in (verified.stdout + verified.stderr)


def test_without_a_ca_the_compose_form_keeps_its_self_signed_certificate(tmp_path, e2e):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    e2e.certificates(private)
    assert (private / 'ca/lab.crt').read_bytes() == (private / 'server.crt').read_bytes()


# ---------------------------------------------------------------- the lab's use of the unit

@pytest.fixture
def zlab(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('lab_trust_zabbix_native_lab', ROOT / 'lab/zabbix_native_lab.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = tmp_path / '.zabbix-lab'
    state.mkdir()
    for key, value in [('REPO', tmp_path), ('STATE', state), ('MARKER', state / 'ISOLATED_ZABBIX_LAB'), ('PGDATA', state / 'pgdata'),
                       ('TRUST_UNIT_FILE', tmp_path / 'unit.service'), ('TRUST_DIR', tmp_path / 'trust'),
                       ('TRUST_CONF', tmp_path / 'run/zabalack-lab-zabbix/zabbix_server.conf'), ('TRUST_EXECUTABLE', tmp_path / 'zabbix_server')]:
        monkeypatch.setattr(module, key, value)
    monkeypatch.setattr(module, 'PID_FILES', {'postgres': state / 'pgdata/postmaster.pid', 'zabbix_server': state / 'zabbix_server.pid',
                                               'php': state / 'php.pid'})
    (tmp_path / 'unit.service').write_text('')
    (tmp_path / 'trust').mkdir()
    (tmp_path / 'zabbix_server').write_text('')
    (tmp_path / 'run/zabalack-lab-zabbix').mkdir(parents=True, mode=0o700)
    (tmp_path / 'run/zabalack-lab-zabbix').chmod(0o700)
    (state / 'zabbix-db-password').write_text('synthetic\n')
    monkeypatch.setattr(module, 'password', lambda role, create=False: 'synthetic')
    calls = []
    # A model of systemd's view of the fixed unit; only the privileged start/stop change it.
    unit = {'LoadState': 'loaded', 'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': 0, 'InvocationID': '',
            'ControlGroup': ''}
    invocations = iter(f'{n:032x}' for n in range(1, 100))

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ['sudo', '-n'] and command[3] == 'start':
            module.PID_FILES['zabbix_server'].write_text(f'{os.getpid()}\n')
            unit.update(ActiveState='active', SubState='running', MainPID=os.getpid(), InvocationID=next(invocations),
                        ControlGroup='/system.slice/zabalack-lab-zabbix.service')
        if command[:2] == ['sudo', '-n'] and command[3] == 'stop':
            unit.update(ActiveState='inactive', SubState='dead', MainPID=0, ControlGroup='')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.setattr(module, 'unit_status', lambda: dict(unit), raising=False)  # absent before R6
    module.unit = unit
    monkeypatch.setattr(module, 'port_listening', lambda port: True)
    monkeypatch.setattr(module, 'component_alive', lambda name: False)
    monkeypatch.setattr(module, 'expected_uid', lambda: os.getuid())
    module.calls = calls
    return module


def test_with_the_unit_installed_the_lab_starts_zabbix_only_through_it(zlab, monkeypatch):
    identity = zlab.process_identity(os.getpid())
    monkeypatch.setattr(zlab, 'verified_pid', lambda name: (identity, 'running'))
    zlab.start_server({'zabbix_server': zlab.TRUST_EXECUTABLE})
    assert zlab.calls == [['sudo', '-n', '/usr/bin/systemctl', 'start', 'zabalack-lab-zabbix.service']]
    conf = zlab.TRUST_CONF.read_text()
    assert f'PidFile={zlab.STATE}/zabbix_server.pid' in conf and (zlab.TRUST_CONF.stat().st_mode & 0o777) == 0o600
    assert not (zlab.STATE / 'zabbix_server.conf').exists()
    assert zlab.expected_argv('zabbix_server') == ['--config', str(zlab.TRUST_CONF)]


@pytest.mark.parametrize('problem', ['symlink', 'mode', 'owner', 'missing', 'other-executable'])
def test_an_unsafe_runtime_directory_or_another_executable_is_refused_before_anything_starts(zlab, monkeypatch, tmp_path, problem):
    runtime = zlab.TRUST_CONF.parent
    paths = {'zabbix_server': zlab.TRUST_EXECUTABLE}
    if problem == 'symlink':
        runtime.rmdir()
        (tmp_path / 'elsewhere').mkdir(mode=0o700)
        runtime.symlink_to(tmp_path / 'elsewhere')
    elif problem == 'mode':
        runtime.chmod(0o755)
    elif problem == 'owner':
        monkeypatch.setattr(zlab.os, 'geteuid', lambda: os.getuid() + 1)
    elif problem == 'missing':
        runtime.rmdir()
    else:
        paths = {'zabbix_server': tmp_path / 'another-zabbix_server'}
        paths['zabbix_server'].write_text('')
    with pytest.raises(SystemExit):
        zlab.start_server(paths)
    assert zlab.calls == [] and not (tmp_path / 'elsewhere/zabbix_server.conf').exists()


def test_stopping_the_lab_also_stops_the_unit(zlab, monkeypatch):
    monkeypatch.setattr(zlab, 'stop_component', lambda name, timeout: {'status': 'STOPPED'})
    monkeypatch.setattr(zlab, 'verified_pid', lambda name: (None, 'stopped'))
    zlab.unit.update(ActiveState='active', MainPID=os.getpid(), InvocationID='a' * 32, ControlGroup='/system.slice/x')
    zlab.record_unit_owner(zlab.unit_status())
    outcomes = zlab.stop_all({'pg_bin': Path('/nonexistent')})
    assert ['sudo', '-n', '/usr/bin/systemctl', 'stop', 'zabalack-lab-zabbix.service'] in zlab.calls
    assert outcomes['zabbix_server']['status'] == 'STOPPED' and outcomes['zabbix_server']['trust_unit_stop_exit_code'] == 0


def test_without_the_unit_the_lab_is_unchanged(zlab, monkeypatch):
    zlab.TRUST_UNIT_FILE.unlink()
    assert not zlab.trust_unit_installed() and zlab.server_conf() == zlab.STATE / 'zabbix_server.conf'
    assert zlab.expected_argv('zabbix_server') == ['-c', str(zlab.STATE / 'zabbix_server.conf')]


def test_the_deployment_runner_refuses_before_any_service_work_without_lab_trust(tmp_path, monkeypatch):
    import run_deployment_e2e_local as local_e2e
    monkeypatch.setattr(local_e2e, 'LINK', tmp_path / 'link')
    (tmp_path / 'link').symlink_to(local_e2e.ROOT)
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    (private / 'pg-password').write_text('x')
    monkeypatch.setattr(local_e2e, 'PRIVATE', private)
    monkeypatch.setattr(local_e2e.socket, 'getaddrinfo', lambda name, port: [(None, None, None, None, ('127.0.0.1', 0))])
    monkeypatch.setattr(local_e2e, 'LAB_TRUST_UNIT', tmp_path / 'absent.service')
    with pytest.raises(RuntimeError, match='lab_trust_not_provisioned'):
        local_e2e.check_host_layout()


# Review of 8211b1b (R5-UNIT-OWNERSHIP): stop_all stopped the global unit whenever it was installed, whoever's
# invocation it was running. The lab now starts, stops or reconfigures it only when inactive or its own.
SUDO_START = ['sudo', '-n', '/usr/bin/systemctl', 'start', 'zabalack-lab-zabbix.service']
SUDO_STOP = ['sudo', '-n', '/usr/bin/systemctl', 'stop', 'zabalack-lab-zabbix.service']


def mark(zlab):
    zlab.MARKER.write_text('isolated\n')


def foreign_invocation(zlab, state, main_pid, owner):
    zlab.unit.update(ActiveState=state, SubState='start' if state == 'activating' else 'running', MainPID=main_pid,
                     InvocationID='f' * 32, ControlGroup='/system.slice/zabalack-lab-zabbix.service')
    if owner == 'other-run':  # this checkout once owned an earlier invocation, not this one
        zlab.unit_owner_file().write_text(json.dumps({'invocation_id': 'e' * 32, 'control_group': zlab.unit['ControlGroup'],
                                                      'state': str(zlab.STATE), 'main_pid': main_pid, 'main_starttime': 1}))


@pytest.mark.parametrize('state', ['active', 'activating', 'deactivating'])
@pytest.mark.parametrize('owner', ['no-record', 'other-run'])
def test_a_foreign_unit_invocation_is_never_started_stopped_or_reconfigured(zlab, monkeypatch, capsys, state, owner):
    mark(zlab)
    monkeypatch.setattr(zlab, 'port_listening', lambda port: False)  # e.g. before listen: the port guard sees nothing
    monkeypatch.setattr(zlab, 'verified_pid', lambda name: (None, 'stale_pid' if name == 'zabbix_server' else 'stopped'))
    monkeypatch.setattr(zlab, 'inputs', lambda: {'pg_bin': Path('/nonexistent')})
    foreign_invocation(zlab, state, 0 if state == 'deactivating' else os.getpid(), owner)
    observed = zlab.inspect()
    assert observed['state'] == 'ambiguous' and observed['trust_unit']['ownership'] == 'foreign'
    with pytest.raises(SystemExit, match='Refusing to stop'):
        zlab.main(['stop', '--isolated-lab'])
    outcomes = zlab.stop_all({'pg_bin': Path('/nonexistent')})  # even called directly
    assert outcomes['zabbix_server']['status'] == 'FAILED' and outcomes['zabbix_server']['trust_unit']['ownership'] == 'foreign'
    with pytest.raises(SystemExit, match='Refusing to start'):
        zlab.start_server({'zabbix_server': zlab.TRUST_EXECUTABLE})
    assert zlab.calls == [] and not zlab.TRUST_CONF.exists() and zlab.unit['InvocationID'] == 'f' * 32


def test_an_unobservable_unit_is_refused_like_a_foreign_one(zlab, monkeypatch):
    mark(zlab)
    monkeypatch.setattr(zlab, 'unit_status', lambda: None)
    monkeypatch.setattr(zlab, 'verified_pid', lambda name: (None, 'stopped'))
    assert zlab.inspect()['state'] == 'ambiguous'
    assert zlab.stop_all({'pg_bin': Path('/nonexistent')})['zabbix_server']['status'] == 'FAILED'
    with pytest.raises(SystemExit):
        zlab.start_server({'zabbix_server': zlab.TRUST_EXECUTABLE})
    assert zlab.calls == [] and not zlab.TRUST_CONF.exists()


def test_the_lab_binds_its_own_invocation_and_stops_and_restarts_only_it(zlab, monkeypatch):
    identity = zlab.process_identity(os.getpid())
    monkeypatch.setattr(zlab, 'verified_pid', lambda name: (identity, 'running'))
    zlab.start_server({'zabbix_server': zlab.TRUST_EXECUTABLE})
    first = json.loads(zlab.unit_owner_file().read_text())
    assert first['invocation_id'] == zlab.unit['InvocationID'] and first['main_pid'] == os.getpid()
    assert first['main_starttime'] == identity['starttime'] and first['state'] == str(zlab.STATE)
    assert zlab.unit_ownership()[1] == 'owned'
    monkeypatch.setattr(zlab, 'stop_component', lambda name, timeout: {'status': 'STOPPED'})
    outcomes = zlab.stop_all({'pg_bin': Path('/nonexistent')})
    assert [call for call in zlab.calls if call[0] == 'sudo'] == [SUDO_START, SUDO_STOP]
    assert outcomes['zabbix_server']['status'] == 'STOPPED'
    assert not zlab.unit_owner_file().exists() and zlab.unit['ActiveState'] == 'inactive'
    zlab.start_server({'zabbix_server': zlab.TRUST_EXECUTABLE})  # restart: a new invocation, bound afresh
    assert json.loads(zlab.unit_owner_file().read_text())['invocation_id'] == zlab.unit['InvocationID'] != first['invocation_id']


def test_an_own_invocation_that_never_listened_is_still_stopped(zlab, monkeypatch):
    monkeypatch.setattr(zlab, 'port_listening', lambda port: False)
    monkeypatch.setattr(zlab, 'wait_until', lambda predicate, timeout, interval=0.5: predicate())
    with pytest.raises(SystemExit, match='not listening'):
        zlab.start_server({'zabbix_server': zlab.TRUST_EXECUTABLE})
    assert zlab.unit_ownership()[1] == 'owned'  # bound as soon as the start returned
    monkeypatch.setattr(zlab, 'verified_pid', lambda name: (None, 'stopped'))
    assert zlab.stop_all({'pg_bin': Path('/nonexistent')})['zabbix_server']['status'] == 'STOPPED'
    assert zlab.calls == [SUDO_START, SUDO_STOP]


def test_descendants_of_an_own_invocation_are_stopped_after_its_main_process_exits(zlab, monkeypatch):
    mark(zlab)
    zlab.unit.update(ActiveState='active', MainPID=os.getpid(), InvocationID='a' * 32, ControlGroup='/system.slice/x')
    zlab.record_unit_owner(zlab.unit_status())
    zlab.unit.update(MainPID=0, SubState='exited')  # main process gone; its control group still has processes
    monkeypatch.setattr(zlab, 'port_listening', lambda port: False)
    monkeypatch.setattr(zlab, 'verified_pid', lambda name: (None, 'stale_pid' if name == 'zabbix_server' else 'stopped'))
    observed = zlab.inspect()
    assert observed['state'] == 'running' and observed['trust_unit']['ownership'] == 'owned'
    outcomes = zlab.stop_all({'pg_bin': Path('/nonexistent')})
    assert zlab.calls == [SUDO_STOP] and outcomes['zabbix_server']['status'] == 'STOPPED'
    assert zlab.unit['ActiveState'] == 'inactive' and not zlab.unit_owner_file().exists()


def test_an_own_invocation_whose_stop_does_not_take_effect_fails(zlab, monkeypatch):
    zlab.unit.update(ActiveState='active', MainPID=os.getpid(), InvocationID='a' * 32, ControlGroup='/system.slice/x')
    zlab.record_unit_owner(zlab.unit_status())
    monkeypatch.setattr(zlab, 'stop_component', lambda name, timeout: {'status': 'NOT_RUNNING'})
    monkeypatch.setattr(zlab, 'verified_pid', lambda name: (None, 'stopped'))
    monkeypatch.setattr(zlab, 'trust_unit', lambda verb: subprocess.CompletedProcess([], 0, '', ''))  # still active after
    assert zlab.stop_all({'pg_bin': Path('/nonexistent')})['zabbix_server']['status'] == 'FAILED'
    assert zlab.unit_owner_file().exists()


def test_a_reused_main_pid_under_the_recorded_invocation_is_not_owned(zlab):
    zlab.unit.update(ActiveState='active', MainPID=os.getpid(), InvocationID='a' * 32, ControlGroup='/system.slice/x')
    owner = zlab.record_unit_owner(zlab.unit_status())
    zlab.unit_owner_file().write_text(json.dumps({**owner, 'main_starttime': owner['main_starttime'] - 1}))
    assert zlab.unit_ownership()[1] == 'foreign'


# Review of 8211b1b (installation audit): verify.sh accepted any CA whose constraints contained "Permitted:", any
# *.0 entry and any hash-link target; it could not run as the runner at all (0440 sudoers). The exact content check
# is check-ca.sh; verify.sh runs as root, from an immutable snapshot (snapshot-check.sh); uninstall.sh removes
# nothing until the unit is inactive with an empty control group.
def ca_tree(tmp_path, *extensions, key_bits='2048'):
    d = tmp_path / 'installed'
    (d / 'trust').mkdir(parents=True)
    (d / 'ca').mkdir()
    subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:' + key_bits, '-nodes', '-days', '1',
                    '-subj', '/CN=Zabalack isolated lab CA', '-keyout', str(d / 'ca/lab-ca.key'), '-out', str(d / 'ca/lab-ca.pem'),
                    *[argument for extension in extensions for argument in ('-addext', extension)]], check=True, capture_output=True)
    shutil.copy(d / 'ca/lab-ca.pem', d / 'trust/lab-ca.pem')
    shutil.copy(d / 'ca/lab-ca.pem', d / 'trust/ca-certificates.crt')
    subject_hash = subprocess.run(['/usr/bin/openssl', 'x509', '-noout', '-subject_hash', '-in', str(d / 'ca/lab-ca.pem')],
                                  capture_output=True, text=True, check=True).stdout.strip()
    (d / 'trust' / f'{subject_hash}.0').symlink_to('lab-ca.pem')
    return d, subject_hash


REVIEWED = ('basicConstraints=critical,CA:TRUE,pathlen:0', 'keyUsage=critical,keyCertSign,cRLSign',
            'nameConstraints=critical,permitted;DNS:runner,permitted;IP:127.0.0.1/255.255.255.255')


def check_ca(d):
    return subprocess.run(['sh', str(TRUST / 'check-ca.sh'), str(d / 'trust'), str(d / 'ca')], capture_output=True, text=True)


@pytest.fixture
def openssl_cli():
    if not Path('/usr/bin/openssl').exists():
        pytest.skip('/usr/bin/openssl unavailable')


def test_the_exact_check_accepts_the_installed_ca_as_install_sh_makes_it(tmp_path, openssl_cli, lab_ca):
    d, _ = ca_tree(tmp_path, *REVIEWED)
    result = check_ca(d)
    assert result.returncode == 0, result.stdout
    for name in ('lab-ca.pem', 'lab-ca.key'):  # and install.sh's own CA
        shutil.copy(lab_ca[0] if name.endswith('pem') else lab_ca[1], d / 'ca' / name)
    for name in ('lab-ca.pem', 'ca-certificates.crt'):
        shutil.copy(lab_ca[0], d / 'trust' / name)
    for link in (d / 'trust').glob('*.0'):
        link.unlink()
    subject_hash = subprocess.run(['/usr/bin/openssl', 'x509', '-noout', '-subject_hash', '-in', str(lab_ca[0])],
                                  capture_output=True, text=True, check=True).stdout.strip()
    (d / 'trust' / f'{subject_hash}.0').symlink_to('lab-ca.pem')
    assert check_ca(d).returncode == 0


@pytest.mark.parametrize('extensions', [
    ('basicConstraints=critical,CA:TRUE,pathlen:5', 'keyUsage=critical,keyCertSign,cRLSign',
     'nameConstraints=critical,permitted;DNS:example.invalid'),                                        # Astra's probe CA
    ('basicConstraints=critical,CA:TRUE,pathlen:5', *REVIEWED[1:]),
    ('basicConstraints=critical,CA:TRUE', *REVIEWED[1:]),
    (REVIEWED[0], REVIEWED[1], 'nameConstraints=critical,permitted;DNS:runner'),
    (REVIEWED[0], REVIEWED[1], 'nameConstraints=critical,permitted;DNS:runner,permitted;IP:127.0.0.0/255.0.0.0'),
    (REVIEWED[0], REVIEWED[1], 'nameConstraints=critical,permitted;DNS:runner,permitted;DNS:other,permitted;IP:127.0.0.1/255.255.255.255'),
    (REVIEWED[0], REVIEWED[1], 'nameConstraints=permitted;DNS:runner,permitted;IP:127.0.0.1/255.255.255.255'),
    (REVIEWED[0], 'keyUsage=critical,keyCertSign,cRLSign,digitalSignature', REVIEWED[2]),
    (*REVIEWED, 'extendedKeyUsage=serverAuth'),
    REVIEWED[:2],
], ids=['astra-example-invalid-pathlen5', 'pathlen5', 'no-pathlen', 'no-ip', 'wider-ip', 'extra-name', 'not-critical',
        'extra-key-usage', 'extra-extension', 'no-name-constraints'])
def test_any_other_ca_fails_the_exact_check(tmp_path, openssl_cli, extensions):
    d, _ = ca_tree(tmp_path, *extensions)
    result = check_ca(d)
    assert result.returncode == 1, result.stdout


@pytest.mark.parametrize('change', ['extra-trust-file', 'extra-hash-link', 'link-elsewhere', 'trust-bundle-differs',
                                    'ca-copy-differs', 'two-certificates', 'other-key', 'extra-ca-file'])
def test_trust_entries_links_and_copies_must_be_exact(tmp_path, openssl_cli, change):
    d, subject_hash = ca_tree(tmp_path, *REVIEWED)
    other, _ = ca_tree(tmp_path / 'other', *REVIEWED)
    if change == 'extra-trust-file':
        (d / 'trust/extra.pem').write_bytes((other / 'ca/lab-ca.pem').read_bytes())
    elif change == 'extra-hash-link':
        shutil.copy(other / 'ca/lab-ca.pem', d / 'trust/other.pem')
        (d / 'trust/other.pem').rename(d / 'trust/deadbeef.0')
    elif change == 'link-elsewhere':
        (d / 'trust' / f'{subject_hash}.0').unlink()
        (d / 'trust' / f'{subject_hash}.0').symlink_to('ca-certificates.crt')
    elif change == 'trust-bundle-differs':
        shutil.copy(other / 'ca/lab-ca.pem', d / 'trust/ca-certificates.crt')
    elif change == 'ca-copy-differs':
        shutil.copy(other / 'ca/lab-ca.pem', d / 'ca/lab-ca.pem')
    elif change == 'two-certificates':
        for name in ('trust/lab-ca.pem', 'trust/ca-certificates.crt', 'ca/lab-ca.pem'):
            with (d / name).open('ab') as bundle:
                bundle.write((other / 'ca/lab-ca.pem').read_bytes())
    elif change == 'other-key':
        shutil.copy(other / 'ca/lab-ca.key', d / 'ca/lab-ca.key')
    else:
        (d / 'ca/serial').write_text('01\n')
    assert check_ca(d).returncode == 1


def test_the_audit_runs_only_as_root_and_only_from_an_immutable_snapshot():
    if os.geteuid() == 0:
        pytest.skip('checks the refusal for a non-root caller')
    result = subprocess.run(['sh', str(TRUST / 'verify.sh')], capture_output=True, text=True)
    assert result.returncode == 2 and 'run as root' in result.stderr
    installer = subprocess.run(['sh', str(TRUST / 'install.sh'), '0' * 64], capture_output=True, text=True)
    assert installer.returncode == 2 and 'run as root' in installer.stderr
    snapshot = subprocess.run(['sh', str(TRUST / 'snapshot-check.sh'), str(TRUST)], capture_output=True, text=True)
    assert snapshot.returncode == 1 and 'not owned by root' in snapshot.stderr  # a user checkout is not a snapshot


def test_the_snapshot_manifest_lists_exactly_the_current_sources():
    listed = [line.split() for line in (TRUST / 'SHA256SUMS').read_text().splitlines()]
    assert sorted(name for _, name in listed) == sorted(path.name for path in TRUST.iterdir() if path.name != 'SHA256SUMS')
    for digest, name in listed:
        assert hashlib.sha256((TRUST / name).read_bytes()).hexdigest() == digest, name


def sandboxed_uninstall(tmp_path, after_stop, procs=''):
    """uninstall.sh with its absolute host paths moved under tmp_path and systemctl replaced by a recorder."""
    (tmp_path / 'etc/zabalack-lab/trust').mkdir(parents=True)
    for name in ('systemd/system/zabalack-lab-zabbix.service', 'sudoers.d/zabalack-lab-zabbix', 'tmpfiles.d/zabalack-lab-zabbix.conf'):
        (tmp_path / 'etc' / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / 'etc' / name).write_text('installed\n')
    (tmp_path / 'run/zabalack-lab-zabbix').mkdir(parents=True)
    (tmp_path / 'cgroup/system.slice/zabalack-lab-zabbix.service').mkdir(parents=True)
    (tmp_path / 'cgroup/system.slice/zabalack-lab-zabbix.service/cgroup.procs').write_text(procs)
    systemctl = tmp_path / 'systemctl'
    systemctl.write_text(f"""#!/bin/sh
echo "$*" >> {tmp_path}/systemctl.log
case "$1 $3" in
  "show ActiveState") cat {tmp_path}/active 2>/dev/null || echo active ;;
  "show ControlGroup") echo /system.slice/zabalack-lab-zabbix.service ;;
  "show LoadState") [ -e {tmp_path}/etc/systemd/system/zabalack-lab-zabbix.service ] && echo loaded || echo not-found ;;
esac
[ "$1" = stop ] && echo {after_stop} > {tmp_path}/active
exit 0
""")
    systemctl.chmod(0o755)
    script = (TRUST / 'uninstall.sh').read_text()
    for host, sandbox in (('/usr/bin/systemctl', str(systemctl)), ('/sys/fs/cgroup', f'{tmp_path}/cgroup'), (' /etc/', f' {tmp_path}/etc/'),
                          (' /run/', f' {tmp_path}/run/'), ('[ "$(id -u)" = 0 ]', 'true')):
        assert host in script
        script = script.replace(host, sandbox)
    (tmp_path / 'uninstall.sh').write_text(script)
    return subprocess.run(['sh', str(tmp_path / 'uninstall.sh')], capture_output=True, text=True)


@pytest.mark.parametrize('after_stop, procs', [('active', ''), ('deactivating', ''), ('inactive', '4242\n')])
def test_uninstall_removes_nothing_until_the_unit_is_inactive_with_an_empty_control_group(tmp_path, after_stop, procs):
    result = sandboxed_uninstall(tmp_path, after_stop, procs)
    assert result.returncode == 1 and 'nothing removed' in result.stderr
    assert (tmp_path / 'etc/zabalack-lab').is_dir() and (tmp_path / 'etc/sudoers.d/zabalack-lab-zabbix').exists()


@pytest.mark.parametrize('after_stop', ['inactive', 'failed'])
def test_uninstall_rolls_back_once_the_unit_is_inactive(tmp_path, after_stop):
    result = sandboxed_uninstall(tmp_path, after_stop)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'etc/zabalack-lab').exists() and not (tmp_path / 'run/zabalack-lab-zabbix').exists()
    assert not (tmp_path / 'etc/sudoers.d/zabalack-lab-zabbix').exists()
