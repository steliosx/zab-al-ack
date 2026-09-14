#!/usr/bin/env python3
"""T30 loopback-only per-alert /action batch harness; never a production runner."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
        raise ValueError('lab URL must be HTTP(S), without embedded credentials')
    if parsed.query or parsed.fragment:
        raise ValueError('lab URL must not contain a query or fragment')
    try:
        address = ipaddress.ip_address(parsed.hostname or '')
    except ValueError as exc:
        raise ValueError('lab URL requires a literal loopback IP; DNS names are not accepted') from exc
    if not address.is_loopback:
        raise ValueError('this runner is restricted to loopback addresses')
    return value.rstrip('/')


def validate_manifest(value: object, expected_count: int) -> list[dict]:
    if not isinstance(value, dict) or value.get('environment') != 'isolated_synthetic_lab':
        raise ValueError('manifest must declare environment=isolated_synthetic_lab')
    rows = value.get('alerts')
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ValueError(f'manifest must contain exactly {expected_count} fresh alerts')
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'alert_id', 'tenant', 'instance', 'source_event_id', 'already_acknowledged'}:
            raise ValueError('manifest row contains unsupported fields')
        alert_id = row.get('alert_id')
        if not isinstance(alert_id, str) or str(UUID(alert_id)) != alert_id or alert_id in seen:
            raise ValueError('manifest alert IDs must be distinct canonical UUIDs')
        seen.add(alert_id)
        for key in ('tenant', 'instance', 'source_event_id'):
            text = row.get(key)
            if not isinstance(text, str) or not text or len(text) > 128 or not all(c.isalnum() or c in '-_.' for c in text):
                raise ValueError(f'manifest {key} must be a bounded synthetic label')
    return rows


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    return round(sorted(values)[max(0, math.ceil(quantile * len(values)) - 1)], 6)


def http_json(base: str, path: str, token: str, method: str = 'GET', payload: dict | None = None) -> tuple[int, dict]:
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    data = None
    if payload is not None:
        data = json.dumps(payload, separators=(',', ':')).encode()
        headers['Content-Type'] = 'application/json'
    # Explicitly ignore proxy environment variables and reject redirects.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(base + path, data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=10) as response:
            raw = response.read(1048577)
            if len(raw) > 1048576:
                return response.status, {'error_class': 'response_too_large'}
            decoded = json.loads(raw)
            return response.status, decoded if isinstance(decoded, dict) else {'error_class': 'response_shape'}
    except HTTPError as exc:
        # Deliberately do not retain error body, headers, token or full alert text.
        return exc.code, {'error_class': 'http_error'}
    except (URLError, TimeoutError, OSError, ValueError):
        return 0, {'error_class': 'transport_or_json_error'}


def action(base: str, token: str, row: dict) -> dict:
    start = time.monotonic()
    code, response = http_json(base, f"/alert/{row['alert_id']}/action", token, 'PUT',
                               {'action': 'ack', 'text': 'Isolated synthetic T30 batch'})
    return {**row, 'http_status': code, 'api_seconds': time.monotonic() - start,
            'action_returned_at': time.monotonic(), 'api_ok': code == 200 and response.get('status') == 'ok',
            'projection_state': 'unknown', 'projection_observed_seconds': None}


def run(args: argparse.Namespace) -> tuple[dict, int]:
    base = validate_base_url(args.base_url)
    token = os.environ.get(args.token_env, '')
    if not token or any(c.isspace() for c in token):
        raise ValueError('protected environment reference must contain a lab human Bearer token')
    rows = validate_manifest(json.loads(Path(args.manifest).read_text()), args.count)
    started = time.monotonic()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(action, base, token, row) for row in rows]
        for future in as_completed(futures):
            results.append(future.result())
    pending = [row for row in results if row['api_ok']]
    deadline = time.monotonic() + args.projection_timeout
    while pending and time.monotonic() < deadline:
        next_pending = []
        for row in pending:
            code, response = http_json(base, f"/alert/{row['alert_id']}", token)
            alert = response.get('alert', {}) if code == 200 else {}
            attributes = alert.get('attributes', {}) if isinstance(alert, dict) else {}
            state = attributes.get('zabbix_ack_sync', 'unknown')
            if state not in {'unknown', 'pending', 'ok', 'failed', 'skipped', 'expired'}:
                state = 'unknown'
            row['projection_state'] = state
            if state in {'ok', 'failed', 'skipped', 'expired'}:
                row['projection_observed_seconds'] = time.monotonic() - row['action_returned_at']
            else:
                next_pending.append(row)
        pending = next_pending
        if pending:
            time.sleep(0.25)
    for row in results:
        row.pop('action_returned_at', None)
    api = [r['api_seconds'] for r in results]
    projection = [r['projection_observed_seconds'] for r in results if r['projection_observed_seconds'] is not None]
    counts = {state: sum(r['projection_state'] == state for r in results)
              for state in ('unknown', 'pending', 'ok', 'failed', 'skipped', 'expired')}
    report = {
        'test_id': 'T30', 'evidence_layer': 'real Alerta API plus asynchronously observed projection',
        'environment': 'isolated_synthetic_lab', 'catalogue_result': 'MANUAL_GATE_PENDING',
        'observations_result': 'PASS' if all(r['api_ok'] and r['projection_state'] == 'ok' for r in results) else 'FAIL',
        'count': args.count, 'api_concurrency': args.concurrency, 'elapsed_seconds': time.monotonic() - started,
        'runtime': {'python': platform.python_version(), 'platform': platform.system() + '-' + platform.machine()},
        'api_seconds': {'p50': percentile(api, .5), 'p95': percentile(api, .95), 'max': max(api)},
        'projection_observed_seconds': {'p50': percentile(projection, .5), 'p95': percentile(projection, .95),
                                         'max': max(projection) if projection else None},
        'http_409_count': sum(r['http_status'] == 409 for r in results),
        'api_error_count': sum(not r['api_ok'] for r in results), 'projection_counts': counts,
        'unmeasured': ['exact validation-plus-write duration (join ledger telemetry)', 'PHP-FPM queue/load',
                       'PostgreSQL load and locks', 'PgBouncer statistics', 'cross-site behavior',
                       'exact commit-to-source latency', 'database-enforced delivery slot concurrency'],
        'limitations': 'Observed projection latency includes polling delay. No live deployment gate is passed.',
        'actions': results,
    }
    return report, 0 if report['observations_result'] == 'PASS' else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm-isolated-lab', action='store_true', required=True)
    parser.add_argument('--base-url', required=True, help='literal loopback Alerta API URL')
    parser.add_argument('--manifest', required=True, help='fresh synthetic alert manifest JSON')
    parser.add_argument('--count', type=int, choices=(50, 200), required=True)
    parser.add_argument('--token-env', default='ZABALACK_LAB_BEARER_TOKEN', help='environment variable name, never token text')
    parser.add_argument('--concurrency', type=int, default=8, choices=range(1, 33))
    parser.add_argument('--projection-timeout', type=float, default=120)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not 1 <= args.projection_timeout <= 600:
        parser.error('projection timeout must be 1..600 seconds')
    try:
        report, result = run(args)
    except (ValueError, OSError, json.JSONDecodeError):
        parser.error('invalid lab input; inspect manifest/URL/protected token reference without printing credentials')
    Path(args.output).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('test_id', 'count', 'observations_result', 'catalogue_result', 'api_error_count')}))
    return result


if __name__ == '__main__':
    sys.exit(main())
