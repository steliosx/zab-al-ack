#!/usr/bin/env python3
"""Validate the lab-trust unit (deploy/lab-trust, docs/lab-trust.md) on the isolated lab host. Root; a test driver.

This driver is not the lab account's privileged interface (that is only `sudo systemctl start|stop
zabalack-lab-zabbix.service`); an operator runs it as root to observe the boundary.

  --mode pre-install  No persistent change. The shipped unit is copied to /run/systemd/system with one
                      difference, its trust source (a root-owned directory under /run holding a lab CA generated
                      with install.sh's command); /run/zabalack-lab-zabbix is created as the tmpfiles rule would.
                      The lab starts PostgreSQL and the frontend; its own zabbix_server is replaced by the unit's.
  --mode installed    After install.sh (owner-authorized): first the read-only installation audit (verify.sh, as
                      root, from the operator's snapshot given as --checkout); any nonzero exit refuses before
                      anything starts. Then the lab account starts the Zabbix lab itself, which now starts
                      zabbix_server through the installed unit with its sudo rule, and the lab's unit ownership
                      is exercised: it refuses to stop an invocation it did not start and stops its own.

Observed in both: host trust and hosts before and after (unchanged); the daemon's and every descendant's UID,
GID, groups, capabilities and no_new_privs; its mount namespace against init's; the trust it sees; deliveries of
the shipped webhook through the packaged engine to receivers named `runner`: a lab-CA certificate (delivered),
and untrusted, other-anchor, wrong-name and expired certificates (refused before any HTTP data); the host's own
trust refusing the lab certificate; start, stop, restart and a stop by signal from the lab account. Everything
it created is removed at the end.

The result is PASS only when every required case for the mode is present and passed: prerequisites and the
installation audit, identity, deliveries, lifecycle, cleanup and the host comparison are cases like any other,
and an exception is recorded as the error that ended the run, never hidden by the cases that did pass.

python3 lab/lab_trust_validation.py --mode pre-install|installed --checkout DIR --work DIR --out FILE
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parent)]
import webhook_transport_acceptance as acceptance  # noqa: E402

ACCOUNT = 'zabalack-runner'
UNIT = 'zabalack-lab-zabbix.service'
RUNTIME_UNIT = Path('/run/systemd/system') / UNIT
INSTALLED_UNIT = Path('/etc/systemd/system') / UNIT
CONF = Path('/run/zabalack-lab-zabbix/zabbix_server.conf')
STAGE = Path('/run/zabalack-lab-trust-validation')
sh = acceptance.sh
COMMON_CASES = ['daemon-identity-and-namespace', 'delivery:lab-ca', 'delivery:untrusted', 'delivery:other-anchor', 'delivery:wrong-name',
                'delivery:expired', 'host-trust-does-not-trust-the-lab-ca', 'lifecycle-stop-restart-signal', 'cleanup', 'host-unchanged']
REQUIRED_CASES = {'pre-install': ['prerequisites', *COMMON_CASES],
                  'installed': ['installation-audit', 'prerequisites', *COMMON_CASES[:-2], 'installed-lab-unit-ownership',
                                *COMMON_CASES[-2:]]}
UNIT_BUSY = ('active', 'activating', 'deactivating', 'reloading', 'refreshing')


class Refused(Exception):
    """A required precondition failed; nothing after it runs."""


def summarize(mode: str, cases: list[dict], error: str | None) -> dict:
    """PASS only when every required case is present exactly once and passed, and nothing raised."""
    names = [case['case'] for case in cases]
    missing = [name for name in REQUIRED_CASES[mode] if name not in names]
    failed = [case['case'] for case in cases if case.get('pass') is not True]
    duplicated = sorted({name for name in names if names.count(name) > 1})
    passed = not missing and not failed and not duplicated and error is None
    return {'status': 'PASS' if passed else 'FAIL', 'required': len(REQUIRED_CASES[mode]), 'cases': len(cases),
            'passed': sum(1 for case in cases if case.get('pass') is True), 'failed': failed, 'missing': missing,
            'duplicated': duplicated, 'error': error}


def unit_show() -> dict:
    fields = sh('systemctl', 'show', '-p', 'ActiveState', '-p', 'InvocationID', '-p', 'MainPID', UNIT, check=False).stdout
    return dict(line.partition('=')[::2] for line in fields.splitlines())


class Invocations:
    """The unit invocations this attempt created (by root, or by the lab through its sudo rule).

    The driver stops the unit as root only while it runs one of these, so a start that refused a pre-existing or
    foreign invocation never ends by stopping it. The unit must be idle before the attempt starts anything.
    """

    def __init__(self) -> None:
        self.created: list[str] = []
        self.ignored: list[str] = []  # owner records present before a start: never this attempt's

    def note(self) -> dict:
        shown = unit_show()
        if shown.get('ActiveState') in UNIT_BUSY and shown.get('InvocationID') and shown['InvocationID'] not in self.created:
            self.created.append(shown['InvocationID'])
        return shown

    def root_start(self) -> dict:
        sh('systemctl', 'start', UNIT)
        return self.note()

    @staticmethod
    def lab_owner(checkout: Path) -> str | None:
        """The invocation named by the lab's owner record (lab/zabbix_native_lab.py, trust-unit.owner.json), if any."""
        try:
            owner = json.loads((checkout / '.zabbix-lab/trust-unit.owner.json').read_text())
        except (OSError, ValueError):
            return None
        invocation = owner.get('invocation_id') if isinstance(owner, dict) else None
        return invocation if isinstance(invocation, str) and invocation else None

    def lab_start(self, start, checkout: Path):
        """Run one of this attempt's own lab starts and adopt only the invocation that start bound.

        The owner record is read immediately before and after the start: a record that was already there (a stale
        checkout, or one copied in) is never adopted; only a new invocation the lab recorded during this call is.
        It is adopted even when the start then fails part-way, so this attempt can still stop what it started.
        """
        before = self.lab_owner(checkout)
        try:
            return start()
        finally:
            after = self.lab_owner(checkout)
            if after is not None and after != before and after not in self.created:
                self.created.append(after)
            elif after is not None and after == before:
                self.ignored.append(after)

    def stop_if_owned(self) -> dict:
        shown = unit_show()
        if shown.get('ActiveState') not in UNIT_BUSY:
            return {'action': 'none', 'unit': shown}
        if shown.get('InvocationID') not in self.created:
            return {'action': 'left_running_not_this_attempt', 'unit': shown}
        stopped = sh('systemctl', 'stop', UNIT, check=False)
        return {'action': 'stopped', 'exit': stopped.returncode, 'unit_after': unit_show()}


