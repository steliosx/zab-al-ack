-- Re-seal the readiness manifest after a migration that changes the zabalack
-- catalog. Run under the separately approved alerts owner/installation
-- principal, the same identity that ran 10_trigger.sql, while eligible
-- acknowledgements are fenced.
--
-- This script never creates, alters or drops an object, never grants a
-- privilege and never changes ownership. It refuses rather than sealing a tree
-- that is not the one it was written for.
BEGIN;
-- pg_get_triggerdef renders names according to search_path, so the fingerprint
-- is only comparable under the same path seal_installation() itself uses.
SET LOCAL search_path=pg_catalog,zabalack;
DO $$
DECLARE installed integer;
BEGIN
 SELECT schema_version INTO STRICT installed FROM zabalack.installation WHERE singleton;
 IF installed<>5 THEN
  RAISE EXCEPTION 'expected zabalack schema version 5, found %; apply the migrations first',installed;
 END IF;
 IF to_regprocedure('zabalack.retire_intents(integer,integer)') IS NULL
  OR to_regprocedure('zabalack.note_staging(text,uuid,timestamptz,text)') IS NULL
  OR to_regclass('zabalack.alert_staging') IS NULL THEN
  RAISE EXCEPTION 'schema version 4 objects absent; refusing to seal an incomplete tree';
 END IF;
 -- The trigger definition is unchanged by 008, so its fingerprint must still
 -- match the sealed one. A mismatch here means something else moved.
 IF NOT EXISTS(SELECT 1 FROM zabalack.readiness_manifest m JOIN pg_trigger t
   ON t.tgrelid='public.alerts'::regclass AND t.tgname='zabalack_capture'
   WHERE m.singleton AND md5(pg_get_triggerdef(t.oid))=m.trigger_md5) THEN
  RAISE EXCEPTION 'trigger fingerprint changed; this is not a 008-only upgrade';
 END IF;
END $$;
SELECT zabalack.seal_installation();
COMMIT;
