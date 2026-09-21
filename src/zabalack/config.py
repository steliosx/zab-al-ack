"""Strict inert JSON configuration; secret references are read only when needed."""
from __future__ import annotations
import dataclasses
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

class ConfigError(ValueError):
    pass

@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    external_writes_enabled: bool = False
    capture_bypass: bool = False
    bypass_change_ref: str = ""
    config_revision: str = "UNCONFIGURED"
    policy_revision: str = "1"
    deployment_id: str = "REQUIRED_FOR_DEPLOYMENT"
    database_name: str = "REQUIRED_FOR_DEPLOYMENT"
    probe_dsn_file: str = ""
    worker_dsn_file: str = ""
    operator_dsn_file: str = ""
    signing_kid: str = ""
    signing_keys: dict[str, str] = field(default_factory=dict)
    cohorts: tuple[dict[str, Any], ...] = ()
    ingestion_mappings: dict[str, dict[str, str]] = field(default_factory=dict)
    endpoint_registry: dict[str, dict[str, Any]] = field(default_factory=dict)
    admin_exceptions: tuple[str, ...] = ()
    approved_versions: tuple[str, ...] = ("7.0.0",)
    capture_lifetime_seconds: int = 60
    execution_lifetime_seconds: int = 14400
    idle_poll_seconds: float = .25
    delivery_slots: int = 2
    verifier_slots: int = 4
    verification_lease_seconds: int = 10
    delivery_lease_seconds: int = 30
    connect_timeout_seconds: float = 1
    read_timeout_seconds: float = 3
    overall_timeout_seconds: float = 5
    background_connect_timeout_seconds: float = 2
    background_read_timeout_seconds: float = 15
    background_overall_timeout_seconds: float = 20
    fast_cycle_seconds: int = 60
    fast_budget: int = 200
    # The retirement sweep's own statement budget: its scan of public.alerts
    # is bounded by the installed table, not by an index (see
    # docs/deployment_acceptance.md, T41). Seconds; the worker's other calls
    # keep their 3-second statement timeout.
    retirement_timeout_seconds: int = 30
    nightly_cycle_seconds: int = 86400
    recent_terminal_seconds: int = 86400
    host_inventory_seconds: int = 900
    health_cache_seconds: float = 5
    health_acquire_timeout_seconds: float = 1
    health_overall_timeout_seconds: float = 2
    response_limit_bytes: int = 1048576
    lab_mode: bool = False

    def redacted(self) -> dict[str, Any]:
        """Do not return usernames, secret paths or entire policy snapshots."""
        return {
            "enabled": self.enabled, "external_writes_enabled": self.external_writes_enabled,
            "capture_bypass": self.capture_bypass, "config_revision": self.config_revision,
            "policy_revision": self.policy_revision, "deployment_id": self.deployment_id,
            "cohort_count": len(self.cohorts), "instance_count": len(self.endpoint_registry),
            "verification_key_ids": sorted(self.signing_keys), "lab_mode": self.lab_mode,
        }

def read_protected_file(path: str, *, maximum: int = 16384) -> bytes:
    """Open regular, owner-controlled secret files without following symlinks."""
    if not path or not Path(path).is_absolute():
        raise ConfigError("secret_reference_must_be_absolute")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid not in (0, os.geteuid()):
            raise ConfigError("secret_file_permissions")
        value = os.read(fd, maximum + 1)
        if len(value) > maximum or not value:
            raise ConfigError("secret_file_size")
        return value
    finally:
        os.close(fd)

def strict_json_loads(raw: str | bytes | bytearray) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ConfigError("duplicate_json_key")
            result[key] = value
        return result
    def no_constant(value: str) -> NoReturn:
        raise ConfigError("nonfinite_json_number")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=no_constant)

def validate_endpoint(endpoint: dict[str, Any], *, lab_mode: bool = False) -> None:
    allowed = {"url", "token_file", "ca_file", "api_version", "logical_instance", "token_expires_at"}
    if not isinstance(endpoint, dict) or set(endpoint) - allowed:
        raise ConfigError("endpoint_shape")
    parsed = urlsplit(endpoint.get("url", ""))
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
        or parsed.query or parsed.fragment or parsed.path != "/api_jsonrpc.php"):
        raise ConfigError("endpoint_requires_exact_https_api_path")
    if not endpoint.get("ca_file") or not endpoint.get("token_file"):
        raise ConfigError("endpoint_secret_and_ca_required")
    for key in ("ca_file", "token_file"):
        if not Path(endpoint[key]).is_absolute():
            raise ConfigError("endpoint_paths_must_be_absolute")
    if not isinstance(endpoint.get("api_version"), str):
        raise ConfigError("endpoint_reference_version_required")
    # A registry entry is the only endpoint allowlist; no payload URL is accepted.

