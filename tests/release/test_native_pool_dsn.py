"""Mocked pool endpoint guards; no native service evidence is created."""
import importlib.util
from pathlib import Path

import pytest


class ControlConnectionReached(Exception):
    """Stop after endpoint validation, before contacting any database."""


@pytest.fixture
def pool_guard(monkeypatch):
    source = Path(__file__).resolve().parents[1] / "alerta/test_health_postgres.py"
    spec = importlib.util.spec_from_file_location("native_pool_guard", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    connections = []

    def connect(*args, **kwargs):
        connections.append((args, kwargs))
        raise ControlConnectionReached

    def no_database_mutation(*args, **kwargs):
        pytest.fail("Pool endpoint guard reached fixture database mutation")

    monkeypatch.setattr(module.psycopg, "connect", connect)

    def run(overrides=None, expected_port="42017"):
        parameters = {"host": "127.0.0.1", "port": "42017", "dbname": "alerta", "user": "zabalack_probe"}
        parameters.update(overrides or {})
        pool_dsn = module.psycopg.conninfo.make_conninfo(**parameters)
        monkeypatch.setenv("ACK_TEST_PGBOUNCER_DSN", pool_dsn)
        if expected_port is None:
            monkeypatch.delenv("ACK_TEST_PGBOUNCER_EXPECTED_PORT", raising=False)
        else:
            monkeypatch.setenv("ACK_TEST_PGBOUNCER_EXPECTED_PORT", expected_port)
        lab = {"backend": "native_postgres18", "admin_dsn": "postgresql://lab_admin@127.0.0.1:42016/alerta"}
        module.test_T34_native_pgbouncer_timeout_role_and_clean_reuse(lab, no_database_mutation, lambda *args: None)

    return run, connections


@pytest.mark.parametrize("port,expected_port", [("56432", None), ("42017", "42017"), ("1", "1"), ("65535", "65535")])
def test_declared_dynamic_or_default_pool_port_reaches_control_connection(pool_guard, port, expected_port):
    run, connections = pool_guard
    with pytest.raises(ControlConnectionReached):
        run({"port": port}, expected_port)
    assert len(connections) == 1
    args, parameters = connections[0]
    assert not args
    assert parameters == {"host": "127.0.0.1", "port": port, "dbname": "pgbouncer", "user": "lab_admin",
                          "connect_timeout": "2", "autocommit": True}


@pytest.mark.parametrize("expected_port", ["", "0", "65536", "-1", "+42017", "042017", "42017 ",
                                          "42017\n", "٤٢٠١٧", "42017,42018"])
def test_malformed_expected_pool_port_is_rejected_before_connection(pool_guard, expected_port):
    run, connections = pool_guard
    with pytest.raises(pytest.fail.Exception, match="canonical decimal"):
        run(expected_port=expected_port)
    assert not connections


@pytest.mark.parametrize("overrides,expected_port", [
    ({"port": "42018"}, "42017"),
    ({"port": "42017"}, None),
    ({"host": "192.0.2.10"}, "42017"),
    ({"hostaddr": "192.0.2.10"}, "42017"),
    ({"host": "localhost"}, "42017"),
    ({"host": "/tmp"}, "42017"),
    ({"dbname": "postgres"}, "42017"),
    ({"user": "lab_admin"}, "42017"),
    ({"port": "42017,42018"}, "42017"),
])
def test_wrong_pool_endpoint_or_identity_is_rejected_before_connection(pool_guard, overrides, expected_port):
    run, connections = pool_guard
    with pytest.raises(pytest.fail.Exception, match="declared loopback port"):
        run(overrides, expected_port)
    assert not connections
