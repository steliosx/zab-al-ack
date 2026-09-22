"""Offline safety checks for the real deployment lab's Zabbix provisioner.

These exercise request construction and fail-closed orchestration. They are
not real Zabbix API, permission, macro expansion or delivery evidence.
"""
import copy
import json
from pathlib import Path

import httpx
import pytest

from lab import deployment_e2e_zabbix as zbx


@pytest.fixture
def lab(monkeypatch):
    monkeypatch.setenv("ACK_DEPLOYMENT_E2E", zbx.ISOLATION_VALUE)
    instance = zbx.ZabbixLab(isolated_lab=True, fresh_instance=True)
    yield instance
    instance.close()


@pytest.mark.parametrize("isolated,fresh,env", [
    (False, True, zbx.ISOLATION_VALUE), (True, False, zbx.ISOLATION_VALUE),
    (True, True, ""), (1, True, zbx.ISOLATION_VALUE),
])
def test_provisioner_guard_precedes_client(monkeypatch, isolated, fresh, env):
    monkeypatch.setenv("ACK_DEPLOYMENT_E2E", env)
    constructed = []
    monkeypatch.setattr(zbx.httpx, "Client", lambda **kwargs: constructed.append(kwargs))
    with pytest.raises(zbx.LabZabbixError, match="fresh_isolated_compose_required"):
        zbx.ZabbixLab(isolated_lab=isolated, fresh_instance=fresh)
    assert constructed == []


def test_rpc_uses_only_fixed_internal_endpoint_and_no_redirects(lab):
    seen = []

    def serve(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://unapproved.invalid/"})

    lab._client.close()
    lab._client = httpx.Client(transport=httpx.MockTransport(serve), trust_env=False, follow_redirects=False)
    with pytest.raises(zbx.LabZabbixError, match="synthetic_rpc_transport_rejected"):
        lab._rpc("apiinfo.version", {}, authenticated=False)
    assert len(seen) == 1
    assert str(seen[0].url) == zbx.API_URL
    assert "authorization" not in seen[0].headers


@pytest.mark.parametrize("body", [
    {"jsonrpc": "2.0", "id": True, "result": zbx.REFERENCE_VERSION},
    {"jsonrpc": "2.0", "id": 99, "result": zbx.REFERENCE_VERSION},
    {"jsonrpc": "2.0", "id": 1, "error": {"data": "PRIVATE_REMOTE_BODY"}},
    {"jsonrpc": "2.0", "id": 1, "result": "ok", "error": "PRIVATE_REMOTE_BODY"},
])
def test_rpc_correlation_and_error_redaction(lab, body):
    lab._client.close()
    lab._client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)))
    with pytest.raises(zbx.LabZabbixError) as caught:
        lab._rpc("apiinfo.version", {}, authenticated=False)
    assert str(caught.value) == "synthetic_rpc_result_rejected"
    assert "PRIVATE_REMOTE_BODY" not in str(caught.value)


def test_action_has_exact_trigger_recipient_and_no_command_or_broadcast():
    action = zbx.source_action("101", "102", "103")
    assert action["filter"]["conditions"] == [{"conditiontype": 2, "operator": 0, "value": "101"}]
    for phase in ("operations", "update_operations", "recovery_operations"):
        assert len(action[phase]) == 1
        operation = action[phase][0]
        assert operation["operationtype"] == 0
        assert operation["opmessage_usr"] == [{"userid": "102"}]
        assert operation["opmessage"]["mediatypeid"] == "103"
        assert not any(key.startswith("opcommand") or key == "opmessage_grp" for key in operation)
    assert action["operations"][0]["esc_step_to"] == 1


def test_bootstrap_refuses_active_actions_before_any_mutation(lab, monkeypatch):
    methods = []

    def rpc(method, params, **kwargs):
        methods.append(method)
        return {"apiinfo.version": zbx.REFERENCE_VERSION, "user.login": "a" * 32,
                "action.get": [{"actionid": "1"}]}[method]

    monkeypatch.setattr(lab, "_rpc", rpc)
    with pytest.raises(zbx.LabZabbixError, match="fresh_instance_has_active_actions"):
        lab.bootstrap()
    assert methods == ["apiinfo.version", "user.login", "action.get"]


