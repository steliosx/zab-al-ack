"""Mocked fixture regressions; no native service or source-permission evidence."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=[('test_contract.py', 'real_fixture'), ('test_end_to_end.py', 'real_lab')],
                ids=['contract', 'end-to-end'])
def fixture_call(request, monkeypatch, tmp_path):
    filename, name = request.param
    spec = importlib.util.spec_from_file_location('guard_' + name, ROOT / 'tests/real_zabbix' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture_path = tmp_path / 'fixture.json'
    token_path = tmp_path / 'token-must-not-be-opened'
    data = {'isolated_lab': True, 'fresh_instance_no_notifications': True,
            'url': 'http://127.0.0.1:58080/api_jsonrpc.php', 'token_file': str(token_path),
            'end_to_end': {'zabbix_event_id': '1001', 'zabbix_trigger_id': '1002'}}
    monkeypatch.setenv('ACK_TEST_REAL_ZABBIX_FIXTURE', str(fixture_path))
    monkeypatch.setenv('ACK_TEST_REAL_ZABBIX_LAB', 'YES_ISOLATED_LAB')
    monkeypatch.setenv('ACK_TEST_REAL_ZABBIX_WRITE', 'ACKNOWLEDGE_SYNTHETIC_LAB_EVENT')
    read_text = Path.read_text

    def read_without_token(path, *args, **kwargs):
        assert path != token_path, 'A fixture guard must never open the token'
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', read_without_token)

    def run():
        fixture_path.write_text(json.dumps(data))
        return getattr(module, name).__wrapped__()

    return module, data, run


@pytest.mark.parametrize('url', [
    'http://192.0.2.1:58080/api_jsonrpc.php',
    'http://localhost:58080/api_jsonrpc.php',
    'http://[::1]:58080/api_jsonrpc.php',
    'http://127.0.0.1:58081/api_jsonrpc.php',
    'https://127.0.0.1:58080/api_jsonrpc.php',
    'http://127.0.0.1:58080/other',
    'http://user:synthetic-secret@127.0.0.1:58080/api_jsonrpc.php',
    'http://127.0.0.1:58080/api_jsonrpc.php?token=synthetic-secret',
    'http://127.0.0.1:58080/api_jsonrpc.php#synthetic-secret',
    'http://[malformed/api_jsonrpc.php',
    'http://127.0.0.1:not-a-port/api_jsonrpc.php',
    '\nhttp://127.0.0.1:58080/api_jsonrpc.php',
    None,
], ids=['remote', 'hostname', 'other-loopback', 'port', 'scheme', 'path', 'userinfo',
        'query', 'fragment', 'malformed-host', 'malformed-port', 'control-character', 'missing'])
def test_real_zabbix_fixture_rejects_target_before_client_or_token(fixture_call, monkeypatch, url):
    module, data, run = fixture_call
    data['url'] = url

    def no_client(*args, **kwargs):
        pytest.fail('No HTTP client may be constructed for a rejected target')

    monkeypatch.setattr(module.httpx, 'Client', no_client)
    with pytest.raises(pytest.fail.Exception, match='declared literal-loopback') as caught:
        run()
    assert 'synthetic-secret' not in str(caught.value)


def fake_version_client(module, monkeypatch, replacement):
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            assert url == 'http://127.0.0.1:58080/api_jsonrpc.php'
            assert 'headers' not in kwargs, 'Version probe must remain unauthenticated'
            request = kwargs['json']
            assert request['method'] == 'apiinfo.version'
            calls.append(request)
            envelope = {'jsonrpc': '2.0', 'id': request['id'], 'result': '7.0.10'}
            envelope = replacement(envelope)

            class Response:
                def raise_for_status(self):
                    pass

                def json(self):
                    return envelope

            return Response()

    monkeypatch.setattr(module.httpx, 'Client', Client)
    return calls


@pytest.mark.parametrize('replacement', [
    lambda valid: valid | {'id': 'another-request'},
    lambda valid: valid | {'jsonrpc': '1.0'},
    lambda valid: valid | {'error': {'data': 'synthetic-secret'}},
    lambda valid: {key: value for key, value in valid.items() if key != 'result'},
    lambda valid: valid | {'result': '8.0.0'},
    lambda valid: valid | {'result': {'token': 'synthetic-secret'}},
    lambda valid: [],
], ids=['wrong-id', 'wrong-jsonrpc', 'error-and-result', 'missing-result', 'wrong-version',
        'nonstring-result', 'nonobject-envelope'])
def test_real_zabbix_fixture_rejects_invalid_version_envelope(fixture_call, monkeypatch, replacement):
    module, _, run = fixture_call
    calls = fake_version_client(module, monkeypatch, replacement)
    with pytest.raises(pytest.fail.Exception, match='must match the JSON-RPC request') as caught:
        run()
    assert len(calls) == 1
    assert 'synthetic-secret' not in str(caught.value)


def test_real_zabbix_fixture_accepts_exact_lab_and_correlated_version(fixture_call, monkeypatch):
    module, _, run = fixture_call
    calls = fake_version_client(module, monkeypatch, lambda valid: valid)
    result = run()
    observed = result[0] if isinstance(result, tuple) else result
    assert observed['observed_api_version'] == '7.0.10'
    assert len(calls) == 1


def test_real_zabbix_fixture_requires_isolated_opt_in_before_network(fixture_call, monkeypatch):
    module, _, run = fixture_call
    monkeypatch.delenv('ACK_TEST_REAL_ZABBIX_LAB')
    monkeypatch.setattr(module.httpx, 'Client', lambda *a, **k: pytest.fail('Unexpected network access'))
    with pytest.raises(pytest.fail.Exception, match='explicit isolated-lab'):
        run()


def test_real_zabbix_fixture_preserves_write_opt_in(fixture_call, monkeypatch):
    module, _, run = fixture_call
    monkeypatch.delenv('ACK_TEST_REAL_ZABBIX_WRITE')
    if hasattr(module, 'real_lab'):
        monkeypatch.setattr(module.httpx, 'Client', lambda *a, **k: pytest.fail('Unexpected network access'))
        with pytest.raises(pytest.skip.Exception, match='write opt-in absent'):
            run()
    else:
        # Contract reads remain available without authorising an acknowledgement.
        calls = fake_version_client(module, monkeypatch, lambda valid: valid)
        assert run()[0]['observed_api_version'] == '7.0.10'
        assert len(calls) == 1
