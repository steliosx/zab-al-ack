"""Shared boundary for disposable real-Zabbix tests; no network or credentials."""
import re


LAB_API_URL = 'http://127.0.0.1:58080/api_jsonrpc.php'


def validate_lab_fixture(data):
    if (not isinstance(data, dict) or data.get('isolated_lab') is not True
            or data.get('fresh_instance_no_notifications') is not True):
        raise ValueError('Fixture must identify the fresh isolated notification-free lab')
    # Accept one canonical URL, before constructing a client or opening a token.
    # This also rejects userinfo, query/fragment, control characters and parser
    # ambiguities without including any supplied URL or credentials in errors.
    if data.get('url') != LAB_API_URL:
        raise ValueError('Real Zabbix tests require the declared literal-loopback API at 127.0.0.1:58080')
    return data


def validate_version_envelope(envelope, expected_id):
    if (not isinstance(envelope, dict) or envelope.get('jsonrpc') != '2.0'
            or envelope.get('id') != expected_id or 'error' in envelope
            or not isinstance(envelope.get('result'), str)
            or re.fullmatch(r'7\.0\.[0-9]+', envelope['result']) is None):
        raise ValueError('Real Zabbix version response must match the JSON-RPC request and report 7.0.x')
    return envelope['result']
