-- Explicit owner-only restoration after behavioural tests and fleet fencing.
BEGIN;
DO $$ BEGIN
 IF pg_catalog.to_regnamespace('ackline') IS NOT NULL THEN
  RAISE EXCEPTION 'legacy_ackline_schema_present: fresh database required; see docs/RENAME.md';
 END IF;
END $$;
ALTER TABLE public.alerts ENABLE TRIGGER zabalack_capture;
COMMIT;
