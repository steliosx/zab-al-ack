"""Two-stage signing adapter for the inspected Alerta 9.1 dispatch path."""
from __future__ import annotations

from copy import deepcopy
from functools import wraps
from importlib.metadata import version as installed_version
import logging
import traceback
import json
from typing import Any, Callable
from uuid import UUID

from flask import current_app, g, has_request_context, request
from alerta.app import alarm_model
from alerta.exceptions import AlertaException, ApiError, InvalidAction
from alerta.plugins import PluginBase

from .config import Settings, read_protected_file
from . import query_guard
from .crypto import INTENT_ATTRIBUTE, build_intent, load_key_files, sign_intent
from .health import CaptureUnhealthy, ProbeConfig, get_probe
from .normalizer import project_skip, settings_for
from .policy import PolicyError, authorize_actor, make_snapshot, select_cohort, validate_source


def _contains_evidence(value: str) -> bool:
    return any(part in value.lower() for part in ("[zabalack:", "[ackline:", "zabbix_ack_sync_intent"))


class CaptureLogRedactor(logging.Filter):
    """Suppress SQL diagnostics carrying restricted candidate evidence."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Alerta's own `ContextFilter` attaches the raw request body and query
        # arguments to every record, and `JSONFormatter` emits both, so under
        # `LOG_FORMAT=json` the operator's acknowledgement note reaches the log
        # without ever passing through the message. The body is outside the data
        # inventory whatever it contains, so it is blanked unconditionally rather
        # than only when the evidence test matches.
        for attribute in ("data", "reqargs"):
            if hasattr(record, attribute):
                setattr(record, attribute, "-")
        try:
            message = record.getMessage()
            exception = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else ""
            if _contains_evidence(message + exception + (record.exc_text or "") + (record.stack_info or "")):
                record.msg = "zabalack_restricted_diagnostic_redacted"
                record.args = ()
                record.exc_info = None
                record.exc_text = None
                record.stack_info = None
        except Exception:
            record.msg, record.args = "zabalack_diagnostic_format_redacted", ()
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def _error_redact(value: Any) -> Any:
    if isinstance(value, str):
        return "zabalack_restricted_error_redacted" if _contains_evidence(value) else value
    if isinstance(value, dict):
        return {key: ("zabalack_restricted_error_redacted" if _contains_evidence(str(key))
                      else _error_redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_error_redact(item) for item in value]
    return value


def _filter_serialized_attributes(owner: Any) -> None:
    """Drop the reserved staging key from one serialising property's payload."""
    current = getattr(owner, "serialize", None)
    if getattr(getattr(current, "fget", None), "_zabalack_filtered", False):
        return
    original = current.fget

    def serialize(self: Any) -> Any:
        payload = original(self)
        attributes = payload.get("attributes") if isinstance(payload, dict) else None
        if isinstance(attributes, dict) and INTENT_ATTRIBUTE in attributes:
            payload["attributes"] = {key: value for key, value in attributes.items()
                                     if key != INTENT_ATTRIBUTE}
        return payload

    serialize._zabalack_filtered = True  # type: ignore[attr-defined]
    owner.serialize = property(serialize)


def exclude_intent_from_responses() -> None:
    """Keep restricted staging out of every serialised alert response (A5, T40).

    Reference Alerta builds alert response bodies from two independent
    serialising properties, and both carry the alert's current `attributes`:

    * `Alert.serialize` — the single-alert, create and collection responses.
    * `RichHistory.serialize` — `/alerts/history`. Its rows come from
      `SELECT … attributes … FROM alerts, unnest(history[…])`, so each entry
      carries the alert row's live attributes, not a historical copy.

    Patching only the first leaves the intent readable through the history
    endpoint. Both are filtered here. The `jsonp` decorator wraps whichever body
    the view produced, inside the view and before `after_request`, so filtering
    the payload ahead of serialisation covers both response modes for both.

    Removal is targeted to the one reserved staging key. The `_contains_evidence`
    scrubber is deliberately not reused: it also matches the committed history
    proof, which A1 retains in successful history responses. `RichHistory`'s
    `user` and `text` — the permitted attribution and that committed proof — are
    left untouched.

    The stored dictionary is never mutated. Each `serialize` returns a fresh
    payload whose `attributes` value is the live object, so a filtered copy is
    substituted into the payload alone: the capture trigger still reads the
    intent it needs, and the operator's own attributes are untouched.

    This is a partial mitigation. It closes response bodies, not the
    `attributes.<key>:<term>` search predicate, and not the interval in which a
    staged intent is committed and queryable before signing. It does not close
    A5 or T40.
    """
    from alerta.models.alert import Alert
    from alerta.models.history import RichHistory
    for owner in (Alert, RichHistory):
        _filter_serialized_attributes(owner)


