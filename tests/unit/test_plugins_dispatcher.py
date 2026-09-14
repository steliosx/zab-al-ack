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
from alerta.exceptions import AlertaException, InvalidAction, RejectException
from alerta.models.alert import Alert
from alerta.utils.api import process_action, process_status
from alerta.utils.hooks import status_change_hook

from zabalack.config import Settings
from zabalack.health import CaptureUnhealthy
from zabalack.normalizer import ZabbixNormaliser
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
