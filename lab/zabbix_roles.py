"""Explicit permissions for fresh synthetic Zabbix 7.0 lab roles (A4).

CRole::compileActionsRules/compileUiRules preserve the old/default value of
omitted rules even when *.default_access changes. Enumerate each inspected
User-type rule, then verify effective role.get output before issuing a token.
Unknown rules fail the read-back check and need a new upstream inspection.
"""
from __future__ import annotations

from typing import Any


ACTION_NAMES = (
    "edit_dashboards", "edit_maps", "acknowledge_problems", "suppress_problems",
    "close_problems", "change_severity", "add_problem_comments", "execute_scripts",
    "manage_api_tokens", "invoke_execute_now", "change_problem_ranking",
)
UI_NAMES = (
    "monitoring.dashboard", "monitoring.problems", "monitoring.hosts",
    "monitoring.latest_data", "monitoring.maps", "services.services",
    "services.sla_report", "inventory.overview", "inventory.hosts",
    "reports.availability_report", "reports.top_triggers",
)
SOURCE_METHODS = ("event.get", "event.acknowledge", "host.get")
SOURCE_ACTIONS = ("acknowledge_problems", "add_problem_comments")


def role_rules(*, methods=SOURCE_METHODS, actions=SOURCE_ACTIONS,
               ui=("monitoring.problems",)) -> dict[str, Any]:
    if (not set(methods) <= set(SOURCE_METHODS) or not set(actions) <= set(SOURCE_ACTIONS)
            or not set(ui) <= {"monitoring.problems"}):
        raise ValueError("synthetic_role_scope_invalid")
    return {
        "ui.default_access": 0,
        "ui": [{"name": name, "status": int(name in ui)} for name in UI_NAMES],
        "modules.default_access": 0, "api.access": 1, "api.mode": 1,
        "api": list(methods), "actions.default_access": 0,
        "actions": [{"name": name, "status": int(name in actions)} for name in ACTION_NAMES],
    }


def verify_role(result: Any, role_id: str, expected: dict[str, Any]) -> None:
    """Reject broadened, omitted, duplicate or ambiguous effective permissions."""
    def flag(value):
        if type(value) not in (int, str) or value not in (0, 1, "0", "1"):
            raise ValueError("synthetic_role_readback_rejected")
        return int(value)

    def rules_map(rows):
        if not isinstance(rows, list):
            raise ValueError("synthetic_role_readback_rejected")
        mapped = {}
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("name"), str)
                    or row["name"] in mapped):
                raise ValueError("synthetic_role_readback_rejected")
            mapped[row["name"]] = flag(row.get("status"))
        return mapped

    try:
        if (not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], dict)
                or result[0].get("roleid") != role_id or str(result[0].get("type")) != "1"):
            raise ValueError
        actual = result[0]["rules"]
        if not isinstance(actual, dict):
            raise ValueError
        for key in ("ui.default_access", "modules.default_access", "api.access", "api.mode", "actions.default_access"):
            if flag(actual.get(key)) != expected[key]:
                raise ValueError
        for key in ("actions", "ui"):
            if rules_map(actual.get(key)) != rules_map(expected[key]):
                raise ValueError
        methods = actual.get("api")
        if (not isinstance(methods, list) or any(not isinstance(x, str) for x in methods)
                or len(methods) != len(set(methods)) or set(methods) != set(expected["api"])):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("synthetic_role_readback_rejected") from None