def snapshot_audit(snapshot: Path) -> dict:
    """verify.sh, run as root from the operator's snapshot (never from the runner-owned working copy)."""
    audit = sh('sh', str(snapshot / 'deploy/lab-trust/verify.sh'), check=False)
    return {'returncode': audit.returncode, 'stdout': audit.stdout, 'stderr': audit.stderr}


def host_state() -> dict:
    listing = '\n'.join(sorted(os.listdir('/etc/ssl/certs')))
    return {'ca_bundle_sha256': acceptance.digest(Path('/etc/ssl/certs/ca-certificates.crt')),
            'certs_listing_sha256': hashlib.sha256(listing.encode()).hexdigest(),
            'hosts_sha256': acceptance.digest(Path('/etc/hosts')),
            'mounts_over_ssl_or_hosts': [line for line in Path('/proc/self/mountinfo').read_text().splitlines()
                                         if ' /etc/ssl' in line or ' /etc/hosts' in line]}


def proc_identity(pid: int) -> dict:
    status = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
    keep = ('Name', 'Uid', 'Gid', 'Groups', 'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs')
    return {'pid': pid, **{key: status.get(key, '').strip() for key in keep},
            'exe': os.readlink(f'/proc/{pid}/exe'), 'mnt_ns': os.readlink(f'/proc/{pid}/ns/mnt')}


def descendants(pid: int) -> list[int]:
    found, frontier = [], [pid]
    while frontier:
        parent = frontier.pop()
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit():
                try:
                    if int((entry / 'stat').read_text().rpartition(')')[2].split()[1]) == parent:
                        found.append(int(entry.name))
                        frontier.append(int(entry.name))
                except (OSError, ValueError, IndexError):
                    pass
    return found


