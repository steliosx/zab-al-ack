-- Explicit trigger-installation step. Run under the separately approved alerts
-- owner/installation principal, while eligible acknowledgements are fenced.
-- This script never grants privileges or changes ownership.
BEGIN;
DO $$ BEGIN
 IF pg_catalog.to_regnamespace('ackline') IS NOT NULL THEN
  RAISE EXCEPTION 'legacy_ackline_schema_present: fresh database required; see docs/RENAME.md';
 END IF;
END $$;
CREATE OR REPLACE TRIGGER zabalack_capture
AFTER UPDATE ON public.alerts FOR EACH ROW
WHEN (
 NEW.status='ack' AND (NEW.history[1]).type='ack'
 AND ROW((NEW.history[1]).update_time,(NEW.history[1]).type,(NEW.history[1]).text)
   IS DISTINCT FROM ROW((OLD.history[1]).update_time,(OLD.history[1]).type,(OLD.history[1]).text)
 AND position('[zabalack:' IN left(coalesce((NEW.history[1]).text,''),65536))>0
)
EXECUTE FUNCTION zabalack.capture_ack();
SELECT zabalack.seal_installation();
COMMIT;
