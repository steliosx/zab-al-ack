"""Sealing a fingerprint is not permission to accept malformed installation state.

These synthetic catalog changes always roll back. WASM role selection is not
native authentication or concurrency evidence. Exercise the real trigger script
as the alerts owner, as well as the seal entry point, then query the real probe.
"""

from pathlib import Path

import pytest
from psycopg import sql

from conftest import connect_role

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    return (
        (ROOT / "sql/install" / name)
        .read_text()
        .replace("BEGIN;", "")
        .replace("COMMIT;", "")
    )


@pytest.mark.parametrize("sealing", ["seal-entrypoint", "trigger-install"])
@pytest.mark.parametrize(
    "malformation",
    [
        "extra-grant",
        "missing-grant",
        "routine-drift",
        "duplicate-trigger",
        "alerts-rls",
    ],
)
def test_matching_seal_cannot_hide_malformed_state(pg_lab, sealing, malformation):
    with connect_role(pg_lab, "zabalack_owner") as conn:
        conn.prepare_threshold = None
        with conn.transaction(force_rollback=True):

            def health():
                conn.execute("SET ROLE zabalack_probe")
                result = conn.execute("SELECT zabalack.capture_health()").fetchone()[0]
                conn.execute("SET ROLE zabalack_owner")
                return result

            initial = health()
            assert initial["functions_ok"] and initial["trigger_ok"]
            if malformation == "extra-grant":
                conn.execute(
                    "GRANT EXECUTE ON FUNCTION zabalack.select_binding(text,text,timestamp without time zone) TO alerta_app"
                )
            elif malformation == "missing-grant":
                conn.execute(
                    "REVOKE EXECUTE ON FUNCTION zabalack.read_binding(text,text,timestamp without time zone) FROM alerta_app"
                )
            elif malformation == "routine-drift":
                conn.execute(
                    "ALTER FUNCTION zabalack.worker_call(text,jsonb) SET timezone='Europe/Paris'"
                )
            elif malformation == "duplicate-trigger":
                conn.execute("SET ROLE alerta_owner")
                conn.execute(
                    "CREATE TRIGGER synthetic_second_capture AFTER UPDATE ON public.alerts FOR EACH ROW EXECUTE FUNCTION zabalack.capture_ack()"
                )
            else:
                conn.execute("SET ROLE alerta_owner")
                conn.execute("ALTER TABLE public.alerts ENABLE ROW LEVEL SECURITY")
            conn.execute("SET ROLE alerta_owner")
            assert conn.execute("SELECT current_user").fetchone()[0] == "alerta_owner"
            conn.execute(
                script("10_trigger.sql")
                if sealing == "trigger-install"
                else "SELECT zabalack.seal_installation()"
            )
            conn.execute("SET ROLE zabalack_owner")
            # A matching fingerprint alone would accept the malformed tree.
            assert conn.execute(
                "SELECT m.functions_md5=h->>'functions' AND m.structures_md5=h->>'structures' AND m.trigger_md5=h->>'trigger' FROM zabalack.readiness_manifest m CROSS JOIN LATERAL zabalack.catalog_fingerprints() h"
            ).fetchone()[0]
            observed = health()
            assert not (observed["functions_ok"] and observed["trigger_ok"]), observed
            # The approved reseal workflow also refuses. Keep the SQL error in
            # a server-side subtransaction for the supplemental WASM socket.
            conn.execute(
                sql.SQL("""DO $test$ DECLARE failure text := ''; BEGIN
              BEGIN EXECUTE {}; EXCEPTION WHEN OTHERS THEN failure := SQLERRM; END;
              PERFORM set_config('ack_test.reseal_refusal', failure, true);
            END $test$""").format(sql.Literal(script("13_reseal.sql")))
            )
            failure = conn.execute(
                "SELECT current_setting('ack_test.reseal_refusal')"
            ).fetchone()[0]
            expected = {
                "extra-grant": "grant",
                "missing-grant": "grant",
                "routine-drift": "routine",
                "duplicate-trigger": "more than one enabled trigger",
                "alerts-rls": "row-level security",
            }[malformation]
            assert expected in failure.lower(), failure
        conn.execute("SET ROLE zabalack_probe")
        restored = conn.execute("SELECT zabalack.capture_health()").fetchone()[0]
        assert restored["functions_ok"] and restored["trigger_ok"]
