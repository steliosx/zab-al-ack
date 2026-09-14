"""Version-1 strict HMAC binding, independent of endpoints and database privileges."""
from __future__ import annotations
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping, TypedDict, cast
from .config import read_protected_file
from .policy import make_snapshot, PolicyError, SourceSnapshot


class ActorIdentity(TypedDict):
    login: str
    customers: list[str]
    admin: bool
    auth_type: Literal["user"]


class Intent(TypedDict):
    """Validated shape, not proof of a valid MAC or of actor authority.

    Datetimes are accepted by the shape validator; canonical JSON still rejects
    them. The constructor emits UTC strings, preserving the version-one wire.
    """

    version: Literal[1]
    action_id: str
    alert_id: str
    customer: str
    source: SourceSnapshot
    actor: ActorIdentity
    route: str
    action: Literal["ack"]
    cohort_revision: str
    policy_revision: str
    issued_at: str | datetime
    capture_expires_at: str | datetime
    execution_expires_at: str | datetime

INTENT_ATTRIBUTE = "zabbix_ack_sync_intent"
MAX_INTENT_BYTES = 16384
MAX_MARKER_BYTES = 256
MAX_HISTORY_BYTES = 65536
MAX_NOTE_BYTES = 8192
MARKER_PREFIX = "[zabalack:"
KID_RE = re.compile(r"[A-Za-z0-9_.-]{1,32}\Z")
MARKER_RE = re.compile(r"\[zabalack:v1 action=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) kid=([A-Za-z0-9_.-]{1,32}) mac=([0-9a-f]{64})\]\Z")
INTENT_KEYS = {"version", "action_id", "alert_id", "customer", "source", "actor", "route", "action",
               "cohort_revision", "policy_revision", "issued_at", "capture_expires_at", "execution_expires_at"}
SOURCE_KEYS = {"zabbix_instance", "zabbix_event_id", "zabbix_trigger_id"}
ACTOR_KEYS = {"login", "customers", "admin", "auth_type"}

class BindingError(ValueError):
    """Exception text is a stable redacted reason code, never input/proof text."""
    pass

