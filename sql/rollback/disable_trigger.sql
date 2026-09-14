-- Break-glass, separately approved alerts table OWNER authority only.
-- Pause writes, account for in-flight HTTP, distribute and verify plugin bypass
-- across every serving node BEFORE running. A disabled trigger cannot diagnose.
BEGIN;
ALTER TABLE public.alerts DISABLE TRIGGER zabalack_capture;
COMMIT;
