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


import io
from logging.config import dictConfig

from flask import Flask, g
from alerta import settings as alerta_defaults
from alerta.app import alarm_model, qb

from zabalack.plugins import configure_app


OPERATOR_NOTE = 'escalated per synthetic-private-RUNBOOK-77'
PRIVATE_TOKEN = 'synthetic-private-RUNBOOK-77'
ACTION_ROUTE = '/alert/91000000-0000-4000-8000-000000000003/action'


@pytest.fixture
def json_log_chain():
    """Alerta's own logging topology with `LOG_FORMAT=json`, into a local stream.

    `Logger.setup_logging` installs the `context` and `requests` filters on the
    handler before the mandatory adapter appends its redactor, so the record the
    redactor sees already carries `ContextFilter`'s extras. Reproducing that
    order is the whole point: a redactor tested on a bare `LogRecord` never sees
    them. Every logger the `dictConfig` call touches is restored afterwards so
    this module does not reconfigure logging for the rest of the session.
    """
    registry = {name: obj for name, obj in logging.Logger.manager.loggerDict.items()
                if isinstance(obj, logging.Logger)}
    root = logging.getLogger()
    saved = {name: (obj.level, obj.propagate, obj.disabled, obj.handlers[:], obj.filters[:])
             for name, obj in list(registry.items()) + [('', root)]}
    stream = io.StringIO()
    try:
        dictConfig({
            'version': 1, 'disable_existing_loggers': True,
            'formatters': {'json': {'()': 'alerta.utils.logging.JSONFormatter'}},
            'filters': {'requests': {'()': 'alerta.utils.logging.RequestFilter', 'methods': ['PUT']},
                        'context': {'()': 'alerta.utils.logging.ContextFilter'}},
            'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'json',
                                     'level': 'DEBUG', 'filters': ['context', 'requests'],
                                     'stream': stream}},
            'loggers': {'alerta': {'level': 'DEBUG'}},
            'root': {'level': 'DEBUG', 'handlers': ['console']},
        })
        app = Flask('synthetic-alerta-json-logging')
        app.config.from_object(alerta_defaults)
        app.config.update(TESTING=True, SECRET_KEY='synthetic-lab-only', LOG_FORMAT='json',
                          DATABASE_URL='postgres://synthetic-lab-only:5432/unused')
        alarm_model.init_app(app)
        qb.init_app(app)  # selects the backend module without connecting
        configure_app(app)
        app.logger.setLevel(logging.DEBUG)
        yield stream, app
    finally:
        for name, (level, propagate, disabled, handlers, filters) in saved.items():
            obj = root if name == '' else registry[name]
            obj.level, obj.propagate, obj.disabled = level, propagate, disabled
            obj.handlers[:], obj.filters[:] = handlers, filters


def test_T19_request_body_never_reaches_the_json_log(json_log_chain):
    """`LOG_FORMAT=json` emits `record.data`, which is the raw acknowledgement body.

    `ContextFilter` attaches `request.get_data()` and `request.args` to every
    record in the request, and `JSONFormatter.RECORD_ATTRS` emits both, so the
    operator's note left the deployment through the log channel without ever
    appearing in a message. The adapter's redactor is the mandated control for
    that channel, and logs carry allowlisted identifiers and reason classes, not
    the note.
    """
    stream, app = json_log_chain
    with app.test_request_context(ACTION_ROUTE, method='PUT',
                                  json={'action': 'ack', 'text': OPERATOR_NOTE}):
        g.request_id = 'synthetic-request-1'
        logging.getLogger('alerta.database').error('Database connection error')
        app.logger.info('"PUT %s HTTP/1.1" 200 12', ACTION_ROUTE)

    emitted = stream.getvalue()
    assert PRIVATE_TOKEN not in emitted, 'the operator note reached the log channel'
    records = [json.loads(line) for line in emitted.splitlines() if line.strip()]
    assert len(records) == 2, emitted
    assert [record['data'] for record in records] == ['-', '-']
    assert [record['reqargs'] for record in records] == ['-', '-']
    # Ordinary records still flow, with their allowlisted context intact.
    assert records[0]['message'] == 'Database connection error'
    assert records[1]['message'] == f'"PUT {ACTION_ROUTE} HTTP/1.1" 200 12'
    assert records[0]['request_id'] == 'synthetic-request-1'


def test_T19_restricted_evidence_is_still_redacted_in_the_json_log(json_log_chain):
    """Blanking the body must not displace the existing evidence redaction."""
    stream, app = json_log_chain
    marker = '[zabalack:v1 action=91000000-0000-4000-8000-000000000003 mac=synthetic-private-proof]'
    with app.test_request_context(ACTION_ROUTE, method='PUT', json={'action': 'ack'}):
        g.request_id = 'synthetic-request-2'
        logging.getLogger('alerta.database').error('Database connection error: %s', marker)

    emitted = stream.getvalue()
    assert 'synthetic-private-proof' not in emitted
    assert '[zabalack:' not in emitted
    assert json.loads(emitted)['message'] == 'zabalack_restricted_diagnostic_redacted'
