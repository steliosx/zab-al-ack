"""Where a request line can reach a log in the supported configuration, and what it carries.

Opus's cross-review follow-up (issue #13, `opus-qparam-access-log-20260921`) showed
that the adapter's redactor rewrites `record.url` but not a query string embedded in
a record's *message*, using a constructed message. This module drives the real
route instead: Alerta's own after-request access line, registered by
`alerta.utils.logging.Logger.setup_logging`, which formats `request.path`, and the
`werkzeug` development-server request line, which Alerta's logging configuration
holds at WARNING. A free-text `q=` value that the restricted-evidence pattern does
not match is the probe, so the assertions cannot pass for the wrong reason.

Separate WSGI-server (Gunicorn) and reverse-proxy access logs do not pass through
this handler chain and are not exercised here; `docs/security_and_data.md` records
the path-only format to configure at those originating loggers.
"""
import contextlib
import io
import json
import logging

import pytest
from flask import Flask
from alerta import settings as alerta_defaults
from alerta.app import alarm_model, qb
from alerta.utils import logging as alerta_logging
from zabalack.plugins import configure_app

# A free-text search term: no `[zabalack:` marker, no reserved attribute name, so the
# existing evidence redaction cannot be what removes it.
SEARCH_TERM = 'escalated+per+synthetic-RUNBOOK-77'
ROUTE = f'/synthetic/alerts?q={SEARCH_TERM}&sort-by=lastReceiveTime'
EVIDENCE_MARKER = '[zabalack:v1 action=91000000-0000-4000-8000-000000000003 mac=synthetic-private-proof]'


@pytest.fixture
def restore_logging():
    """`Logger.setup_logging` calls `dictConfig` with `disable_existing_loggers`;
    every logger it touches is restored afterwards, as `json_log_chain` does."""
    registry = {name: obj for name, obj in logging.Logger.manager.loggerDict.items()
                if isinstance(obj, logging.Logger)}
    root = logging.getLogger()
    saved = {name: (obj.level, obj.propagate, obj.disabled, obj.handlers[:], obj.filters[:])
             for name, obj in list(registry.items()) + [('', root)]}
    try:
        yield
    finally:
        for name, (level, propagate, disabled, handlers, filters) in saved.items():
            obj = root if name == '' else registry[name]
            obj.level, obj.propagate, obj.disabled = level, propagate, disabled
            obj.handlers[:], obj.filters[:] = handlers, filters


def served_through_alerta(log_format, *, name):
    """Alerta's real logging setup (console handler on stdout, its filters, its
    after-request access line) and the mandatory adapter, then one GET carrying the
    search term in the query, then the request line the werkzeug development
    server would log for it. Returns what reached stdout."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        app = Flask(name)
        app.config.from_object(alerta_defaults)
        app.config.update(TESTING=True, SECRET_KEY='synthetic-lab-only', LOG_FORMAT=log_format,
                          LOG_LEVEL='INFO', DATABASE_URL='postgres://synthetic-lab-only:5432/unused')
        alerta_logging.Logger().setup_logging(app)
        alarm_model.init_app(app)
        qb.init_app(app)  # selects the backend module without connecting
        configure_app(app)

        @app.route('/synthetic/alerts')
        def synthetic():
            return {'ok': True}

        with app.test_client() as client:
            response = client.get(ROUTE)
        assert response.status_code == 200
        werkzeug = logging.getLogger('werkzeug')
        werkzeug.info('"GET %s HTTP/1.1" 200 -', ROUTE)
        werkzeug.warning('synthetic werkzeug warning stays')
        logging.getLogger('alerta.database').error('Database connection error: %s', EVIDENCE_MARKER)
    return captured.getvalue()


def test_alerta_access_line_carries_the_path_and_not_the_query(restore_logging):
    """`Logger.log_response` formats `request.path`: the free-text search term never
    reaches the message, `url` is the path (the adapter), `reqargs` is blank, and the
    method, path, protocol, status and length are all still there."""
    emitted = served_through_alerta('json', name='synthetic-alerta-access-json')
    assert SEARCH_TERM not in emitted and 'q=' not in emitted, emitted
    records = [json.loads(line) for line in emitted.splitlines() if line.strip()]
    assert [r['message'] for r in records] == [
        '"GET /synthetic/alerts HTTP/1.1" 200 12',
        'synthetic werkzeug warning stays',
        'zabalack_restricted_diagnostic_redacted',
    ], emitted
    access = records[0]
    assert access['method'] == 'GET'
    assert access['url'] == 'http://localhost/synthetic/alerts'
    assert access['reqargs'] == '-' and access['data'] == '-'
    assert access['levelname'] == 'INFO'
    for key, value in access.items():
        assert SEARCH_TERM not in str(value), (key, value)
    # The development server's own request line, which does carry the query, is held
    # at WARNING by Alerta's configuration; its warnings still flow.
    assert records[1]['name'] == 'werkzeug' and records[1]['levelname'] == 'WARNING'
    # The restricted-evidence redaction on the same chain is unchanged.
    assert 'synthetic-private-proof' not in emitted and '[zabalack:' not in emitted


def test_alerta_access_line_in_the_default_format_is_path_only_too(restore_logging):
    """The same route under `LOG_FORMAT=default` (Alerta's `CustomFormatter`)."""
    emitted = served_through_alerta('default', name='synthetic-alerta-access-default')
    lines = [line for line in emitted.splitlines() if line.strip()]
    assert SEARCH_TERM not in emitted and 'q=' not in emitted, emitted
    assert len(lines) == 3, emitted
    assert '"GET /synthetic/alerts HTTP/1.1" 200 12' in lines[0]
    assert 'ip=127.0.0.1' in lines[0]
    assert 'synthetic werkzeug warning stays' in lines[1]
    assert 'zabalack_restricted_diagnostic_redacted' in lines[2]


def test_the_assertions_would_catch_a_full_path_access_line(restore_logging, monkeypatch):
    """Negative control: if Alerta's access line formatted `request.full_path`
    instead of `request.path`, the search term would reach the message and the
    case above would fail. Nothing in the product is changed; this shows what the
    assertions are sensitive to."""
    from flask import current_app, request

    def full_path_line(response):
        current_app.logger.info(
            f'"{request.method} {request.full_path} {request.environ["SERVER_PROTOCOL"]}" '
            f'{response.status_code} {response.content_length}')
        return response
    monkeypatch.setattr(alerta_logging.Logger, 'log_response', staticmethod(full_path_line))
    emitted = served_through_alerta('json', name='synthetic-alerta-access-control')
    records = [json.loads(line) for line in emitted.splitlines() if line.strip()]
    assert SEARCH_TERM in records[0]['message'], emitted
    assert records[0]['url'] == 'http://localhost/synthetic/alerts', 'the adapter still keeps url path-only'
