#!/usr/bin/env python3
"""Validate the lab-trust unit (deploy/lab-trust, docs/lab-trust.md) on the isolated lab host. Root; a test driver.

This driver is not the lab account's privileged interface (that is only `sudo systemctl start|stop
zabalack-lab-zabbix.service`); an operator runs it as root to observe the boundary.

  --mode pre-install  No persistent change. The shipped unit is copied to /run/systemd/system with one
                      difference, its trust source (a root-owned directory under /run holding a lab CA generated
                      with install.sh's command); /run/zabalack-lab-zabbix is created as the tmpfiles rule would.
                      The lab starts PostgreSQL and the frontend; its own zabbix_server is replaced by the unit's.
  --mode installed    After install.sh (owner-authorized): the lab account starts the Zabbix lab itself, which
                      now starts zabbix_server through the installed unit with its sudo rule.

Observed in both: host trust and hosts before and after (unchanged); the daemon's and every descendant's UID,
GID, groups, capabilities and no_new_privs; its mount namespace against init's; the trust it sees; deliveries of
the shipped webhook through the packaged engine to receivers named `runner`: a lab-CA certificate (delivered),
and untrusted, other-anchor, wrong-name and expired certificates (refused before any HTTP data); the host's own
trust refusing the lab certificate; start, stop, restart and a stop by signal from the lab account. Everything
it created is removed at the end.

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


def wait_port(port: int, up: bool, timeout: float = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if (probe.connect_ex(('127.0.0.1', port)) == 0) == up:
                return True
        time.sleep(0.5)
    return False


def lab_ca(work: Path, stage: Path) -> tuple[Path, Path]:
    """A lab CA made with install.sh's own openssl command, in root-owned staging."""
    install = (work / 'checkout/deploy/lab-trust/install.sh').read_text()
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
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        raise SystemExit('run as root on the isolated lab host')
    work = args.work.resolve()
    shutil.copytree(args.checkout, work / 'checkout', symlinks=True)
    sh('chown', '-R', f'{ACCOUNT}:{ACCOUNT}', str(work / 'checkout'))
    checkout = work / 'checkout'
    results: dict = {'mode': args.mode, 'host_before': host_state(), 'observations': {}, 'cases': []}
    obs = results['observations']
    lab = acceptance.Lab(checkout, ACCOUNT)
    created: list[Path] = []
    try:
        if args.mode == 'pre-install':
            for path in (RUNTIME_UNIT, INSTALLED_UNIT, STAGE, CONF.parent):
                if path.exists():
                    raise SystemExit(f'refusing: {path} exists')
            STAGE.mkdir(mode=0o755)
            created.append(STAGE)
            ca_dir = STAGE / 'ca'
            ca_dir.mkdir(mode=0o700)
            ca = lab_ca(work, ca_dir)
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
            start_text = lab.start()
            # Replace the lab's own zabbix_server with the unit's, reading the same configuration.
            state = checkout / '.zabbix-lab'
            sh('runuser', '-u', ACCOUNT, '--', 'cp', str(state / 'zabbix_server.conf'), str(CONF))
            sh('runuser', '-u', ACCOUNT, '--', 'kill', '-TERM', (state / 'zabbix_server.pid').read_text().strip())
            assert wait_port(20051, up=False)
            sh('systemctl', 'start', UNIT)
        else:
            ca = (Path('/etc/zabalack-lab/ca/lab-ca.pem'), Path('/etc/zabalack-lab/ca/lab-ca.key'))
            obs['verify_installation'] = sh('sh', str(checkout / 'deploy/lab-trust/verify.sh'), check=False).stdout
            start_text = lab.start()  # the lab starts zabbix_server through the unit (sudo rule)
        obs['lab_start'] = json.loads(start_text.strip().splitlines()[-1])
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
        sh('systemctl', 'start', UNIT)
        restarted = wait_port(20051, up=True) and main_pid() not in (0, first)
        second = main_pid()
        sh('runuser', '-u', ACCOUNT, '--', 'kill', '-TERM', str(second))
        signalled = wait_port(20051, up=False) and sh('systemctl', 'is-active', UNIT, check=False).stdout.strip() != 'active'
        results['cases'].append({'case': 'lifecycle-stop-restart-signal', 'stopped': stopped, 'leftover_pids': leftovers, 'restarted': restarted,
                                 'stopped_by_lab_account_signal': signalled, 'pass': stopped and not leftovers and restarted and signalled})
        for receiver in receivers.values():
            receiver.shutdown()
    finally:
        results['cleanup'] = {'unit_stop': sh('systemctl', 'stop', UNIT, check=False).returncode, 'lab': lab.stop_and_wipe()}
        for path in reversed(created):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        if RUNTIME_UNIT in created:
            sh('systemctl', 'daemon-reload')
            sh('systemctl', 'reset-failed', UNIT, check=False)
        results['host_after'] = host_state()
        results['host_unchanged'] = results['host_after'] == results['host_before']
        results['summary'] = {'cases': len(results['cases']), 'passed': sum(1 for c in results['cases'] if c['pass']),
                              'failed': [c['case'] for c in results['cases'] if not c['pass']]}
        args.out.write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps({**results['summary'], 'host_unchanged': results['host_unchanged']}))
    return 0 if not results['summary']['failed'] and results['host_unchanged'] else 1


if __name__ == '__main__':
    sys.exit(main())
