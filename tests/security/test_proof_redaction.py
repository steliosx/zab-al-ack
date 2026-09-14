"""T19 regressions for diagnostic channels around the real Alerta adapter."""
import json
import logging

from zabalack.plugins import CaptureLogRedactor, _error_redact


def test_T19_structured_reserved_error_subtree_is_redacted():
    # A structured upstream diagnostic must not expose the reserved intent payload.
    body = {'message': 'save failed', 'errors': {'zabbix_ack_sync_intent':
            {'actor': {'login': 'private-synthetic-login'}, 'source': {'zabbix_event_id': '101'}}}}
    result = json.dumps(_error_redact(body))
    assert 'private-synthetic-login' not in result


def test_T19_preformatted_exception_text_is_redacted():
    record = logging.LogRecord('alerta', logging.ERROR, __file__, 1, 'database exception', (), None)
    record.exc_text = 'DETAIL [zabalack:synthetic-sensitive-diagnostic]'
    assert CaptureLogRedactor().filter(record)
    assert '[zabalack:' not in (record.getMessage() + (record.exc_text or ''))

import pytest
from pathlib import Path
from zabalack.normalizer import settings_for, strip_reserved
from tools.evidence_utils import public_text

@pytest.mark.parametrize('prefix', ['ackline', 'ACKLINE', 'zabalack', 'ZABALACK'])
def test_T19_rename_redacts_both_namespaces(prefix):
    value = '[' + prefix + ':v1 action=synthetic-private-proof]'
    record = logging.LogRecord('alerta', logging.ERROR, __file__, 1, '%s', (value,), None)
    record.exc_text = value
    assert CaptureLogRedactor().filter(record)
    assert 'synthetic-private-proof' not in record.getMessage() + (record.exc_text or '')
    assert 'synthetic-private-proof' not in _error_redact(value)
    assert 'synthetic-private-proof' not in public_text(value, Path.cwd())


def test_T19_rename_strips_old_and_new_reserved_attributes():
    assert strip_reserved({'ackline_private': 1, 'zabalack_private': 2,
                           'zabbix_ack_sync_intent': 3, 'ordinary': 4}) == {'ordinary': 4}


def test_rename_legacy_config_cannot_enable_new_plugin(tmp_path):
    path = tmp_path / 'old-config.json'
    path.write_text('{"enabled":true}')
    settings = settings_for({'ACKLINE_CONFIG_FILE': str(path)})
    assert not settings.enabled and not settings.external_writes_enabled
