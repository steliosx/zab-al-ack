"""Actual Alerta9.1 dispatcher/alarm model, with explicitly mocked persistence.

These tests exercise installed upstream Python, but do not claim live permission,
database transaction, notification, fleet, or PgBouncer evidence.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from flask import Flask, g
from alerta import settings as alerta_defaults
from alerta.app import alarm_model, hooks, plugins
from alerta.exceptions import AlertaException, ApiError, InvalidAction, RejectException
from alerta.models.alert import Alert
from alerta.utils.api import process_action, process_status
from alerta.utils.hooks import status_change_hook

from zabalack.config import Settings
from zabalack.health import CaptureUnhealthy
from zabalack.normalizer import ZabbixNormaliser
from alerta.app import qb
from zabalack.plugins import AckSigningPlugin, CaptureLogRedactor, configure_app


COHORT = dict(customer="synthetic-a", zabbix_instance="synthetic-zbx", zabbix_trigger_id="101",
              host_ids=["201"], group_ids=["301"], revision="cohort-1", policy_revision="1",
              enabled=True, one_to_one=True, comment_enabled=True, allow_admin=False)
SOURCE = dict(source_system="zabbix", zabbix_instance="synthetic-zbx", zabbix_event_id="401",
              zabbix_trigger_id="101", zabbix_cause_event_id=None, zabbix_recovery_event_id=None,
              source_contract_version="1")
SETTINGS = Settings(enabled=True, config_revision="synthetic-1", signing_kid="lab",
                    cohorts=(COHORT,), ingestion_mappings={"synthetic-ingest": {
                        "customer": "synthetic-a", "instance": "synthetic-zbx"}})
ALERT_ID = "71000000-0000-4000-8000-000000000001"


class Probe:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def check(self):
        self.calls += 1
        if self.fail:
            raise CaptureUnhealthy("synthetic")
        return {"healthy": True}


class Observer:
    name = "synthetic_observer"

    def __init__(self):
        self.seen = []

    def take_action(self, alert, action, text, **kwargs):
        self.seen.append(("action", text, deepcopy(alert.attributes)))

    def status_change(self, alert, status, text, **kwargs):
        self.seen.append(("status", text, deepcopy(alert.attributes)))

    def post_action(self, alert, action, text, **kwargs):
        self.seen.append(("post", text, deepcopy(alert.attributes)))


@pytest.fixture
def context(monkeypatch):
    app = Flask("synthetic-alerta-dispatch")
    app.config.from_object(alerta_defaults)
    app.config.update(AUTH_REQUIRED=True, CUSTOMER_VIEWS=True, PLUGINS_RAISE_ON_ERROR=False,
                      SECRET_KEY="synthetic-lab-only", TESTING=True)
    app.add_url_rule("/alert/<alert_id>/action", endpoint="api.action_alert", view_func=lambda: "unused", methods=["PUT"])
    app.add_url_rule("/alert/<alert_id>/status", endpoint="api.set_status", view_func=lambda: "unused", methods=["PUT"])
    app.add_url_rule("/_bulk/alerts/action", endpoint="api.bulk_action", view_func=lambda: "unused", methods=["PUT"])
    alarm_model.init_app(app)
    hooks.init_app(app)
    # The query builder must exist before configure_app: it installs the reader
    # restriction on it, and refuses rather than reporting the adapter ready
    # over an absent one. init_app selects the backend module without
    # connecting, which is why an unreachable DSN is enough here.
    app.config.update(DATABASE_URL="postgres://synthetic-lab-only:5432/unused")
    qb.init_app(app)
    configure_app(app)
    probe = Probe()
    generations = []
    updates = []
    # No PostgreSQL behind this app: the protected staging operation is recorded
    # instead of executed, in the order it is called relative to the writes.
    signer = AckSigningPlugin(settings=SETTINGS, probe=probe, keys={"lab": b"synthetic-test-key-only-32bytes!!!!"},
                              stager=lambda alert_id, intent: generations.append((alert_id, intent["action_id"], len(updates))) or 1)
    observer = Observer()
    monkeypatch.setattr(plugins, "plugins", OrderedDict(observer=observer, signer=signer))
    monkeypatch.setattr(plugins, "rules", None)
    monkeypatch.setattr(plugins, "config", app.config)
    monkeypatch.setattr(Alert, "update_tags", lambda self, tags: True)
    monkeypatch.setattr(Alert, "tag", lambda self, tags: True)

    def attributes(self, attrs):
        updates.append(deepcopy(attrs))
        return deepcopy(attrs)

    monkeypatch.setattr(Alert, "update_attributes", attributes)
    # from_action() decides the transition from the history; without persistence
    # the history's current status is the alert's own.
    monkeypatch.setattr(Alert, "_get_hist_info", lambda self, action=None: (self.status, None, "open", 3600))
    with app.test_request_context(f"/alert/{ALERT_ID}/action", method="PUT", json={"action": "ack"}):
        g.user_id = "synthetic-user-1"
        g.login = "synthetic-human"
        g.customers = ["synthetic-a"]
        g.scopes = ["write:alerts"]
        alert = Alert("unchanged-resource", "stable-trigger-family", id=ALERT_ID,
                      attributes=deepcopy(SOURCE), customer="synthetic-a", status="open", severity="major")
        yield SimpleNamespace(app=app, signer=signer, observer=observer, probe=probe, alert=alert, updates=updates,
                              generations=generations)


def stage(context):
    return process_action(context.alert, "ack", "synthetic operator note")


def test_T17_T19_two_stages_no_proof_before_final_dispatch(context):
    alert, action, note, timeout = stage(context)
    assert "[zabalack:" not in note
    assert "[zabalack:" not in repr(context.updates)
    unsigned = deepcopy(alert.attributes["zabbix_ack_sync_intent"])
    _, status, text = process_status(alert, "ack", note)
    assert text.startswith(note + "\n[zabalack:v1 action=")
    assert all("[zabalack:" not in repr(item) for item in context.observer.seen)
    assert "[zabalack:" not in repr(context.updates)
    assert alert.attributes["zabbix_ack_sync_intent"] == unsigned
    assert unsigned["source"]["zabbix_event_id"] == "401"
    assert context.probe.calls == 1


def test_duplicate_callbacks_no_duplicate_proof_or_identity(context):
    alert, action, note, timeout = stage(context)
    identity = deepcopy(alert.attributes["zabbix_ack_sync_intent"])
    stage(context)
    assert alert.attributes["zabbix_ack_sync_intent"] == identity
    _, status, text = process_status(alert, "ack", note)
    _, status, twice = process_status(alert, "ack", text)
    assert twice == text and text.count("[zabalack:") == 1


@pytest.mark.parametrize("rule", [lambda a, p, **kw: [p["signer"], p["observer"]],
                                   lambda a, p, **kw: [p["observer"]]])
def test_T18_effective_routing_reorders_or_omits_signer(context, monkeypatch, rule):
    monkeypatch.setattr(plugins, "rules", rule)
    alert, action, note, timeout = stage(context)
    _, status, text = process_status(alert, "ack", note)
    assert "[zabalack:" not in text
    assert "zabbix_ack_sync_intent" not in alert.attributes
    assert context.probe.calls == 0


class PassthroughAckPlugin(Observer):
    """A second acknowledgement plugin that declares the coexistence contract."""
    name = "synthetic_passthrough_ack"
    ACK_FOREIGN_SOURCE_PASSTHROUGH = True


class UndeclaredTrailingPlugin(Observer):
    name = "synthetic_undeclared_trailing"


def test_coexistence_declared_passthrough_plugin_may_follow_the_signer(context, monkeypatch):
    trailing = PassthroughAckPlugin()
    monkeypatch.setattr(plugins, "rules", lambda a, p, **kw: [p["signer"], trailing])
    alert, action, note, timeout = stage(context)
    assert "zabbix_ack_sync_intent" in alert.attributes
    _, status, text = process_status(alert, "ack", note)
    assert text.count("[zabalack:") == 1


def test_coexistence_undeclared_trailing_plugin_still_blocks_signing(context, monkeypatch):
    trailing = UndeclaredTrailingPlugin()
    monkeypatch.setattr(plugins, "rules", lambda a, p, **kw: [p["signer"], trailing])
    alert, action, note, timeout = stage(context)
    assert "zabbix_ack_sync_intent" not in alert.attributes
    assert alert.attributes["zabbix_ack_sync_reason"] == "skipped_routing_order"
    assert context.probe.calls == 0


def test_coexistence_instance_level_declaration_is_not_trusted(context, monkeypatch):
    trailing = UndeclaredTrailingPlugin()
    trailing.ACK_FOREIGN_SOURCE_PASSTHROUGH = True  # instance attribute, not the class contract
    monkeypatch.setattr(plugins, "rules", lambda a, p, **kw: [p["signer"], trailing])
    alert, action, note, timeout = stage(context)
    assert "zabbix_ack_sync_intent" not in alert.attributes


def test_coexistence_signer_declares_the_contract_itself():
    from zabalack.plugins import ACK_PASSTHROUGH_ATTRIBUTE, AckSigningPlugin
    assert ACK_PASSTHROUGH_ATTRIBUTE == "ACK_FOREIGN_SOURCE_PASSTHROUGH"
    assert getattr(AckSigningPlugin, ACK_PASSTHROUGH_ATTRIBUTE) is True
    assert AckSigningPlugin.__doc__.startswith("Stage unsigned intent")


def test_T18_routing_snapshot_is_actual_not_second_rules_evaluation(context, monkeypatch):
    calls = []

    def rules(alert, chain, **kwargs):
        calls.append(1)
        return [chain["observer"], chain["signer"]] if len(calls) == 1 else [chain["signer"], chain["observer"]]

    monkeypatch.setattr(plugins, "rules", rules)
    alert, action, note, timeout = stage(context)
    assert len(calls) == 1
    with pytest.raises(AlertaException, match="final_routing_invalid"):
        process_status(alert, "ack", note)
    assert len(calls) == 2
    assert all("[zabalack:" not in repr(item) for item in context.observer.seen)


def test_T17_unknown_status_signal_receiver_blocks_activation(context):
    def unreviewed_receiver(sender, **kwargs):
        return None

    status_change_hook.connect(unreviewed_receiver, weak=False)
    try:
        alert, action, note, timeout = stage(context)
        assert "zabbix_ack_sync_intent" not in alert.attributes
        assert alert.attributes["zabbix_ack_sync_reason"] == "skipped_routing_order"
    finally:
        status_change_hook.disconnect(unreviewed_receiver)


def test_T15_health_AlertaException_propagates_when_generic_errors_suppressed(context):
    context.probe.fail = True
    with pytest.raises(AlertaException, match="capture_unhealthy"):
        stage(context)
    assert not context.updates
    assert "zabbix_ack_sync_intent" not in context.alert.attributes


@pytest.mark.parametrize("field,value", [("zabbix_event_id", "402"), ("zabbix_trigger_id", "102")])
def test_T4_T10_changed_snapshot_before_final_hook_rejected(context, field, value):
    alert, action, note, timeout = stage(context)
    alert.attributes[field] = value
    with pytest.raises(AlertaException, match="final_binding_invalid"):
        process_status(alert, "ack", note)


def test_T5_stale_intent_bulk_and_status_do_not_get_proof(context):
    alert, action, note, timeout = stage(context)
    for path in ("/_bulk/alerts/action", f"/alert/{ALERT_ID}/status"):
        with context.app.test_request_context(path, method="PUT", json={"action": "ack"}):
            g.user_id, g.login, g.customers, g.scopes = "synthetic-user", "synthetic-human", ["synthetic-a"], ["write:alerts"]
            _, _, local, _ = process_action(alert, "ack", note)
            _, _, local = process_status(alert, "ack", local)
            assert "[zabalack:" not in local


@pytest.mark.parametrize("user,customers,scopes", [(None, ["synthetic-a"], ["write:alerts"]),
                                                 ("u", ["synthetic-b"], ["write:alerts"]),
                                                 ("u", [], ["admin"])])
def test_T24_service_key_wrong_customer_and_unapproved_admin_local_only(context, user, customers, scopes):
    g.user_id, g.customers, g.scopes = user, customers, scopes
    alert, action, note, timeout = stage(context)
    assert alert.attributes["zabbix_ack_sync_reason"] == "skipped_actor_policy"
    assert context.probe.calls == 0


def test_T36_native_repeat_ack_raises_InvalidAction_without_new_intent(context, monkeypatch):
    context.alert.status = "ack"
    monkeypatch.setattr(context.alert, "_get_hist_info", lambda action: ("ack", None, "open", 3600))
    alert, action, note, timeout = stage(context)
    with pytest.raises(InvalidAction):
        alert.from_action(action, note, timeout)
    assert not context.updates
    assert context.probe.calls == 0
    assert "zabbix_ack_sync_intent" not in alert.attributes


def test_T20_bypass_preserves_local_status_and_change_reference(context):
    context.signer.settings = replace(SETTINGS, capture_bypass=True, bypass_change_ref="LAB-CHANGE-1")
    alert, action, note, timeout = stage(context)
    assert alert.status == "open"  # staged projection is not a saved acknowledgement
    assert alert.attributes["zabbix_ack_sync_reason"] == "skipped_capture_bypass"
    assert alert.attributes["zabbix_ack_sync_change_reference"] == "LAB-CHANGE-1"
    assert alert.attributes["zabbix_ack_sync_local_at"]
    assert context.probe.calls == 0


def test_new_eligible_intent_clears_old_local_projection_generation(context):
    context.alert.attributes["zabbix_ack_sync_local_at"] = "2026-01-01T00:00:00+00:00"
    stage(context)
    assert "zabbix_ack_sync_local_at" not in context.alert.attributes


def test_T23_ingestion_customer_bound_and_reserved_stripping(context):
    g.user_id, g.login, g.customers = None, "synthetic-ingest", ["synthetic-a"]
    normalizer = ZabbixNormaliser(settings=SETTINGS)
    context.alert.attributes.update(zabbix_ack_sync="ok", zabbix_ack_sync_intent={"spoof": True}, customer="synthetic-b")
    normalized = normalizer.pre_receive(context.alert, config=context.app.config)
    assert "zabbix_ack_sync_intent" not in normalized.attributes
    assert normalized.customer == "synthetic-a"
    assert normalized.resource == "unchanged-resource" and normalized.event == "stable-trigger-family"
    normalized.customer = "synthetic-b"
    with pytest.raises(RejectException, match="customer_mismatch"):
        normalizer.pre_receive(normalized, config=context.app.config)


def test_normalizer_invalid_macro_legacy_ambiguous_and_non_zabbix(context):
    g.user_id, g.login, g.customers = None, "synthetic-ingest", ["synthetic-a"]
    normalizer = ZabbixNormaliser(settings=SETTINGS)
    context.alert.attributes["zabbix_event_id"] = "{EVENT.ID}"
    normalizer.pre_receive(context.alert)
    assert context.alert.attributes["zabbix_ack_sync_reason"] == "legacy_missing_mapping"
    context.alert.attributes = dict(SOURCE, source_members=["401", "402"])
    normalizer.pre_receive(context.alert)
    assert context.alert.attributes["zabbix_ack_sync_reason"] == "skipped_ambiguous_source"
    context.alert.attributes = {"source_system": "other", "zabbix_ack_sync": "forged"}
    normalizer.pre_receive(context.alert)
    assert context.alert.attributes == {"source_system": "other"}


def test_disabled_normalizer_preserves_existing_unmapped_ingestion(context):
    g.user_id, g.login, g.customers = None, "legacy-unmapped", []
    context.alert.customer = None
    context.alert.attributes["zabbix_ack_sync_intent"] = {"spoof": True}
    normalizer = ZabbixNormaliser(settings=Settings())
    normalizer.pre_receive(context.alert)
    assert context.alert.attributes["zabbix_event_id"] == "401"
    assert context.alert.attributes["zabbix_ack_sync_reason"] == "skipped_disabled"
    assert "zabbix_ack_sync_intent" not in context.alert.attributes


def test_T19_logging_and_error_detail_redacted_without_altering_history(context):
    import io
    import logging
    app = Flask("synthetic-privacy")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG)
    app.config.update(DATABASE_URL="postgres://synthetic-lab-only:5432/unused")
    qb.init_app(app)
    configure_app(app)

    @app.route("/fail")
    def fail():
        try:
            raise RuntimeError("DETAIL [zabalack:v1 synthetic-proof]")
        except RuntimeError:
            app.logger.exception("failed acknowledgement SQL")
        app.logger.debug("SELECT %s", "'zabbix_ack_sync_intent': {'private': 'value'}")
        return {"message": "DETAIL [zabalack:v1 synthetic-proof]", "errors": ["[ZABALACK:V1 private]"]}, 500

    @app.route("/history")
    def history():
        return {"text": "[zabalack:v1 synthetic-committed-history]"}

    client = app.test_client()
    response = client.get("/fail")
    assert response.status_code == 500
    assert "[zabalack:" not in response.get_data(as_text=True).lower()
    assert "private" not in stream.getvalue() and "[zabalack:" not in stream.getvalue()
    assert "restricted_diagnostic_redacted" in stream.getvalue()
    assert "[zabalack:" in client.get("/history").get_data(as_text=True)


def test_T19_missing_privacy_adapter_rejects_eligible_ack(context):
    context.app.extensions.pop("zabalack_privacy_ready")
    with pytest.raises(AlertaException, match="privacy_adapter_required"):
        stage(context)


def test_T40_configure_app_refuses_rather_than_reporting_a_control_it_did_not_install(monkeypatch):
    """`restrict_intent_queries()` used to return silently when the alert query
    builder was absent, and `configure_app` set `zabalack_privacy_ready`
    immediately afterwards regardless.

    That flag is what `take_action` reads to decide staging is safe, so the pair
    could report the reader restriction installed while it was not there at all
    — the same shape as a probe passing over a control that is missing. It now
    fails at start-up, and the flag is only set over a control that took.
    """
    from alerta.app import qb
    app = Flask("synthetic-no-builder")
    app.config.from_object(alerta_defaults)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-lab-only")
    monkeypatch.setattr(qb, "alerts", None, raising=False)
    with pytest.raises(RuntimeError, match="privacy_adapter_required"):
        configure_app(app)
    assert not app.extensions.get("zabalack_privacy_ready"), "no readiness over an absent control"


def test_T40_a_discarded_query_guard_refuses_the_acknowledgement(context, monkeypatch):
    """The flag is set once at start-up; the control lives on an object Alerta
    rebinds.

    `QueryBuilder.init_app` assigns a fresh `alerts` class on every call, so a
    second `create_app()` in one process replaces the object the guard wrapped
    while the first app still reports ready. The dispatcher therefore asks
    whether the builder serving *this* request carries the guard, rather than
    trusting the flag alone.
    """
    from alerta.app import qb
    from zabalack import query_guard

    assert context.app.extensions.get("zabalack_privacy_ready") is True
    assert query_guard.is_installed(qb.alerts), "precondition: the guard installed"

    class Rebound:
        """What init_app leaves behind: same name, unwrapped `from_params`."""
        VALID_PARAMS = getattr(qb.alerts, "VALID_PARAMS", {})

        @staticmethod
        def from_params(params, customers=None, query_time=None):
            return None

    monkeypatch.setattr(qb, "alerts", Rebound, raising=False)
    assert not query_guard.is_installed(qb.alerts)
    with pytest.raises(AlertaException, match="privacy_adapter_required"):
        stage(context)


@pytest.mark.parametrize("variant", ["version", "model"])
def test_unknown_reference_version_or_alarm_model_never_signs(context, monkeypatch, variant):
    if variant == "version":
        monkeypatch.setattr("zabalack.plugins.installed_version", lambda name: "unverified")
    else:
        context.app.config["ALARM_MODEL"] = "ISA_18_2"
    alert, action, note, timeout = stage(context)
    assert alert.attributes["zabbix_ack_sync_reason"] == "skipped_reference_version"
    assert "zabbix_ack_sync_intent" not in alert.attributes


def test_non_eligible_activity_does_not_load_broken_capture_config(context):
    context.signer.settings = None
    context.app.config["ZABALACK_CONFIG_FILE"] = "/does-not-exist-synthetic"
    assert context.signer.take_action(context.alert, "unack", "note", config=context.app.config) is None
    context.alert.attributes = {"source_system": "unrelated"}
    assert context.signer.take_action(context.alert, "ack", "note", config=context.app.config) is None
    assert ZabbixNormaliser().pre_receive(context.alert, config=context.app.config) is context.alert


def test_T41_the_generation_is_recorded_before_any_attribute_write(context):
    """The protected staging operation runs before Alerta writes anything.

    The stager records how many attribute writes had happened when it was
    called: zero. The generation it recorded is the one the staged intent
    carries, and the write that follows carries that same intent.
    """
    alert, action, note, timeout = stage(context)
    action_id = alert.attributes["zabbix_ack_sync_intent"]["action_id"]
    assert context.generations == [(str(alert.id), action_id, 0)]
    assert context.updates and context.updates[-1]["zabbix_ack_sync_intent"]["action_id"] == action_id
    stage(context)
    assert len(context.generations) == 1, "a duplicate callback reuses its staged identity and records nothing new"


def test_T41_a_refused_generation_refuses_the_acknowledgement_and_stages_nothing(context):
    """No staging exists without its generation recorded."""
    def refuse(alert_id, intent):
        raise RuntimeError("synthetic fence unavailable")
    context.signer.stager = refuse
    with pytest.raises(AlertaException) as refused:
        stage(context)
    assert refused.value.code == 503 and "zabalack_generation_unavailable" in str(refused.value)
    assert context.updates == [], "Alerta must not have been asked to write anything"
    assert "zabbix_ack_sync_intent" not in context.alert.attributes
    assert context.alert.attributes.get("zabbix_ack_sync") is None


@pytest.mark.parametrize("state", ["shelved", "closed", "expired"])
def test_T41_an_acknowledgement_alerta_will_refuse_stages_nothing(context, state):
    """Alerta raises InvalidAction for `ack` from these states — in
    from_action(), after take_action() has returned. The plugin decides first,
    from the same history, and stages nothing: no generation, no attribute
    write, no health probe. Alerta's own 409 then follows with nothing behind it."""
    context.alert.status = state
    alert, action, note, timeout = stage(context)
    with pytest.raises(InvalidAction):
        alert.from_action(action, note, timeout)
    assert context.generations == [], "a generation nothing could ever capture must not be recorded"
    assert context.updates == [] and context.probe.calls == 0
    assert not any(key.startswith("zabbix_ack_sync") for key in alert.attributes)


