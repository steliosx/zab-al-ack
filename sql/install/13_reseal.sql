-- Re-seal the readiness manifest after a migration that changes the zabalack
-- catalog but not the trigger. Run as zabalack_owner — the principal that
-- changed the catalog, the owner of seal_installation() and the only
-- non-superuser that can read the tables the checks below consult — while
-- eligible acknowledgements are fenced, and after 12_application_grant.sql:
-- the grant changes ACLs the manifest hashes. 10_trigger.sql, which changes
-- the trigger, still seals under the alerts owner.
--
-- This script never creates, alters or drops an object, never grants a
-- privilege and never changes ownership. It refuses rather than sealing a tree
-- that is not the one it was written for.
BEGIN;
DO $$ BEGIN
 IF current_user<>'zabalack_owner' THEN RAISE EXCEPTION 'run as zabalack_owner'; END IF;
END $$;
-- pg_get_triggerdef renders names according to search_path, so the fingerprint
-- is only comparable under the same path seal_installation() itself uses.
SET LOCAL search_path=pg_catalog,zabalack;
DO $$
DECLARE installed integer;
BEGIN
 SELECT schema_version INTO STRICT installed FROM zabalack.installation WHERE singleton;
 IF installed<>4 THEN
  RAISE EXCEPTION 'expected zabalack schema version 4, found %; apply the migrations first',installed;
 END IF;
 IF to_regprocedure('zabalack.stage_generation(text,uuid,jsonb)') IS NULL
  OR to_regprocedure('zabalack.newest_generation(text)') IS NULL
  OR to_regprocedure('zabalack.retire_intents(integer,integer)') IS NULL
  OR to_regprocedure('zabalack.intent_retirable(jsonb,interval,timestamptz)') IS NULL
  OR to_regprocedure('zabalack.intent_due_at(jsonb,interval)') IS NULL
  OR to_regprocedure('zabalack.claim_retirement_slot()') IS NULL
  OR to_regclass('zabalack.alert_generations') IS NULL THEN
  RAISE EXCEPTION 'schema version 4 objects absent; refusing to seal an incomplete tree';
 END IF;
 -- The application role's USAGE on the schema rests on these two facts, which
 -- capture_health() asserts under functions_ok. A seal over a tree that breaks
 -- either would make the digests agree with a real misconfiguration: refuse.
 IF EXISTS(SELECT 1 FROM pg_proc p WHERE p.pronamespace='zabalack'::regnamespace
   AND (p.proacl IS NULL OR EXISTS(SELECT 1 FROM aclexplode(p.proacl) e WHERE e.grantee=0))) THEN
  RAISE EXCEPTION 'a zabalack function is executable by PUBLIC; revoke that before sealing';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) e
   WHERE c.oid='zabalack.alert_generations'::regclass AND e.grantee<>c.relowner) THEN
  RAISE EXCEPTION 'zabalack.alert_generations admits a grantee other than its owner; revoke that before sealing';
 END IF;
 -- The staging entry point has exactly one non-owner grantee, the application
 -- role 12_application_grant.sql recorded. Any other is a second credential
 -- able to stage; a grant that was never recorded did not come from that
 -- script; and none at all — the grant revoked, the role dropped — would seal
 -- a tree on which every eligible acknowledgement fails behind a passing probe.
 IF NOT EXISTS(SELECT 1 FROM zabalack.installation i WHERE i.singleton AND i.application_role IS NOT NULL
    AND (SELECT count(*) FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
     WHERE p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure AND e.grantee<>p.proowner)=1
    AND EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
     WHERE p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure AND e.grantee<>p.proowner
      AND e.grantee<>0 AND pg_get_userbyid(e.grantee)=i.application_role)) THEN
  RAISE EXCEPTION 'zabalack.stage_generation() must have exactly one grantee, the recorded application role; run sql/install/12_application_grant.sql for that role, or revoke any other, before sealing';
 END IF;
 -- That role must also hold USAGE on the schema: without it the fully
 -- qualified call fails at schema access, and a seal over that would leave
 -- every eligible acknowledgement failing behind a passing probe.
 IF NOT EXISTS(SELECT 1 FROM zabalack.installation i JOIN pg_roles r ON r.rolname=i.application_role
    WHERE i.singleton AND has_schema_privilege(r.oid,'zabalack','USAGE')) THEN
  RAISE EXCEPTION 'the recorded application role does not hold USAGE on schema zabalack; run sql/install/12_application_grant.sql for that role before sealing';
 END IF;
 -- A fresh installation has no manifest yet: it is sealed by 10_trigger.sql
 -- under the alerts owner, not re-sealed here.
 IF NOT EXISTS(SELECT 1 FROM zabalack.readiness_manifest m WHERE m.singleton) THEN
  RAISE EXCEPTION 'readiness manifest not yet sealed; a fresh installation seals with sql/install/10_trigger.sql as the alerts owner';
 END IF;
 -- 008 does not touch the trigger definition, so its fingerprint must still
 -- match the sealed one. A mismatch here means something else moved.
 IF NOT EXISTS(SELECT 1 FROM zabalack.readiness_manifest m JOIN pg_trigger t
   ON t.tgrelid='public.alerts'::regclass AND t.tgname='zabalack_capture'
   WHERE m.singleton AND md5(pg_get_triggerdef(t.oid))=m.trigger_md5) THEN
  RAISE EXCEPTION 'trigger fingerprint changed; this is not an 008 upgrade';
 END IF;
END $$;
SELECT zabalack.seal_installation();
COMMIT;
