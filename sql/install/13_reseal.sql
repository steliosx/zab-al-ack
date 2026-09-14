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
  OR to_regprocedure('zabalack.claim_retirement_slot()') IS NULL
  OR to_regclass('zabalack.alert_generations') IS NULL THEN
  RAISE EXCEPTION 'schema version 4 objects absent; refusing to seal an incomplete tree';
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
