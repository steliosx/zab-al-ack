"""T29/T31 coordination regression; engine/lab scope follows pg_lab evidence."""
from pathlib import Path
from conftest import connect_role


def test_T29_T31_T38_late_responses_preserve_uncertainty_and_fencing(pg_lab):
    script = Path(__file__).with_name('assertions_late_response.sql').read_text()
    with connect_role(pg_lab, 'zabalack_worker') as conn:
        conn.execute('RESET ROLE')
        conn.execute(script)


def test_T21_T29_T31_forward_migration_repairs_existing_late_response(pg_lab):
    """Exercise 005→006 in a rolled-back isolated fixture, including resealing."""
    root = Path(__file__).resolve().parents[2]
    old = (root / 'sql/migrations/003_worker.sql').read_text()
    old = old.replace('CREATE FUNCTION zabalack.worker_call', 'CREATE OR REPLACE FUNCTION zabalack.worker_call', 1).replace(
        'CREATE FUNCTION zabalack.expire_due_effects', 'CREATE OR REPLACE FUNCTION zabalack.expire_due_effects', 1)
    migration = (root / 'sql/migrations/006_late_response_fencing.sql').read_text()
    with connect_role(pg_lab, 'zabalack_owner') as conn:
        with conn.transaction(force_rollback=True):
            # Test-only rollback of the function body, not migration-ledger edits.
            conn.execute(old)
            conn.execute('DROP FUNCTION zabalack.attempt_unsettled(uuid)')
            conn.execute('SET ROLE alerta_owner')
            conn.execute('SELECT zabalack.seal_installation()')
            conn.execute(Path(__file__).with_name('seed_old_late_response.sql').read_text())
            conn.execute('SET ROLE zabalack_owner')
            conn.execute(migration)
            conn.execute('SET ROLE zabalack_worker')
            for case in (1, 2):
                selector = f'ack_test.late_upgrade_id_{case}'
                row = conn.execute("SELECT zabalack.worker_call('inspect',jsonb_build_object('action_id',current_setting(%s)))", (selector,)).fetchone()[0][0]
                assert row['state'] == 'unknown' and row['uncertain'] is True
                assert conn.execute("SELECT zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','upgrade-new','action_id',current_setting(%s)))", (selector,)).fetchone()[0] is None
            health = conn.execute("SELECT zabalack.worker_call('doctor','{}')").fetchone()[0]
            assert health['zabalack_schema_version'] == 2
            assert health['functions_ok'] is False, 'migration must not silently reseal readiness'
            conn.execute('SET ROLE alerta_owner')
            conn.execute('SELECT zabalack.seal_installation()')
            conn.execute('SET ROLE zabalack_worker')
            assert conn.execute("SELECT zabalack.worker_call('doctor','{}')").fetchone()[0]['functions_ok'] is True


def test_T42_an_abandoned_send_is_fenced_whatever_state_it_was_left_in(pg_lab):
    """The sweep's subject is the attempt and its lease, not the action's state.

    Astra's minimized sequence, deterministic and without Hypothesis: a retry
    requeues a claimed action while its delivery lease is live, the replacement
    send is recorded against that lease, and the lease expires unobserved.
    """
    script = Path(__file__).with_name('assertions_abandoned_attempt.sql').read_text()
    with connect_role(pg_lab, 'zabalack_worker') as conn:
        conn.execute('RESET ROLE')
        conn.execute(script)
