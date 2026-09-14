"""The service harness must observe the asynchronous projection, not infer it."""
from types import SimpleNamespace

from lab import deployment_e2e_runtime as runtime


def test_projection_wait_requires_all_original_conditions(monkeypatch):
    states = iter([
        ({"zabbix_ack_sync": "pending", "zabbix_event_id": "123"}, "ack", "ack"),
        ({"zabbix_ack_sync": "ok", "zabbix_event_id": "456"}, "ack", "ack"),
        ({"zabbix_ack_sync": "ok", "zabbix_event_id": "123"}, "open", "ack"),
        ({"zabbix_ack_sync": "ok", "zabbix_event_id": "123"}, "ack", "severity"),
        ({"zabbix_ack_sync": "ok", "zabbix_event_id": "123"}, "ack", "ack"),
    ])
    reads = []

    def execute(query, args):
        reads.append(args)
        return SimpleNamespace(fetchone=lambda: next(states))

    monkeypatch.setattr(runtime.time, "sleep", lambda _: None)
    result = runtime.wait_for_projection(SimpleNamespace(execute=execute), "synthetic-alert", "123",
                                         SimpleNamespace(poll=lambda: None))
    assert result == ({"zabbix_ack_sync": "ok", "zabbix_event_id": "123"}, "ack", "ack")
    assert reads == [("synthetic-alert",)] * 5
