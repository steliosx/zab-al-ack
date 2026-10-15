"""Synthetic Compose-only provisioning through the pinned Zabbix 7.0.25 API.

This helper never accepts an endpoint or credentials from a fixture. Its only
destination is the private Compose service; the outer runner must establish a
fresh internal network before setting ACK_DEPLOYMENT_E2E. No product transport,
webhook script, source event or authentication boundary is replaced here.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from lab.zabbix_roles import role_rules, verify_role


API_URL = "http://zabbix-web:8080/api_jsonrpc.php"
INGEST_URL = "https://runner:58081/alert"
ISOLATION_VALUE = "GENERATED_ISOLATED_PROJECT"
REFERENCE_VERSION = "7.0.25"
# Historical identity only: the Zabbix 7.0.10 source commit whose API classes were
# inspected for this lab (docs/deployment-e2e.md). It is not the 7.0.25 reference
# and nothing here derives the reference from it.
INSPECTED_SOURCE_COMMIT_7_0_10 = "237358b56b2584b94b1f8358f479ade669693ba2"
# Synthetic provisioning/observation only: a cold trigger.create took 13 seconds
# under QEMU TCG. This client never carries the production worker's write path.
LAB_RPC_TIMEOUT_SECONDS = 30
# The server evaluates actions from its configuration cache, refreshed every
# CacheUpdateFrequency seconds (default 10). A problem raised before the cache
# holds the freshly created action never starts an escalation, so no webhook
# alert is ever produced for it (observed on a real 7.0.10 lab).
CONFIG_CACHE_GRACE_SECONDS = 12.0
CUSTOMER = "zabalack-lab"
INSTANCE = "deployment-lab"
HOST_NAME = "zabalack-deployment-synthetic"
ITEM_KEY = "zabalack.deployment.value"
_ID = re.compile(r"[1-9][0-9]{0,19}\Z")


class LabZabbixError(RuntimeError):
    """A bounded public reason, never a remote body or credential."""


@dataclass(frozen=True)
class LabSource:
    group_id: str
    host_id: str
    item_id: str
    trigger_id: str
    worker_token: str = field(repr=False)


def _id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise LabZabbixError("invalid_synthetic_identifier")
    return value


def _created(result: Any, key: str) -> str:
    if not isinstance(result, dict) or not isinstance(result.get(key), list) or len(result[key]) != 1:
        raise LabZabbixError("unexpected_create_result")
    return _id(result[key][0])


def source_action(trigger_id: str, recipient_id: str, media_id: str) -> dict[str, Any]:
    """One trigger and one private webhook recipient; no command operation."""
    operation = {
        "operationtype": 0,
        "opmessage": {"default_msg": 1, "mediatypeid": _id(media_id)},
        "opmessage_usr": [{"userid": _id(recipient_id)}],
    }
    return {
        "name": "Zab-al-ack disposable deployment source facts",
        "eventsource": 0, "status": 0, "esc_period": "60s",
        "filter": {"evaltype": 0, "conditions": [
            {"conditiontype": 2, "operator": 0, "value": _id(trigger_id)},
        ]},
        "operations": [{**copy.deepcopy(operation), "esc_step_from": 1, "esc_step_to": 1}],
        "recovery_operations": [copy.deepcopy(operation)],
        "update_operations": [copy.deepcopy(operation)],
    }


class ZabbixLab:
    def __init__(self, *, isolated_lab: bool, fresh_instance: bool) -> None:
        if (isolated_lab is not True or fresh_instance is not True
                or os.environ.get("ACK_DEPLOYMENT_E2E") != ISOLATION_VALUE):
            raise LabZabbixError("fresh_isolated_compose_required")
        self._client = httpx.Client(timeout=LAB_RPC_TIMEOUT_SECONDS, trust_env=False, follow_redirects=False)
        self._sequence = 0
        self._admin_session: str | None = None
        self._source: LabSource | None = None
        self._recipient_id: str | None = None
        self._action_id: str | None = None
        self._media_id: str | None = None
        self._action_created_at: float | None = None

    def close(self) -> None:
        self._client.close()
        self._admin_session = None

    def _rpc(self, method: str, params: Any, *, authenticated: bool = True) -> Any:
        self._sequence += 1
        headers = {"Content-Type": "application/json-rpc"}
        if authenticated:
            if not self._admin_session:
                raise LabZabbixError("synthetic_login_required")
            headers["Authorization"] = "Bearer " + self._admin_session
        try:
            response = self._client.post(API_URL, headers=headers, json={
                "jsonrpc": "2.0", "method": method, "params": params, "id": self._sequence,
            })
            if response.status_code != 200 or len(response.content) > 2_097_152:
                raise LabZabbixError("synthetic_rpc_transport_rejected")
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise LabZabbixError("synthetic_rpc_transport_rejected") from None
        if (not isinstance(data, dict) or data.get("jsonrpc") != "2.0"
                or type(data.get("id")) is not int or data["id"] != self._sequence
                or "result" not in data or "error" in data):
            raise LabZabbixError("synthetic_rpc_result_rejected")
        return data["result"]

    def wait_ready(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                version = self._rpc("apiinfo.version", {}, authenticated=False)
            except LabZabbixError:
                if time.monotonic() >= deadline:
                    raise LabZabbixError("synthetic_zabbix_not_ready") from None
                time.sleep(1)
                continue
            if version != REFERENCE_VERSION:
                raise LabZabbixError("unexpected_zabbix_reference_version")
            return

    def bootstrap(self) -> LabSource:
        self.wait_ready()
        session = self._rpc("user.login", {"username": "Admin", "password": "zabbix"}, authenticated=False)
        if not isinstance(session, str) or not re.fullmatch(r"[a-f0-9]{32}", session):
            raise LabZabbixError("synthetic_login_result_rejected")
        self._admin_session = session
        # Refuse reuse instead of disabling other actions or changing credentials.
        if self._rpc("action.get", {"output": ["actionid"], "filter": {"status": 0}}):
            raise LabZabbixError("fresh_instance_has_active_actions")
        if self._rpc("host.get", {"output": ["hostid"], "filter": {"host": HOST_NAME}}):
            raise LabZabbixError("synthetic_host_already_exists")
        group_id = _created(self._rpc("hostgroup.create", {"name": "Zab-al-ack disposable deployment"}), "groupids")
        host_id = _created(self._rpc("host.create", {
            "host": HOST_NAME, "groups": [{"groupid": group_id}], "interfaces": [],
        }), "hostids")
        item_id = _created(self._rpc("item.create", {
            "hostid": host_id, "name": "Synthetic deployment value", "key_": ITEM_KEY,
            "type": 2, "value_type": 3, "delay": "0", "trapper_hosts": "",
        }), "itemids")
        trigger_id = _created(self._rpc("trigger.create", {
            "description": "Zab-al-ack synthetic deployment problem",
            "expression": f"last(/{HOST_NAME}/{ITEM_KEY})=1", "priority": 2,
        }), "triggerids")
        rules = role_rules()
        role_id = _created(self._rpc("role.create", {
            "name": "Zab-al-ack disposable restricted source", "type": 1,
            "rules": rules,
        }), "roleids")
        try:
            verify_role(self._rpc("role.get", {"roleids": [role_id], "output": ["roleid", "type"],
                                               "selectRules": list(rules)}), role_id, rules)
        except ValueError:
            raise LabZabbixError("synthetic_role_readback_rejected") from None
        usergroup_id = _created(self._rpc("usergroup.create", {
            "name": "Zab-al-ack disposable source readers", "gui_access": 1,
            "hostgroup_rights": [{"id": group_id, "permission": 2}],
        }), "usrgrpids")
        users = []
        for username in ("zabalack-deployment-worker", "zabalack-deployment-recipient"):
            users.append(_created(self._rpc("user.create", {
                "username": username, "passwd": "Synthetic-lab-only-7!", "roleid": role_id,
                "usrgrps": [{"usrgrpid": usergroup_id}],
            }), "userids"))
        token_id = _created(self._rpc("token.create", {
            "name": "Zab-al-ack disposable worker token", "userid": users[0],
            "expires_at": int(time.time()) + 3600,
        }), "tokenids")
        generated = self._rpc("token.generate", [token_id])
        if (not isinstance(generated, list) or len(generated) != 1
                or not isinstance(generated[0], dict) or generated[0].get("tokenid") != token_id
                or not isinstance(generated[0].get("token"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", generated[0]["token"])):
            raise LabZabbixError("synthetic_token_result_rejected")
        self._recipient_id = users[1]
        self._source = LabSource(group_id, host_id, item_id, trigger_id, generated[0]["token"])
        return self._source

    def enable_media(self, ingest_key: str, *, customer: str = CUSTOMER, instance: str = INSTANCE) -> None:
        if not self._source or not self._recipient_id or self._action_id:
            raise LabZabbixError("synthetic_media_sequence_rejected")
        if customer != CUSTOMER or instance != INSTANCE or not isinstance(ingest_key, str) or not ingest_key:
            raise LabZabbixError("synthetic_ingestion_binding_required")
        root = Path(__file__).resolve().parents[1]
        artifact = json.loads((root / "zabbix/v1/media_type.reference.json").read_text())
        definition = artifact["zabbix_export"]["media_types"][0]
        script = (root / "zabbix/v1/webhook.js").read_text()
        if definition["script"] != script or definition["status"] != "DISABLED":
            raise LabZabbixError("reference_media_artifact_mismatch")
        if self._rpc("configuration.import", {
            "format": "json", "source": json.dumps(artifact),
            "rules": {"mediaTypes": {"createMissing": True, "updateExisting": False}},
        }) is not True:
            raise LabZabbixError("reference_media_import_failed")
        imported = self._rpc("mediatype.get", {
            "output": ["mediatypeid", "script"], "filter": {"name": definition["name"]},
        })
        # Zabbix 7.0 stores imported multi-line text with CRLF line terminators
        # (observed on a real 7.0.10 import); only the line terminators may differ.
        stored = imported[0].get("script") if isinstance(imported, list) and len(imported) == 1 and isinstance(imported[0], dict) else None
        if not isinstance(stored, str) or stored.replace("\r\n", "\n") != script:
            raise LabZabbixError("reference_media_import_changed_script")
        media_id = _id(imported[0].get("mediatypeid"))
        values = {
            "alerta_api_key": ingest_key, "alerta_api_url": INGEST_URL,
            "alerta_customer": customer, "alerta_environment": "Development",
            "zabbix_instance": instance, "external_writes_enabled": "true",
        }
        parameters = [{"name": row["name"], "value": values.get(row["name"], row["value"])}
                      for row in definition["parameters"]]
        self._rpc("mediatype.update", {
            "mediatypeid": media_id, "status": 0, "parameters": parameters,
            "maxattempts": 1, "attempt_interval": "10s",
        })
        self._rpc("user.update", {"userid": self._recipient_id, "medias": [{
            "mediatypeid": media_id, "sendto": ["synthetic-internal-webhook"],
            "active": 0, "severity": 63, "period": "1-7,00:00-24:00",
        }]})
        self._action_id = _created(self._rpc("action.create", source_action(
            self._source.trigger_id, self._recipient_id, media_id,
        )), "actionids")
        self._media_id = media_id
        self._action_created_at = time.monotonic()

    def _push(self, value: int, timeout: float = 120) -> None:
        if not self._source or not self._action_id:
            raise LabZabbixError("synthetic_source_not_configured")
        deadline = time.monotonic() + timeout
        while True:
            result = self._rpc("history.push", [{"itemid": self._source.item_id, "value": value}])
            rows = result.get("data") if isinstance(result, dict) else None
            if (isinstance(result, dict) and result.get("response") == "success"
                    and isinstance(rows, list) and len(rows) == 1
                    and isinstance(rows[0], dict)
                    # Zabbix server emits a JSON integer here, unlike event.get
                    # database identifiers. Preserve exact identity; no bool/float coercion.
                    and type(rows[0].get("itemid")) in (str, int)
                    and str(rows[0]["itemid"]) == self._source.item_id
                    and "error" not in rows[0]):
                return
            if time.monotonic() >= deadline:
                raise LabZabbixError("synthetic_history_push_not_accepted")
            time.sleep(1)

    def raise_problem(self, timeout: float = 120) -> str:
        if self._action_created_at is not None:
            # Bounded wait for the server's configuration cache to include the action.
            time.sleep(max(0.0, CONFIG_CACHE_GRACE_SECONDS - (time.monotonic() - self._action_created_at)))
        self._push(1, timeout)
        deadline = time.monotonic() + timeout
        while True:
            events = self._rpc("event.get", {
                "output": ["eventid", "objectid", "value", "acknowledged"],
                "source": 0, "object": 0, "objectids": [self._source.trigger_id],
                "value": 1,
            })
            if events:
                if (not isinstance(events, list) or len(events) != 1
                        or events[0].get("objectid") != self._source.trigger_id
                        or events[0].get("value") != "1" or events[0].get("acknowledged") != "0"):
                    raise LabZabbixError("synthetic_problem_identity_rejected")
                return _id(events[0].get("eventid"))
            if time.monotonic() >= deadline:
                raise LabZabbixError("synthetic_problem_not_observed")
            time.sleep(1)

    def event(self, event_id: str) -> dict[str, Any]:
        events = self._rpc("event.get", {
            "output": ["eventid", "objectid", "source", "object", "value", "acknowledged", "r_eventid"],
            "eventids": [_id(event_id)], "selectAcknowledges": ["acknowledgeid", "action", "message"],
        })
        if (not isinstance(events, list) or len(events) != 1
                or events[0].get("eventid") != event_id
                or not self._source or events[0].get("objectid") != self._source.trigger_id):
            raise LabZabbixError("synthetic_event_identity_rejected")
        return events[0]

    def wait_delivery(self, event_id: str, minimum_count: int, *, update: bool = False,
                      timeout: float = 120) -> list[dict[str, Any]]:
        if type(minimum_count) is not int or minimum_count < 1 or not self._action_id:
            raise LabZabbixError("synthetic_delivery_request_rejected")
        deadline = time.monotonic() + timeout
        while True:
            rows = self._rpc("alert.get", {
                "output": ["alertid", "status", "acknowledgeid"],
                "eventids": [_id(event_id)], "actionids": [self._action_id],
                "mediatypeids": [self._media_id],
            })
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise LabZabbixError("synthetic_delivery_result_rejected")
            if any(row.get("status") == "2" for row in rows):
                raise LabZabbixError("synthetic_webhook_delivery_failed")
            sent = [row for row in rows if row.get("status") == "1"]
            has_update = any(isinstance(row.get("acknowledgeid"), str)
                             and _ID.fullmatch(row["acknowledgeid"]) for row in sent)
            if len(sent) >= minimum_count and (not update or has_update):
                return sent
            if time.monotonic() >= deadline:
                raise LabZabbixError("synthetic_webhook_delivery_not_observed")
            time.sleep(1)

    def human_update(self, event_id: str) -> None:
        self.event(event_id)  # Exact source binding before a synthetic update.
        self._rpc("event.acknowledge", {
            "eventids": [_id(event_id)], "action": 4, "message": "Synthetic source operator update",
        })

    def recover(self, event_id: str, timeout: float = 120) -> str:
        self.event(event_id)
        self._push(0, timeout)
        deadline = time.monotonic() + timeout
        while True:
            recovery_id = self.event(event_id).get("r_eventid")
            if recovery_id and recovery_id != "0":
                return _id(recovery_id)
            if time.monotonic() >= deadline:
                raise LabZabbixError("synthetic_recovery_not_observed")
            time.sleep(1)
