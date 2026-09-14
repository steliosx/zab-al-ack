-- Forward correction to 010. Preserve 001-010.
--
-- 010's repair removed only entries whose window was non-finite or materially
-- in the future. A row forged through the API with a finite, plausible
-- `capture_expires_at` passed every one of those bounds, so on a database that
-- applied 008 it survived the repair even though its `staged_action_id`
-- corresponds to no action and no capture. `project()` then treats that UUID as
-- the authority and refuses the legitimate current action indefinitely.
-- Reproduced against 010: `{"removed": 0}`, the forged row still present, and no
-- action row for its UUID.
--
-- 010 also re-admitted entries by reading `public.alerts.attributes` directly,
-- which is the same API-writable source, so the repair could reinstate a forged
-- generation it had just removed.
--
-- The repair now keeps only what authenticated evidence corroborates:
-- `zabalack.actions`, which exist only after `verify_complete`, and
-- `zabalack.captures`, whose intent `capture_ack()` validated against the
-- committed history. An alert whose only evidence is an uncaptured staged
-- attribute is deliberately left without an entry: nothing can distinguish a
-- genuine uncaptured staging from a forged one at repair time, the marker
-- governs in that state, and the ordinary bounded sweep re-admits a genuine one
-- on its next pass.
--
-- No grant change and no trigger change; re-seal with `sql/install/12_reseal.sql`.
CREATE OR REPLACE FUNCTION zabalack.repair_staging_ledger() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE row record; removed integer:=0; restored integer:=0;
BEGIN
 WITH invalid AS (
  DELETE FROM zabalack.alert_staging s
   -- A staged window is issued_at + 60 seconds by construction, so anything
   -- non-finite or materially in the future did not come from build_intent.
   WHERE s.staged_until IS NULL OR isfinite(s.staged_until) IS NOT TRUE
      OR s.staged_until>clock_timestamp()+interval '60 seconds'
   -- And a generation nothing authenticated ever saw cannot be the authority,
   -- however plausible its timestamp. Both sources below are signer-verified:
   -- an action exists only after verify_complete, and a capture's intent was
   -- validated by capture_ack() against the committed history entry.
      OR NOT EXISTS(SELECT 1 FROM zabalack.actions a
                     WHERE a.alert_id=s.alert_id AND a.action_id=s.staged_action_id)
     AND NOT EXISTS(SELECT 1 FROM zabalack.captures c
                     WHERE c.alert_id=s.alert_id AND c.intent IS NOT NULL
                       AND c.intent->>'action_id'=s.staged_action_id::text)
  RETURNING alert_id)
 SELECT count(*) INTO removed FROM invalid;

 -- Re-derive only from authenticated evidence. Nothing here reads
 -- public.alerts.attributes: that is the forgeable source this repair exists to
 -- undo, and reinstating from it would defeat the deletion above.
 FOR row IN SELECT DISTINCT ON (alert_id) alert_id,action_id,issued_at FROM zabalack.actions
   WHERE NOT EXISTS(SELECT 1 FROM zabalack.alert_staging s WHERE s.alert_id=zabalack.actions.alert_id)
   ORDER BY alert_id,capture_order DESC
 LOOP
  IF zabalack.note_staging(row.alert_id,row.action_id,row.issued_at+interval '60 seconds','repair') THEN
   restored:=restored+1;
  END IF;
 END LOOP;
 IF removed>0 OR restored>0 THEN
  INSERT INTO zabalack.audit(operation,actor_ref,details) VALUES('repair_staging_ledger',session_user,
   jsonb_build_object('removed',removed,'restored',restored));
 END IF;
 RETURN jsonb_build_object('removed',removed,'restored',restored);
END $$;
REVOKE ALL ON FUNCTION zabalack.repair_staging_ledger() FROM PUBLIC;

SELECT zabalack.repair_staging_ledger();

UPDATE zabalack.installation SET schema_version=7 WHERE singleton;
