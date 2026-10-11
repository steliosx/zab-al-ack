"""Mock HTTP evidence for bounded deadlines and strict JSON-RPC contracts."""
import asyncio
import json
import time

import httpx
import pytest

from zabalack.rpc import Endpoint, RPCError, Timeouts, ZabbixClient, neutral_comment


@pytest.fixture
def endpoint(tmp_path):
    token = tmp_path / "lab-token"
    token.write_text("synthetic-token-not-live")
    token.chmod(0o600)
    return Endpoint("lab-zabbix", "http://127.0.0.1/api_jsonrpc.php", str(token), None, lab=True)


def responder(result):
    def handle(request):
        data = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"], "result": result})
    return httpx.MockTransport(handle)


@pytest.mark.asyncio
async def test_T1_rpc_action_six_exact_neutral_comment(endpoint):
    seen = []
    def handler(request):
        data = json.loads(request.content)
        seen.append(data)
        assert request.headers["Authorization"] == "Bearer synthetic-token-not-live"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"], "result": {"eventids": ["12001"]}})
    client = ZabbixClient(endpoint, transport=httpx.MockTransport(handler))
    await client.acknowledge("12001", comment=True, public_reference="publicreference0123456", issued_at="2026-01-01T01:00:00Z")
    assert seen[0]["params"] == {"eventids": ["12001"], "action": 6,
        "message": "[zabalack] Acknowledged by the monitoring operations team.\nRequested at 2026-01-01T01:00:00Z. Reference: publicreference0123456."}


@pytest.mark.asyncio
async def test_action_two_has_no_comment(endpoint):
    seen = []
    def handler(request):
        data = json.loads(request.content)
        seen.append(data)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"], "result": {"eventids": ["12001"]}})
    await ZabbixClient(endpoint, transport=httpx.MockTransport(handler)).acknowledge(
        "12001", comment=False, public_reference="publicreference0123456", issued_at="2026-01-01T01:00:00Z")
    assert seen[0]["params"] == {"eventids": ["12001"], "action": 2}


@pytest.mark.asyncio
async def test_T29_overall_deadline_marks_write_unknown_without_retry(endpoint):
    requests = 0
    async def slow(request):
        nonlocal requests
        requests += 1
        await asyncio.sleep(0.2)
        return httpx.Response(200)
    client = ZabbixClient(endpoint, transport=httpx.MockTransport(slow), timeouts=Timeouts(1, 3, 0.02))
    started = time.monotonic()
    with pytest.raises(RPCError) as error:
        await client.acknowledge("12001", comment=False, public_reference="publicreference0123456", issued_at="2026-01-01T01:00:00Z")
    assert error.value.uncertain and error.value.transient
    assert time.monotonic() - started < 0.15
    assert requests == 1


@pytest.mark.asyncio
async def test_oversized_stream_marks_write_unknown(endpoint):
    client = ZabbixClient(endpoint, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 1025)), response_limit=1024)
    with pytest.raises(RPCError) as error:
        await client.acknowledge("12001", comment=False, public_reference="publicreference0123456", issued_at="2026-01-01T01:00:00Z")
    assert error.value.reason == "response_oversized"
    assert error.value.uncertain


@pytest.mark.parametrize("result", [{"eventids": []}, {"eventids": ["different"]}, {"eventids": ["12001", "12002"]}, {"eventids": [True]}, {}, []])
@pytest.mark.asyncio
async def test_strict_result_event_ids(endpoint, result):
    client = ZabbixClient(endpoint, transport=responder(result))
    with pytest.raises(RPCError) as error:
        await client.acknowledge("12001", comment=False, public_reference="publicreference0123456", issued_at="2026-01-01T01:00:00Z")
    assert error.value.reason == "write_result_ids_invalid"
    assert error.value.uncertain


@pytest.mark.asyncio
async def test_T28_jsonrpc_permission_error_is_not_http_success(endpoint):
    def handler(request):
        data = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"], "error": {"code": -32602, "message": "redacted permission error"}})
    client = ZabbixClient(endpoint, transport=httpx.MockTransport(handler))
    with pytest.raises(RPCError) as error:
        await client.acknowledge("12001", comment=False, public_reference="publicreference0123456", issued_at="2026-01-01T01:00:00Z")
    assert error.value.reason == "api_permission_or_request_rejected"
    assert not error.value.transient