def configure_app(app: Any) -> Any:
    """Install before serving, after the upstream factory configures logging.

    Alerta's reference PostgreSQL logger prints mogrified SQL, and its error
    response can include PostgreSQL DETAIL. This mandatory factory adapter
    redacts those channels. It does not hide proofs from committed history.
    """
    if app.extensions.get("zabalack_privacy_ready"):
        # The flag records that this app was configured; it does not record
        # that the control is still on the builder. `qb.init_app` rebinds
        # `qb.alerts` to a fresh class, so returning on the flag alone would
        # leave the reader restriction gone while readiness reports installed.
        # Reinstalling is idempotent when the guard is already there.
        restrict_intent_queries()
        return app
    redactor = CaptureLogRedactor()
    logger = app.logger
    logger.addFilter(redactor)
    while logger is not None:
        for handler in logger.handlers:
            handler.addFilter(redactor)
        logger = logger.parent

    @app.after_request
    def redact_error_response(response: Any) -> Any:
        if response.status_code >= 400 and response.is_json:
            body = response.get_json(silent=True)
            if body is not None:
                response.set_data(app.json.dumps(_error_redact(body)))
        return response

    @app.before_request
    def require_reader_query_guard() -> None:
        """Hold the reader boundary itself, not just the acknowledgement path.

        `take_action` asks whether the guard is live, but that only covers new
        acknowledgements. Every reader route — the collection GET, `/alerts/count`,
        the aggregate endpoints and `/alerts/history` — reaches storage through
        the same rebindable `qb.alerts`, and staged intents already committed
        stay searchable through them if a rebinding discarded the wrapper. This
        runs before any of those views, reinstalls the control when it can, and
        refuses the request when it cannot: a reader query is never served over
        an unguarded builder.
        """
        if _query_guard_live():
            return
        try:
            restrict_intent_queries()
        except Exception:  # pragma: no cover - reinstallation is best effort
            pass
        if not _query_guard_live():
            raise AlertaException("zabalack_privacy_adapter_required", code=503)

    exclude_intent_from_responses()
    # Raises if the guard is absent or did not take, so the flag below is only
    # ever set over a control that installed.
    restrict_intent_queries()
    _reinstall_guard_on_rebinding()
    app.extensions["zabalack_privacy_ready"] = True
    return app


