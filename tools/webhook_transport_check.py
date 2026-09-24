#!/usr/bin/env python3
"""Enablement precondition for the reference webhook's transport (SPEC_AMENDMENTS A2). Read-only.

Run on every Zabbix server node that can execute the webhook (each HA node: any of them can become active).
The webhook asks Zabbix's HttpRequest to verify the ingestion peer and host; only some builds honour that,
and the trust is the executing server's own CA store. Enablement is refused unless all of these hold:

1. executor: `zabbix_server -V` names a qualified build exactly (version and source revision). Only the
   7.0 branch reads the constructor options, from 7.0.28; 7.0.31 (tag 7.0.31, commit e7da53eea21) is the
   qualified build. A later number, another branch or an unreadable version is refused, never assumed.
2. script: the media type script stored in Zabbix (a JSON export given with --media-export, from
   configuration.export) equals zabbix/v1/webhook.js, whose constructor enables both verifications.
3. trust: a TLS handshake to the ingestion URL's host and port verifies the certificate chain and host
   name against the CA file and directory the executor's libcurl uses (Ubuntu libcurl: the defaults below).
   Only a handshake is made: no HTTP request, credential or payload is sent.

Redirect containment is not checked here: HttpRequest follows redirects, so the supported configuration
requires an ingestion route that never returns one (docs/webhook-transport.md), checked separately.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
# (version, source revision as printed by `zabbix_server -V`): builds whose HttpRequest reads
# {SSLVerifyPeer, SSLVerifyHost} and that were qualified with this artifact.
QUALIFIED_EXECUTORS = {('7.0.31', 'e7da53eea21')}
LIBCURL_CA_FILE = '/etc/ssl/certs/ca-certificates.crt'
LIBCURL_CA_PATH = '/etc/ssl/certs'
URL = re.compile(r'https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9_.~-]+)*/alert')


def executor(binary: str) -> dict:
    try:
        output = subprocess.run([binary, '-V'], capture_output=True, text=True, timeout=10, env={'PATH': '/usr/bin:/bin'}).stdout
    except (OSError, subprocess.SubprocessError) as error:
        return {'ok': False, 'reason': 'executor_unreadable: ' + type(error).__name__}
    lines = output.splitlines()
    version = re.fullmatch(r'zabbix_server \(Zabbix\) (\S+)', lines[0].strip()) if lines else None
    revision = re.match(r'Revision ([0-9a-f]{7,40}) ', lines[1].strip()) if len(lines) > 1 else None
    identity = (version[1] if version else None, revision[1] if revision else None)
    if identity in QUALIFIED_EXECUTORS:
        return {'ok': True, 'version': identity[0], 'revision': identity[1]}
    return {'ok': False, 'version': identity[0], 'revision': identity[1], 'reason': 'executor_not_qualified'}


def script(export: Path) -> dict:
    expected = (ROOT / 'zabbix/v1/webhook.js').read_text()
    try:
        media = json.loads(export.read_text())['zabbix_export']['media_types']
        stored = [m['script'] for m in media if m.get('type') == 'WEBHOOK' and 'Zab-al-ack' in m.get('name', '')]
    except (OSError, ValueError, KeyError, TypeError):
        return {'ok': False, 'reason': 'media_export_unreadable'}
    if len(stored) != 1 or not isinstance(stored[0], str):
        return {'ok': False, 'reason': 'media_type_not_found_once'}
    # Zabbix stores imported multi-line text with CRLF; only line terminators may differ.
    if stored[0].replace('\r\n', '\n') != expected:
        return {'ok': False, 'reason': 'media_script_differs_from_the_reviewed_artifact'}
    return {'ok': True}


def trust(url: str, ca_file: str, ca_path: str, timeout: float = 10) -> dict:
    if not URL.fullmatch(url):
        return {'ok': False, 'reason': 'ingestion_url_not_an_https_alert_url'}
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 443
    try:
        context = ssl.create_default_context(cafile=ca_file, capath=ca_path)
    except OSError as error:
        return {'ok': False, 'reason': 'trust_store_unreadable: ' + type(error).__name__}
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw, context.wrap_socket(raw, server_hostname=host) as tls:
            return {'ok': True, 'host': host, 'port': port, 'protocol': tls.version()}
    except ssl.SSLCertVerificationError as error:
        return {'ok': False, 'host': host, 'port': port, 'reason': 'certificate_not_verified: ' + str(error.verify_message)}
    except (OSError, ssl.SSLError) as error:
        return {'ok': False, 'host': host, 'port': port, 'reason': 'tls_handshake_failed: ' + type(error).__name__}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--zabbix-server', default='/usr/sbin/zabbix_server')
    parser.add_argument('--media-export', type=Path, required=True, help='configuration.export JSON of the media type')
    parser.add_argument('--ingest-url', required=True, help='the alerta_api_url the media type will use')
    parser.add_argument('--ca-file', default=LIBCURL_CA_FILE)
    parser.add_argument('--ca-path', default=LIBCURL_CA_PATH)
    args = parser.parse_args(argv)
    checks = {'executor': executor(args.zabbix_server), 'script': script(args.media_export),
              'trust': trust(args.ingest_url, args.ca_file, args.ca_path)}
    ready = all(check['ok'] for check in checks.values())
    print(json.dumps({'status': 'READY' if ready else 'REFUSED', 'checks': checks,
                      'not_checked': 'redirect containment of the ingestion route (see docs/webhook-transport.md)'}, indent=2))
    return 0 if ready else 3


if __name__ == '__main__':
    sys.exit(main())