def test_worker_role_has_only_source_reads_ack_and_comment(lab, monkeypatch):
    requests = []
    creates = {"hostgroup": "groupids", "host": "hostids", "item": "itemids",
               "trigger": "triggerids", "role": "roleids", "usergroup": "usrgrpids",
               "user": "userids", "token": "tokenids"}
    next_id = 100
    token_id = None
    stored_role = None

    def rpc(method, params, **kwargs):
        nonlocal next_id, token_id, stored_role
        requests.append((method, params))
        if method == "apiinfo.version":
            return zbx.REFERENCE_VERSION
        if method == "user.login":
            return "a" * 32
        if method in ("action.get", "host.get"):
            return []
        if method == "token.generate":
            return [{"tokenid": token_id, "token": "b" * 64}]
        if method == "role.get":
            return [copy.deepcopy(stored_role)]
        if method.endswith(".create"):
            next_id += 1
            if method == "role.create":
                stored_role = {"roleid": str(next_id), "type": "1", "rules": copy.deepcopy(params["rules"])}
                # Real Zabbix preserves ENABLED for omitted existing actions,
                # even when the new default is 0. This is not a deny-all switch.
                supplied = {row["name"]: row["status"] for row in params["rules"]["actions"]}
                available = ("edit_dashboards", "edit_maps", "acknowledge_problems", "suppress_problems",
                             "close_problems", "change_severity", "add_problem_comments", "execute_scripts",
                             "manage_api_tokens", "invoke_execute_now", "change_problem_ranking")
                stored_role["rules"]["actions"] = [{"name": name, "status": supplied.get(name, 1)} for name in available]
            if method == "token.create":
                token_id = str(next_id)
            return {creates[method.split(".")[0]]: [str(next_id)]}
        raise AssertionError(method)

    monkeypatch.setattr(lab, "_rpc", rpc)
    source = lab.bootstrap()
    role = next(params for method, params in requests if method == "role.create")
    assert role["type"] == 1
    assert role["rules"]["api.mode"] == 1
    assert set(role["rules"]["api"]) == {"event.get", "event.acknowledge", "host.get"}
    assert role["rules"]["actions.default_access"] == 0
    assert {row["name"] for row in stored_role["rules"]["actions"] if row["status"]} == {"acknowledge_problems", "add_problem_comments"}
    assert requests.index(next(row for row in requests if row[0] == "role.get")) < requests.index(next(row for row in requests if row[0] == "token.create"))
    rights = next(params for method, params in requests if method == "usergroup.create")
    assert rights["hostgroup_rights"] == [{"id": source.group_id, "permission": 2}]
    assert source.worker_token not in repr(source)


def test_media_import_preserves_shipped_script_and_parameter_macros(lab, monkeypatch):
    lab._source = zbx.LabSource("10", "11", "12", "13", "b" * 64)
    lab._recipient_id = "14"
    root = Path(__file__).resolve().parents[2]
    reference = json.loads((root / "zabbix/v1/media_type.reference.json").read_text())
    definition = reference["zabbix_export"]["media_types"][0]
    requests = []

    def rpc(method, params, **kwargs):
        requests.append((method, params))
        if method == "configuration.import":
            return True
        if method == "mediatype.get":
            return [{"mediatypeid": "15", "script": definition["script"]}]
        if method == "action.create":
            return {"actionids": ["16"]}
        return {"mediatypeids": ["15"]}

    monkeypatch.setattr(lab, "_rpc", rpc)
    lab.enable_media("synthetic-ingest-key")
    imported = next(params for method, params in requests if method == "configuration.import")
    assert json.loads(imported["source"]) == reference
    updated = next(params for method, params in requests if method == "mediatype.update")
    assert "script" not in updated
    parameters = {row["name"]: row["value"] for row in updated["parameters"]}
    assert parameters["alerta_api_url"] == "https://runner:58081/alert"
    assert parameters["event_update_message"] == "{EVENT.UPDATE.MESSAGE}"
    assert parameters["event_update_status"] == "{EVENT.UPDATE.STATUS}"
    assert parameters["event_id"] == "{EVENT.ID}"
    assert parameters["event_recovery_id"] == "{EVENT.RECOVERY.ID}"
    assert parameters["external_writes_enabled"] == "true"
    assert updated["maxattempts"] == 1


@pytest.mark.parametrize("stored,accepted", [
    ("crlf", True), ("identical", True), ("changed", False), ("truncated", False), ("missing", False),
])
def test_media_import_accepts_crlf_storage_but_no_script_change(lab, monkeypatch, stored, accepted):
    """A real Zabbix 7.0.10 import stores the script with CRLF terminators (observed
    on the source-built lab); only the line terminators may differ from the shipped file."""
    lab._source = zbx.LabSource("10", "11", "12", "13", "b" * 64)
    lab._recipient_id = "14"
    root = Path(__file__).resolve().parents[2]
    script = json.loads((root / "zabbix/v1/media_type.reference.json").read_text())["zabbix_export"]["media_types"][0]["script"]
    variants = {"crlf": script.replace("\n", "\r\n"), "identical": script, "changed": script.replace("Acknowledged by", "Modified by"),
                "truncated": script[:-40], "missing": None}

    def rpc(method, params, **kwargs):
        if method == "configuration.import":
            return True
        if method == "mediatype.get":
            return [{"mediatypeid": "15"}] if stored == "missing" else [{"mediatypeid": "15", "script": variants[stored]}]
        if method == "action.create":
            return {"actionids": ["16"]}
        return {"mediatypeids": ["15"]}

    monkeypatch.setattr(lab, "_rpc", rpc)
    if accepted:
        lab.enable_media("synthetic-ingest-key")
    else:
        with pytest.raises(zbx.LabZabbixError, match="reference_media_import_changed_script"):
            lab.enable_media("synthetic-ingest-key")


