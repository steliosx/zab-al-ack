"""Inbound contract enforcement against Alerta 9.1 PluginBase."""
from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

from flask import current_app, g, has_request_context
from alerta.exceptions import AlertaException, RejectException
from alerta.plugins import PluginBase

from .config import Settings, load_settings
from .policy import PolicyError, select_cohort, validate_source


# The old prefix is stripped for privacy only; it cannot authorise delivery.
RESERVED_PREFIXES = ("zabbix_ack_sync", "zabalack_", "ackline_")
RESERVED_FIELDS = {"zabbix_ack_intent", "zabbix_ack_proof", "ack_intent", "ack_proof"}


def settings_for(config: Any, injected: Settings | None = None) -> Settings:
    if injected is not None:
        return injected
    path = (config or {}).get("ZABALACK_CONFIG_FILE")
    if not path:
        return Settings()
    try:
        return load_settings(path)
    except Exception:
        # Configuration errors must not be swallowed by Alerta's generic plugin
        # exception handler. No paths or secret values enter the response.
        raise AlertaException("zabalack_configuration_invalid", code=503) from None


def strip_reserved(attributes: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attributes.items()
            if key not in RESERVED_FIELDS and not key.startswith(RESERVED_PREFIXES)}


def project_skip(alert: Any, reason: str, settings: Settings) -> Any:
    alert.attributes.pop("zabbix_ack_sync_intent", None)
    alert.attributes.update({
        "zabbix_ack_sync": "skipped",
        "zabbix_ack_sync_reason": reason,
        "zabbix_ack_sync_config_revision": settings.config_revision,
        "zabbix_ack_sync_local_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    })
    return alert


class ZabbixNormaliser(PluginBase):
    """Validate customer-bound inbound source facts without external I/O."""

    def __init__(self, name: str | None = None, *, settings: Settings | None = None) -> None:
        super().__init__(name)
        self.settings = settings

    def pre_receive(self, alert: Any, **kwargs: Any) -> Any:
        alert.attributes = strip_reserved(dict(alert.attributes or {}))
        if alert.attributes.get("source_system") != "zabbix":
            return alert
        settings = settings_for(kwargs.get("config"), self.settings)
        if not settings.enabled:
            return project_skip(alert, "skipped_disabled", settings)
        if not has_request_context():
            raise RejectException("zabbix_ingestion_requires_authenticated_context")
        scopes = set(g.get("scopes", ()))
        mapping = settings.ingestion_mappings.get(g.get("login"))
        if (not current_app.config.get("AUTH_REQUIRED")
                or not current_app.config.get("CUSTOMER_VIEWS")):
            raise RejectException("zabbix_ingestion_tenant_gate")
        if (not mapping or g.get("user_id") is not None
                or "admin" in scopes or "admin:alerts" in scopes):
            raise RejectException("zabbix_ingestion_identity_policy")
        customer = alert.customer
        if (not customer or customer != mapping.get("customer")
                or customer not in g.get("customers", ())):
            raise RejectException("zabbix_ingestion_customer_mismatch")
        required = ("source_contract_version", "zabbix_instance", "zabbix_event_id", "zabbix_trigger_id")
        if any(not alert.attributes.get(key) for key in required):
            return project_skip(alert, "legacy_missing_mapping", settings)
        if alert.attributes.get("zabbix_instance") != mapping.get("instance"):
            raise RejectException("zabbix_ingestion_instance_mismatch")
        try:
            source = validate_source(alert.attributes)
        except PolicyError:
            return project_skip(alert, "legacy_missing_mapping", settings)
        # Preserve resource/event/correlation identity. Copy only normalized
        # contract keys, never any policy fields supplied by a producer.
        alert.attributes.update(source)
        cohort = select_cohort(settings, customer, source)
        if not cohort:
            return project_skip(alert, "skipped_cohort_policy", settings)
        if not cohort.get("one_to_one", False) or any(
            key in alert.attributes for key in ("zabbix_event_ids", "zabbix_trigger_ids", "source_members")
        ):
            return project_skip(alert, "skipped_ambiguous_source", settings)
        if settings.capture_bypass:
            project_skip(alert, "skipped_capture_bypass", settings)
            alert.attributes["zabbix_ack_sync_change_reference"] = settings.bypass_change_ref
        return alert

    def post_receive(self, alert: Any, **kwargs: Any) -> Any:
        return None

    def status_change(self, alert: Any, status: str, text: str, **kwargs: Any) -> Any:
        return alert, status, text
