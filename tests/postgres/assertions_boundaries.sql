-- T14 source-backed parser boundary and capture-record allowlist regressions.
-- This script follows assertions.sql + assertions_extended.sql in an isolated DB.
SET ROLE zabalack_owner;
SELECT set_config('ack_test.original_intent',(SELECT intent::text FROM zabalack.captures WHERE state='verified' ORDER BY captured_at LIMIT 1),false);
SELECT set_config('ack_test.baseline_count',(SELECT count(*)::text FROM zabalack.captures),false);
SET ROLE alerta_app;
DO $$ DECLARE i jsonb:=current_setting('ack_test.original_intent')::jsonb; mark text; txt text; n integer:=0;
BEGIN
 mark:='[zabalack:v1 action='||(i->>'action_id')||' kid=lab mac='||repeat('0',64)||']';
 -- Consecutive entries carry distinct operator text before the marker: the
 -- trigger's WHEN predicate ignores a history head whose (update_time, type,
 -- text) is unchanged, and clock_timestamp() alone is not guaranteed to differ
 -- between two updates on an engine with millisecond resolution (PGlite).
 UPDATE public.alerts SET attributes=(attributes-'zabbix_ack_sync_intent')||jsonb_build_object('zabbix_event_id',i#>>'{source,zabbix_event_id}'),
  history=ARRAY[ROW('missing','stable-trigger',NULL,'ack',NULL,'missing '||mark,'ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id=i->>'alert_id';
 UPDATE public.alerts SET attributes=attributes||jsonb_build_object('zabbix_ack_sync_intent',i||jsonb_build_object('action_id',gen_random_uuid())),
  history=ARRAY[ROW('mismatch','stable-trigger',NULL,'ack',NULL,'mismatch '||mark,'ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id=i->>'alert_id';
 UPDATE public.alerts SET attributes=attributes||jsonb_build_object('zabbix_ack_sync_intent',i||'{"unexpected_private_text":"MUST_NOT_CAPTURE_SENTINEL"}'),
  history=ARRAY[ROW('extra','stable-trigger',NULL,'ack',NULL,'extra '||mark,'ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id=i->>'alert_id';
 -- Even a malformed marker must not preserve an oversized arbitrary intent.
 UPDATE public.alerts SET attributes=attributes||jsonb_build_object('zabbix_ack_sync_intent',jsonb_build_object('free_text',repeat('MUST_NOT_CAPTURE_SENTINEL',1000))),
  history=ARRAY[ROW('oversize-intent','stable-trigger',NULL,'ack',NULL,'[zabalack:bad','ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id=i->>'alert_id';
 FOREACH txt IN ARRAY ARRAY[repeat('n',65536-length(mark))||mark,repeat('n',65537-length(mark))||mark,repeat('α',32768)||mark] LOOP
  n:=n+1;
  UPDATE public.alerts SET attributes=attributes||jsonb_build_object('zabbix_ack_sync_intent',i),
   history=ARRAY[ROW('boundary-'||n,'stable-trigger',NULL,'ack',NULL,txt,'ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id=i->>'alert_id';
 END LOOP;
 -- Prefix entirely outside the bounded candidate region is intentionally local-only.
 UPDATE public.alerts SET history=ARRAY[ROW('outside-bound','stable-trigger',NULL,'ack',NULL,repeat('n',65536)||mark,'ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id=i->>'alert_id';
END $$;
SET ROLE zabalack_owner;
DO $$ BEGIN
 IF (SELECT count(*) FROM zabalack.captures)<>current_setting('ack_test.baseline_count')::int+7 THEN RAISE EXCEPTION 'T14 bounded candidate count'; END IF;
 IF EXISTS(SELECT 1 FROM zabalack.captures WHERE intent::text LIKE '%MUST_NOT_CAPTURE_SENTINEL%') THEN RAISE EXCEPTION 'T14 private data escaped allowlist'; END IF;
 IF NOT EXISTS(SELECT 1 FROM zabalack.captures WHERE diagnostic_reason='intent_missing') THEN RAISE EXCEPTION 'T14 missing intent not diagnosed'; END IF;
 IF NOT EXISTS(SELECT 1 FROM zabalack.captures WHERE diagnostic_reason='binding_mismatch') THEN RAISE EXCEPTION 'T14 mismatched action not diagnosed'; END IF;
END $$;
-- T21 catalog fingerprints detect replacement, ACL broadening and restore.
GRANT SELECT ON zabalack.captures TO zabalack_worker;
SET ROLE zabalack_probe;
DO $$ BEGIN IF (zabalack.capture_health()->>'functions_ok')::boolean THEN RAISE EXCEPTION 'T21 unapproved grant drift not detected'; END IF; END $$;
SET ROLE zabalack_owner;
REVOKE SELECT ON zabalack.captures FROM zabalack_worker;
RESET ROLE;