def test_T41_an_acknowledgement_that_does_not_reach_ack_stages_nothing(context):
    """From `assign`, Alerta answers 200 and keeps the status (rule ALL-*): no
    signed status write follows, so nothing is staged and the skip is recorded."""
    context.alert.status = "assign"
    alert, action, note, timeout = stage(context)
    assert alarm_model.transition(alert=alert, current_status="assign", previous_status="open", action="ack")[1] == "assign"
    assert context.generations == [] and context.probe.calls == 0
    assert alert.attributes["zabbix_ack_sync"] == "skipped"
    assert alert.attributes["zabbix_ack_sync_reason"] == "skipped_no_ack_transition"
    assert "zabbix_ack_sync_intent" not in alert.attributes and "zabbix_ack_sync_action_id" not in alert.attributes
    assert context.updates and context.updates[-1]["zabbix_ack_sync_reason"] == "skipped_no_ack_transition"


def _unguarded_builder():
    """A fresh stand-in for what `QueryBuilder.init_app` leaves behind.

    Built per call: installing the guard mutates the class, so a shared one
    would silently arrive already guarded in a later case.
    """

    class Unguarded:
        served: list = []

        @staticmethod
        def from_params(params, customers=None, query_time=None):
            Unguarded.served.append(dict(params))
            return "served-from-storage"

    return Unguarded


