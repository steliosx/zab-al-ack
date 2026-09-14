-- Grant the Alerta application role its one entry point into zabalack for A5
-- stage two: zabalack.stage_generation(). Run as zabalack_owner — the owner of
-- the schema and of the function — with :alert_app_role set to the role the
-- Alerta application connects as (the alerts owner where the application runs
-- as the upstream default; the separate login role where the deployment splits
-- them, which docs/deployment.md prefers).
--
-- It grants nothing else: no table, no other function, no ownership. USAGE on
-- the schema confers nothing on its own, because every function in it has
-- EXECUTE revoked from PUBLIC and the generation fence admits no grantee but
-- its owner — both of which capture_health() asserts under functions_ok.
--
-- This changes the schema and function ACLs the readiness manifest hashes, so
-- it runs after migration 008 and before the manifest is sealed
-- (10_trigger.sql on a fresh install, 13_reseal.sql on an upgrade).
BEGIN;
DO $$ BEGIN
 IF current_user<>'zabalack_owner' THEN RAISE EXCEPTION 'run as zabalack_owner'; END IF;
 IF (SELECT schema_version FROM zabalack.installation WHERE singleton)<4 THEN
  RAISE EXCEPTION 'apply migration 008 before granting the staging entry point';
 END IF;
END $$;
GRANT USAGE ON SCHEMA zabalack TO :"alert_app_role";
GRANT EXECUTE ON FUNCTION zabalack.stage_generation(text,uuid,jsonb) TO :"alert_app_role";
COMMIT;
