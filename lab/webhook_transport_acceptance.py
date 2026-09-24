#!/usr/bin/env python3
"""A2 (S2) acceptance through the actual Zabbix engine. Isolated lab host only; never a shared or production host.

The packaged zabbix_server executes the shipped media-type script (zabbix/v1/webhook.js, imported from
media_type.reference.json) through its real alerter: each case is the frontend's media-type test request
(server trapper `alert.send` with an API session), sent directly. Receivers on loopback record what actually
arrives: TCP connections, TLS handshake failures, and per HTTP request the method, path, body digest and
whether the synthetic canary key arrived. Nothing real is used: synthetic names, a synthetic key, a test PKI.

Isolation: run as root inside a private mount namespace (`unshare -m --propagation private`). The script
refuses otherwise. It bind-mounts a test trust directory over /etc/ssl/certs (the CA file and directory the
executor's libcurl uses) and a hosts file with the synthetic names over /etc/hosts, for this namespace only;
the host's own trust store and hosts file are not modified. The Zabbix lab (lab/zabbix_native_lab.py) and the
candidate gateway (deploy/ingress/alerta-ingest.nginx.conf.template, an nginx binary extracted from the
distribution package, not installed) run as the lab account inside the namespace and are stopped at the end.

python3 lab/webhook_transport_acceptance.py --checkout DIR --work DIR --nginx PATH --lab-user USER --out FILE
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import zlib

NAMES = {'ingest': 'ingest.s2.test', 'direct': 'direct.s2.test', 'other': 'other.s2.test', 'alerta': 'alerta.s2.test'}
API = 'http://127.0.0.1:8080/api_jsonrpc.php'
TRAPPER = ('127.0.0.1', 20051)
LAB_ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp', 'TZ': 'UTC', 'ACK_LAB_PG_BIN': '/usr/lib/postgresql/18/bin',
           'ZABALACK_ZABBIX_SERVER': '/usr/sbin/zabbix_server', 'ZABALACK_ZABBIX_UI': '/usr/share/zabbix',
           'ZABALACK_ZABBIX_SCHEMA': '/usr/share/zabbix-sql-scripts/postgresql/server.sql.gz', 'ZABALACK_PHP': '/usr/bin/php8.3'}


def sh(*argv, cwd=None, check=True, env=None):
    return subprocess.run(argv, cwd=cwd, check=check, capture_output=True, text=True, env=env)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- test PKI (openssl CLI, OpenSSL 3.0 compatible)

def pki(d: Path) -> None:
    d.mkdir()
    for ca in ('trusted', 'other'):
        sh('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', f'/CN=s2-{ca}-ca', '-keyout', f'{ca}-ca.key',
           '-out', f'{ca}-ca.pem', '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign', cwd=d)
    (d / 'ca.conf').write_text('[ca]\ndefault_ca=s2\n[s2]\ndatabase=index.txt\nnew_certs_dir=.\nserial=serial\ndefault_md=sha256\n'
                               'policy=any\ncopy_extensions=copy\nunique_subject=no\n[any]\ncommonName=supplied\n')
    (d / 'index.txt').write_text('')
    (d / 'serial').write_text('1000\n')

    def leaf(name, host, ca, start=None, end=None, self_signed=False):
        sh('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=' + host, '-keyout', name + '.key', '-out', name + '.csr',
           '-addext', f'subjectAltName=DNS:{host}', cwd=d)
        if self_signed:
            sh('openssl', 'x509', '-req', '-in', name + '.csr', '-signkey', name + '.key', '-days', '1', '-copy_extensions', 'copy',
               '-out', name + '.pem', cwd=d)
            return
        dates = ['-startdate', start, '-enddate', end] if start else ['-days', '1']
        sh('openssl', 'ca', '-batch', '-config', 'ca.conf', '-cert', ca + '-ca.pem', '-keyfile', ca + '-ca.key', '-in', name + '.csr',
           '-out', name + '.pem', '-notext', *dates, cwd=d)
    leaf('ingest', NAMES['ingest'], 'trusted')
    leaf('alerta', NAMES['alerta'], 'trusted')
    leaf('other', NAMES['other'], 'trusted')
    leaf('direct', NAMES['direct'], 'trusted')
    leaf('direct-untrusted', NAMES['direct'], None, self_signed=True)
    leaf('direct-other-anchor', NAMES['direct'], 'other')
    leaf('direct-wrong-name', NAMES['other'], 'trusted')
    leaf('direct-expired', NAMES['direct'], 'trusted', start='20000101000000Z', end='20000102000000Z')
    for path in d.iterdir():
        path.chmod(0o644)


def isolate(work: Path) -> dict:
    """Test trust and names for this mount namespace only."""
    if os.geteuid() != 0:
        raise SystemExit('run as root on the isolated lab host')
    if os.readlink('/proc/self/ns/mnt') == os.readlink('/proc/1/ns/mnt'):
        raise SystemExit('run inside a private mount namespace: unshare -m --propagation private')
    trust = work / 'trust'
    trust.mkdir()
    shutil.copy(work / 'pki/trusted-ca.pem', trust / 'ca-certificates.crt')
    shutil.copy(work / 'pki/trusted-ca.pem', trust / 's2-trusted-ca.pem')
    sh('openssl', 'rehash', str(trust))
    hosts = work / 'hosts'
    hosts.write_text(Path('/etc/hosts').read_text() + '\n# S2 acceptance (this namespace only)\n'
                     + ''.join(f'127.0.0.1 {name}\n' for name in NAMES.values()))
    for source, target in ((trust, '/etc/ssl/certs'), (hosts, '/etc/hosts')):
        sh('mount', '--bind', str(source), target)
    return {'trust_ca_sha256': digest(trust / 'ca-certificates.crt'), 'hosts_sha256': digest(hosts)}


# ---------------------------------------------------------------- receivers

class Receiver(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, label, pki_dir=None, cert=None, behaviour=('ok',)):
        self.label, self.behaviour = label, behaviour
        self.log = {'connections': 0, 'handshake_failures': 0, 'requests': []}
        self.context = None
        if cert:
            self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.context.load_cert_chain(pki_dir / (cert + '.pem'), pki_dir / (cert + '.key'))
        super().__init__(('127.0.0.1', 0), Handler)
        self.port = self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def get_request(self):
        connection, address = self.socket.accept()
        self.log['connections'] += 1
        if self.context:
            try:
                connection = self.context.wrap_socket(connection, server_side=True)
            except OSError:
                self.log['handshake_failures'] += 1
                connection.close()
                raise
        return connection, address

    def handle_error(self, request, client_address):
        pass

    def snapshot(self):
        return {'connections': self.log['connections'], 'handshake_failures': self.log['handshake_failures'],
                'requests': list(self.log['requests'])}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def handle_any(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        auth = self.headers.get('Authorization') or ''
        canary = self.server.canary
        self.server.log['requests'].append({'method': self.command, 'path': self.path, 'host': self.headers.get('Host'),
                                            'body_bytes': len(body), 'body_sha256': hashlib.sha256(body).hexdigest() if body else None,
                                            'canary_key_arrived': bool(canary) and canary in auth,
                                            'authorization_sha256': hashlib.sha256(auth.encode()).hexdigest() if auth else None})
        kind = self.server.behaviour
        if kind[0] == 'redirect':
            self.send_response(kind[1])
            self.send_header('Location', kind[2])
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        out = json.dumps({'status': 'ok', 'id': 's2-' + secrets.token_hex(4)}).encode()
        self.send_response(201)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = do_PUT = do_HEAD = do_OPTIONS = do_DELETE = handle_any


# ---------------------------------------------------------------- Zabbix: API and the trapper media-type test

def rpc(method, params, auth=None):
    headers = {'Content-Type': 'application/json-rpc'}
    if auth:
        headers['Authorization'] = 'Bearer ' + auth
    body = json.dumps({'jsonrpc': '2.0', 'method': method, 'params': params, 'id': 1}).encode()
    with urllib.request.urlopen(urllib.request.Request(API, data=body, headers=headers), timeout=30) as response:
        reply = json.loads(response.read())
    if 'error' in reply:
        raise RuntimeError(f"{method}: {reply['error']}")
    return reply['result']


def trapper(request: dict, timeout=90) -> dict:
    payload = json.dumps(request).encode()
    with socket.create_connection(TRAPPER, timeout=timeout) as connection:
        connection.sendall(b'ZBXD\x01' + struct.pack('<II', len(payload), 0) + payload)
        header = b''
        while len(header) < 13:
            chunk = connection.recv(13 - len(header))
            if not chunk:
                raise RuntimeError('trapper closed the connection')
            header += chunk
        flags, length = header[4], struct.unpack('<I', header[5:9])[0]
        data = b''
        while len(data) < length:
            chunk = connection.recv(length - len(data))
            if not chunk:
                break
            data += chunk
    if flags & 0x02:
        data = zlib.decompress(data)
    return json.loads(data)


class Lab:
    def __init__(self, checkout: Path, user: str):
        self.checkout, self.user = checkout, user

    def command(self, *args, check=True):
        return sh('runuser', '-u', self.user, '--', 'env', '-i', *[f'{k}={v}' for k, v in LAB_ENV.items()],
                  '/usr/bin/python3', 'lab/zabbix_native_lab.py', *args, cwd=self.checkout, check=check)

    def start(self):
        return self.command('start', '--isolated-lab').stdout

    def stop_and_wipe(self):
        results = [self.command('stop', '--isolated-lab', check=False)]
        results.append(self.command('reset', '--isolated-lab', '--wipe', check=False))
        return [{'argv_tail': r.args[-3:], 'exit': r.returncode, 'stdout': r.stdout[-300:]} for r in results]


# ---------------------------------------------------------------- the gateway candidate

def gateway(work: Path, nginx: str, user: str, upstream_port: int) -> tuple[subprocess.Popen, int, Path]:
    root = work / 'nginx'
    (root / 'logs').mkdir(parents=True)
    port = free_port()
    template = (work / 'checkout/deploy/ingress/alerta-ingest.nginx.conf.template').read_text()
    server = template
    for key, value in {'@LISTEN@': f'127.0.0.1:{port}', '@INGEST_NAME@': NAMES['ingest'], '@INGEST_PATH@': '/api/alert',
                       '@CERT@': str(work / 'pki/ingest.pem'), '@KEY@': str(work / 'pki/ingest.key'),
                       '@UPSTREAM_URL@': f"https://{NAMES['alerta']}:{upstream_port}/api/alert", '@UPSTREAM_NAME@': NAMES['alerta'],
                       '@UPSTREAM_CA@': str(work / 'pki/trusted-ca.pem')}.items():
        server = server.replace(key, value)
    assert '@' not in re.sub(r'#.*', '', server), 'unrendered placeholder'
    conf = root / 'nginx.conf'
    conf.write_text(f'pid {root}/nginx.pid;\nerror_log {root}/logs/error.log info;\nevents {{ worker_connections 64; }}\n'
                    f'http {{\n  access_log {root}/logs/access.log;\n  client_body_temp_path {root}/body;\n  proxy_temp_path {root}/proxy;\n'
                    f'  fastcgi_temp_path {root}/fcgi;\n  uwsgi_temp_path {root}/uwsgi;\n  scgi_temp_path {root}/scgi;\n'
                    + server + '\n}\n')
    sh('chown', '-R', f'{user}:{user}', str(root))
    sh('runuser', '-u', user, '--', nginx, '-t', '-p', str(root), '-c', str(conf))
    process = subprocess.Popen(['runuser', '-u', user, '--', nginx, '-p', str(root), '-c', str(conf), '-g', 'daemon off;'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                return process, port, root
        time.sleep(0.2)
    raise RuntimeError('gateway did not listen')


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def no_redirect_check(url_host: str, port: int, path: str, canary: str, body: bytes) -> dict:
    """T7: an initial explicit 2xx with application semantics, without following anything."""
    context = ssl.create_default_context(cafile='/etc/ssl/certs/ca-certificates.crt')
    connection = http.client.HTTPSConnection(url_host, port, context=context, timeout=15)
    try:
        connection.request('POST', path, body=body, headers={'Content-Type': 'application/json', 'Authorization': 'Key ' + canary})
        response = connection.getresponse()
        text = response.read(1048577)
    finally:
        connection.close()
    try:
        semantic = json.loads(text).get('status') == 'ok'
    except ValueError:
        semantic = False
    return {'initial_status': response.status, 'location': response.getheader('Location'), 'application_ok': semantic,
            'passes': 200 <= response.status < 300 and semantic}


# ---------------------------------------------------------------- run

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--nginx', required=True)
    parser.add_argument('--lab-user', default='zabalack-runner')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    work = args.work.resolve()
    pki(work / 'pki')
    shutil.copytree(args.checkout, work / 'checkout', symlinks=True)
    sh('chown', '-R', f'{args.lab_user}:{args.lab_user}', str(work / 'checkout'))
    identity = {'isolation': isolate(work)}
    checkout = work / 'checkout'
    canary = 'S2-CANARY-' + secrets.token_hex(12)
    fixture = json.loads((checkout / 'zabbix/v1/fixtures/problem.params.json').read_text())
    pkidir = work / 'pki'

    receivers = {
        'upstream': Receiver('upstream', pkidir, 'alerta'),
        'target-other': Receiver('target-other', pkidir, 'other'),
        'target-alt-port': Receiver('target-alt-port', pkidir, 'ingest'),
        'target-plain': Receiver('target-plain'),
        'direct-ok': Receiver('direct-ok', pkidir, 'direct'),
        'direct-untrusted': Receiver('direct-untrusted', pkidir, 'direct-untrusted'),
        'direct-other-anchor': Receiver('direct-other-anchor', pkidir, 'direct-other-anchor'),
        'direct-wrong-name': Receiver('direct-wrong-name', pkidir, 'direct-wrong-name'),
        'direct-expired': Receiver('direct-expired', pkidir, 'direct-expired'),
        'direct-redirect': Receiver('direct-redirect', pkidir, 'direct'),
    }
    for receiver in receivers.values():
        receiver.canary = canary
    lab = Lab(checkout, args.lab_user)
    process = None
    results: dict = {'scope': __doc__.split('\n\n')[0], 'cases': [], 'identity': identity}
    try:
        process, gw_port, gw_root = gateway(work, args.nginx, args.lab_user, receivers['upstream'].port)
        identity['gateway'] = {'nginx': sh(args.nginx, '-v').stderr.strip(), 'config_sha256': digest(gw_root / 'nginx.conf'),
                               'template_sha256': digest(checkout / 'deploy/ingress/alerta-ingest.nginx.conf.template')}
        identity['lab_start'] = json.loads(lab.start().strip().splitlines()[-1])
        identity['executor'] = sh('/usr/sbin/zabbix_server', '-V').stdout.splitlines()[:2]
        identity['packages'] = sh('dpkg-query', '-W', '-f', '${Package} ${Version} ${Architecture}\n', 'zabbix-server-pgsql',
                                  'zabbix-frontend-php', 'libcurl4t64', 'openssl').stdout.splitlines()
        identity['executor_libcurl'] = [line.strip() for line in sh('ldd', '/usr/sbin/zabbix_server').stdout.splitlines() if 'libcurl' in line]
        identity['webhook_js_sha256'] = digest(checkout / 'zabbix/v1/webhook.js')
        identity['media_export_sha256'] = digest(checkout / 'zabbix/v1/media_type.reference.json')

        session = rpc('user.login', {'username': 'Admin', 'password': 'zabbix'})
        artifact = json.loads((checkout / 'zabbix/v1/media_type.reference.json').read_text())
        assert rpc('configuration.import', {'format': 'json', 'source': json.dumps(artifact),
                                            'rules': {'mediaTypes': {'createMissing': True, 'updateExisting': False}}}, session) is True
        name = artifact['zabbix_export']['media_types'][0]['name']
        media = rpc('mediatype.get', {'output': ['mediatypeid', 'script'], 'filter': {'name': name}}, session)
        media_id = media[0]['mediatypeid']
        stored_script = media[0]['script'].replace('\r\n', '\n')
        identity['stored_script_equals_artifact'] = stored_script == (checkout / 'zabbix/v1/webhook.js').read_text()
        rpc('mediatype.update', {'mediatypeid': media_id, 'status': 0, 'maxattempts': 1}, session)
        exported = rpc('configuration.export', {'format': 'json', 'options': {'mediaTypes': [media_id]}}, session)
        (work / 'media-export.json').write_text(exported)

        def deliver(url, overrides=None):
            parameters = {row['name']: row['value'] for row in artifact['zabbix_export']['media_types'][0]['parameters']}
            parameters.update(fixture)
            parameters.update({'external_writes_enabled': 'true', 'alerta_api_url': url, 'alerta_api_key': canary,
                               'alerta_customer': 's2-lab', 'zabbix_instance': 's2-lab', 'alerta_environment': 'Development'})
            parameters.update(overrides or {})
            before = {label: r.snapshot() for label, r in receivers.items()}
            access_before = (gw_root / 'logs/access.log').read_text() if (gw_root / 'logs/access.log').exists() else ''
            reply = trapper({'request': 'alert.send', 'sid': session, 'data': {'mediatypeid': media_id, 'parameters': parameters}})
            time.sleep(0.3)
            observed = {}
            for label, receiver in receivers.items():
                now, then = receiver.snapshot(), before[label]
                delta = {'connections': now['connections'] - then['connections'],
                         'handshake_failures': now['handshake_failures'] - then['handshake_failures'],
                         'requests': now['requests'][len(then['requests']):]}
                if delta['connections'] or delta['requests']:
                    observed[label] = delta
            access = (gw_root / 'logs/access.log').read_text()[len(access_before):] if (gw_root / 'logs/access.log').exists() else ''
            return {'zabbix_response': reply.get('response'), 'zabbix_value': (reply.get('data') or reply.get('info') or '')[:300],
                    'observed': observed, 'gateway_access': [line.split('"')[1] + ' ' + line.split('"')[2].split()[0]
                                                             for line in access.splitlines() if '"' in line]}

        def record(case, expectation, outcome, passes):
            results['cases'].append({'case': case, 'expected': expectation, **outcome, 'pass': bool(passes)})

        def arrived_only(outcome, allowed):
            return all(label in allowed for label in outcome['observed'])

        def no_http(outcome, label):
            item = outcome['observed'].get(label, {})
            return not item.get('requests')

        gw = f"https://{NAMES['ingest']}:{gw_port}/api/alert"
        up = receivers['upstream']

        # Trust: trusted, untrusted, missing anchor, wrong name, expired (direct, no gateway)
        out = deliver(f"https://{NAMES['direct']}:{receivers['direct-ok'].port}/api/alert")
        record('trust:valid-direct', 'success; one POST with the canary key', out,
               out['zabbix_response'] == 'success' and len(out['observed'].get('direct-ok', {}).get('requests', [])) == 1
               and out['observed']['direct-ok']['requests'][0]['canary_key_arrived'] and arrived_only(out, {'direct-ok'}))
        for label in ('direct-untrusted', 'direct-other-anchor', 'direct-wrong-name', 'direct-expired'):
            out = deliver(f"https://{NAMES['direct']}:{receivers[label].port}/api/alert")
            record('trust:' + label, 'Zabbix failure; TLS refused; no HTTP request, body or key at the listener', out,
                   out['zabbix_response'] != 'success' and no_http(out, label) and arrived_only(out, {label}))

        # The approved route (gateway), positive
        up.behaviour = ('ok',)
        out = deliver(gw)
        record('route:valid', 'success; exactly one POST /api/alert upstream with the canary key; nothing else', out,
               out['zabbix_response'] == 'success' and len(out['observed'].get('upstream', {}).get('requests', [])) == 1
               and out['observed']['upstream']['requests'][0]['method'] == 'POST'
               and out['observed']['upstream']['requests'][0]['canary_key_arrived'] and arrived_only(out, {'upstream'})
               and out['gateway_access'] == ['POST /api/alert HTTP/1.1 201'])

        # Upstream redirects through the route: every code, every target
        targets = {'other-origin': f"https://{NAMES['other']}:{receivers['target-other'].port}/api/alert",
                   'alternate-port': f"https://{NAMES['ingest']}:{receivers['target-alt-port'].port}/api/alert",
                   'downgrade-http': f"http://{NAMES['ingest']}:{receivers['target-plain'].port}/api/alert",
                   'http-to-route-port': f"http://{NAMES['ingest']}:{gw_port}/api/alert",
                   'same-origin-path': '/api/other'}
        for code in (301, 302, 303, 307, 308):
            for target, location in targets.items():
                up.behaviour = ('redirect', code, location)
                out = deliver(gw)
                record(f'route:redirect-{code}-{target}', 'Zabbix failure; route answers 502; no second request anywhere', out,
                       out['zabbix_response'] != 'success' and arrived_only(out, {'upstream'})
                       and len(out['observed'].get('upstream', {}).get('requests', [])) == 1
                       and out['gateway_access'] == ['POST /api/alert HTTP/1.1 502'])
        up.behaviour = ('ok',)

        # Another hostname on the route's address
        out = deliver(f"https://{NAMES['other']}:{gw_port}/api/alert")
        record('route:other-name-same-address', 'Zabbix failure; no TLS session; nothing forwarded', out,
               out['zabbix_response'] != 'success' and not out['observed'] and not out['gateway_access'])

        # Plain HTTP to the route's TLS port (the webhook itself refuses http:// URLs)
        connection = http.client.HTTPConnection('127.0.0.1', gw_port, timeout=10)
        before = up.snapshot()
        connection.request('POST', '/api/alert', body=b'{}', headers={'Host': NAMES['ingest'], 'Authorization': 'Key ' + canary})
        status = connection.getresponse().status
        connection.close()
        record('route:plain-http-to-tls-port', 'refused by the route (400), nothing forwarded',
               {'http_status': status, 'upstream_new_requests': len(up.snapshot()['requests']) - len(before['requests'])},
               status == 400 and len(up.snapshot()['requests']) == len(before['requests']))

        # Disabled and echo paths: no HttpRequest, no credential-bearing send
        out = deliver(gw, {'external_writes_enabled': 'false'})
        record('path:disabled', 'script result disabled_reference_artifact; nothing sent', out,
               out['zabbix_response'] == 'success' and 'disabled_reference_artifact' in out['zabbix_value'] and not out['observed'])
        echo = json.loads((checkout / 'zabbix/v1/fixtures/writeback_echo.params.json').read_text())
        out = deliver(gw, echo)
        record('path:echo', 'script result suppressed_echo; nothing sent', out,
               out['zabbix_response'] == 'success' and 'suppressed_echo' in out['zabbix_value'] and not out['observed'])

        # Baseline without the route: what the real engine does with a redirect it receives (why the route is required)
        for code in (301, 302, 303, 307, 308):
            for target, location in (('other-origin', targets['other-origin']), ('same-origin-path', '/api/other'),
                                     ('downgrade-http', targets['downgrade-http'])):
                receivers['direct-redirect'].behaviour = ('redirect', code, location)
                out = deliver(f"https://{NAMES['direct']}:{receivers['direct-redirect'].port}/api/alert")
                results['cases'].append({'case': f'baseline-no-route:redirect-{code}-{target}',
                                         'expected': 'characterisation only (no control in front)', **out, 'pass': None})
        receivers['direct-redirect'].behaviour = ('ok',)

        # T7 no-redirect check and its deliberate-redirect control
        body = json.dumps({'resource': 's2', 'event': 's2'}).encode()
        up.behaviour = ('ok',)
        t7 = no_redirect_check(NAMES['ingest'], gw_port, '/api/alert', canary, body)
        record('t7:route-no-redirect', 'initial 2xx with status ok', t7, t7['passes'])
        receivers['direct-redirect'].behaviour = ('redirect', 302, targets['other-origin'])
        t7 = no_redirect_check(NAMES['direct'], receivers['direct-redirect'].port, '/api/alert', canary, body)
        record('t7:deliberate-redirect-fails-the-check', 'the check fails on a 302', t7, not t7['passes'] and t7['initial_status'] == 302)
        up.behaviour = ('redirect', 307, targets['other-origin'])
        t7 = no_redirect_check(NAMES['ingest'], gw_port, '/api/alert', canary, body)
        record('t7:route-with-redirecting-upstream-fails-the-check', 'the check fails (502, no Location)', t7,
               not t7['passes'] and t7['initial_status'] == 502 and t7['location'] is None)
        up.behaviour = ('ok',)

        # The enablement precondition on this executor
        tool = [sys.executable, str(checkout / 'tools/webhook_transport_check.py'), '--media-export', str(work / 'media-export.json')]
        fake = work / 'zabbix_server-7.0.27'
        fake.write_text("#!/bin/sh\nprintf 'zabbix_server (Zabbix) 7.0.27\\nRevision 0000000 1 January 2026, compilation time: x\\n'\n")
        fake.chmod(0o755)
        for case, extra, expected in (
                ('precondition:qualified-executor-route', ['--ingest-url', gw], 0),
                ('precondition:missing-trust-anchor', ['--ingest-url',
                                                       f"https://{NAMES['direct']}:{receivers['direct-other-anchor'].port}/api/alert"], 3),
                ('precondition:unqualified-executor', ['--zabbix-server', str(fake), '--ingest-url', gw], 3)):
            run = sh(*tool, *extra, check=False)
            outcome = json.loads(run.stdout) if run.stdout.strip().startswith('{') else {'raw': run.stdout[-300:]}
            record(case, 'READY' if expected == 0 else 'REFUSED', {'exit': run.returncode, 'tool': outcome}, run.returncode == expected)

        results['not_applicable'] = {
            'proxy': 'the webhook calls no setProxy and the lab service environment carries no proxy variables (service_env)',
            'ipv6': 'the route and names are IPv4 loopback only in this lab',
            'ha': 'single server node; each HA node must pass the precondition (docs/webhook-transport.md)'}
    finally:
        results['cleanup'] = {'lab': lab.stop_and_wipe()}
        if process:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            results['cleanup']['gateway_exit'] = process.returncode
        for receiver in receivers.values():
            receiver.shutdown()
        results['summary'] = {'acceptance_cases': sum(1 for c in results['cases'] if c['pass'] is not None),
                              'passed': sum(1 for c in results['cases'] if c['pass'] is True),
                              'failed': [c['case'] for c in results['cases'] if c['pass'] is False]}
        args.out.write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results['summary']))
    return 0 if not results['summary']['failed'] else 1


if __name__ == '__main__':
    sys.exit(main())
