-- Forward correction to 011. Preserve 001-011.
--
-- Two defects in 011's repair, both of which made it less safe than doing
-- nothing for the alerts concerned.
--
-- 1. It treated any capture carrying a matching intent as authentication.
--    `capture_ack()` never verifies the MAC — it regex-matches the marker's
--    structure and binds it to the committed history entry, and the repository
--    states the boundary outright: "SQL does not verify cryptography; Python
--    tests prove the mandatory verify-before-promote trust boundary." A forged
--    intent captured with a fabricated proof therefore produced a capture row
--    that corroborated its own forged generation, so the poisoned ledger entry
--    survived the repair and kept refusing legitimate projections. Verification
--    happens in `verify_complete`, which is also where the action row is created
--    and `captures.verified_action_id` is set, so an action row is the only
--    corroboration worth having.
--
-- 2. It restored a fallback entry from the newest action for any alert left
--    without one. Where the ledger legitimately held an uncaptured newer
--    generation B, the broadened deletion removed B and the fallback
--    immediately installed older A in its place. A subsequent whole-object
--    attribute replay that drops B's marker then leaves both authorities
--    agreeing on A, and `project(A)` overwrites B's pending state — where
--    before the repair the B entry would have refused it. The repair now
--    deletes and never restores: an alert left without an entry falls to the
--    marker, and a missing marker yields `projection_generation_unknown`, which
--    refuses. Failing closed is the only safe direction here, because nothing
--    at repair time can distinguish a genuine uncaptured staging from a forged
--    one.
--
-- No grant change and no trigger change; re-seal with `sql/install/12_reseal.sql`.
CREATE OR REPLACE FUNCTION zabalack.repair_staging_ledger() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE removed integer:=0;
BEGIN
 WITH invalid AS (
  DELETE FROM zabalack.alert_staging s
   -- A staged window is issued_at + 60 seconds by construction, so anything
   -- non-finite or materially in the future did not come from build_intent.
   WHERE s.staged_until IS NULL OR isfinite(s.staged_until) IS NOT TRUE
      OR s.staged_until>clock_timestamp()+interval '60 seconds'
   -- And only a verified generation corroborates itself. An action row exists
   -- only after verify_complete, which is the point at which the signature was
   -- checked; a capture alone proves nothing about the MAC.
      OR NOT EXISTS(SELECT 1 FROM zabalack.actions a
                     WHERE a.alert_id=s.alert_id AND a.action_id=s.staged_action_id)
  RETURNING alert_id)
 SELECT count(*) INTO removed FROM invalid;
 -- Nothing is restored. Installing a fallback generation is what let an older
 -- action displace an observed newer staging; an alert without an entry falls
 -- to the marker, and a missing marker refuses.
 IF removed>0 THEN
  INSERT INTO zabalack.audit(operation,actor_ref,details) VALUES('repair_staging_ledger',session_user,
   jsonb_build_object('removed',removed));
 END IF;
 RETURN jsonb_build_object('removed',removed);
END $$;
REVOKE ALL ON FUNCTION zabalack.repair_staging_ledger() FROM PUBLIC;

SELECT zabalack.repair_staging_ledger();

UPDATE zabalack.installation SET schema_version=8 WHERE singleton;
