"""T7 of the S2 acceptance (lab/webhook_transport_acceptance.py no_redirect_check) judged by its own verdict.

The engine runs exercise the shipped webhook; these tests exercise T7 itself, with a synthetic HTTP connection,
so that a response the webhook refuses can never make T7 pass (review of 8211b1b, R4-T7).
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('webhook_transport_acceptance', ROOT / 'lab/webhook_transport_acceptance.py')
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)

GOOD = b'{"status":"ok"}'
# Astra's exact R4 case: the good body padded with ASCII spaces to one byte over the webhook's 1 MiB limit.
OVER_LIMIT = GOOD + b' ' * (1048577 - len(GOOD))
OVER_LIMIT_SHA256 = '083d6d602aa30ae11283bb40dc7a23d73bc62c5e663de5e9c44188290762210a'


def run_t7(monkeypatch, status, body, location=None):
    reads = []

    class Response:
        def read(self, limit):
            reads.append(limit)
            return body[:limit]

        def getheader(self, name):
            return location if name == 'Location' else None

    response = Response()
    response.status = status

    class Connection:
        def __init__(self, *unused, **unused_too):
            pass

        def request(self, *unused, **unused_too):
            pass

        def getresponse(self):
            return response

        def close(self):
            pass

    monkeypatch.setattr(acceptance.ssl, 'create_default_context', lambda **unused: None)
    monkeypatch.setattr(acceptance.http.client, 'HTTPSConnection', Connection)
    result = acceptance.no_redirect_check('synthetic.invalid', 443, '/api/alert', 'SYNTHETIC', b'{}')
    assert reads == [1048577]  # bounded: never more than one byte over the limit
    return result


def test_the_exact_oversized_r4_response_fails_t7(monkeypatch):
    assert len(OVER_LIMIT) == 1048577 and hashlib.sha256(OVER_LIMIT).hexdigest() == OVER_LIMIT_SHA256
    assert json.loads(OVER_LIMIT) == {'status': 'ok'}  # valid JSON with status ok: only the size makes it wrong
    result = run_t7(monkeypatch, 201, OVER_LIMIT)
    assert result['passes'] is False and result['application_reason'] == 'response_over_limit'


@pytest.mark.parametrize('status, body, passes, reason', [
    (201, GOOD, True, 'status_ok'),
    (200, GOOD + b' ' * (1048576 - len(GOOD)), True, 'status_ok'),   # exactly at the limit is still accepted
    (500, GOOD, False, 'status_ok'),                                  # application ok but not 2xx
    (201, b'{"status":', False, 'response_not_json'),
    (201, b'\xff\xfe', False, 'response_not_json'),
    (201, b'{"status":"error"}', False, 'status_not_ok'),
    (201, b'[]', False, 'response_not_an_object'),
    (201, b'null', False, 'response_not_an_object'),
    (201, b'"ok"', False, 'response_not_an_object'),
    (201, b'[' * 100000, False, 'response_not_json'),
], ids=['good', 'at-limit', 'non-2xx', 'invalid-json', 'invalid-utf8', 'status-error', 'array', 'null', 'string', 'deeply-nested'])
def test_t7_passes_only_an_initial_2xx_json_object_with_status_ok(monkeypatch, status, body, passes, reason):
    result = run_t7(monkeypatch, status, body)
    assert result['passes'] is passes and result['application_reason'] == reason


def test_an_initial_redirect_fails_t7_even_to_a_good_body(monkeypatch):
    result = run_t7(monkeypatch, 302, GOOD, location='https://elsewhere.invalid/api/alert')
    assert result['passes'] is False and result['location'] == 'https://elsewhere.invalid/api/alert'