def _reinstall_guard_on_rebinding() -> None:
    """Follow `qb.alerts` when Alerta replaces it.

    `QueryBuilder.init_app` assigns a fresh `alerts` class per call, discarding
    whatever wrapped the previous one. Wrapping `init_app` puts the guard back
    on the new object at the moment of rebinding, so the window in which a
    reader query could reach an unguarded builder does not open. The
    `before_request` refusal above remains the backstop for any path this does
    not observe.
    """
    from alerta.app import qb
    original = getattr(qb, "init_app", None)
    if original is None or getattr(original, "_zabalack_reinstalls", False):
        return

    @wraps(original)
    def init_app(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        try:
            restrict_intent_queries()
        except Exception:  # pragma: no cover - the request-time refusal still holds
            pass
        return result

    init_app._zabalack_reinstalls = True  # type: ignore[attr-defined]
    qb.init_app = init_app


def _query_guard_live() -> bool:
    """Whether the alert query builder in use right now carries the guard."""
    from alerta.app import qb
    builder = getattr(qb, "alerts", None)
    return builder is not None and query_guard.is_installed(builder)


def restrict_intent_queries() -> None:
    """Refuse reader queries that can inspect the staged intent (A5, T40).

    Installed at the shared alert query-builder boundary so the list, history,
    count and aggregate endpoints inherit one control. See `query_guard` for the
    selectors this covers and why resolved fields are validated rather than raw
    text. Runs after the upstream factory, which is when `qb.alerts` exists.
    """
    from alerta.app import qb
    from alerta.exceptions import ApiError
    from alerta.database.backends.postgres.queryparser import QueryParser
    builder = getattr(qb, "alerts", None)
    if builder is None:
        # Refusing here rather than returning: the caller sets the flag that
        # tells take_action() staging is safe, and a silent return would set it
        # over an absent control — the reader-facing restriction gone while
        # readiness reports installed. Fail at start-up instead.
        raise RuntimeError("zabalack_privacy_adapter_required: alert query builder not initialised")
    query_guard.install(builder, QueryParser, ApiError)
    if not query_guard.is_installed(builder):  # pragma: no cover - defensive
        raise RuntimeError("zabalack_privacy_adapter_required: query guard did not install")


def create_app(config_override: dict[str, Any] | None = None, environment: str | None = None) -> Any:
    """Gunicorn target: ``zabalack.plugins:create_app()``."""
    from alerta.app import create_app as upstream_create_app
    return configure_app(upstream_create_app(config_override=config_override, environment=environment))


def observe_routing(manager: Any = None) -> None:
    """Record the exact iterable the dispatcher consumes, without rerunning rules.

    This adapter is necessary because PluginBase does not receive the effective
    plugin list. The tuple preserves order, identity and per-route configuration;
    no plugin is added, removed or called by this observer.
    """
    if manager is None:
        from alerta.app import plugins
        manager = plugins
    if getattr(manager.routing, "_zabalack_observer", False):
        return
    original = manager.routing

    @wraps(original)
    def recorded(alert: Any) -> tuple[tuple[Any, ...], Any]:
        chain, config = original(alert)
        chain = tuple(chain)
        if has_request_context():
            g.zabalack_effective_route = (str(alert.id), chain)
        return chain, config

    recorded._zabalack_observer = True  # type: ignore[attr-defined]
    manager.routing = recorded


def _human_actor(settings: Settings) -> dict[str, Any] | None:
    if not has_request_context() or not g.get("user_id") or not g.get("login"):
        return None
    login = g.login
    if login in settings.ingestion_mappings:
        return None
    scopes = set(g.get("scopes", ()))
    admin = "admin" in scopes or "admin:alerts" in scopes
    if admin and login not in settings.admin_exceptions:
        return None
    if not isinstance(g.get("customers"), (list, tuple)):
        return None
    return {"login": login, "customers": sorted(set(g.customers)), "admin": admin, "auth_type": "user"}


def _supported_route(alert: Any) -> bool:
    return bool(has_request_context() and request.method == "PUT"
                and request.endpoint == "api.action_alert"
                and request.url_rule and request.url_rule.rule == "/alert/<alert_id>/action"
                and request.view_args and str(request.view_args.get("alert_id")) == str(alert.id))


def _safe_status_receivers(alert: Any) -> bool:
    from alerta.utils.hooks import HookTrigger, status_change_hook
    receivers = list(status_change_hook.receivers_for(alert))
    return (len(receivers) == 1
            and getattr(receivers[0], "__func__", None) is HookTrigger.process_status_change)


def stage_generation(alert_id: str, intent: dict[str, Any]) -> int:
    """Stage through the protected operation, as one committed transaction.

    `zabalack.stage_generation()` records the generation in the fence and writes
    the reserved staging keys to the alert row in one statement. It runs on the
    request's own Alerta connection, so it executes as the role Alerta connects
    as — the only role granted that function — and it is committed here
    explicitly: `process_action()` commits `update_tags()` before
    `update_attributes()`, so a write left pending on this connection would ride
    the wrong commit. Any failure rolls the transaction back and refuses the
    acknowledgement; nothing is ever staged without its generation recorded.
    """
    from alerta.app import db
    connection = db.get_db()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT zabalack.stage_generation(%s,%s,%s)",
                           (alert_id, intent["action_id"], json.dumps(intent)))
            row = cursor.fetchone()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return int(row[0])


