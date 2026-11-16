"""Generate Zabalack's version-1 signing test vectors (docs/signing-vectors-v1.json).

The vectors let another implementation of the signer (for example alrust's
Rust adapter, spec/integration-hooks.md X1) check its canonical bytes, HMAC
and marker byte for byte against src/zabalack/crypto.py. Every key is built
from the public text below and is for tests only; no production, lab or
evidence key is used or read.

    python tools/signing_vectors.py            # print the vectors
    python tools/signing_vectors.py --check    # compare with the checked-in file

tests/unit/test_signing_vectors.py runs the same comparison, so the file and
crypto.py cannot drift apart unnoticed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from zabalack.crypto import BindingError, KeyUnavailable, canonical_bytes, parse_marker, sign_intent  # noqa: E402

VECTOR_FILE = ROOT / 'docs/signing-vectors-v1.json'
KEY_TEXT = 'zabalack synthetic test key, public, never for production use. '
ALERT_ID = '3f6c2a0e-8d1b-4c5e-9a7f-1b2c3d4e5f60'
ACTION_ID = '9b8a7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d'


def test_key(length: int) -> bytes:
    """KEY_TEXT repeated and cut to length bytes (ASCII, so bytes equal characters)."""
    return (KEY_TEXT * (length // len(KEY_TEXT) + 1)).encode('ascii')[:length]


def base_intent(**changes: Any) -> dict[str, Any]:
    intent: dict[str, Any] = {
        'version': 1, 'action_id': ACTION_ID, 'alert_id': ALERT_ID, 'customer': 'customer-a',
        'source': {'zabbix_instance': 'zbx-prod.example:1', 'zabbix_event_id': '4012345',
                   'zabbix_trigger_id': '23456'},
        'actor': {'login': 'operator.one', 'customers': ['customer-a', 'customer-b'], 'admin': False,
                  'auth_type': 'user'},
        'route': f'/alert/{ALERT_ID}/action', 'action': 'ack', 'cohort_revision': 'cohort-r7',
        'policy_revision': 'policy-r3', 'issued_at': '2026-11-15T12:00:00.000000Z',
        'capture_expires_at': '2026-11-15T12:01:00.000000Z',
        'execution_expires_at': '2026-11-15T16:00:00.000000Z'}
    for path, value in changes.items():
        target = intent
        *parents, leaf = path.split('__')
        for parent in parents:
            target = target[parent]
        target[leaf] = value
    return intent


def sized_customers(total_payload_bytes: int) -> list[str]:
    """Actor customers that make the canonical JSON payload exactly total_payload_bytes long.

    Each '"' costs two payload bytes but one byte of its 128-byte bound, so
    escaping, not raw length, reaches the 16,384-byte payload limit.
    """
    def payload(customers: list[str]) -> int:
        # Sizing only, with crypto.py's own json.dumps settings: canonical_bytes
        # refuses an oversized payload, so it cannot measure one. The result is
        # checked against canonical_bytes (and its refusal) in generate().
        intent = base_intent(actor__customers=customers)
        return len(json.dumps(intent, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                              allow_nan=False).encode('utf-8'))

    customers = [f'q{index:03d}' + '"' * 124 for index in range(100)]
    excess = payload(customers) - total_payload_bytes
    if excess < 0:
        raise ValueError('target larger than the reachable payload')
    index = 99
    while excess > 0:
        # Each removed '"' shortens the payload by two bytes; a trailing 'x'
        # instead of a '"' shortens it by one.
        quotes = customers[index].count('"')
        remove = min(quotes, excess // 2)
        customers[index] = customers[index][:4] + '"' * (quotes - remove)
        excess -= 2 * remove
        if excess == 1:
            customers[index] = customers[index][:-1] + 'x'
            excess = 0
        index -= 1
    assert payload(customers) == total_payload_bytes
    return customers


def positive(name: str, description: str, intent: dict[str, Any], kid: str, key_length: int,
             include_hex: bool = True) -> dict[str, Any]:
    data = canonical_bytes(intent)
    marker = sign_intent(intent, kid, test_key(key_length))
    action_id, marker_kid, mac = parse_marker(marker)
    assert (action_id, marker_kid) == (intent['action_id'], kid)
    vector: dict[str, Any] = {
        'name': name, 'description': description, 'intent': intent, 'kid': kid,
        'key_length': key_length, 'canonical_length': len(data),
        'canonical_sha256': hashlib.sha256(data).hexdigest(), 'hmac_sha256_hex': mac, 'marker': marker}
    if include_hex:
        vector['canonical_hex'] = data.hex()
    return vector


def negative(name: str, description: str, intent: dict[str, Any], kid: str, key_length: int) -> dict[str, Any]:
    try:
        sign_intent(intent, kid, test_key(key_length))
    except (BindingError, KeyUnavailable) as error:
        return {'name': name, 'description': description, 'intent': intent, 'kid': kid,
                'key_length': key_length, 'refusal': f'{type(error).__name__}:{error}'}
    raise AssertionError(f'{name} was accepted')


def generate() -> dict[str, Any]:
    vectors = [
        positive('ascii-k32', 'ASCII intent; minimum key length (32 bytes).', base_intent(), 'lab', 32),
        positive('ascii-k128-kid-charset', 'Same intent; maximum key length (128 bytes); a kid using every '
                 'allowed character class.', base_intent(), 'Rot-2026.11_a', 128),
        positive('unicode-bmp-sorted-customers', 'Non-ASCII text is written as raw UTF-8, not \\u escapes '
                 '(ensure_ascii=False). Customers are sorted by code point, which is also UTF-8 byte order.',
                 base_intent(customer='Ärger GmbH', actor__login='zoë.ops',
                             actor__customers=['Zeta', 'alpha', 'Ärger GmbH', '日本']), 'lab', 32),
        positive('unicode-astral', 'A character outside the Basic Multilingual Plane is written as four '
                 'raw UTF-8 bytes, not a surrogate pair.', base_intent(actor__login='op-\U0001F600'), 'lab', 32),
        positive('json-escapes', 'Only the quotation mark and the backslash are escaped. "/", DEL (U+007F) '
                 'and U+2028 are written unescaped.',
                 base_intent(actor__login='o"brien\\ops/x\x7f y'), 'lab', 32),
        positive('admin-true-empty-customers', 'Boolean true and an empty customer list.',
                 base_intent(actor__admin=True, actor__customers=[]), 'lab', 32),
        positive('payload-at-limit', 'The JSON payload after the 12-byte prefix is exactly 16,384 bytes, '
                 'the largest accepted. canonical_hex is omitted; compare length and SHA-256.',
                 base_intent(actor__customers=sized_customers(16384)), 'lab', 32, include_hex=False),
    ]
    refusals = [
        negative('payload-over-limit', 'One payload byte over the limit (16,385) is refused.',
                 base_intent(actor__customers=sized_customers(16385)), 'lab', 32),
        negative('key-too-short', 'A 31-byte key is refused.', base_intent(), 'lab', 31),
        negative('key-too-long', 'A 129-byte key is refused.', base_intent(), 'lab', 129),
        negative('kid-invalid', 'A kid outside [A-Za-z0-9_.-]{1,32} is refused.', base_intent(), 'bad kid', 32),
        negative('customers-not-canonical', 'Actor customers that are not sorted and unique are refused.',
                 base_intent(actor__customers=['b', 'a']), 'lab', 32),
        negative('unpaired-surrogate', 'An unpaired UTF-16 surrogate (written here as the JSON escape '
                 '\\ud800) cannot be encoded as UTF-8 and is refused. A Rust String cannot hold it, so an '
                 'implementation that parses this file may report a parse error instead.',
                 base_intent(actor__login='op-\ud800'), 'lab', 32),
    ]
    return {
        'format': 'zabalack-signing-vectors', 'format_version': 1,
        'source': 'src/zabalack/crypto.py (canonical_bytes, sign_intent); generated by tools/signing_vectors.py',
        'scheme': {
            'canonical': "b'zabalack\\x00v1\\x00' + UTF-8 of json.dumps(intent, sort_keys=True, "
                         "separators=(',', ':'), ensure_ascii=False, allow_nan=False)",
            'payload_limit_bytes': 16384, 'mac': 'HMAC-SHA256 over the canonical bytes, lowercase hex',
            'marker': '[zabalack:v1 action=<action_id> kid=<kid> mac=<hmac_sha256_hex>]',
            'key_length_bytes': [32, 128], 'kid_pattern': '[A-Za-z0-9_.-]{1,32}'},
        'test_keys': {
            'construction': 'key_text repeated and cut to key_length bytes; ASCII, so one byte per character',
            'key_text': KEY_TEXT,
            'warning': 'Public test material. Never use these keys, or keys built this way, outside tests.'},
        'vectors': vectors, 'refusals': refusals}


def render(data: dict[str, Any]) -> str:
    # ensure_ascii keeps the file pure ASCII and represents the unpaired surrogate.
    return json.dumps(data, indent=2, ensure_ascii=True) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    text = render(generate())
    if args.check:
        same = VECTOR_FILE.read_text() == text
        print('signing vectors: ' + ('match' if same else 'DIFFER'))
        return 0 if same else 1
    if args.write:
        VECTOR_FILE.write_text(text)
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
