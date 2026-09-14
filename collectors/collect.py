#!/usr/bin/env python3
"""Explicit-target G1–G4 read-only inventories. No application config execution.

Output is an allowlisted evidence envelope, not an unredacted configuration dump.
Live execution is an operator deployment action and is never part of lab tests.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

MAX_BYTES = 4 * 1024 * 1024
MACRO = re.compile(r"^\{(?:EVENT|TRIGGER|HOST|ALERT|USER)\.[A-Z0-9_.]+\}$")
ID = re.compile(r"^[1-9][0-9]{0,19}$")
CONFIG_FIELDS = {"AUTH_REQUIRED", "CUSTOMER_VIEWS", "PLUGINS", "PLUGINS_RAISE_ON_ERROR", "HISTORY_LIMIT", "ALARM_MODEL", "DATABASE_NAME"}
METHODS = {"apiinfo.version", "action.get", "mediatype.get", "trigger.get", "host.get"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_file(path: Path, protected: bool = False) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("file must be regular and within size limit")
        if protected and (info.st_mode & 0o077 or info.st_uid not in (0, os.geteuid())):
            raise ValueError("secret/service reference file must have mode 0600 or stricter")
        return os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)


def local_inventory(config_path: Path) -> dict[str, Any]:
    raw = read_file(config_path)
    tree = ast.parse(raw.decode("utf-8"), filename="<configuration>")
    literal: dict[str, Any] = {}
    unresolved = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        for target in statement.targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if name not in CONFIG_FIELDS and name not in {"DATABASE_URL", "DATABASE_URI", "DATABASE", "ROUTING"}:
                continue
            try:
                value = ast.literal_eval(statement.value)
            except (ValueError, TypeError, MemoryError, RecursionError):
                unresolved.append(name)
                continue
            if name in {"DATABASE_URL", "DATABASE_URI", "DATABASE"}:
                literal[name] = {"configured": bool(value), "value": "[REDACTED]", "alignment_requires_operator_evidence": True}
            elif name == "ROUTING":
                literal[name] = {"configured": bool(value), "effective_order_requires_runtime_test": True}
            elif name == "DATABASE_NAME":
                literal[name] = "[REDACTED_DATABASE_NAME]"
            elif name == "PLUGINS" and isinstance(value, (list, tuple)):
                literal[name] = [v for v in value if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", v)]
            elif name == "ALARM_MODEL":
                literal[name] = value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) else "[REDACTED_NONSTANDARD]"
            elif isinstance(value, (bool, int, type(None))):
                literal[name] = value
            else:
                unresolved.append(name)
    versions = {}
    for package in ("alerta-server", "psycopg", "psycopg2", "psycopg2-binary", "Flask", "zabalack"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {"python": sys.version.split()[0], "packages": versions, "static_configuration": literal,
            "unresolved_configuration": sorted(set(unresolved)), "runtime_configuration_verified": False,
            "user_key_customer_mappings_collected": False, "source_sha256": hashlib.sha256(raw).hexdigest()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any) -> None:
        raise ValueError("redirect refused")


def rpc_read(endpoint: str, ca_file: str, token: str, method: str, params: dict[str, Any]) -> Any:
    if method not in METHODS:
        raise ValueError("method outside collector read-only allowlist")
    url = urllib.parse.urlsplit(endpoint)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment or url.path != "/api_jsonrpc.php":
        raise ValueError("explicit HTTPS /api_jsonrpc.php endpoint required")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    headers = {"Content-Type": "application/json-rpc"}
    if method != "apiinfo.version":
        headers["Authorization"] = "Bearer " + token
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))
    with opener.open(urllib.request.Request(endpoint, data=body, headers=headers), timeout=15) as response:
        content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("response size limit")
    payload = json.loads(content)
    if payload.get("jsonrpc") != "2.0" or type(payload.get("id")) is not int or payload["id"] != 1 or "error" in payload or "result" not in payload:
        raise ValueError("invalid or unsuccessful JSON-RPC response")
    return payload["result"]


def operation_summary(operation: dict[str, Any]) -> dict[str, Any]:
    result = {k: operation[k] for k in ("operationtype", "esc_step_from", "esc_step_to", "evaltype") if k in operation}
    message = operation.get("opmessage", {})
    result["message"] = {k: message[k] for k in ("default_msg", "mediatypeid") if k in message}
    result["recipient_user_count"] = len(operation.get("opmessage_usr", []))
    result["recipient_group_count"] = len(operation.get("opmessage_grp", []))
    result["command_present"] = bool(operation.get("opcommand"))
    result["custom_message_present"] = bool(message.get("message") or message.get("subject"))
    # No command body, destination, person, free text, or script is emitted.
    return result


def zabbix_inventory(args: argparse.Namespace) -> dict[str, Any]:
    token = read_file(Path(args.token_file), protected=True).decode().strip()
    for identifiers in (args.action_id, args.media_type_id, args.trigger_id):
        if any(not ID.fullmatch(value) for value in identifiers):
            raise ValueError("positive decimal object IDs required")
    call = lambda method, params: rpc_read(args.endpoint, args.ca_file, token, method, params)
    result: dict[str, Any] = {"api_version": call("apiinfo.version", {}), "api_permission_verified": False,
                              "endpoint": "[REDACTED]", "selected_only": True, "actions": [], "media_types": [], "triggers": []}
    if args.action_id:
        actions = call("action.get", {"actionids": args.action_id, "output": ["actionid", "eventsource", "status"],
                                      "selectOperations": "extend", "selectRecoveryOperations": "extend", "selectUpdateOperations": "extend"})
        for action in actions:
            row = {key: action[key] for key in ("actionid", "eventsource", "status") if key in action}
            for key in ("operations", "recovery_operations", "update_operations"):
                row[key] = [operation_summary(item) for item in action.get(key, [])]
            result["actions"].append(row)
    if args.media_type_id:
        media = call("mediatype.get", {"mediatypeids": args.media_type_id, "output": ["mediatypeid", "type", "status", "script", "parameters"]})
        for item in media:
            script = item.get("script", "")
            result["media_types"].append({"mediatypeid": item.get("mediatypeid"), "type": item.get("type"), "status": item.get("status"),
                "script_sha256": hashlib.sha256(script.encode()).hexdigest(), "script_review_required": True,
                "parameters": [{"name": p["name"] if re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", p.get("name", "")) else "[REDACTED]",
                                "value": p["value"] if MACRO.fullmatch(p.get("value", "")) else "[REDACTED]"} for p in item.get("parameters", [])]})
    if args.trigger_id:
        triggers = call("trigger.get", {"triggerids": args.trigger_id, "output": ["triggerid", "type", "recovery_mode", "correlation_mode", "manual_close", "status"], "selectHosts": ["hostid"]})
        for trigger in triggers:
            fields = ("triggerid", "type", "recovery_mode", "correlation_mode", "manual_close", "status")
            row = {key: value for key in fields if isinstance((value := trigger.get(key)), str) and re.fullmatch(r"[0-9]{1,20}", value)}
            row["hosts"] = [{"hostid": host["hostid"]} for host in trigger.get("hosts", [])
                            if isinstance(host.get("hostid"), str) and ID.fullmatch(host["hostid"])]
            result["triggers"].append(row)
    return result


PG_QUERIES = {
    "server": "SELECT current_setting('server_version') AS version, pg_is_in_recovery() AS in_recovery",
    "roles": "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = ANY(%s)",
    "role_memberships": "WITH RECURSIVE ancestry(root,member,roleid,admin_option,inherit_option,set_option,path) AS (SELECT r.rolname, a.member,a.roleid,a.admin_option,a.inherit_option,a.set_option,ARRAY[a.member,a.roleid] FROM pg_roles r JOIN pg_auth_members a ON a.member=r.oid WHERE r.rolname = ANY(%s) UNION ALL SELECT s.root,a.member,a.roleid,a.admin_option,a.inherit_option,a.set_option,s.path||a.roleid FROM ancestry s JOIN pg_auth_members a ON a.member=s.roleid WHERE NOT a.roleid=ANY(s.path)) SELECT root,pg_get_userbyid(member) AS member_role,pg_get_userbyid(roleid) AS granted_role,admin_option,inherit_option,set_option FROM ancestry",
    "alerts_owner": "SELECT n.nspname AS schema, c.relname, r.rolname AS owner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='public' AND c.relname='alerts'",
    "columns": "SELECT column_name, data_type, udt_name FROM information_schema.columns WHERE table_schema='public' AND table_name='alerts' ORDER BY ordinal_position",
    "triggers": "SELECT t.tgname, t.tgenabled, t.tgtype, pn.nspname AS function_schema, p.proname AS function_name FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace WHERE n.nspname='public' AND c.relname='alerts' AND NOT t.tgisinternal",
    "column_grants": "SELECT grantee, column_name, privilege_type FROM information_schema.column_privileges WHERE table_schema='public' AND table_name='alerts' AND grantee = ANY(%s)",
    "zabalack_functions": "SELECT p.proname, pg_get_userbyid(p.proowner) AS owner, p.prosecdef AS security_definer, ARRAY(SELECT item FROM unnest(p.proconfig) AS item WHERE item LIKE 'search_path=%') AS controlled_search_path, pg_get_function_identity_arguments(p.oid) AS arguments FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='zabalack' ORDER BY p.proname",
}


def postgres_inventory(args: argparse.Namespace) -> dict[str, Any]:
    import psycopg
    from psycopg.rows import dict_row
    # libpq service file may contain a password reference; never dump DSN or exceptions.
    service = args.service
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", service):
        raise ValueError("invalid explicit libpq service name")
    result = {}
    with psycopg.connect(service=service, connect_timeout=3, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("BEGIN READ ONLY")
        try:
            conn.execute("SET LOCAL statement_timeout = '3s'")
            conn.execute("SET LOCAL lock_timeout = '500ms'")
            for name, query in PG_QUERIES.items():
                result[name] = conn.execute(query, (args.role,) if "%s" in query else None).fetchall()
        finally:
            conn.execute("ROLLBACK")
    return result


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_attributes = {"source_system", "zabbix_instance", "zabbix_event_id", "zabbix_trigger_id", "zabbix_cause_event_id", "zabbix_recovery_event_id", "source_contract_version"}
    attributes = payload.get("attributes", {})
    clean = {}
    for key in allowed_attributes:
        value = attributes.get(key)
        if key.endswith("_id"):
            clean[key] = value if value is None or isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}|\{[A-Z0-9_.]+\}", value) else "[INVALID_OR_REDACTED]"
        elif key == "source_system":
            clean[key] = value if value == "zabbix" else "[INVALID_OR_REDACTED]"
        elif key == "source_contract_version":
            clean[key] = value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,40}", value) else "[INVALID_OR_REDACTED]"
        elif key == "zabbix_instance":
            clean[key] = "[INSTANCE_REFERENCE_REQUIRED]" if value else None
    return {"customer_present": bool(payload.get("customer")), "resource_present": bool(payload.get("resource")),
            "event_present": bool(payload.get("event")), "identity_values_redacted": True,
            "attributes": clean, "full_alert_omitted": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    sub = parser.add_subparsers(dest="kind", required=True)
    local = sub.add_parser("local")
    local.add_argument("--config", required=True, type=Path)
    pg = sub.add_parser("postgres")
    pg.add_argument("--service", required=True)
    pg.add_argument("--role", action="append", required=True)
    zbx = sub.add_parser("zabbix")
    zbx.add_argument("--endpoint", required=True)
    zbx.add_argument("--ca-file", required=True)
    zbx.add_argument("--token-file", required=True)
    for option in ("action-id", "media-type-id", "trigger-id"):
        zbx.add_argument("--" + option, action="append", default=[])
    red = sub.add_parser("payload")
    red.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.kind == "local":
            data = local_inventory(args.config)
        elif args.kind == "postgres":
            data = postgres_inventory(args)
        elif args.kind == "zabbix":
            data = zabbix_inventory(args)
        else:
            data = redact_payload(json.loads(read_file(args.input)))
        envelope = {"schema_version": 1, "collector": args.kind, "collected_at": datetime.now(timezone.utc).isoformat(),
                    "evidence_type": "operator_inventory", "deployment_gate_passed": False, "data": data}
        output = json.dumps(envelope, indent=2, sort_keys=True, default=str).encode() + b"\n"
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(output)
        return 0
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": "collector failed; inspect locally without exporting secrets"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