def _reader_route(app):
    """A stand-in for the collection GET: it reaches storage through qb.alerts.

    The real reader routes — the collection response, `/alerts/count`, the
    aggregate endpoints and `/alerts/history` — all resolve their selector
    through this one object, which is why the guard lives there.
    """
    from alerta.app import qb

    def read():
        return str(qb.alerts.from_params({"attributes.zabbix_ack_sync_intent": "~lab-b"}))

    app.add_url_rule("/synthetic/alerts", endpoint="synthetic.read", view_func=read, methods=["GET"])
    return app.test_client()


def test_T40_a_rebound_builder_cannot_serve_reader_queries_over_staged_data(context, monkeypatch):
    """The reader boundary, not just the acknowledgement path.

    Staged intents already committed stay searchable through the reader routes,
    and those routes never call `take_action`. A rebinding that discards the
    guard must therefore be caught before a reader query reaches storage: the
    control is put back and the selector refused, or the request is refused.
    Serving the selector from an unguarded builder is the failure.
    """
    from alerta.app import qb
    from zabalack import query_guard

    unguarded = _unguarded_builder()
    client = _reader_route(context.app)
    assert query_guard.is_installed(qb.alerts), "precondition: the guard installed"

    # Rebinding as init_app does, then a reader request — no acknowledgement.
    monkeypatch.setattr(qb, "alerts", unguarded, raising=False)
    assert not query_guard.is_installed(qb.alerts), "precondition: the guard was discarded"
    # This synthetic app registers none of Alerta's error handlers, so a refusal
    # surfaces as the exception the handlers would have turned into 4xx/503.
    served = None
    try:
        served = client.get("/synthetic/alerts")
    except (ApiError, AlertaException):
        pass

    assert unguarded.served == [], "a reader query reached an unguarded builder"
    assert served is None or served.status_code >= 400, "the reader query was served"
    assert served is None or b"served-from-storage" not in served.data


def test_T40_reconfiguring_a_ready_app_reinstalls_the_discarded_guard(context, monkeypatch):
    """`configure_app` must not trust its own readiness flag.

    The flag stays set on an app whose builder has since been rebound, so an
    early return would leave the reader restriction gone while readiness
    reports installed.
    """
    from alerta.app import qb
    from zabalack import query_guard

    assert context.app.extensions.get("zabalack_privacy_ready") is True
    monkeypatch.setattr(qb, "alerts", _unguarded_builder(), raising=False)
    assert not query_guard.is_installed(qb.alerts)

    configure_app(context.app)

    assert query_guard.is_installed(qb.alerts), "a ready app returned over an absent control"


def test_T40_rebinding_the_builder_reinstalls_the_guard_at_the_rebinding(context):
    """The guard follows `qb.alerts` when Alerta replaces it."""
    from alerta.app import qb
    from zabalack import query_guard

    qb.init_app(context.app)

    assert query_guard.is_installed(qb.alerts), "a fresh builder was left unguarded"
