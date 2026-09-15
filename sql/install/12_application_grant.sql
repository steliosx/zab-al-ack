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
-- its owner — both of which capture_health() asserts under functions_ok, as it
-- asserts that the function's only non-owner grantee is the role recorded here.
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
-- The function admits one non-owner grantee. Every existing one loses it
-- first, so changing the application role never leaves a former role's
-- credentials able to stage; running it again for the same role is a no-op.
DO $$
DECLARE former name;
BEGIN
 FOR former IN SELECT pg_get_userbyid(e.grantee) FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
   WHERE p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure AND e.grantee<>p.proowner AND e.grantee<>0
 LOOP
  EXECUTE format('REVOKE EXECUTE ON FUNCTION zabalack.stage_generation(text,uuid,jsonb) FROM %I',former);
 END LOOP;
END $$;
GRANT USAGE ON SCHEMA zabalack TO :"alert_app_role";
GRANT EXECUTE ON FUNCTION zabalack.stage_generation(text,uuid,jsonb) TO :"alert_app_role";
-- Record the grantee. capture_health() reports functions_ok false while the
-- function has any non-owner grantee other than this one, and 13_reseal.sql
-- refuses to seal such a tree.
UPDATE zabalack.installation SET application_role=(SELECT pg_get_userbyid(e.grantee) FROM pg_proc p
 CROSS JOIN LATERAL aclexplode(p.proacl) e
 WHERE p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure AND e.grantee<>p.proowner AND e.grantee<>0)
 WHERE singleton;
COMMIT;
