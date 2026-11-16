"""The published signing test vectors match crypto.py, and each one checks itself.

docs/signing-vectors-v1.json lets another signer implementation (alrust's Rust
adapter, spec/integration-hooks.md X1) compare its bytes with Zabalack's. The
keys in it are public test material built from a fixed text.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys

import pytest

from zabalack.crypto import BindingError, KeyUnavailable, canonical_bytes, sign_intent

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import signing_vectors  # noqa: E402

DATA = json.loads(signing_vectors.VECTOR_FILE.read_text())


def test_the_checked_in_file_is_exactly_what_crypto_py_produces_now():
    assert signing_vectors.VECTOR_FILE.read_text() == signing_vectors.render(signing_vectors.generate())


def key(length: int) -> bytes:
    text = DATA['test_keys']['key_text']
    return (text * (length // len(text) + 1)).encode('ascii')[:length]


@pytest.mark.parametrize('vector', DATA['vectors'], ids=[v['name'] for v in DATA['vectors']])
def test_each_vector_holds_with_an_independent_hmac(vector):
    data = canonical_bytes(vector['intent'])
    assert data.startswith(b'zabalack\x00v1\x00')
    assert len(data) == vector['canonical_length']
    assert hashlib.sha256(data).hexdigest() == vector['canonical_sha256']
    if 'canonical_hex' in vector:
        assert data.hex() == vector['canonical_hex']
    mac = hmac.new(key(vector['key_length']), data, hashlib.sha256).hexdigest()
    assert mac == vector['hmac_sha256_hex']
    assert vector['marker'] == f"[zabalack:v1 action={vector['intent']['action_id']} kid={vector['kid']} mac={mac}]"
    assert sign_intent(vector['intent'], vector['kid'], key(vector['key_length'])) == vector['marker']


def test_the_limit_vectors_sit_exactly_at_and_one_byte_over_the_payload_limit():
    prefix = len(b'zabalack\x00v1\x00')
    at_limit = next(v for v in DATA['vectors'] if v['name'] == 'payload-at-limit')
    assert at_limit['canonical_length'] - prefix == DATA['scheme']['payload_limit_bytes'] == 16384
    over = next(r for r in DATA['refusals'] if r['name'] == 'payload-over-limit')
    payload = json.dumps(over['intent'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    assert len(payload) == 16385


@pytest.mark.parametrize('refusal', DATA['refusals'], ids=[r['name'] for r in DATA['refusals']])
def test_each_refusal_is_refused_with_its_stable_reason(refusal):
    kind, reason = refusal['refusal'].split(':', 1)
    intent = json.loads(refusal['intent_json']) if 'intent_json' in refusal else refusal['intent']
    with pytest.raises((BindingError, KeyUnavailable)) as refused:
        sign_intent(intent, refusal['kid'], key(refusal['key_length']))
    assert (type(refused.value).__name__, str(refused.value)) == (kind, reason)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key_name, item in value.items():
            yield key_name
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_the_file_holds_no_unpaired_surrogate_so_strict_parsers_load_it():
    # A strict parser (Rust's serde_json) refuses the whole file on one lone
    # surrogate escape; only the inner intent_json text may carry one.
    for text in _strings(DATA):
        text.encode('utf-8')
    surrogate = next(r for r in DATA['refusals'] if r['name'] == 'unpaired-surrogate')
    assert 'intent' not in surrogate and '\\ud800' in surrogate['intent_json']
    assert json.loads(surrogate['intent_json'])['actor']['login'] == 'op-\ud800'


def test_the_file_is_ascii_and_names_its_keys_as_public_test_material():
    raw = signing_vectors.VECTOR_FILE.read_bytes()
    assert raw.isascii()
    assert 'never for production' in DATA['test_keys']['key_text']
    assert {v['key_length'] for v in DATA['vectors']} == {32, 128}