@pytest.mark.asyncio
async def test_T13_read_uses_exact_problem_id_no_latest_lookup(endpoint):
    seen = []
    def handler(request):
        data = json.loads(request.content)
        seen.append(data)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"], "result": []})
    await ZabbixClient(endpoint, transport=httpx.MockTransport(handler)).event_get(
        "12001", {"group_ids": ["6001"]}, history=True)
    params = seen[0]["params"]
    assert params["eventids"] == ["12001"]
    assert {k: params[k] for k in ("source", "object", "value")} == {"source": 0, "object": 0, "value": 1}
    assert "objectids" not in params and "sortorder" not in params
    assert params["selectHosts"] == ["hostid"]
    assert params["selectAcknowledges"] == ["acknowledgeid", "action", "message", "clock", "userid"]


@pytest.mark.asyncio
async def test_no_multiple_event_write_or_other_methods(endpoint):
    client = ZabbixClient(endpoint, transport=responder({}))
    with pytest.raises(RPCError, match="write_scope_invalid"):
        await client.call("event.acknowledge", {"eventids": ["12001", "12002"], "action": 6})
    with pytest.raises(RPCError, match="method_not_allowed"):
        await client.call("script.execute", {})


@pytest.mark.parametrize("code", [-32500, -32603, -32400, -32300, -32700, 12345])
@pytest.mark.parametrize("method", ["event.acknowledge", "event.get"])
async def test_application_errors_never_establish_no_write_effect(endpoint, code, method):
    requests = []
    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"],
            "error": {"code": code, "message": "private-upstream-text", "data": "private-data"}})
    client = ZabbixClient(endpoint, transport=httpx.MockTransport(handler))
    with pytest.raises(RPCError) as error:
        await client.call(method, {"eventids": ["12001"], "action": 2})
    assert error.value.reason == "api_application_error"
    assert error.value.uncertain is (method == "event.acknowledge")
    assert not error.value.transient
    assert "private" not in str(error.value)
    assert len(requests) == 1


@pytest.mark.parametrize("code", [-32600, -32601, -32602])
async def test_verified_dispatch_rejections_are_settled(endpoint, code):
    def handler(request):
        data = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"],
            "error": {"code": code, "message": "private"}})
    client = ZabbixClient(endpoint, transport=httpx.MockTransport(handler))
    with pytest.raises(RPCError) as error:
        await client.call("event.acknowledge", {"eventids": ["12001"], "action": 2})
    assert error.value.reason == "api_permission_or_request_rejected"
    assert not error.value.uncertain and not error.value.transient


async def test_boolean_error_code_is_not_an_integer_rejection(endpoint):
    def handler(request):
        data = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": data["id"], "error": {"code": True}})
    client = ZabbixClient(endpoint, transport=httpx.MockTransport(handler))
    with pytest.raises(RPCError, match="jsonrpc_error_invalid") as error:
        await client.call("event.acknowledge", {"eventids": ["12001"], "action": 2})
    assert error.value.uncertain


@pytest.mark.parametrize("status", [501, 505, 520, 522, 524, 400, 403, 404], ids=lambda s: f"http-{s}")
@pytest.mark.asyncio
async def test_any_non_200_write_response_is_uncertain_not_rejected(endpoint, status):
    """Internal adversarial pass F2 (11 Oct 2026): an intermediary may answer with a
    status outside the transient set after the origin applied the write."""
    client = ZabbixClient(endpoint, transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    with pytest.raises(RPCError) as error:
        await client.acknowledge("12001", comment=False, public_reference="publicreference0123456", issued_at="2026-01-01T01:00:00Z")
    assert error.value.reason == "http_rejected"
    assert error.value.uncertain is True
    assert error.value.transient is False


@pytest.mark.parametrize("status", [502, 503, 504, 429], ids=lambda s: f"http-{s}")
@pytest.mark.asyncio
async def test_transient_write_response_stays_uncertain_and_transient(endpoint, status):
    client = ZabbixClient(endpoint, transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    with pytest.raises(RPCError) as error:
        await client.acknowledge("12001", comment=False, public_reference="publicreference0123456", issued_at="2026-01-01T01:00:00Z")
    assert error.value.reason == "http_transient" and error.value.uncertain and error.value.transient


@pytest.mark.parametrize("status", [501, 520, 403], ids=lambda s: f"http-{s}")
@pytest.mark.asyncio
async def test_non_200_read_response_is_rejected_and_certain(endpoint, status):
    """Reads have no effect to be uncertain about; a refusal stays a definite refusal."""
    client = ZabbixClient(endpoint, transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    with pytest.raises(RPCError) as error:
        await client.call("event.get", {"eventids": ["12001"]}, background=True)
    assert error.value.reason == "http_rejected"
    assert error.value.uncertain is False