class KeyUnavailable(Exception):
    pass

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise BindingError("timezone_required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

def parse_time(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            # PostgreSQL Alerta timestamps are UTC without zone, capture timestamps are aware.
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or len(value) > 40:
        raise BindingError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise BindingError("invalid_timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise BindingError("utc_timestamp_required")
    return parsed.astimezone(timezone.utc)

def _uuid(value: object) -> None:
    if not isinstance(value, str):
        raise BindingError("invalid_uuid")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise BindingError("invalid_uuid") from None

def _bounded(value: object, limit: int = 128) -> None:
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= limit or any(ord(c) < 32 for c in value):
        raise BindingError("invalid_string")

def validate_intent(intent: object) -> Intent:
    if not isinstance(intent, dict) or set(intent) != INTENT_KEYS:
        raise BindingError("intent_shape")
    if type(intent["version"]) is not int or intent["version"] != 1:
        raise BindingError("intent_version")
    _uuid(intent["action_id"])
    _uuid(intent["alert_id"])
    for key in ("customer", "cohort_revision", "policy_revision"):
        _bounded(intent[key])
    if not isinstance(intent["source"], dict) or set(intent["source"]) != SOURCE_KEYS:
        raise BindingError("source_shape")
    try:
        make_snapshot(intent["source"])
    except PolicyError:
        raise BindingError("source_shape") from None
    actor = intent["actor"]
    if not isinstance(actor, dict) or set(actor) != ACTOR_KEYS:
        raise BindingError("actor_shape")
    _bounded(actor["login"], 256)
    if (type(actor["admin"]) is not bool or actor["auth_type"] != "user"
        or not isinstance(actor["customers"], list) or len(actor["customers"]) > 100):
        raise BindingError("actor_shape")
    for customer in actor["customers"]:
        _bounded(customer)
    if actor["customers"] != sorted(set(actor["customers"])):
        raise BindingError("actor_customers_not_canonical")
    if intent["route"] != f'/alert/{intent["alert_id"]}/action' or intent["action"] != "ack":
        raise BindingError("route_action_invalid")
    issued = parse_time(intent["issued_at"])
    if parse_time(intent["capture_expires_at"]) - issued != timedelta(seconds=60):
        raise BindingError("capture_lifetime_invalid")
    if parse_time(intent["execution_expires_at"]) - issued != timedelta(hours=4):
        raise BindingError("execution_lifetime_invalid")
    # Cast only after all existing shape, identity, actor, and lifetime checks.
    return cast(Intent, intent)

def canonical_bytes(intent: Mapping[str, object]) -> bytes:
    validate_intent(intent)
    try:
        data = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError, TypeError):
        raise BindingError("intent_encoding") from None
    if len(data) > MAX_INTENT_BYTES:
        raise BindingError("intent_oversized")
    # Domain separation prevents a key's use in another protocol from becoming a valid proof.
    return b"zabalack\x00v1\x00" + data

def build_intent(alert_id: str, customer: str, source: Mapping[str, object], actor: Mapping[str, Any],
                 cohort_revision: str, policy_revision: str, now: datetime | None = None,
                 action_id: str | None = None) -> Intent:
    now = now or utcnow()
    actor = dict(actor)
    actor["customers"] = sorted(set(actor["customers"]))
    result: dict[str, Any] = {"version": 1, "action_id": action_id or str(uuid.uuid4()), "alert_id": alert_id,
              "customer": customer, "source": dict(source), "actor": actor,
              "route": f"/alert/{alert_id}/action", "action": "ack", "cohort_revision": cohort_revision,
              "policy_revision": policy_revision, "issued_at": timestamp(now),
              "capture_expires_at": timestamp(now + timedelta(seconds=60)),
              "execution_expires_at": timestamp(now + timedelta(hours=4))}
    canonical_bytes(result)
    # canonical_bytes has validated the shape without rewriting any payload.
    return cast(Intent, result)

def sign_intent(intent: Mapping[str, object], kid: str, key: bytes) -> str:
    if not isinstance(kid, str) or not KID_RE.fullmatch(kid):
        raise BindingError("key_id_invalid")
    if not isinstance(key, bytes) or len(key) < 32 or len(key) > 128:
        raise KeyUnavailable("signing_key_unavailable")
    mac = hmac.new(key, canonical_bytes(intent), hashlib.sha256).hexdigest()
    return f'[zabalack:v1 action={intent["action_id"]} kid={kid} mac={mac}]'

def parse_marker(marker: object) -> tuple[str, str, str]:
    if not isinstance(marker, str) or len(marker.encode("utf-8")) > MAX_MARKER_BYTES:
        raise BindingError("marker_malformed")
    match = MARKER_RE.fullmatch(marker)
    if not match:
        raise BindingError("marker_malformed")
    action_id, kid, mac = match.groups()
    _uuid(action_id)
    return action_id, kid, mac

def extract_marker(text: str) -> str:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_HISTORY_BYTES:
        raise BindingError("marker_malformed")
    start = text.find(MARKER_PREFIX)
    if start < 0 or text.find(MARKER_PREFIX, start + len(MARKER_PREFIX)) >= 0:
        raise BindingError("marker_malformed")
    marker = text[start:]
    parse_marker(marker)
    return marker

def append_marker(text: str, marker: str) -> str:
    parse_marker(marker)
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_NOTE_BYTES:
        raise BindingError("operator_note_bound")
    # Historical proofs remain private and must not be wrapped in a new proof.
    if "[ackline:" in text.lower():
        raise BindingError("reserved_marker_in_note")
    if MARKER_PREFIX in text:
        if text.endswith(marker) and text.count(MARKER_PREFIX) == 1:
            return text
        raise BindingError("reserved_marker_in_note")
    return f"{text}\n{marker}" if text else marker

def load_key_files(references: Mapping[str, str]) -> dict[str, bytes]:
    result = {}
    for kid, path in references.items():
        if not KID_RE.fullmatch(kid):
            raise KeyUnavailable("key_id_unavailable")
        try:
            key = read_protected_file(path, maximum=128)
            if len(key) < 32:
                raise ValueError
        except (OSError, ValueError):
            # Missing one rotation key must not prevent candidates signed with other valid keys.
            continue
        result[kid] = key
    return result

def verify_capture(capture: dict[str, Any], keys: Mapping[str, bytes], now: datetime | None = None) -> Intent:
    if not isinstance(capture, dict):
        raise BindingError("capture_shape")
    marker = capture.get("marker")
    action_id, kid, mac = parse_marker(marker)
    intent = capture.get("intent")
    if intent is None:
        raise BindingError("intent_missing")
    intent = validate_intent(intent)
    if action_id != intent["action_id"]:
        raise BindingError("binding_mismatch")
    data = canonical_bytes(intent)
    key = keys.get(kid)
    if key is None or not isinstance(key, bytes) or len(key) < 32:
        raise KeyUnavailable("verification_key_unavailable")
    if not hmac.compare_digest(hmac.new(key, data, hashlib.sha256).hexdigest(), mac):
        raise BindingError("mac_invalid")
    for field in ("alert_id", "customer", "source"):
        if capture.get(field) != intent[field]:
            raise BindingError("binding_mismatch")
    if (capture.get("history_type") != "ack" or capture.get("resulting_status") != "ack"
        or capture.get("history_user") != intent["actor"]["login"]):
        raise BindingError("history_actor_mismatch")
    observed = parse_time(capture.get("captured_at"))
    issued = parse_time(intent["issued_at"])
    if not issued <= observed <= parse_time(intent["capture_expires_at"]):
        raise BindingError("capture_expired")
    # Worker processing time does NOT govern capture expiry. Execution expiry is checked
    # separately by delivery and database before each source write (read-only reconciliation survives).
    return intent

def public_reference() -> str:
    return uuid.uuid4().hex
