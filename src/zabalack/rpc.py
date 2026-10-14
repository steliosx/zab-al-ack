"""Narrow Zabbix 7.0 JSON-RPC client with real overall deadlines.

Interface sources: versioned official event.get/event.acknowledge/host.get docs
and pinned CEvent.php in docs/reference_manifest.json. No retry middleware.
"""
from __future__ import annotations

import asyncio
import re
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

ID = re.compile(r"^[1-9][0-9]{0,19}$")


class RPCError(Exception):
    def __init__(self, reason: str, *, transient: bool = False, uncertain: bool = False) -> None:
        super().__init__(reason)
        self.reason, self.transient, self.uncertain = reason, transient, uncertain


class ValidationError(RPCError):
    pass


@dataclass(frozen=True)
class Timeouts:
    connect: float = 1.0
    read: float = 3.0
    overall: float = 5.0


WRITE_TIMEOUTS = Timeouts()
READ_TIMEOUTS = Timeouts(2.0, 15.0, 20.0)


def read_protected_file(path: str) -> str:
    from .config import read_protected_file as read_secret
    try:
        result = read_secret(path, maximum=65536).decode().strip()
    except (OSError, ValueError, UnicodeError) as exc:
        raise RPCError("secret_file_unavailable") from exc
    if not result or "\n" in result or "\r" in result:
        raise RPCError("secret_file_invalid")
    return result


