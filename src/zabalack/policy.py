"""Pure bounded policy functions. No network and no secret resolution."""
from __future__ import annotations
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from .crypto import Intent


class SourceSnapshot(TypedDict):
    """The three validated identity fields covered by the version-one MAC."""

    zabbix_instance: str
    zabbix_event_id: str
    zabbix_trigger_id: str


class CohortSettings(Protocol):
    """Policy selection needs approved cohorts, never endpoint credentials."""

    @property
    def cohorts(self) -> Sequence[dict[str, Any]]: ...

SOURCE_KEYS = ("zabbix_instance", "zabbix_event_id", "zabbix_trigger_id")
SOURCE_CONTRACT_KEYS = ("source_system", *SOURCE_KEYS, "zabbix_cause_event_id", "zabbix_recovery_event_id", "source_contract_version")
ID_RE = re.compile(r"[1-9][0-9]{0,19}\Z")
INSTANCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")

class PolicyError(ValueError):
    pass

def numeric_id(value: object, *, nullable: bool = False) -> str | None:
    if nullable and value in (None, "", "0", 0):
        return None
    if not isinstance(value, str) or not ID_RE.fullmatch(value) or int(value) > 18446744073709551615:
        raise PolicyError("invalid_source_id")
    return value

def make_snapshot(attributes: Mapping[str, object]) -> SourceSnapshot:
    source = {key: attributes.get(key) for key in SOURCE_KEYS}
    if not isinstance(source["zabbix_instance"], str) or not INSTANCE_RE.fullmatch(source["zabbix_instance"]):
        raise PolicyError("invalid_logical_instance")
    numeric_id(source["zabbix_event_id"])
    numeric_id(source["zabbix_trigger_id"])
    # The existing checks above establish the exact three string-valued keys.
    return cast(SourceSnapshot, source)

def validate_source(attributes: dict[str, Any]) -> dict[str, Any]:
    if any(key not in attributes for key in SOURCE_CONTRACT_KEYS):
        raise PolicyError("legacy_missing_mapping")
    if attributes["source_system"] != "zabbix" or attributes["source_contract_version"] != "1":
        raise PolicyError("source_contract_invalid")
    result = dict(attributes)
    result.update(make_snapshot(attributes))
    for name in ("zabbix_cause_event_id", "zabbix_recovery_event_id"):
        result[name] = numeric_id(attributes[name], nullable=True)
    # Recovery event must never replace the problem target.
    if result["zabbix_recovery_event_id"] == result["zabbix_event_id"]:
        raise PolicyError("source_contract_invalid")
    return result

def validate_cohort(cohort: dict[str, Any]) -> None:
    required = {"customer", "zabbix_instance", "zabbix_trigger_id", "host_ids", "group_ids",
                "revision", "policy_revision", "enabled", "one_to_one", "comment_enabled", "allow_admin"}
    optional = {"host_groups", "host_names", "change_ref", "host_drift"}
    if not isinstance(cohort, dict) or required - set(cohort) or set(cohort) - required - optional:
        raise PolicyError("cohort_shape")
    for key in ("customer", "revision", "policy_revision"):
        if not isinstance(cohort[key], str) or not 1 <= len(cohort[key]) <= 128:
            raise PolicyError("cohort_identity")
    if not isinstance(cohort["zabbix_instance"], str) or not INSTANCE_RE.fullmatch(cohort["zabbix_instance"]):
        raise PolicyError("cohort_instance")
    numeric_id(cohort["zabbix_trigger_id"])
    for key in ("enabled", "one_to_one", "comment_enabled", "allow_admin"):
        if type(cohort[key]) is not bool:
            raise PolicyError("cohort_boolean")
    if "host_drift" in cohort and type(cohort["host_drift"]) is not bool:
        raise PolicyError("cohort_drift_boolean")
    for key in ("host_ids", "group_ids"):
        values = cohort[key]
        if not isinstance(values, list) or not 1 <= len(values) <= 100 or len(set(values)) != len(values):
            raise PolicyError("cohort_host_group_bound")
        for value in values:
            numeric_id(value)

def select_cohort(settings: CohortSettings, customer: str | None, source: Mapping[str, object]) -> dict[str, Any] | None:
    if not customer:
        return None
    for cohort in settings.cohorts:
        if (cohort.get("customer") == customer and cohort.get("zabbix_instance") == source.get("zabbix_instance")
            and cohort.get("zabbix_trigger_id") == source.get("zabbix_trigger_id") and cohort.get("enabled") is True):
            return cohort
    return None

def authorize_actor(actor: Mapping[str, Any], customer: str, cohort: Mapping[str, Any]) -> bool:
    if not customer or not isinstance(actor, dict) or actor.get("auth_type") != "user" or not actor.get("login"):
        return False
    if actor.get("admin") is True:
        return cohort.get("allow_admin") is True
    return actor.get("admin") is False and customer in actor.get("customers", [])

def authorize_intent(intent: Intent, cohort: dict[str, Any]) -> None:
    validate_cohort(cohort)
    source = intent["source"]
    if (cohort["customer"] != intent["customer"] or cohort["zabbix_instance"] != source["zabbix_instance"]
        or cohort["zabbix_trigger_id"] != source["zabbix_trigger_id"] or not cohort["enabled"]
        or not cohort["one_to_one"] or cohort.get("host_drift", False) or cohort["revision"] != intent["cohort_revision"]
        or cohort["policy_revision"] != intent["policy_revision"]):
        raise PolicyError("cohort_not_authorised")
    if not authorize_actor(intent["actor"], intent["customer"], cohort):
        raise PolicyError("skipped_actor_policy")
