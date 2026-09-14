"""Pure generated checks of the signed-capture trust boundary.

These properties establish binding and capture validity only. They do not prove
database capture, actor authorisation, delivery expiry, or remote execution.
All keys and proofs are synthetic and created at run time.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID

from hypothesis import given, strategies as st
import pytest

from zabalack.crypto import (
    BindingError,
    KeyUnavailable,
    append_marker,
    build_intent,
    extract_marker,
    sign_intent,
    timestamp,
    verify_capture,
)
from property.profiles import property_seed, property_settings


_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)
_UINT64_MAX = (1 << 64) - 1
_TOKEN = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-é", max_size=20)
_SOURCE_FIELDS = ("zabbix_instance", "zabbix_event_id", "zabbix_trigger_id")


@st.composite
def signed_packets(draw):
    """Create a valid baseline without sharing mutable source dictionaries."""
    issued = _EPOCH + timedelta(microseconds=draw(st.integers(0, 86_400_000_000)))
    customer = "synthetic-customer-" + draw(_TOKEN)
    source = {
        "zabbix_instance": "synthetic-zbx-" + str(draw(st.integers(0, 99999))),
        "zabbix_event_id": str(draw(st.integers(1, _UINT64_MAX))),
        "zabbix_trigger_id": str(draw(st.integers(1, _UINT64_MAX))),
    }
    actor = {
        "login": "synthetic-human-" + draw(_TOKEN),
        "customers": [customer],
        "admin": draw(st.booleans()),
        "auth_type": "user",
    }
    intent = build_intent(
        str(draw(st.uuids())), customer, source, actor,
        "synthetic-cohort-" + draw(_TOKEN), "synthetic-policy-" + draw(_TOKEN),
        now=issued, action_id=str(draw(st.uuids())),
    )
    kid = "synthetic-key-" + str(draw(st.integers(0, 99999)))
    key = draw(st.binary(min_size=32, max_size=64))
    capture = {
        "intent": intent,
        "marker": sign_intent(intent, kid, key),
        "alert_id": intent["alert_id"],
        "customer": customer,
        "source": deepcopy(source),
        "history_type": "ack",
        "history_user": actor["login"],
        "resulting_status": "ack",
        "captured_at": timestamp(issued + timedelta(seconds=1)),
    }
    return capture, {kid: key}, issued


def _different_uuid(value):
    return str(UUID(int=UUID(value).int ^ 1))


def _different_source_value(field, value):
    if field == "zabbix_instance":
        return value + "-other"
    return str(int(value) % _UINT64_MAX + 1)


def _assert_redacted(error, reason, capture):
    # A literal reason-code assertion is also a bound: no input, actor, proof,
    # or parser detail may be interpolated into the public exception text.
    assert str(error.value) == reason
    assert capture["marker"] not in str(error.value)
    assert capture["intent"]["actor"]["login"] not in str(error.value)
    assert capture["intent"]["customer"] not in str(error.value)


@property_seed()
@property_settings()
@given(packet=signed_packets())
def test_valid_capture_round_trip_preserves_the_frozen_snapshot(packet):
    capture, keys, issued = packet
    before = deepcopy(capture)
    verified = verify_capture(capture, keys, now=issued + timedelta(hours=3))
    assert verified == before["intent"]
    assert verified["source"] == before["source"]
    assert capture == before


_SIGNED_VALUE_MUTATIONS = (
    "action_id", "alert_id", "customer", *_SOURCE_FIELDS,
    "login", "customers", "admin", "cohort_revision", "policy_revision", "issued_at",
)


@pytest.mark.parametrize("field", _SIGNED_VALUE_MUTATIONS)
@property_seed()
@property_settings()
@given(packet=signed_packets())
def test_signed_value_changes_fail_even_when_capture_fields_are_consistent(field, packet):
    capture, keys, issued = packet
    forged = deepcopy(capture)
    intent = forged["intent"]
    if field == "action_id":
        old = intent[field]
        intent[field] = _different_uuid(old)
        forged["marker"] = forged["marker"].replace(old, intent[field], 1)
    elif field == "alert_id":
        intent[field] = _different_uuid(intent[field])
        intent["route"] = f'/alert/{intent[field]}/action'
        forged[field] = intent[field]
    elif field == "customer":
        intent[field] += "-other"
        forged[field] = intent[field]
    elif field in _SOURCE_FIELDS:
        intent["source"][field] = _different_source_value(field, intent["source"][field])
        forged["source"] = deepcopy(intent["source"])
    elif field == "login":
        intent["actor"][field] += "-other"
        forged["history_user"] = intent["actor"][field]
    elif field == "customers":
        intent["actor"][field] = sorted(intent["actor"][field] + ["synthetic-added-scope"])
    elif field == "admin":
        intent["actor"][field] = not intent["actor"][field]
    elif field == "issued_at":
        # Preserve both schema lifetimes and the observed capture interval, so
        # only authentication can reject this otherwise self-consistent shift.
        shifted = issued + timedelta(microseconds=1)
        intent["issued_at"] = timestamp(shifted)
        intent["capture_expires_at"] = timestamp(shifted + timedelta(seconds=60))
        intent["execution_expires_at"] = timestamp(shifted + timedelta(hours=4))
        forged["captured_at"] = timestamp(shifted + timedelta(seconds=1))
    else:
        intent[field] += "-other"

    with pytest.raises(BindingError) as error:
        verify_capture(forged, keys, now=issued)
    _assert_redacted(error, "mac_invalid", forged)
    # A forged sibling must not mutate or consume the original signed capture.
    assert verify_capture(capture, keys, now=issued) == capture["intent"]


@pytest.mark.parametrize("field,reason", [
    ("version", "intent_version"),
    ("auth_type", "actor_shape"),
    ("route", "route_action_invalid"),
    ("action", "route_action_invalid"),
    ("capture_expires_at", "capture_lifetime_invalid"),
    ("execution_expires_at", "execution_lifetime_invalid"),
])
@property_seed()
@property_settings()
@given(packet=signed_packets(), delta=st.integers(1, 1_000_000))
def test_changes_to_fixed_signed_contract_fields_are_rejected(field, reason, packet, delta):
    capture, keys, issued = packet
    forged = deepcopy(capture)
    intent = forged["intent"]
    if field == "version":
        intent[field] = 2
    elif field == "auth_type":
        intent["actor"][field] = "api_key"
    elif field == "route":
        intent[field] = f'/alert/{intent["alert_id"]}/status'
    elif field == "action":
        intent[field] = "shelve"
    else:
        lifetime = timedelta(seconds=60) if field == "capture_expires_at" else timedelta(hours=4)
        intent[field] = timestamp(issued + lifetime + timedelta(microseconds=delta))
    with pytest.raises(BindingError) as error:
        verify_capture(forged, keys, now=issued)
    _assert_redacted(error, reason, forged)


@pytest.mark.parametrize("field", ("alert_id", "customer", *_SOURCE_FIELDS))
@property_seed()
@property_settings()
@given(packet=signed_packets())
def test_valid_signature_cannot_authorise_another_observed_source_identity(field, packet):
    capture, keys, issued = packet
    substituted = deepcopy(capture)
    if field == "alert_id":
        substituted[field] = _different_uuid(substituted[field])
    elif field == "customer":
        substituted[field] += "-other"
    else:
        substituted["source"][field] = _different_source_value(field, substituted["source"][field])
    assert substituted["intent"] == capture["intent"]
    with pytest.raises(BindingError) as error:
        verify_capture(substituted, keys, now=issued)
    _assert_redacted(error, "binding_mismatch", substituted)


@pytest.mark.parametrize("field", ("history_type", "resulting_status", "history_user"))
@property_seed()
@property_settings()
@given(packet=signed_packets(), suffix=_TOKEN)
def test_ack_proof_requires_matching_observed_history_and_actor(field, packet, suffix):
    capture, keys, issued = packet
    substituted = deepcopy(capture)
    substituted[field] += "-unrelated-" + suffix
    with pytest.raises(BindingError) as error:
        verify_capture(substituted, keys, now=issued)
    _assert_redacted(error, "history_actor_mismatch", substituted)


@property_seed()
@property_settings()
@given(packet=signed_packets(), position=st.integers(0, 63))
def test_changing_any_mac_digit_rejects_the_marker_without_disclosing_it(packet, position):
    capture, keys, issued = packet
    forged = deepcopy(capture)
    # The signed marker's final 64 characters before ']' are its hex MAC.
    index = len(forged["marker"]) - 65 + position
    old = forged["marker"][index]
    replacement = "1" if old == "0" else "0"
    forged["marker"] = forged["marker"][:index] + replacement + forged["marker"][index + 1:]
    with pytest.raises(BindingError) as error:
        verify_capture(forged, keys, now=issued)
    _assert_redacted(error, "mac_invalid", forged)


@property_seed()
@property_settings()
@given(packet=signed_packets())
def test_valid_marker_from_another_action_cannot_be_transplanted(packet):
    capture, keys, issued = packet
    unrelated = deepcopy(capture["intent"])
    unrelated["action_id"] = _different_uuid(unrelated["action_id"])
    kid, key = next(iter(keys.items()))
    transplanted = deepcopy(capture)
    transplanted["marker"] = sign_intent(unrelated, kid, key)
    with pytest.raises(BindingError) as error:
        verify_capture(transplanted, keys, now=issued)
    _assert_redacted(error, "binding_mismatch", transplanted)


@property_seed()
@property_settings()
@given(packet=signed_packets())
def test_wrong_available_key_is_invalid_but_missing_rotation_key_is_unavailable(packet):
    capture, keys, issued = packet
    kid, key = next(iter(keys.items()))
    changed_key = bytes([key[0] ^ 1]) + key[1:]
    with pytest.raises(BindingError) as wrong_key:
        verify_capture(capture, {kid: changed_key}, now=issued)
    _assert_redacted(wrong_key, "mac_invalid", capture)
    with pytest.raises(KeyUnavailable) as missing_key:
        verify_capture(capture, {}, now=issued)
    _assert_redacted(missing_key, "verification_key_unavailable", capture)


@pytest.mark.parametrize("malformation", ("truncated", "trailing", "duplicate", "invalid_uuid"))
@property_seed()
@property_settings()
@given(packet=signed_packets(), note=_TOKEN)
def test_marker_extraction_accepts_only_one_complete_suffix(malformation, packet, note):
    capture, _, _ = packet
    marker = capture["marker"]
    text = append_marker("synthetic-note-" + note, marker)
    assert extract_marker(text) == marker
    assert append_marker(text, marker) == text
    if malformation == "truncated":
        bad_text = text[:-1]
    elif malformation == "trailing":
        bad_text = text + "\nsynthetic-extra-" + note
    elif malformation == "duplicate":
        bad_text = text + "\n" + marker
    else:
        bad_text = text.replace(capture["intent"]["action_id"], "synthetic-invalid-uuid", 1)
    with pytest.raises(BindingError) as error:
        extract_marker(bad_text)
    _assert_redacted(error, "marker_malformed", capture)


@pytest.mark.parametrize("observed_us", (-1, 0, 1, 59_999_999, 60_000_000, 60_000_001))
@property_seed()
@property_settings()
@given(packet=signed_packets(), worker_delay_seconds=st.integers(61, 60 * 60 * 24 * 30))
def test_capture_interval_boundaries_depend_on_observed_time_not_worker_delay(
    observed_us, packet, worker_delay_seconds,
):
    capture, keys, issued = packet
    capture["captured_at"] = timestamp(issued + timedelta(microseconds=observed_us))
    # Verification is deliberately repeatable even beyond execution expiry;
    # delivery owns its separate four-hour gate, and reconciliation can verify.
    for worker_time in (issued, issued + timedelta(seconds=worker_delay_seconds)):
        if 0 <= observed_us <= 60_000_000:
            assert verify_capture(capture, keys, now=worker_time) == capture["intent"]
        else:
            with pytest.raises(BindingError) as error:
                verify_capture(capture, keys, now=worker_time)
            _assert_redacted(error, "capture_expired", capture)


@property_seed()
@property_settings()
@given(
    packet=signed_packets(),
    observed_us=st.integers(-60_000_000, 120_000_000),
    worker_delay_seconds=st.integers(0, 60 * 60 * 24 * 30),
)
def test_capture_lifetime_matches_the_observed_interval_for_generated_times(
    packet, observed_us, worker_delay_seconds,
):
    capture, keys, issued = packet
    capture["captured_at"] = timestamp(issued + timedelta(microseconds=observed_us))
    worker_time = issued + timedelta(seconds=worker_delay_seconds)
    if 0 <= observed_us <= 60_000_000:
        assert verify_capture(capture, keys, now=worker_time) == capture["intent"]
    else:
        with pytest.raises(BindingError) as error:
            verify_capture(capture, keys, now=worker_time)
        _assert_redacted(error, "capture_expired", capture)
