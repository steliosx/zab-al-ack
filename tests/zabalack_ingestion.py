"""Model Zabalack's authenticated POST /alert ingestion for SQL fixtures (F1, migration 015).

In production the ZabbixNormaliser's post_receive hook calls zabalack.bind_source()
once per authenticated ingestion, with the epoch (last_receive_id, last_receive_time)
of the row the ingestion statement returned. A fixture that writes public.alerts
directly models that ingestion only when it calls `ingest()`: it stamps the row with
an exact epoch and records the binding as the application role. Fixtures that model
anything else (an attribute edit, a forged or unauthenticated write) must leave the
alert unbound, so a missing binding is never hidden by the harness.
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid


def epoch_now() -> datetime:
    """A naive UTC timestamp with microseconds, the type of alerts.last_receive_time."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ingest(conn, alert_id: str, customer: str, source: dict, *, receive_id: str | None = None,
           receive_time: datetime | None = None) -> tuple[str, datetime, str]:
    """Stamp the alert with a new epoch and bind its validated source, as alerta_app.

    `conn` must already act as the application role (the only grantee of
    bind_source). Returns (receive_id, receive_time, bind_source outcome).
    """
    receive_id = receive_id or str(uuid.uuid4())
    receive_time = receive_time or epoch_now()
    conn.execute("UPDATE alerts SET last_receive_id=%s,last_receive_time=%s WHERE id=%s",
                 (receive_id, receive_time, alert_id))
    outcome = conn.execute(
        "SELECT zabalack.bind_source(%s,%s,%s,%s,%s,%s,%s)",
        (alert_id, receive_id, receive_time, customer, source["zabbix_instance"],
         source["zabbix_event_id"], source["zabbix_trigger_id"])).fetchone()[0]
    return receive_id, receive_time, outcome


def create_ingested(alert):
    """Create `alert` through Alerta's storage and bind it as the normaliser's post_receive
    does after an authenticated POST /alert: from the returned row's own epoch, through the
    production helper, on the request's Alerta connection (the application role).

    For fixtures that build alerts with `Alert.create()` instead of the HTTP route. Call it
    inside an app or request context. The HTTP route itself is covered end to end in
    tests/alerta/test_postgres_http.py.
    """
    from zabalack.normalizer import bind_source
    created = alert.create()
    outcome = bind_source(created.id, created.last_receive_id, created.last_receive_time,
                          created.customer, created.attributes)
    assert outcome == "bound", outcome
    return created
