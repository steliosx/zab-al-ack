"""Independent negative-boundary review tests; HTTP responses are mocked evidence."""
import json

import httpx
import pytest

from zabalack.rpc import (Endpoint, EndpointRegistry, RPCError, ZabbixClient,
                                ValidationError, neutral_comment, observed_reference,
                                read_protected_file, validate_event)


@pytest.fixture
def token_file(tmp_path):
    value = tmp_path / 'synthetic-token'
    value.write_text('synthetic-lab-token')
    value.chmod(0o600)
    return value


@pytest.mark.parametrize('body_type', ['duplicate_id', 'duplicate_result', 'nonfinite'])
async def test_T28_rpc_rejects_ambiguous_json_objects(token_file, body_type):
    def handler(request):
        req = json.loads(request.content)
        ident = json.dumps(req['id'])
        if body_type == 'duplicate_id':
            body = '{"jsonrpc":"2.0","id":"unrelated","id":' + ident + ',"result":[]}'
        elif body_type == 'duplicate_result':
            body = '{"jsonrpc":"2.0","id":' + ident + ',"result":null,"result":[]}'
        else:
            body = '{"jsonrpc":"2.0","id":' + ident + ',"result":NaN}'
        return httpx.Response(200, content=body)
    client = ZabbixClient(Endpoint('lab', 'http://127.0.0.1/api_jsonrpc.php', str(token_file), None, lab=True),
                          transport=httpx.MockTransport(handler))
    with pytest.raises(RPCError):
        await client.call('event.get', {'eventids': ['101']})


def test_T28_token_symlink_is_rejected(token_file, tmp_path):
    alias = tmp_path / 'token-alias'
    alias.symlink_to(token_file)
    with pytest.raises((RPCError, OSError, ValueError)):
        read_protected_file(str(alias))


def test_T13_registry_checks_logical_identity():
    registry = EndpointRegistry({'lab-A': {'logical_instance': 'lab-B',
        'url': 'https://127.0.0.1/api_jsonrpc.php', 'token_file': '/tmp/lab-token',
        'ca_file': '/tmp/lab-ca', 'api_version': '7.0.0'}})
    with pytest.raises(RPCError):
        registry.resolve('lab-A')


@pytest.mark.parametrize('bits', [-1, True, 'nonsense', None])
def test_T29_malformed_reference_history_cannot_confirm_outcome(bits):
    stamp, ref = '2026-01-01T00:00:00Z', 'public_reference_0123456789'
    event = {'acknowledges': [{'action': bits, 'message': neutral_comment(stamp, ref)}]}
    with pytest.raises(ValidationError):
        observed_reference(event, public_reference=ref, issued_at=stamp)


def test_T12_full_host_set_rejects_partial_group_match():
    source = {'zabbix_event_id': '101', 'zabbix_trigger_id': '201'}
    event = {'eventid': '101', 'objectid': '201', 'source': '0', 'object': '0', 'value': '1',
             'clock': '1700000000', 'acknowledged': '0', 'r_eventid': '0',
             'hosts': [{'hostid': '301'}, {'hostid': '999'}]}
    with pytest.raises(ValidationError, match='host_set_mismatch'):
        validate_event([event], source, {'host_ids': ['301']})


@pytest.mark.parametrize('url', [
    'https://example.invalid/api_jsonrpc.php?eventids=101',
    'https://user:token@example.invalid/api_jsonrpc.php',
    'https://example.invalid/redirect', 'http://example.invalid/api_jsonrpc.php',
])
def test_T13_endpoint_disallows_callback_components(url):
    with pytest.raises(RPCError):
        Endpoint('lab', url, '/tmp/token', '/tmp/ca').validate()


async def test_T28_redirect_never_resolves_target(token_file):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={'Location': 'http://192.0.2.1/api_jsonrpc.php'})
    client = ZabbixClient(Endpoint('lab', 'http://127.0.0.1/api_jsonrpc.php', str(token_file), None, lab=True),
                          transport=httpx.MockTransport(handler))
    with pytest.raises(RPCError):
        await client.call('event.get', {'eventids': ['101']})
    assert len(requests) == 1
