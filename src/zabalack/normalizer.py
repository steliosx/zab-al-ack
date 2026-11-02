"""Inbound contract enforcement against Alerta 9.1 PluginBase."""
from __future__ import annotations

from typing import Any, Callable
from datetime import datetime, timezone
import logging

from flask import current_app, g, has_request_context, request
from alerta.exceptions import AlertaException, RejectException
from alerta.plugins import PluginBase

from .config import Settings, load_settings
from .policy import PolicyError, select_cohort, validate_source


# The old prefix is stripped for privacy only; it cannot authorise delivery.
RESERVED_PREFIXES = ("zabbix_ack_sync", "zabalack_", "ackline_")
RESERVED_FIELDS = {"zabbix_ack_intent", "zabbix_ack_proof", "ack_intent", "ack_proof"}

# F1 (migration 015): the single-use hand-off from pre_receive to post_receive of
# one authenticated POST /alert ingestion. Owned by Zabalack; request-scoped.
INGESTION_SLOT = "zabalack_ingestion_slot"
BIND_REASONS = frozenset({"no_handoff", "endpoint_not_receive", "receipt_mismatch",
                          "natural_key_mismatch", "snapshot_mismatch", "database_unavailable"})
SOURCE_KEYS = ("zabbix_instance", "zabbix_event_id", "zabbix_trigger_id")
log = logging.getLogger(__name__)


def bind_source(alert_id: str, receive_id: str, receive_time: Any, customer: str,
                source: dict[str, Any]) -> str:
    """Record the binding on the request's own Alerta connection, committed here.

    `zabalack.bind_source()` is granted to the role Alerta connects as and to no
    other. It reads nothing in public.alerts; any failure rolls back and nothing
    durable is written.
    """
    from alerta.app import db
    connection = db.get_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT zabalack.bind_source(%s,%s,%s,%s,%s,%s,%s)",
                           (alert_id, receive_id, receive_time, customer, source["zabbix_instance"],
                            source["zabbix_event_id"], source["zabbix_trigger_id"]))
            row = cursor.fetchone()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return str(row[0])


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

    def __init__(self, name: str | None = None, *, settings: Settings | None = None,
                 binder: Callable[..., str] | None = None) -> None:
        super().__init__(name)
        self.settings = settings
        # The binding write. Injected only by tests with no PostgreSQL behind them.
        self.binder = binder if binder is not None else bind_source

    def pre_receive(self, alert: Any, **kwargs: Any) -> Any:
        # F1: clear the hand-off first, on every call and every branch, so a slot
        # can only ever describe the ingestion this call validates.
        if has_request_context():
            g.pop(INGESTION_SLOT, None)
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
        # F1: the last step of the successful authenticated path fills the
        # hand-off. Any exception or skip before this leaves it empty.
        setattr(g, INGESTION_SLOT, {
            "endpoint": request.endpoint, "receive_id": str(alert.id), "customer": customer,
            "environment": alert.environment, "resource": alert.resource, "event": alert.event,
            "source": {key: source[key] for key in SOURCE_KEYS}})
        return alert

    def post_receive(self, alert: Any, **kwargs: Any) -> Any:
        """F1: bind the source of one authenticated POST /alert ingestion.

        Consumes the hand-off once, binds only when the returned alert is the one
        pre_receive validated, never raises into ingestion, never writes
        public.alerts, and returns None.
        """
        slot = g.pop(INGESTION_SLOT, None) if has_request_context() else None
        reason = self._bind(alert, slot)
        if reason is not None:
            log.warning("zabalack_binding_skipped reason=%s alert=%s", reason, getattr(alert, "id", None))
        return None

    def _bind(self, alert: Any, slot: Any) -> str | None:
        if not isinstance(slot, dict):
            return "no_handoff"
        if slot.get("endpoint") != "api.receive":
            return "endpoint_not_receive"
        if str(getattr(alert, "last_receive_id", None)) != slot.get("receive_id"):
            return "receipt_mismatch"
        if (alert.customer, alert.environment, alert.resource, alert.event) != (
                slot.get("customer"), slot.get("environment"), slot.get("resource"), slot.get("event")):
            return "natural_key_mismatch"
        attributes = alert.attributes or {}
        if any(attributes.get(key) != slot["source"].get(key) for key in SOURCE_KEYS):
            return "snapshot_mismatch"
        try:
            self.binder(str(alert.id), str(alert.last_receive_id), alert.last_receive_time,
                        alert.customer, slot["source"])
        except Exception:
            return "database_unavailable"
        return None

    def status_change(self, alert: Any, status: str, text: str, **kwargs: Any) -> Any:
        return alert, status, text
