"""T29/T31/T42/T43/T44/T45/T46/T47/T48 coordination regression; engine/lab scope follows pg_lab evidence."""
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


def test_T44_a_claim_after_the_deadline_fences_an_abandoned_send_before_expiring_it(pg_lab):
    """claim_delivery reads the delivery facts before recording a lifetime expiry.

    The nightly profile's minimized sequence (29 October 2026), deterministic
    and without Hypothesis: the send is recorded under a live lease, the
    deadline passes, the lease expires, and the next claim arrives before any
    reconciliation pass.
    """
    script = Path(__file__).with_name('assertions_claim_expiry_fence.sql').read_text()
    with connect_role(pg_lab, 'zabalack_worker') as conn:
        conn.execute('RESET ROLE')
        conn.execute(script)


def test_T45_a_cohort_change_fences_an_abandoned_send_before_failing_it(pg_lab):
    """claim_delivery reads the delivery facts before its cohort-policy branch.

    The send is recorded under a live lease, the lease expires before the
    deadline, the cohort revision changes (or the cohort is disabled), and the
    next claim arrives before any reconciliation pass.
    """
    script = Path(__file__).with_name('assertions_cohort_policy_fence.sql').read_text()
    with connect_role(pg_lab, 'zabalack_worker') as conn:
        conn.execute('RESET ROLE')
        conn.execute(script)


T46_SCRIPT = """
BEGIN;
DO $t46$
DECLARE cid uuid:=gen_random_uuid(); lost boolean:=false; result jsonb;
BEGIN
 SET ROLE zabalack_owner;
 INSERT INTO zabalack.captures(capture_id,alert_id,customer,snapshot,history_type,resulting_status,state)
  VALUES(cid,cid::text,'Verification lease synthetic','{}','ack','ack','captured_unverified');
 INSERT INTO zabalack.outbox(capture_id,due_at) VALUES(cid,clock_timestamp()+interval '1 day');
 INSERT INTO zabalack.verification_leases(capture_id,owner,generation,expires_at)
  VALUES(cid,'t46-verifier',7,clock_timestamp()+interval '1 minute');
 SET ROLE zabalack_worker;
 BEGIN
  PERFORM zabalack.worker_call('reject_capture',jsonb_build_object('worker_id','t46-verifier','capture_id',cid,'reason','failed_intent_invalid'));
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM<>'verification lease lost' THEN RAISE; END IF;
  lost:=true;
 END;
 IF NOT lost THEN RAISE EXCEPTION 'T46: a verification outcome without lease_generation passed the generation fence'; END IF;
 -- Control: the same call with the lease's own generation is accepted.
 result:=zabalack.worker_call('reject_capture',jsonb_build_object('worker_id','t46-verifier','capture_id',cid,'lease_generation',7,'reason','failed_intent_invalid'));
 IF result->>'state'<>'failed' THEN RAISE EXCEPTION 'T46 control: the matching generation was not accepted: %',result; END IF;
END $t46$;
ROLLBACK;
"""


def test_T46_a_verification_outcome_without_its_lease_generation_is_refused(pg_lab):
    """verify_complete, reject_capture and hold_capture fence on the lease
    generation; a payload that omits it is a lost lease, not a skipped check."""
    with connect_role(pg_lab, 'zabalack_worker') as conn:
        conn.execute('RESET ROLE')
        conn.execute(T46_SCRIPT)


def test_T48_verification_allows_a_bounded_signing_clock_lead(pg_lab):
    """An intent issued up to 5 s after the database recorded its capture is
    verified; 7 s after is refused (Alerta host clock ahead of the database)."""
    script = Path(__file__).with_name('assertions_capture_clock_skew.sql').read_text()
    with connect_role(pg_lab, 'zabalack_worker') as conn:
        conn.execute('RESET ROLE')
        conn.execute(script)


def _branch(body, start, end):
    begin = body.index(start)
    return body[begin:body.index(end, begin)]


