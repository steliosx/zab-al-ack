"""Synthetic runtime-generated proof tests. No reusable proof is checked in."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import secrets
import pytest
from zabalack.crypto import (build_intent, sign_intent, verify_capture, canonical_bytes,
    append_marker, extract_marker, BindingError, KeyUnavailable, timestamp, MAX_NOTE_BYTES)
from zabalack.config import Settings, load_settings, ConfigError, strict_json_loads, read_protected_file
from zabalack.policy import validate_source, select_cohort, authorize_intent, PolicyError

@pytest.fixture
def packet():
    key = secrets.token_bytes(32)
    now = datetime.now(timezone.utc)
    source = dict(zabbix_instance="lab-zbx", zabbix_event_id="100", zabbix_trigger_id="200")
    actor = dict(login="synthetic-operator", customers=["lab-customer"], admin=False, auth_type="user")
    intent = build_intent(str(uuid4()), "lab-customer", source, actor, "1", "1", now)
    capture = dict(intent=intent, marker=sign_intent(intent,"lab-key",key), alert_id=intent["alert_id"],
                   customer=intent["customer"], source=source, history_type="ack", history_user=actor["login"],
                   resulting_status="ack", captured_at=timestamp(now+timedelta(seconds=1)))
    return capture, {"lab-key":key}

@pytest.fixture
def cohort():
    return dict(customer="lab-customer",zabbix_instance="lab-zbx",zabbix_trigger_id="200",host_ids=["300"],
        group_ids=["400"],revision="1",policy_revision="1",enabled=True,one_to_one=True,
        comment_enabled=True,allow_admin=False)

def test_T9_timely_capture_delayed_worker(packet):
    capture,keys=packet
    assert verify_capture(capture,keys,now=datetime.now(timezone.utc)+timedelta(hours=2)) == capture["intent"]

@pytest.mark.parametrize("field,value",[("customer","outside"),("alert_id",str(uuid4())),("history_type","status"),
    ("history_user","other-person"),("resulting_status","open")])
def test_T8_snapshot_actor_history_binding(packet,field,value):
    capture,keys=packet
    capture[field]=value
    with pytest.raises(BindingError): verify_capture(capture,keys)

def test_T10_capture_snapshot_is_immutable(packet):
    capture,keys=packet
    rewritten=deepcopy(capture)
    rewritten["source"]["zabbix_event_id"]="101"
    with pytest.raises(BindingError): verify_capture(rewritten,keys)
    assert verify_capture(capture,keys)["source"]["zabbix_event_id"]=="100"

def test_T8_forged_action_does_not_validate(packet):
    capture,keys=packet
    bad=deepcopy(capture)
    bad["intent"]["actor"]["login"]="forger"
    with pytest.raises(BindingError,match="mac_invalid"): verify_capture(bad,keys)
    assert verify_capture(capture,keys)

def test_T14_missing_intent(packet):
    capture,keys=packet
    del capture["intent"]
    with pytest.raises(BindingError,match="intent_missing"):verify_capture(capture,keys)

@pytest.mark.parametrize("marker",["[zabalack:","[zabalack:v1 action=garbage]","[zabalack:"+"x"*257])
def test_T14_malformed_bounded_markers(packet,marker):
    capture,keys=packet
    capture["marker"]=marker
    with pytest.raises(BindingError,match="marker_malformed"):verify_capture(capture,keys)

def test_canonicalization_ignores_object_order(packet):
    capture,_=packet
    i=capture["intent"]
    assert canonical_bytes(i)==canonical_bytes(dict(reversed(list(i.items()))))
    assert canonical_bytes(i).startswith(b"zabalack\x00v1\x00")

def test_unknown_extra_signed_field_is_rejected(packet):
    capture,_=packet
    capture["intent"]["callback"]="https://outside.invalid/"
    with pytest.raises(BindingError,match="intent_shape"):canonical_bytes(capture["intent"])

def test_key_unavailability_is_hold_not_forgery(packet):
    capture,_=packet
    with pytest.raises(KeyUnavailable):verify_capture(capture,{})

def test_capture_time_uses_database_clock(packet):
    capture,keys=packet
    from zabalack.crypto import parse_time
    capture["captured_at"]=timestamp(parse_time(capture["intent"]["issued_at"])+timedelta(seconds=61))
    with pytest.raises(BindingError,match="capture_expired"):verify_capture(capture,keys)

def test_proof_suffix_after_note_and_duplicate_callback(packet):
    capture,_=packet
    result=append_marker("synthetic note",capture["marker"])
    assert extract_marker(result)==capture["marker"]
    assert append_marker(result,capture["marker"])==result
    with pytest.raises(BindingError):extract_marker(result+" trailing text")
    with pytest.raises(BindingError):append_marker("[zabalack:forged]",capture["marker"])

def test_unicode_note_byte_bound(packet):
    capture,_=packet
    with pytest.raises(BindingError):append_marker("é"*(MAX_NOTE_BYTES//2+1),capture["marker"])

def test_T11a_authorized_customer_cohort_only(packet,cohort):
    capture,_=packet
    cohort["customer"]="outside"
    with pytest.raises(PolicyError):authorize_intent(capture["intent"],cohort)

def test_T11b_trusted_operator_scope_is_allowed(packet,cohort):
    capture,_=packet
    authorize_intent(capture["intent"],cohort)
    # No claim of protected original ingestion lineage.
    changed=deepcopy(capture["intent"])
    changed["source"]["zabbix_event_id"]="101"
    authorize_intent(changed,cohort)

def test_T33_drifted_cohort_fails(packet,cohort):
    capture,_=packet
    cohort["host_drift"]=True
    with pytest.raises(PolicyError):authorize_intent(capture["intent"],cohort)

def test_top_level_customer_required(cohort):
    settings=Settings(cohorts=(cohort,))
    assert select_cohort(settings,None,cohort) is None

@pytest.mark.parametrize("bad",["{EVENT.ID}","0",0,True,"-1","1.0","1e9","18446744073709551616"])
def test_source_ids_reject_macros_and_noncanonical_values(bad):
    attrs=dict(source_system="zabbix",zabbix_instance="lab",zabbix_event_id=bad,zabbix_trigger_id="20",
        zabbix_cause_event_id=None,zabbix_recovery_event_id=None,source_contract_version="1")
    with pytest.raises(PolicyError):validate_source(attrs)

def test_recovery_id_cannot_equal_problem_id():
    attrs=dict(source_system="zabbix",zabbix_instance="lab",zabbix_event_id="10",zabbix_trigger_id="20",
        zabbix_cause_event_id=None,zabbix_recovery_event_id="10",source_contract_version="1")
    with pytest.raises(PolicyError):validate_source(attrs)

def test_inert_production_config():
    from pathlib import Path
    path=Path(__file__).resolve().parents[2]/"config/production.example.json"
    settings=load_settings(path)
    assert not settings.external_writes_enabled and not settings.enabled
    assert not settings.cohorts and not settings.endpoint_registry

def test_json_duplicate_fields_forbidden():
    with pytest.raises(ConfigError):strict_json_loads('{"enabled":false,"enabled":true}')

def test_secret_permissions_and_symlink(tmp_path):
    p=tmp_path/"secret";p.write_bytes(b"x"*32);p.chmod(0o644)
    with pytest.raises(ConfigError):read_protected_file(str(p))
    p.chmod(0o600)
    assert len(read_protected_file(str(p)))==32
    link=tmp_path/"link";link.symlink_to(p)
    with pytest.raises(OSError):read_protected_file(str(link))

@pytest.mark.parametrize('prefix', ['[ackline:', '[ACKLINE:'])
def test_T19_rename_does_not_wrap_legacy_proof_in_new_signature(packet, prefix):
    capture, _ = packet
    with pytest.raises(BindingError, match='reserved_marker_in_note'):
        append_marker(prefix + 'synthetic-private-note]', capture['marker'])
