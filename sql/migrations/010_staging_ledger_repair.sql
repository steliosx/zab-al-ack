-- Forward corrections to 009. Preserve 001-009.
--
-- 009 is published and registered, so it is immutable for the same reason 008
-- is: `sql/migrate.py` refuses when a recorded migration's bytes change, and a
-- database that applied it would be stranded. Two corrections land here.
--
-- 1. 009 bounded what may *enter* the ledger but repaired nothing already in it.
--    008's backfill passes the staged attribute's `capture_expires_at` straight
--    into `note_staging` with no bounds, and that attribute is API-writable, so
--    a database that applied 008 can hold `staged_until = 'infinity'`. Advances
--    are monotonic, so no legitimate finite generation can ever replace such a
--    row and every later projection for that alert is refused permanently.
--    Reproduced: with 009's bounds in place, a poisoned row still refuses a
--    valid later generation.
-- 2. `claim_retirement_slot()` claimed the cadence with a bare UPDATE. Losing
--    workers blocked on the singleton row, which the winner holds for the whole
--    scan inside one `worker_call` transaction. With `statement_timeout` at
--    3000 ms (`src/zabalack/db.py`) a slow scan makes the losers raise, which
--    marks those workers unhealthy and gates their delivery loops. The claim
--    must be nonblocking.
--
-- No grant change and no trigger change; `functions_md5` and `structures_md5`
-- move, so re-seal with `sql/install/12_reseal.sql`.

-- Remove ledger entries that could not have come from the signer, then re-derive
-- the affected alerts from evidence that can be trusted. Idempotent, and safe to
-- run again: `note_staging` enforces the bounds on everything it re-admits.
CREATE FUNCTION zabalack.repair_staging_ledger() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE row record; removed integer:=0; restored integer:=0;
BEGIN
 -- A staged window is issued_at + 60 seconds by construction, so anything
 -- non-finite or materially in the future did not come from build_intent.
 WITH invalid AS (
  DELETE FROM zabalack.alert_staging
   WHERE staged_until IS NULL OR isfinite(staged_until) IS NOT TRUE
      OR staged_until>clock_timestamp()+interval '60 seconds'
  RETURNING alert_id)
 SELECT count(*) INTO removed FROM invalid;

 -- Re-derive from the authoritative sources, exactly as 008's backfill does:
 -- the newest action row for the alert, then any staged intent that now passes
 -- the bounds. An alert with neither is left without an entry, where the marker
 -- governs and a missing marker refuses.
 FOR row IN SELECT DISTINCT ON (alert_id) alert_id,action_id,issued_at FROM zabalack.actions
   WHERE NOT EXISTS(SELECT 1 FROM zabalack.alert_staging s WHERE s.alert_id=zabalack.actions.alert_id)
   ORDER BY alert_id,capture_order DESC
 LOOP
  IF zabalack.note_staging(row.alert_id,row.action_id,row.issued_at+interval '60 seconds','repair') THEN
   restored:=restored+1;
  END IF;
 END LOOP;
 FOR row IN SELECT id,attributes->'zabbix_ack_sync_intent' AS intent FROM public.alerts
   WHERE attributes ? 'zabbix_ack_sync_intent'
 LOOP
  BEGIN
   PERFORM zabalack.note_staging(row.id,(row.intent->>'action_id')::uuid,
    (row.intent->>'capture_expires_at')::timestamptz,'repair');
  EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format OR datetime_field_overflow THEN NULL;
  END;
 END LOOP;
 IF removed>0 THEN
  INSERT INTO zabalack.audit(operation,actor_ref,details) VALUES('repair_staging_ledger',session_user,
   jsonb_build_object('removed',removed,'restored',restored));
 END IF;
 RETURN jsonb_build_object('removed',removed,'restored',restored);
END $$;
REVOKE ALL ON FUNCTION zabalack.repair_staging_ledger() FROM PUBLIC;

-- Claim the deployment-wide retirement slot without blocking.
--
-- The advisory lock is taken first and with try semantics, so a worker that does
-- not get it returns immediately instead of queuing on the installation row that
-- the winner holds for the duration of its scan. The same lock key is used by
-- retire_intents, and advisory locks are re-entrant within a session, so the
-- winner's later acquisition succeeds.
CREATE OR REPLACE FUNCTION zabalack.claim_retirement_slot() RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
DECLARE claimed boolean;
BEGIN
 IF NOT pg_try_advisory_xact_lock(hashtextextended('zabalack.retire_intents',0)) THEN
  RETURN false;
 END IF;
 UPDATE zabalack.installation SET retirement_due_at=clock_timestamp()
  +make_interval(secs=>least(86400,greatest(1,retirement_interval_seconds)))
  WHERE singleton AND (retirement_due_at IS NULL OR retirement_due_at<=clock_timestamp())
  RETURNING true INTO claimed;
 RETURN coalesce(claimed,false);
END $$;
REVOKE ALL ON FUNCTION zabalack.claim_retirement_slot() FROM PUBLIC;

SELECT zabalack.repair_staging_ledger();

UPDATE zabalack.installation SET schema_version=6 WHERE singleton;