def test_T47_forward_claim_delivery_order_migration_and_guards(pg_lab):
    """016's own preconditions, resealing requirement, grants and statement
    order, from a tree carrying 014's worker_call (schema 5)."""
    import psycopg
    old = (ROOT / 'sql/migrations/014_claim_expiry_fence.sql').read_text()
    # 014's slice re-creates worker_call and ends with the routine-digest
    # re-record, so it leaves a consistent schema-5 tree with 014's body.
    old_body = old[old.index('CREATE OR REPLACE FUNCTION zabalack.worker_call'):]
    new = (ROOT / 'sql/migrations/016_claim_delivery_order.sql').read_text()
    with connect_role(pg_lab, 'zabalack_owner') as conn:
        with conn.transaction(force_rollback=True):
            conn.execute(old_body)
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            before = conn.execute("SELECT pg_get_functiondef('zabalack.worker_call(text,jsonb)'::regprocedure)").fetchone()[0]
            with pytest.raises(psycopg.Error, match='found schema 4'):
                with conn.transaction():
                    conn.execute('UPDATE zabalack.installation SET schema_version=4 WHERE singleton')
                    conn.execute(new)
            with pytest.raises(psycopg.Error, match='routine body drift before 016'):
                with conn.transaction():
                    conn.execute("UPDATE zabalack.installation SET routine_bodies_md5='not-the-live-digest' WHERE singleton")
                    conn.execute(new)
            operator_before = conn.execute("SELECT pg_get_functiondef('zabalack.operator_call(text,jsonb)'::regprocedure)").fetchone()[0]
            conn.execute(new)
            health = conn.execute('SELECT zabalack.capture_health()').fetchone()[0]
            assert health['zabalack_schema_version'] == 5
            assert health['functions_ok'] is False, 'upgrade must not silently reseal'
            worker = conn.execute("SELECT pg_get_functiondef('zabalack.worker_call(text,jsonb)'::regprocedure)").fetchone()[0]
            operator = conn.execute("SELECT pg_get_functiondef('zabalack.operator_call(text,jsonb)'::regprocedure)").fetchone()[0]
            assert operator == operator_before, '016 must not touch operator_call'
            # In claim_delivery the fence now precedes the cohort-policy branch.
            cohort = "reason='cohort_policy_changed'"
            claim = _branch(worker, "ELSIF op='claim_delivery' THEN", "ELSIF op IN ('before_write'")
            assert claim.index("reason='abandoned_attempt'") < claim.index(cohort)
            old_claim = _branch(before, "ELSIF op='claim_delivery' THEN", "ELSIF op IN ('before_write'")
            assert old_claim.index(cohort) < old_claim.index("reason='abandoned_attempt'")
            # record_reconciliation locks the action before instance_control,
            # the order claim_delivery uses.
            action_lock = "FROM zabalack.actions WHERE action_id=(p->>'action_id')::uuid"
            control_lock = 'FROM zabalack.instance_control WHERE instance='
            record = _branch(worker, "ELSIF op='record_reconciliation' THEN", "ELSIF op='worker_stopped'")
            assert record.index(action_lock) < record.index(control_lock)
            old_record = _branch(before, "ELSIF op='record_reconciliation' THEN", "ELSIF op='worker_stopped'")
            assert old_record.index(control_lock) < old_record.index(action_lock)
            assert claim.index('FOR UPDATE OF ac') < claim.index(control_lock)
            conn.execute('SELECT zabalack.seal_installation()')
            assert conn.execute('SELECT zabalack.capture_health()').fetchone()[0]['functions_ok']
            for role, function, expected in (('alerta_app', 'worker_call', False), ('zabalack_worker', 'worker_call', True),
                                             ('alerta_app', 'operator_call', False), ('zabalack_operator', 'operator_call', True)):
                assert conn.execute(f"SELECT has_function_privilege('{role}','zabalack.{function}(text,jsonb)','EXECUTE')").fetchone()[0] is expected


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
        assert installed == 5, 'precondition: the lab is at schema 5 (migration 015)'
        # 009 was written for a schema-4 tree; present one for its guard, and
        # restore the lab's own version below.
        owner.execute('UPDATE zabalack.installation SET schema_version=4 WHERE singleton')
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