@dataclass(frozen=True)
class Endpoint:
    instance: str
    url: str
    token_file: str
    ca_file: str | None
    api_version: str = "7.0.0"
    lab: bool = False

    def validate(self) -> None:
        parsed = urlsplit(self.url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RPCError("endpoint_forbidden_components")
        if parsed.path != "/api_jsonrpc.php" or not parsed.hostname:
            raise RPCError("endpoint_path_invalid")
        loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (self.lab and loopback and parsed.scheme == "http"):
            raise RPCError("endpoint_tls_required")
        if not re.fullmatch(r"7\.0\.[0-9]+", self.api_version):
            raise RPCError("unsupported_api_version")
        if parsed.scheme == "https" and not self.ca_file:
            raise RPCError("endpoint_ca_required")


class EndpointRegistry:
    """Resolve only the trusted logical instance, never any alert URL."""
    def __init__(self, registry: dict[str, dict[str, Any]], *, lab: bool = False,
                 approved_versions: tuple[str, ...] = ("7.0.0",)) -> None:
        self._registry, self._lab = registry, lab
        self._approved_versions = approved_versions

    def resolve(self, instance: str) -> Endpoint:
        item = self._registry.get(instance)
        if item is None:
            raise RPCError("endpoint_not_allowlisted")
        if item.get("logical_instance") != instance:
            raise RPCError("logical_instance_mismatch")
        if item.get("api_version") not in self._approved_versions:
            raise RPCError("unsupported_api_version")
        endpoint = Endpoint(instance, item["url"], item["token_file"], item.get("ca_file"),
                            item.get("api_version", "NOT_VERIFIED"), self._lab)
        endpoint.validate()
        return endpoint


class ZabbixClient:
    def __init__(self, endpoint: Endpoint, *, transport: httpx.AsyncBaseTransport | None = None,
                 timeouts: Timeouts = WRITE_TIMEOUTS, response_limit: int = 1048576,
                 background_timeouts: Timeouts = READ_TIMEOUTS) -> None:
        endpoint.validate()
        self.endpoint, self.transport = endpoint, transport
        self.timeouts, self.response_limit = timeouts, response_limit
        self.background_timeouts = background_timeouts

    async def call(self, method: str, params: dict[str, Any], *, background: bool = False) -> object:
        if method not in {"event.get", "event.acknowledge", "host.get"}:
            raise RPCError("method_not_allowed")
        write = method == "event.acknowledge"
        if write and (type(params.get("action")) is not int or params.get("action") not in (2, 6) or
                      not isinstance(params.get("eventids"), list) or len(params["eventids"]) != 1):
            raise RPCError("write_scope_invalid")
        request_id = uuid.uuid4().hex
        timeout = self.background_timeouts if background else self.timeouts
        # Resolution is intentionally at call time, after the worker has a verified lease.
        token = read_protected_file(self.endpoint.token_file)
        context: ssl.SSLContext | bool = True
        if self.endpoint.ca_file:
            try:
                context = ssl.create_default_context(cafile=self.endpoint.ca_file)
            except (OSError, ssl.SSLError) as exc:
                raise RPCError("tls_ca_invalid") from exc
        try:
            async with asyncio.timeout(timeout.overall):
                async with httpx.AsyncClient(
                    verify=context, follow_redirects=False, trust_env=False, transport=self.transport,
                    timeout=httpx.Timeout(timeout.read, connect=timeout.connect, pool=timeout.connect, write=timeout.read),
                ) as client:
                    async with client.stream("POST", self.endpoint.url,
                        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json-rpc"},
                        json={"jsonrpc": "2.0", "method": method, "params": params, "id": request_id}) as response:
                        if response.status_code != 200:
                            transient = response.status_code in {408, 429, 500, 502, 503, 504}
                            # A non-200 status after a sent write proves nothing about its
                            # effect: an intermediary (520-524, 501, 505, ...) may answer
                            # after the origin applied the acknowledgement. Every non-200
                            # write response is therefore uncertain; only pre-dispatch
                            # refusals (raised before the request) are definite.
                            raise RPCError("http_transient" if transient else "http_rejected",
                                           transient=transient, uncertain=write)
                        body = bytearray()
                        async for part in response.aiter_bytes():
                            body.extend(part)
                            if len(body) > self.response_limit:
                                raise RPCError("response_oversized", uncertain=write)
            try:
                from .config import strict_json_loads
                data = strict_json_loads(body)
            except (ValueError, UnicodeError) as exc:
                raise RPCError("json_response_invalid", uncertain=write) from exc
            if not isinstance(data, dict) or data.get("jsonrpc") != "2.0" or data.get("id") != request_id:
                raise RPCError("jsonrpc_binding_invalid", uncertain=write)
            if "error" in data:
                # Never log an upstream message/data string: it may contain user input.
                if "result" in data or not isinstance(data["error"], dict) or type(data["error"].get("code")) is not int:
                    raise RPCError("jsonrpc_error_invalid", uncertain=write)
                if data["error"]["code"] in {-32600, -32601, -32602}:
                    raise RPCError("api_permission_or_request_rejected")
                # Zabbix 7.0 maps both permission and internal exceptions to
                # -32500, and its outer Throwable handler returns -32603.
                # Neither nor an unfamiliar code proves a sent write had no
                # effect. Suspend delivery in SQL, retaining read-only recovery.
                raise RPCError("api_application_error", uncertain=write)
            if "result" not in data:
                raise RPCError("jsonrpc_result_missing", uncertain=write)
            return data["result"]
        except httpx.ConnectError as exc:
            root: BaseException | None = exc
            tls = False
            while root:
                tls = tls or isinstance(root, ssl.SSLError)
                root = root.__cause__
            raise RPCError("tls_failure" if tls else "endpoint_unreachable", transient=not tls) from exc
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise RPCError("connect_timeout", transient=True) from exc
        except (TimeoutError, httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            raise RPCError("outcome_unknown" if write else "read_transient", transient=True, uncertain=write) from exc

    async def event_get(self, event_id: str, cohort: dict[str, Any], *, history: bool = False,
                        background: bool = False) -> list[dict[str, Any]]:
        if not ID.fullmatch(event_id):
            raise ValidationError("event_id_invalid")
        params: dict[str, Any] = {
            "eventids": [event_id], "source": 0, "object": 0, "value": 1,
            "groupids": cohort["group_ids"],
            "output": ["eventid", "objectid", "source", "object", "value", "clock", "acknowledged", "r_eventid"],
            "selectHosts": ["hostid"],
        }
        if history:
            params["selectAcknowledges"] = ["acknowledgeid", "action", "message", "clock", "userid"]
        result = await self.call("event.get", params, background=background)
        if not isinstance(result, list) or len(result) > 1 or any(not isinstance(x, dict) for x in result):
            raise ValidationError("event_result_shape")
        return result

    async def acknowledge(self, event_id: str, *, comment: bool, public_reference: str,
                          issued_at: str) -> None:
        if not ID.fullmatch(event_id):
            raise ValidationError("event_id_invalid")
        params: dict[str, Any] = {"eventids": [event_id], "action": 6 if comment else 2}
        if comment:
            params["message"] = neutral_comment(issued_at, public_reference)
        result = await self.call("event.acknowledge", params)
        if (not isinstance(result, dict) or set(result) != {"eventids"} or
                not isinstance(result["eventids"], list) or len(result["eventids"]) != 1 or
                str(result["eventids"][0]) != event_id or isinstance(result["eventids"][0], bool)):
            raise RPCError("write_result_ids_invalid", uncertain=True)

    async def host_get(self, host_ids: list[str]) -> list[dict[str, Any]]:
        if not host_ids or len(host_ids) > 50 or any(not ID.fullmatch(x) for x in host_ids):
            raise ValidationError("host_inventory_scope_invalid")
        result = await self.call("host.get", {"hostids": host_ids, "output": ["hostid"],
                                 "selectHostGroups": ["groupid"]}, background=True)
        if not isinstance(result, list) or any(not isinstance(x, dict) for x in result):
            raise ValidationError("host_inventory_shape")
        return result


def neutral_comment(issued_at: str, public_reference: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", public_reference):
        raise ValidationError("public_reference_invalid")
    moment = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValidationError("issued_at_invalid")
    stamp = moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return ("[zabalack] Acknowledged by the monitoring operations team.\n"
            f"Requested at {stamp}. Reference: {public_reference}.")


def validate_event(result: list[dict[str, Any]], source: dict[str, Any], cohort: dict[str, Any]) -> dict[str, Any]:
    if not result:
        raise ValidationError("event_missing_or_not_visible")
    if len(result) != 1:
        raise ValidationError("event_result_shape")
    event = result[0]
    required = {"eventid", "objectid", "source", "object", "value", "clock", "acknowledged", "r_eventid", "hosts"}
    if not isinstance(event, dict) or not required <= event.keys():
        raise ValidationError("event_fields_missing")
    if str(event["eventid"]) != source["zabbix_event_id"] or str(event["objectid"]) != source["zabbix_trigger_id"]:
        raise ValidationError("event_trigger_mismatch")
    if any(str(event[k]) != v for k, v in (("source", "0"), ("object", "0"), ("value", "1"))):
        raise ValidationError("not_original_problem_event")
    if str(event["acknowledged"]) not in {"0", "1"}:
        raise ValidationError("acknowledged_invalid")
    for field in ("r_eventid", "clock"):
        value = event[field]
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"0|[1-9][0-9]{0,19}", str(value)):
            raise ValidationError("event_" + field + "_invalid")
    hosts = event["hosts"]
    if not isinstance(hosts, list) or any(not isinstance(x, dict) or set(x) != {"hostid"} for x in hosts):
        raise ValidationError("host_set_shape")
    actual = [str(x["hostid"]) for x in hosts]
    if len(actual) != len(set(actual)) or set(actual) != set(cohort["host_ids"]):
        raise ValidationError("host_set_mismatch")
    return event


def observed_reference(event: dict[str, Any], *, public_reference: str, issued_at: str) -> bool:
    expected = neutral_comment(issued_at, public_reference)
    records = event.get("acknowledges", [])
    if not isinstance(records, list):
        raise ValidationError("ack_history_invalid")
    for entry in records:
        if not isinstance(entry, dict):
            raise ValidationError("ack_history_invalid")
        try:
            raw = entry.get("action")
            if isinstance(raw, bool) or not isinstance(raw, (str, int)) or not re.fullmatch(r"[0-9]{1,3}", str(raw)):
                raise ValueError
            bits = int(raw)
            if not 0 <= bits <= 511:
                raise ValueError
        except (ValueError, TypeError):
            raise ValidationError("ack_history_invalid") from None
        # A concurrent acknowledgement can make the stored action be message-only (4).
        if bits & 4 and entry.get("message") == expected:
            return True
    return False