def main_pid() -> int:
    return int(sh('systemctl', 'show', '-p', 'MainPID', '--value', UNIT).stdout.strip() or 0)


LAB_PORTS = (55434, 20051, 58080)  # lab/zabbix_native_lab.py: PostgreSQL, zabbix_server, frontend


def listening(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(('127.0.0.1', port)) == 0


def immutable(snapshot: Path) -> str | None:
    """Why the operator's snapshot is not safe for root to execute from, or None: the lab-trust sources and every
    ancestor must be root-owned and not writable by group or others (sticky ancestors such as /tmp excepted)."""
    trust = snapshot / 'deploy/lab-trust'
    for path in [*sorted(trust.iterdir()), trust, *trust.parents]:
        info = path.lstat()
        sticky_ancestor = path in snapshot.parents and info.st_mode & 0o1000
        if info.st_uid != 0 or (info.st_mode & 0o022 and not sticky_ancestor) or (path.is_symlink()):
            return f'{path} is not root-owned and closed to other writers'
    return None


def wait_port(port: int, up: bool, timeout: float = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if (probe.connect_ex(('127.0.0.1', port)) == 0) == up:
                return True
        time.sleep(0.5)
    return False


def lab_ca(snapshot: Path, stage: Path) -> tuple[Path, Path]:
    """A lab CA made with install.sh's own openssl command (read from the operator's snapshot), in root-owned staging."""
    install = (snapshot / 'deploy/lab-trust/install.sh').read_text()
    match = re.search(r'/usr/bin/openssl req -x509 (.*?) 2>/dev/null', install, re.S)
    assert match, 'install.sh no longer carries the CA command'
    command = match[1]
    import shlex
    arguments = shlex.split(command.replace('\\\n', ' ').replace('"$stage/', '"' + str(stage) + '/'))
    sh('/usr/bin/openssl', 'req', '-x509', *arguments)
    return stage / 'lab-ca.pem', stage / 'lab-ca.key'


def leaf(work: Path, ca: tuple[Path, Path], name: str, *, san: str, other_ca: bool = False, expired: bool = False, self_signed: bool = False):
    d = work / 'leaves'
    d.mkdir(exist_ok=True)
    sh('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=runner', '-keyout', f'{name}.key', '-out', f'{name}.csr',
       '-addext', 'subjectAltName=' + san, cwd=d)
    if self_signed:
        sh('openssl', 'x509', '-req', '-in', f'{name}.csr', '-signkey', f'{name}.key', '-days', '1', '-copy_extensions', 'copy',
           '-out', f'{name}.pem', cwd=d)
        return d, name
    if other_ca and not (d / 'other-ca.pem').exists():
        sh('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=other-ca', '-keyout', 'other-ca.key',
           '-out', 'other-ca.pem', '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign', cwd=d)
    signer = (d / 'other-ca.pem', d / 'other-ca.key') if other_ca else ca
    (d / 'ca.conf').write_text('[ca]\ndefault_ca=v\n[v]\ndatabase=index.txt\nnew_certs_dir=.\nserial=serial\ndefault_md=sha256\n'
                               'policy=any\ncopy_extensions=copy\nunique_subject=no\n[any]\ncommonName=supplied\n')
    (d / 'index.txt').write_text('')
    (d / 'serial').write_text(secrets_hex() + '\n')
    dates = ['-startdate', '20000101000000Z', '-enddate', '20000102000000Z'] if expired else ['-days', '1']
    sh('openssl', 'ca', '-batch', '-config', 'ca.conf', '-cert', str(signer[0]), '-keyfile', str(signer[1]), '-in', f'{name}.csr',
       '-out', f'{name}.pem', '-notext', *dates, cwd=d)
    return d, name


def secrets_hex() -> str:
    import secrets
    return secrets.token_hex(8).upper()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=['pre-install', 'installed'], required=True)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--evidence', type=Path,
                        help='PRIVATE bundle directory (outside any checkout; default: next to --out, OUT.private-evidence): '
                             'records and logs, collected before cleanup')
    args = parser.parse_args(argv)
    if args.evidence is None:
        args.evidence = args.out.with_name(args.out.name + '.private-evidence')
    if os.geteuid() != 0:
        raise SystemExit('run as root on the isolated lab host')
    work = args.work.resolve()
    snapshot = args.checkout.resolve()  # root-owned operator snapshot; the lab account gets a copy
    results: dict = {'mode': args.mode, 'host_before': host_state(), 'observations': {}, 'cases': []}
    obs = results['observations']
    checkout = work / 'checkout'
    lab = acceptance.Lab(checkout, ACCOUNT, acceptance.UNIT_LAB_ENV)  # never the harness's direct-launch override
    created: list[Path] = []
    lab_started = False
    invocations = Invocations()
    run_error: str | None = None  # not 'error': an `except ... as error` below would delete it
    attempt = acceptance.evidence_tool().attempt_label(f'lab-trust-{args.mode}')
    collect = acceptance.collector(args.evidence, attempt, [('checkout', 'checkout', checkout)], new=True)
    try:
        if args.mode == 'installed':
            # The audit comes first and gates everything: nothing is copied, started or signalled after a failure.
            unsafe = immutable(snapshot)
            if unsafe:
                obs['verify_installation'] = {'returncode': None, 'not_run': unsafe}
                obs['verify_installation_returncode'] = None
                results['cases'].append({'case': 'installation-audit', 'snapshot': unsafe, 'pass': False})
                raise Refused('the snapshot is not immutable: ' + unsafe + '; nothing was run from it')
            audit = snapshot_audit(snapshot)
            obs['verify_installation'] = audit
            obs['verify_installation_returncode'] = audit['returncode']
            results['cases'].append({'case': 'installation-audit', 'returncode': audit['returncode'], 'pass': audit['returncode'] == 0})
            if audit['returncode'] != 0:
                raise Refused(f"installation audit failed (verify.sh exit {audit['returncode']}); nothing was started")
        # Prerequisites: nothing of an earlier attempt present, and the unit idle; nothing is started otherwise.
        present = [str(path) for path in (RUNTIME_UNIT, INSTALLED_UNIT, STAGE, CONF.parent) if path.exists()] \
            if args.mode == 'pre-install' else []
        unit_before = unit_show()
        obs['unit_before'] = unit_before
        busy = unit_before.get('ActiveState') in UNIT_BUSY or not unit_before.get('ActiveState')
        results['cases'].append({'case': 'prerequisites', 'present': present, 'unit_before': unit_before,
                                 'pass': not present and not busy})
        if present:
            raise Refused('refusing: ' + ', '.join(present) + ' exists')
        if busy:
            raise Refused(f"refusing: {UNIT} is {unit_before.get('ActiveState') or 'unobservable'} before this attempt; "
                          'it is not this attempt\'s to start, stop or reuse')
        shutil.copytree(snapshot, checkout, symlinks=True)
        sh('chown', '-R', f'{ACCOUNT}:{ACCOUNT}', str(checkout))
        if args.mode == 'pre-install':
            STAGE.mkdir(mode=0o755)
            created.append(STAGE)
            ca_dir = STAGE / 'ca'
            ca_dir.mkdir(mode=0o700)
            ca = lab_ca(snapshot, ca_dir)
            trust = STAGE / 'trust'
            trust.mkdir(mode=0o755)
            shutil.copy(ca[0], trust / 'ca-certificates.crt')
            shutil.copy(ca[0], trust / 'lab-ca.pem')
            sh('openssl', 'rehash', str(trust))
            shipped = (checkout / 'deploy/lab-trust/zabalack-lab-zabbix.service').read_text()
            runtime = shipped.replace('BindReadOnlyPaths=/etc/zabalack-lab/trust:/etc/ssl/certs', f'BindReadOnlyPaths={trust}:/etc/ssl/certs')
            assert runtime.count(str(trust)) == 1 and runtime != shipped
            RUNTIME_UNIT.write_text(runtime)
            created.append(RUNTIME_UNIT)
            obs['unit_difference_from_shipped'] = [line for line in runtime.splitlines() if line not in shipped.splitlines()]
            sh('systemctl', 'daemon-reload')
            CONF.parent.mkdir(mode=0o700)
            shutil.chown(CONF.parent, ACCOUNT, ACCOUNT)
            created.append(CONF.parent)
            lab_started = True
            start_text = lab.start()
            # Replace the lab's own zabbix_server with the unit's, reading the same configuration.
            state = checkout / '.zabbix-lab'
            sh('runuser', '-u', ACCOUNT, '--', 'cp', str(state / 'zabbix_server.conf'), str(CONF))
            sh('runuser', '-u', ACCOUNT, '--', 'kill', '-TERM', (state / 'zabbix_server.pid').read_text().strip())
            assert wait_port(20051, up=False)
            invocations.root_start()
        else:
            ca = (Path('/etc/zabalack-lab/ca/lab-ca.pem'), Path('/etc/zabalack-lab/ca/lab-ca.key'))
            lab_started = True
            start_text = invocations.lab_start(lab.start, checkout)  # through the unit (sudo rule); adopts only what it bound
        obs['lab_start'] = json.loads(start_text.strip().splitlines()[-1])
        if args.mode == 'installed' and obs['lab_start'].get('zabbix_launch') != 'lab-trust-unit':
            raise Refused(f"the lab did not start zabbix_server through the installed unit: {obs['lab_start'].get('zabbix_launch')}")
        assert wait_port(20051, up=True)
        pid = main_pid()
        obs['daemon'] = proc_identity(pid)
        obs['init_mnt_ns'] = os.readlink('/proc/1/ns/mnt')
        obs['descendants'] = [proc_identity(child) for child in descendants(pid)]
        obs['daemon_ssl_mount'] = [line for line in Path(f'/proc/{pid}/mountinfo').read_text().splitlines() if ' /etc/ssl/certs ' in line]
        obs['daemon_sees_ca_bundle_sha256'] = acceptance.digest(Path(f'/proc/{pid}/root/etc/ssl/certs/ca-certificates.crt'))
        obs['lab_ca_sha256'] = acceptance.digest(ca[0])
        runner_uid = int(sh('id', '-u', ACCOUNT).stdout)
        runner_gid = int(sh('id', '-g', ACCOUNT).stdout)
        everyone = [obs['daemon'], *obs['descendants']]
        identity_ok = all(p['Uid'].split() == [str(runner_uid)] * 4 and p['Gid'].split() == [str(runner_gid)] * 4 and p['Groups'] == ''
                          and all(int(p[c], 16) == 0 for c in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')) and p['NoNewPrivs'] == '1'
                          and p['exe'] == '/usr/sbin/zabbix_server' and p['mnt_ns'] != obs['init_mnt_ns'] for p in everyone)
        results['cases'].append({'case': 'daemon-identity-and-namespace', 'pass': identity_ok and len(everyone) > 1
                                 and obs['daemon_sees_ca_bundle_sha256'] == obs['lab_ca_sha256']})

        # Deliveries of the shipped webhook through the packaged engine, to receivers named runner.
        good = work / 'good'
        good.mkdir()
        sys.path.insert(0, str(checkout / 'tools'))
        import run_deployment_e2e as e2e
        e2e.certificates(good, ca=ca)
        server_pem = good / 'server.crt'
        chains = {'lab-ca': (server_pem, good / 'server.key')}
        for label, options in (('untrusted', {'san': 'DNS:runner', 'self_signed': True}),
                               ('other-anchor', {'san': 'DNS:runner', 'other_ca': True}),
                               ('wrong-name', {'san': 'IP:127.0.0.1'}),
                               ('expired', {'san': 'DNS:runner', 'expired': True})):
            d, name = leaf(work, ca, label, **options)
            chains[label] = (d / f'{name}.pem', d / f'{name}.key')
        receivers = {}
        for label, (cert, key) in chains.items():
            receiver = acceptance.Receiver(label)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            receiver.context = context
            receivers[label] = receiver
        acceptance.API['url'] = obs['lab_start']['api_url']
        session = acceptance.rpc('user.login', {'username': 'Admin', 'password': 'zabbix'})
        artifact = json.loads((checkout / 'zabbix/v1/media_type.reference.json').read_text())
        acceptance.rpc('configuration.import', {'format': 'json', 'source': json.dumps(artifact),
                                                'rules': {'mediaTypes': {'createMissing': True, 'updateExisting': False}}}, session)
        media = acceptance.rpc('mediatype.get', {'output': ['mediatypeid'], 'filter': {'name': artifact['zabbix_export']['media_types'][0]['name']}},
                               session)[0]['mediatypeid']
        acceptance.rpc('mediatype.update', {'mediatypeid': media, 'status': 0, 'maxattempts': 1}, session)
        canary = 'LT-CANARY-' + secrets_hex()
        fixture = json.loads((checkout / 'zabbix/v1/fixtures/problem.params.json').read_text())
        for receiver in receivers.values():
            receiver.canary = canary
        for label, receiver in receivers.items():
            parameters = {row['name']: row['value'] for row in artifact['zabbix_export']['media_types'][0]['parameters']}
            parameters.update(fixture)
            parameters.update({'external_writes_enabled': 'true', 'alerta_api_url': f'https://runner:{receiver.port}/alert', 'alerta_api_key': canary,
                               'alerta_customer': 'lt-lab', 'zabbix_instance': 'lt-lab', 'alerta_environment': 'Development'})
            before = len(receiver.log['requests'])
            reply = acceptance.trapper({'request': 'alert.send', 'sid': session, 'data': {'mediatypeid': media, 'parameters': parameters}})
            time.sleep(0.3)
            requests = receiver.log['requests'][before:]
            delivered = reply.get('response') == 'success' and len(requests) == 1 and requests[0]['canary_key_arrived']
            refused = reply.get('response') != 'success' and not requests
            results['cases'].append({'case': 'delivery:' + label, 'zabbix_response': reply.get('response'),
                                     'zabbix_value': (reply.get('data') or reply.get('info') or '')[:200], 'http_requests': len(requests),
                                     'pass': delivered if label == 'lab-ca' else refused})
        # The host's own trust (outside the namespace) does not acquire the lab CA.
        try:
            with socket.create_connection(('127.0.0.1', receivers['lab-ca'].port), timeout=5) as raw, \
                    ssl.create_default_context().wrap_socket(raw, server_hostname='runner'):
                outside = 'verified'
        except ssl.SSLCertVerificationError as error:
            outside = 'refused: ' + str(error.verify_message)
        results['cases'].append({'case': 'host-trust-does-not-trust-the-lab-ca', 'outside': outside, 'pass': outside.startswith('refused')})

        # Lifecycle: stop, restart, and a stop by signal from the lab account.
        first = main_pid()
        sh('systemctl', 'stop', UNIT)
        stopped = wait_port(20051, up=False) and sh('systemctl', 'is-active', UNIT, check=False).stdout.strip() != 'active'
        leftovers = [p for p in (first, *[d['pid'] for d in obs['descendants']]) if Path(f'/proc/{p}').exists()]
        invocations.root_start()
        restarted = wait_port(20051, up=True) and main_pid() not in (0, first)
        second = main_pid()
        sh('runuser', '-u', ACCOUNT, '--', 'kill', '-TERM', str(second))
        signalled = wait_port(20051, up=False) and sh('systemctl', 'is-active', UNIT, check=False).stdout.strip() != 'active'
        results['cases'].append({'case': 'lifecycle-stop-restart-signal', 'stopped': stopped, 'leftover_pids': leftovers, 'restarted': restarted,
                                 'stopped_by_lab_account_signal': signalled, 'pass': stopped and not leftovers and restarted and signalled})
        if args.mode == 'installed':
            results['cases'].append(installed_ownership(lab, checkout, invocations))
        for receiver in receivers.values():
            receiver.shutdown()
    except Exception as failure:  # recorded, never hidden by the cases that passed
        run_error = f'{type(failure).__name__}: {failure}'
    finally:
        # Never a pre-existing or foreign invocation; an attempt that started nothing makes no host call at all.
        # No owner record is read here: only this attempt's own starts adopt invocations (Invocations.lab_start).
        cleanup: dict = {}
        cleanup['unit'] = invocations.stop_if_owned() if lab_started or invocations.created else {
            'action': 'none', 'reason': 'this attempt started nothing'}
        cleanup['invocations_created'] = list(invocations.created)  # recorded after the decision it drove
        cleanup['owner_records_not_adopted'] = list(invocations.ignored)
        if lab_started:
            cleanup['lab'] = lab.stop_and_wipe(collect)  # stop, collect, and wipe only after a complete collection
        else:
            cleanup['lab'] = [{'collect': collect()}]  # a refusal before any start: the bundle records what was absent
        for path in reversed(created):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        if RUNTIME_UNIT in created:
            cleanup['daemon_reload_exit'] = sh('systemctl', 'daemon-reload', check=False).returncode
            sh('systemctl', 'reset-failed', UNIT, check=False)
        cleanup['left_behind'] = [str(path) for path in created if path.exists() or path.is_symlink()]
        cleanup['ports_still_listening'] = [port for port in LAB_PORTS if listening(port)]
        results['cleanup'] = cleanup
        collected = [step['collect']['status'] for step in cleanup['lab'] if 'collect' in step]
        unit_end = cleanup['unit'].get('unit_after', cleanup['unit'].get('unit', {}))
        # The unit ends idle, or exactly as it was before this attempt (a pre-existing invocation it refused).
        before = obs.get('unit_before', {})
        unit_as_found = unit_end.get('ActiveState') not in UNIT_BUSY or (
            bool(unit_end.get('InvocationID')) and unit_end.get('InvocationID') == before.get('InvocationID'))
        clean = (collected == ['COLLECTED'] and cleanup['unit'].get('exit', 0) == 0 and unit_as_found
                 and all(step['exit'] == 0 for step in cleanup['lab'] if 'exit' in step)
                 and not cleanup['left_behind'] and not cleanup['ports_still_listening'] and cleanup.get('daemon_reload_exit', 0) == 0)
        results['cases'].append({'case': 'cleanup', 'pass': clean})
        results['host_after'] = host_state()
        results['host_unchanged'] = results['host_after'] == results['host_before']
        results['cases'].append({'case': 'host-unchanged', 'pass': results['host_unchanged']})
        results['summary'] = summarize(args.mode, results['cases'], run_error)
        results['summary']['private_evidence'] = str(args.evidence.resolve() / attempt)
        args.out.write_text(json.dumps(results, indent=2) + '\n')
        result_collected = acceptance.collector(args.evidence, attempt, [('file', 'result', args.out)])()['status']
    print(json.dumps({**results['summary'], 'host_unchanged': results['host_unchanged'], 'result_collected': result_collected}))
    return 0 if results['summary']['status'] == 'PASS' and result_collected == 'COLLECTED' else 1


def installed_ownership(lab: acceptance.Lab, checkout: Path, invocations: Invocations) -> dict:
    """The installed lab refuses to stop an invocation it did not start, and starts and stops its own."""
    record: dict = {'case': 'installed-lab-unit-ownership'}
    invocations.root_start()  # an invocation the lab account did not start, but this attempt did
    wait_port(20051, up=True)
    foreign = unit_show()
    refused = lab.command('stop', '--isolated-lab', check=False)
    after_refusal = unit_show()
    record['foreign'] = {'stop_exit': refused.returncode, 'stderr': refused.stderr[-300:], 'before': foreign, 'after': after_refusal}
    sh('systemctl', 'stop', UNIT)
    own_start = invocations.lab_start(lambda: lab.command('start', '--isolated-lab', check=False), checkout)
    owned = unit_show()
    try:
        owner = json.loads((checkout / '.zabbix-lab/trust-unit.owner.json').read_text())
    except (OSError, ValueError):
        owner = None
    own_stop = lab.command('stop', '--isolated-lab', check=False)
    stopped = unit_show()
    record['own'] = {'start_exit': own_start.returncode, 'unit': owned, 'owner_record': owner, 'stop_exit': own_stop.returncode,
                     'after_stop': stopped}
    record['pass'] = (refused.returncode != 0 and after_refusal.get('ActiveState') == 'active'
                      and after_refusal.get('InvocationID') == foreign.get('InvocationID')
                      and own_start.returncode == 0 and isinstance(owner, dict) and owner.get('invocation_id') == owned.get('InvocationID')
                      and own_stop.returncode == 0 and stopped.get('ActiveState') in ('inactive', 'failed'))
    return record


if __name__ == '__main__':
    sys.exit(main())
