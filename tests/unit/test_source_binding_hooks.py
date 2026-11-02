"""F1 (migration 015): the ingestion hand-off and the binding step, with no PostgreSQL.

The normaliser's pre_receive hands the validated source of one authenticated
POST /alert ingestion to its own post_receive through a single-use request slot;
post_receive binds only when the returned alert is the one validated. The signer
stages only for a usable binding and takes the intent's source from it.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask, g
from alerta import settings as alerta_defaults
from alerta.models.alert import Alert

from zabalack.config import Settings
from zabalack.normalizer import INGESTION_SLOT, ZabbixNormaliser

SOURCE = dict(source_system="zabbix", zabbix_instance="synthetic-zbx", zabbix_event_id="401",
              zabbix_trigger_id="101", zabbix_cause_event_id=None, zabbix_recovery_event_id=None,
              source_contract_version="1")
COHORT = dict(customer="synthetic-a", zabbix_instance="synthetic-zbx", zabbix_trigger_id="101",
              host_ids=["201"], group_ids=["301"], revision="cohort-1", policy_revision="1",
              enabled=True, one_to_one=True, comment_enabled=True, allow_admin=False)
SETTINGS = Settings(enabled=True, config_revision="synthetic-1", signing_kid="lab",
                    cohorts=(COHORT,), ingestion_mappings={"synthetic-ingest": {
                        "customer": "synthetic-a", "instance": "synthetic-zbx"}})
ALERT_ID = "71000000-0000-4000-8000-000000000001"
RECEIVED_AT = datetime(2026, 11, 1, 18, 0, 0, 123456)


@pytest.fixture
def app():
    app = Flask("synthetic-alerta-binding")
    app.config.from_object(alerta_defaults)
    app.config.update(AUTH_REQUIRED=True, CUSTOMER_VIEWS=True, SECRET_KEY="synthetic-lab-only", TESTING=True)
    app.add_url_rule("/alert", endpoint="api.receive", view_func=lambda: "unused", methods=["POST"])
    app.add_url_rule("/webhooks/custom", endpoint="webhooks.custom", view_func=lambda: "unused", methods=["POST"])
    return app


def ingest_identity():
    g.user_id, g.login, g.customers, g.scopes = None, "synthetic-ingest", ["synthetic-a"], ["write:alerts"]


def incoming(**attributes):
    return Alert("synthetic-resource", "stable-trigger-family", id=ALERT_ID, environment="Production",
                 attributes=dict(deepcopy(SOURCE), **attributes), customer="synthetic-a", severity="major")


def returned(alert, **changes):
    """The alert object the ingestion statement returns: the receive id is the incoming id."""
    stored = deepcopy(alert)
    stored.last_receive_id = alert.id
    stored.last_receive_time = RECEIVED_AT
    for key, value in changes.items():
        setattr(stored, key, value)
    return stored


def normaliser(binds):
    return ZabbixNormaliser(settings=SETTINGS, binder=lambda *args: binds.append(args) or "bound")


def test_authenticated_post_alert_binds_the_returned_epoch_once(app):
    binds = []
    plugin = normaliser(binds)
    with app.test_request_context("/alert", method="POST"):
        ingest_identity()
        alert = plugin.pre_receive(incoming())
        assert plugin.post_receive(returned(alert)) is None
        assert binds == [(ALERT_ID, ALERT_ID, RECEIVED_AT, "synthetic-a",
                          {"zabbix_instance": "synthetic-zbx", "zabbix_event_id": "401", "zabbix_trigger_id": "101"})]
        # Popped once: a second post_receive for the same request binds nothing.
        assert plugin.post_receive(returned(alert)) is None
        assert len(binds) == 1


@pytest.mark.parametrize("attributes", [
    {"source_system": "other"},                      # not declared: passes unvalidated, no hand-off
    {"zabbix_event_id": "{EVENT.ID}"},               # invalid source: skipped as legacy
    {"source_members": ["401", "402"]},              # ambiguous: skipped
], ids=["undeclared", "invalid-source", "ambiguous"])
def test_a_skipped_or_undeclared_ingestion_leaves_no_hand_off(app, attributes):
    binds = []
    plugin = normaliser(binds)
    with app.test_request_context("/alert", method="POST"):
        ingest_identity()
        alert = plugin.pre_receive(incoming(**attributes))
        assert g.get(INGESTION_SLOT) is None
        plugin.post_receive(returned(alert))
    assert binds == []


def test_a_rejected_ingestion_clears_an_earlier_hand_off(app):
    from alerta.exceptions import RejectException
    binds = []
    plugin = normaliser(binds)
    with app.test_request_context("/alert", method="POST"):
        ingest_identity()
        plugin.pre_receive(incoming())
        assert g.get(INGESTION_SLOT) is not None
        g.customers = ["synthetic-b"]
        with pytest.raises(RejectException):
            plugin.pre_receive(incoming())
        assert g.get(INGESTION_SLOT) is None


def test_only_post_alert_binds(app):
    binds = []
    plugin = normaliser(binds)
    with app.test_request_context("/webhooks/custom", method="POST"):
        ingest_identity()
        alert = plugin.pre_receive(incoming())
        plugin.post_receive(returned(alert))
    assert binds == []


def test_a_webhook_list_never_binds_and_never_carries_a_slot_across_items(app):
    binds = []
    plugin = normaliser(binds)
    with app.test_request_context("/webhooks/custom", method="POST"):
        ingest_identity()
        first = plugin.pre_receive(incoming())
        plugin.post_receive(returned(first))
        second = plugin.pre_receive(incoming(source_system="other"))
        plugin.post_receive(returned(second))
    assert binds == []


@pytest.mark.parametrize("changes", [
    {"last_receive_id": "a-replayed-receive-id"},
    {"resource": "another-resource"}, {"event": "another-event"},
    {"environment": "Development"}, {"customer": "synthetic-b"},
    {"attributes": dict(SOURCE, zabbix_event_id="402")},
], ids=["receipt", "resource", "event", "environment", "customer", "snapshot"])
def test_a_returned_alert_that_is_not_the_validated_one_is_not_bound(app, changes):
    binds = []
    plugin = normaliser(binds)
    with app.test_request_context("/alert", method="POST"):
        ingest_identity()
        alert = plugin.pre_receive(incoming())
        assert plugin.post_receive(returned(alert, **changes)) is None
    assert binds == []


def test_a_failed_bind_never_raises_into_ingestion(app):
    def broken(*args):
        raise RuntimeError("synthetic database failure")
    plugin = ZabbixNormaliser(settings=SETTINGS, binder=broken)
    with app.test_request_context("/alert", method="POST"):
        ingest_identity()
        alert = plugin.pre_receive(incoming())
        assert plugin.post_receive(returned(alert)) is None


def test_post_receive_without_a_request_or_slot_binds_nothing(app):
    binds = []
    plugin = normaliser(binds)
    with app.app_context():                                  # no request: no hand-off at all
        assert plugin.post_receive(returned(incoming())) is None
    with app.test_request_context("/alert", method="POST"):
        assert plugin.post_receive(returned(incoming())) is None
    assert binds == []