class AckSigningPlugin(PluginBase):
    """Stage unsigned intent; append binding only in the final effective hook."""

    def __init__(self, name: str | None = None, *, settings: Settings | None = None,
                 probe: Any = None, keys: dict[str, bytes] | None = None,
                 stager: Callable[[str, dict[str, Any]], int] | None = None) -> None:
        super().__init__(name)
        self.settings = settings
        self.probe = probe
        self.keys = keys
        # The protected staging operation. Injected only by tests with no
        # PostgreSQL behind them; the real one is the database function.
        self.stager = stager if stager is not None else stage_generation
        observe_routing()

    def pre_receive(self, alert: Any, **kwargs: Any) -> Any:
        return alert

    def post_receive(self, alert: Any, **kwargs: Any) -> Any:
        return None

    def _last(self, alert: Any) -> bool:
        routed = g.get("zabalack_effective_route") if has_request_context() else None
        if not routed or routed[0] != str(alert.id):
            return False
        chain = routed[1]
        return bool(chain and chain[-1] is self and sum(item is self for item in chain) == 1)

    def _check_health(self, settings: Settings) -> None:
        try:
            probe = self.probe
            if probe is None:
                if not settings.probe_dsn_file:
                    raise CaptureUnhealthy("capture_health_unconfigured")
                probe = get_probe(ProbeConfig(
                    dsn=read_protected_file(settings.probe_dsn_file).decode().strip(),
                    database_name=settings.database_name, deployment_id=settings.deployment_id,
                    config_revision=settings.config_revision, cache_seconds=settings.health_cache_seconds,
                    acquisition_timeout=settings.health_acquire_timeout_seconds,
                    overall_timeout=settings.health_overall_timeout_seconds))
            probe.check()
        except Exception:
            # process_action propagates this exact class even when generic
            # plugin errors are configured to be suppressed.
            raise AlertaException("zabalack_capture_unhealthy", code=503) from None

    def take_action(self, alert: Any, action: str, text: str, **kwargs: Any) -> Any:
        if action != "ack" or alert.status == "ack" or alert.attributes.get("source_system") != "zabbix":
            return None
        settings = settings_for(kwargs.get("config"), self.settings)
        if not settings.enabled:
            return project_skip(alert, "skipped_disabled", settings), action, text
        if settings.capture_bypass:
            project_skip(alert, "skipped_capture_bypass", settings)
            alert.attributes["zabbix_ack_sync_change_reference"] = settings.bypass_change_ref
            return alert, action, text
        if not _supported_route(alert):
            return project_skip(alert, "skipped_route_policy", settings), action, text
        try:
            compatible = (installed_version("alerta-server") == "9.1.0"
                          and str(current_app.config.get("ALARM_MODEL", "")).lower() == "alerta")
        except Exception:
            compatible = False
        if not compatible:
            return project_skip(alert, "skipped_reference_version", settings), action, text
        # The flag says configure_app ran; this asks whether the control is
        # still on the builder serving *this* request. init_app rebinds
        # qb.alerts to a fresh class per call, so the two can disagree.
        if not current_app.extensions.get("zabalack_privacy_ready") or not _query_guard_live():
            raise AlertaException("zabalack_privacy_adapter_required", code=503)
        if not self._last(alert) or not _safe_status_receivers(alert):
            return project_skip(alert, "skipped_routing_order", settings), action, text
        if not current_app.config.get("AUTH_REQUIRED") or not current_app.config.get("CUSTOMER_VIEWS"):
            return project_skip(alert, "skipped_actor_policy", settings), action, text
        try:
            if str(UUID(str(alert.id))) != str(alert.id):
                raise ValueError("noncanonical")
            validate_source(alert.attributes)
            source = make_snapshot(alert.attributes)
        except (ValueError, PolicyError):
            return project_skip(alert, "legacy_missing_mapping", settings), action, text
        cohort = select_cohort(settings, alert.customer, source)
        if not cohort:
            return project_skip(alert, "skipped_cohort_policy", settings), action, text
        if not cohort.get("one_to_one", False) or any(
            key in alert.attributes for key in ("zabbix_event_ids", "zabbix_trigger_ids", "source_members")
        ):
            return project_skip(alert, "skipped_ambiguous_source", settings), action, text
        actor = _human_actor(settings)
        if not actor or not authorize_actor(actor, alert.customer, cohort):
            return project_skip(alert, "skipped_actor_policy", settings), action, text
        contexts = g.setdefault("zabalack_context", {}).setdefault(str(alert.id), {})
        if contexts:
            # A duplicate callback within one request reuses exactly its staged
            # identity. Any changed snapshot is rejected at the final stage.
            return alert, action, text
        # A5 stage two: stage only an acknowledgement Alerta will record as one.
        # from_action() decides the transition from the history after this hook
        # returns — after the protected commit below. For a shelved, closed or
        # expired alert it raises (409); for one in `assign` it keeps the status
        # (200). No signed status write follows either, so a staging made for
        # it would be a generation nothing can capture, supersede or retire.
        # The same decision is taken here first, from the inputs from_action()
        # uses, and such a request stages nothing.
        try:
            status, _, previous_status, _ = alert._get_hist_info(action)
            _, new_status = alarm_model.transition(
                alert=alert, current_status=status, previous_status=previous_status, action=action)
        except InvalidAction:
            return None
        except ApiError:
            # Alerta's own refusal of the alert (an unknown severity): it
            # answers the same way a moment later, with nothing staged.
            raise
        except Exception:
            # The transition could not be decided: fail closed rather than
            # let Alerta record an acknowledgement that was never staged.
            raise AlertaException("zabalack_generation_unavailable", code=503) from None
        if new_status != "ack":
            return project_skip(alert, "skipped_no_ack_transition", settings), action, text
        self._check_health(settings)
        try:
            intent = build_intent(
                alert_id=str(alert.id), customer=alert.customer, source=source,
                actor=actor, cohort_revision=cohort["revision"], policy_revision=settings.policy_revision)
        except Exception:
            raise AlertaException("zabalack_intent_invalid", code=503) from None
        # A5 stage two: the staging is a protected database operation, committed
        # before anything below is written from memory. It records the
        # generation in zabalack.alert_generations and writes exactly the
        # reserved keys set here, so Alerta's later whole-object write of this
        # dictionary leaves them as written. (That write drops keys whose value
        # is None, the nullable source-contract keys among them — upstream
        # behaviour on every attribute write, which the fence neither changes
        # nor depends on.) If it fails, nothing is staged and the
        # acknowledgement is refused: no staging exists without its generation.
        try:
            self.stager(str(alert.id), intent)
        except Exception:
            raise AlertaException("zabalack_generation_unavailable", code=503) from None
        contexts[intent["action_id"]] = {"intent": deepcopy(intent), "signed": False,
                                        "config_revision": settings.config_revision}
        alert.attributes.pop("zabbix_ack_sync_local_at", None)
        alert.attributes["zabbix_ack_sync_intent"] = deepcopy(intent)
        alert.attributes.update({"zabbix_ack_sync": "pending", "zabbix_ack_sync_reason": "awaiting_capture",
                                 "zabbix_ack_sync_action_id": intent["action_id"]})
        return alert, action, text

    def status_change(self, alert: Any, status: str, text: str, **kwargs: Any) -> Any:
        if not _supported_route(alert) or status != "ack":
            return alert, status, text
        contexts = g.get("zabalack_context", {}).get(str(alert.id), {})
        if not contexts:
            return alert, status, text
        settings = settings_for(kwargs.get("config"), self.settings)
        if len(contexts) != 1 or not self._last(alert) or not _safe_status_receivers(alert):
            raise AlertaException("zabalack_final_routing_invalid", code=503)
        context = next(iter(contexts.values()))
        intent = context["intent"]
        actor = _human_actor(settings)
        if (not settings.enabled or settings.capture_bypass
                or context["config_revision"] != settings.config_revision
                or intent != alert.attributes.get("zabbix_ack_sync_intent")
                or actor != intent["actor"] or alert.customer != intent["customer"]
                or make_snapshot(alert.attributes) != intent["source"]):
            raise AlertaException("zabalack_final_binding_invalid", code=503)
        if context["signed"]:
            marker = context["marker"]
            # A duplicate hook can return the same proof, never a second one.
            if text.endswith(marker):
                return alert, status, text
            raise AlertaException("zabalack_duplicate_callback_conflict", code=503)
        if (not isinstance(text, str) or any(prefix in text.lower() for prefix in ("[zabalack:", "[ackline:"))
                or len(text.encode("utf-8")) > 8192):
            raise AlertaException("zabalack_note_invalid", code=400)
        try:
            keys = self.keys if self.keys is not None else load_key_files(settings.signing_keys)
            marker = sign_intent(intent, settings.signing_kid, keys[settings.signing_kid])
        except Exception:
            raise AlertaException("zabalack_signer_unavailable", code=503) from None
        context["signed"] = True
        context["marker"] = marker
        # Never put the marker in attributes, alert.text, logs or request JSON.
        return alert, status, text + ("\n" if text else "") + marker

    def post_action(self, alert: Any, action: str, text: str, **kwargs: Any) -> Any:
        # The upstream /action caller passes its original text here; the proof
        # is only in the committed history. No external effects from this hook.
        if has_request_context():
            g.get("zabalack_context", {}).pop(str(alert.id), None)
        return None
