"""Regression for A4; offline orchestration, not source permission evidence."""
import copy

import pytest

from lab.deployment_e2e_zabbix import ZabbixLab, LabZabbixError, ISOLATION_VALUE
from lab.zabbix_roles import role_rules, verify_role


@pytest.mark.parametrize("change", ["suppression", "missing_denial", "duplicate", "future_rule", "boolean", "api_wildcard", "wrong_role"])
def test_effective_role_readback_rejects_ambiguous_or_broader_permissions(change):
    expected = role_rules()
    result = [{"roleid": "7", "type": "1", "rules": copy.deepcopy(expected)}]
    rules = result[0]["rules"]
    if change == "suppression":
        next(row for row in rules["actions"] if row["name"] == "suppress_problems")["status"] = 1
    elif change == "missing_denial":
        rules["actions"] = [row for row in rules["actions"] if row["name"] != "suppress_problems"]
    elif change == "duplicate":
        rules["actions"].append(copy.deepcopy(rules["actions"][0]))
    elif change == "future_rule":
        rules["actions"].append({"name": "new_uninspected_action", "status": 1})
    elif change == "boolean":
        rules["api.access"] = True
    elif change == "api_wildcard":
        rules["api"] = ["*"]
    else:
        result[0]["roleid"] = "8"
    with pytest.raises(ValueError, match="synthetic_role_readback_rejected"):
        verify_role(result, "7", expected)


def test_failed_readback_prevents_user_and_token_creation(monkeypatch):
    monkeypatch.setenv("ACK_DEPLOYMENT_E2E", ISOLATION_VALUE)
    lab = ZabbixLab(isolated_lab=True, fresh_instance=True)
    methods = []
    creates = {"hostgroup.create": "groupids", "host.create": "hostids", "item.create": "itemids",
               "trigger.create": "triggerids", "role.create": "roleids"}

    def rpc(method, params, **kwargs):
        methods.append(method)
        if method == "apiinfo.version":
            return "7.0.10"
        if method == "user.login":
            return "a" * 32
        if method in ("action.get", "host.get"):
            return []
        if method == "role.get":
            return []  # Read-back cannot establish the role actually exists.
        return {creates[method]: ["7"]}

    monkeypatch.setattr(lab, "_rpc", rpc)
    try:
        with pytest.raises(LabZabbixError, match="synthetic_role_readback_rejected"):
            lab.bootstrap()
    finally:
        lab.close()
    assert not {"usergroup.create", "user.create", "token.create", "token.generate"} & set(methods)


def test_witness_shape_enables_no_action_and_only_read_methods():
    """lab/real_zabbix.md documents this constructor for the read-only witness."""
    witness = role_rules(methods=("event.get", "host.get"), actions=())
    assert {row["status"] for row in witness["actions"]} == {0}
    assert witness["api"] == ["event.get", "host.get"] and witness["api.mode"] == 1
    assert [row["name"] for row in witness["ui"] if row["status"]] == ["monitoring.problems"]
    verify_role([{"roleid": "9", "type": "1", "rules": witness}], "9", witness)


def test_role_rules_refuses_scope_beyond_the_inspected_source_contract():
    for kwargs in ({"methods": ("event.get", "trigger.update")}, {"actions": ("close_problems",)},
                   {"ui": ("monitoring.hosts",)}):
        with pytest.raises(ValueError, match="synthetic_role_scope_invalid"):
            role_rules(**kwargs)