def validate_settings(settings: Settings) -> Settings:
    from .policy import validate_cohort
    for name in ("enabled", "external_writes_enabled", "capture_bypass", "lab_mode"):
        if type(getattr(settings, name)) is not bool:
            raise ConfigError("configuration_boolean_required")
    if settings.capture_lifetime_seconds != 60 or settings.execution_lifetime_seconds != 14400:
        raise ConfigError("lifetime_change_requires_reviewed_policy_revision")
    # These are exposed to make the tested SQL profile explicit, not as knobs
    # that silently diverge from database-backed coordination.
    if (settings.delivery_slots, settings.verifier_slots, settings.verification_lease_seconds,
            settings.delivery_lease_seconds) != (2, 4, 10, 30):
        raise ConfigError("coordination_profile_change_requires_reviewed_SQL_migration")
    if not 1 <= settings.verifier_slots <= 32 or not 1 <= settings.fast_budget <= 200:
        raise ConfigError("invalid_capacity")
    if (isinstance(settings.retirement_timeout_seconds, bool) or not isinstance(settings.retirement_timeout_seconds, int)
            or not 3 <= settings.retirement_timeout_seconds <= 900):
        raise ConfigError("retirement_timeout_bound")
    if not 0 < settings.health_cache_seconds <= 5:
        raise ConfigError("health_cache_bound")
    for name in ("connect_timeout_seconds", "read_timeout_seconds", "overall_timeout_seconds",
                 "background_connect_timeout_seconds", "background_read_timeout_seconds",
                 "background_overall_timeout_seconds", "health_acquire_timeout_seconds",
                 "health_overall_timeout_seconds", "idle_poll_seconds"):
        val = getattr(settings, name)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not 0 < val < 120:
            raise ConfigError("invalid_timeout")
    if settings.capture_bypass and not settings.bypass_change_ref:
        raise ConfigError("bypass_requires_change_reference")
    if not isinstance(settings.signing_keys, dict) or not isinstance(settings.ingestion_mappings, dict) or not isinstance(settings.endpoint_registry, dict):
        raise ConfigError("configuration_mapping_required")
    import re
    for kid, path in settings.signing_keys.items():
        if not isinstance(kid, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", kid) or not isinstance(path, str) or not Path(path).is_absolute():
            raise ConfigError("signing_key_reference_invalid")
    for login, mapping in settings.ingestion_mappings.items():
        if (not isinstance(login, str) or not login or not isinstance(mapping, dict)
                or set(mapping) != {"customer", "instance"}
                or any(not isinstance(value, str) or not value for value in mapping.values())):
            raise ConfigError("ingestion_mapping_invalid")
    if any(not isinstance(login, str) or not login for login in settings.admin_exceptions):
        raise ConfigError("admin_exception_invalid")
    if not isinstance(settings.cohorts, (tuple, list)):
        raise ConfigError("configuration_cohorts_required")
    seen = set()
    for cohort in settings.cohorts:
        validate_cohort(cohort)
        ident = (cohort["customer"], cohort["zabbix_instance"], cohort["zabbix_trigger_id"])
        if ident in seen:
            raise ConfigError("duplicate_cohort")
        seen.add(ident)
    for instance, endpoint in settings.endpoint_registry.items():
        validate_endpoint(endpoint, lab_mode=settings.lab_mode)
        # Required, not defaulted: `EndpointRegistry.resolve` refuses an entry
        # whose `logical_instance` is absent, so treating it as optional here
        # would report a configuration valid that can never deliver.
        if endpoint.get("logical_instance") != instance:
            raise ConfigError("endpoint_logical_instance_mismatch")
        if endpoint["api_version"] not in settings.approved_versions:
            raise ConfigError("unrecognised_reference_version")
    if settings.enabled:
        if not settings.signing_kid or settings.signing_kid not in settings.signing_keys:
            raise ConfigError("active_signing_key_missing")
        if not settings.probe_dsn_file or settings.config_revision == "UNCONFIGURED":
            raise ConfigError("capture_configuration_incomplete")
    if settings.external_writes_enabled:
        if not settings.enabled or not settings.worker_dsn_file or not settings.cohorts or not settings.endpoint_registry:
            raise ConfigError("external_write_configuration_incomplete")
        if settings.capture_bypass:
            raise ConfigError("bypass_cannot_enable_writes")
    return settings

def load_settings(path: str | os.PathLike[str]) -> Settings:
    raw = Path(path).read_bytes()
    if len(raw) > 1048576:
        raise ConfigError("configuration_size")
    data = strict_json_loads(raw)
    allowed = {f.name for f in dataclasses.fields(Settings)}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ConfigError("unknown_configuration_field")
    for field_name in ("cohorts", "admin_exceptions", "approved_versions"):
        if field_name in data:
            if not isinstance(data[field_name], list):
                raise ConfigError("configuration_list_required")
            data[field_name] = tuple(data[field_name])
    return validate_settings(Settings(**data))