def test_problem_is_raised_only_after_the_config_cache_grace(lab, monkeypatch):
    """The action must be in the server's configuration cache before the problem
    fires, or no escalation/webhook is ever produced (observed on real 7.0.10)."""
    lab._source = zbx.LabSource("10", "11", "12", "13", "b" * 64)
    lab._action_id = "16"
    lab._media_id = "15"
    clock = {"now": 100.0}
    slept = []
    monkeypatch.setattr(zbx.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(zbx.time, "sleep", lambda seconds: slept.append(seconds))
    lab._action_created_at = 95.0

    def rpc(method, params, **kwargs):
        if method == "history.push":
            return {"response": "success", "data": [{"itemid": 12}]}
        return [{"eventid": "77", "objectid": "13", "value": "1", "acknowledged": "0"}]

    monkeypatch.setattr(lab, "_rpc", rpc)
    assert lab.raise_problem() == "77"
    assert slept and slept[0] == pytest.approx(zbx.CONFIG_CACHE_GRACE_SECONDS - 5.0)
    # Once the grace has elapsed no further wait is inserted.
    slept.clear()
    lab._action_created_at = 100.0 - zbx.CONFIG_CACHE_GRACE_SECONDS - 1
    lab.raise_problem()
    assert slept == [0.0]


def test_update_delivery_requires_sent_record_not_just_silence(lab, monkeypatch):
    lab._action_id, lab._media_id = "10", "11"
    monkeypatch.setattr(lab, "_rpc", lambda *args, **kwargs: [])
    with pytest.raises(zbx.LabZabbixError, match="synthetic_webhook_delivery_not_observed"):
        lab.wait_delivery("12", 1, update=True, timeout=0)


def test_failed_media_delivery_cannot_be_treated_as_filtered_echo(lab, monkeypatch):
    lab._action_id, lab._media_id = "10", "11"
    monkeypatch.setattr(lab, "_rpc", lambda *args, **kwargs: [{"alertid": "13", "status": "2", "acknowledgeid": "14"}])
    with pytest.raises(zbx.LabZabbixError, match="synthetic_webhook_delivery_failed"):
        lab.wait_delivery("12", 1, update=True, timeout=0)


@pytest.mark.parametrize("acknowledge_id", [None, "0", "", True])
def test_update_delivery_requires_a_real_update_identifier(lab, monkeypatch, acknowledge_id):
    lab._action_id, lab._media_id = "10", "11"
    monkeypatch.setattr(lab, "_rpc", lambda *args, **kwargs: [
        {"alertid": "13", "status": "1", "acknowledgeid": acknowledge_id},
    ])
    with pytest.raises(zbx.LabZabbixError, match="synthetic_webhook_delivery_not_observed"):
        lab.wait_delivery("12", 1, update=True, timeout=0)


def test_readiness_rejects_different_reference_version(lab, monkeypatch):
    monkeypatch.setattr(lab, "_rpc", lambda *args, **kwargs: "7.0.0")
    with pytest.raises(zbx.LabZabbixError, match="unexpected_zabbix_reference_version"):
        lab.wait_ready(timeout=0)


@pytest.mark.parametrize("returned_id,accepted", [
    (100, True), ("100", True), (True, False), (100.0, False),
    ("0100", False), (None, False), (101, False),
])
def test_history_push_accepts_exact_native_integer_identifier_only(lab, monkeypatch, returned_id, accepted):
    # Pinned trapper_history_push.c uses zbx_json_adduint64; PHP forwards the
    # decoded integer unchanged. A string-only check loops after successful push.
    lab._source = zbx.LabSource("10", "11", "100", "13", "b" * 64)
    lab._action_id = "14"
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        return {"response": "success", "data": [{"itemid": returned_id}]}

    monkeypatch.setattr(lab, "_rpc", rpc)
    if accepted:
        assert lab._push(1, timeout=0) is None
    else:
        with pytest.raises(zbx.LabZabbixError, match="synthetic_history_push_not_accepted"):
            lab._push(1, timeout=0)
    assert calls == [("history.push", [{"itemid": "100", "value": 1}])]
