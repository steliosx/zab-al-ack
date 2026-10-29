"""T29/T31/T42/T43 coordination regression; engine/lab scope follows pg_lab evidence."""
from pathlib import Path
import pytest
from conftest import connect_role

ROOT = Path(__file__).resolve().parents[2]


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


def test_T43_a_send_in_flight_under_a_live_lease_is_never_expired_with_certainty(pg_lab):
    """The execution-lifetime expiry statement reads the delivery facts.

    The nightly profile's minimized sequence (28 October 2026), deterministic
    and without Hypothesis: a retry requeues a claimed action while its lease
    is live, the send is recorded against that lease, the deadline passes
    while the lease is still live, and the worker stops.
    """
    script = Path(__file__).with_name('assertions_live_lease_expiry.sql').read_text()
    with connect_role(pg_lab, 'zabalack_worker') as conn:
        conn.execute('RESET ROLE')
        conn.execute(script)


def guards_of_the_repair_migration():
    """009's two preconditions, as their own statements."""
    source = (ROOT / 'sql/migrations/009_abandoned_attempt_sweep.sql').read_text()
    version_start = source.index('DO $$\nDECLARE installed integer;')
    routine_start = source.index('DO $$\nDECLARE recorded text; live jsonb;')
    end = source.index('-- worker_call(): 008\'s body')
    return (source[version_start:source.index('DO $$', version_start + 5)],
            source[routine_start:end])


def test_T42_the_repair_migration_refuses_what_it_cannot_repair(pg_lab):
    """A file that replaces a routine body and re-records the digest pinning
    every body here must refuse a tree it was not written for, and must not
    refuse an ordinary upgrade.

    The second half is the one worth stating. A sealed schema-3 installation
    upgrades in one runner pass: 008 adds the fence and its routines, 009
    follows, and only `13_reseal.sql` replaces the seal afterwards. A
    sealed-manifest comparison in 009 would see 008's own work as drift and
    refuse every upgrade, so 009 keeps 008's routine-body pin — which 008
    re-records at the end of itself, before 009 reads it — and leaves the seal
    comparison to the first migration of the pass. The native populated-upgrade
    cases run that pass for real.
    """
    version_guard, routine_guard = guards_of_the_repair_migration()
    with connect_role(pg_lab, 'zabalack_owner') as owner:
        installed = owner.execute('SELECT schema_version FROM zabalack.installation WHERE singleton').fetchone()[0]
        assert installed == 4, 'precondition: the lab is at the version 009 is written for'
        owner.execute(version_guard)
        owner.execute(routine_guard)
        owner.execute('UPDATE zabalack.installation SET schema_version=3 WHERE singleton')
    try:
        with connect_role(pg_lab, 'zabalack_owner') as owner:
            with pytest.raises(Exception, match='found schema 3'):
                owner.execute(version_guard)
    finally:
        with connect_role(pg_lab, 'zabalack_owner') as owner:
            owner.execute('UPDATE zabalack.installation SET schema_version=%s WHERE singleton', (installed,))
    with connect_role(pg_lab, 'zabalack_owner') as owner:
        shipped = owner.execute(
            "SELECT pg_get_functiondef('zabalack.operator_call(text,jsonb)'::regprocedure)").fetchone()[0]
        owner.execute("""CREATE OR REPLACE FUNCTION zabalack.operator_call(op text, p jsonb DEFAULT '{}'::jsonb)
                      RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack
                      AS $drift$ BEGIN RETURN zabalack.worker_call(op,p); END $drift$""")
    try:
        with connect_role(pg_lab, 'zabalack_owner') as owner:
            with pytest.raises(Exception, match='drifted from the set 008 recorded'):
                owner.execute(routine_guard)
    finally:
        with connect_role(pg_lab, 'zabalack_owner') as owner:
            owner.execute(shipped)
    with connect_role(pg_lab, 'zabalack_owner') as owner:
        owner.execute(routine_guard)
        # An upgrade pass in progress: an earlier migration has added structure
        # the seal does not know about, exactly as 008 adds the fence table.
        # 009 must not read that as drift.
        with owner.transaction(force_rollback=True):
            owner.execute('CREATE TABLE zabalack.upgrade_pass_scratch(x integer)')
            live = owner.execute("SELECT zabalack.catalog_fingerprints()->>'structures'").fetchone()[0]
            sealed = owner.execute('SELECT structures_md5 FROM zabalack.readiness_manifest WHERE singleton').fetchone()[0]
            assert live != sealed, 'precondition: the catalog no longer matches the seal'
            owner.execute(routine_guard)
        assert owner.execute("SELECT (zabalack.capture_health())->>'functions_ok'").fetchone()[0] == 'true'
