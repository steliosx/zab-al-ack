-- Executable PostgreSQL assertions. Synthetic data only. Script is also exercised
-- by PGlite PG18 as supplemental single-session engine evidence, never native HA.
SET ROLE zabalack_operator;
SELECT zabalack.operator_call('configure_installation','{"actor_ref":"lab-engineer","change_ref":"LAB-1","deployment_id":"isolated-lab","config_revision":"lab-v1"}');
SELECT zabalack.operator_call('configure_cohort','{"actor_ref":"lab-engineer","change_ref":"LAB-1","cohort":{"customer":"Synthetic A","zabbix_instance":"lab-zabbix","zabbix_trigger_id":"100","host_ids":["10"],"group_ids":["20"],"revision":"lab-v1","policy_revision":"lab-v1","enabled":true,"one_to_one":true,"comment_enabled":true,"allow_admin":false}}');
SELECT zabalack.operator_call('set_external_writes','{"actor_ref":"lab-engineer","change_ref":"LAB-1","enabled":true,"confirmation":"ENABLE_EXTERNAL_WRITES","gates_reviewed":true}');
SELECT zabalack.operator_call('resume','{"instance":"lab-zabbix","actor_ref":"lab-engineer","change_ref":"LAB-1"}');
SET ROLE zabalack_probe;
DO $$ BEGIN
 IF (zabalack.capture_health()->>'trigger_ok')::boolean IS DISTINCT FROM true THEN RAISE EXCEPTION 'T21 trigger health'; END IF;
 IF has_table_privilege(current_user,'public.alerts','SELECT') OR has_table_privilege(current_user,'zabalack.captures','SELECT') THEN RAISE EXCEPTION 'T35 probe broad privilege'; END IF;
 BEGIN PERFORM zabalack.worker_call('status','{}'); RAISE EXCEPTION 'T35 worker API incorrectly granted'; EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SET ROLE alerta_app;
DO $$
DECLARE i jsonb; ts timestamptz:=clock_timestamp(); action_id uuid:=gen_random_uuid(); alert_id text:='10000000-0000-4000-8000-000000000001'; mark text;
BEGIN
 i:=jsonb_build_object('version',1,'action_id',action_id,'alert_id',alert_id,'customer','Synthetic A',
  'source',jsonb_build_object('zabbix_instance','lab-zabbix','zabbix_event_id','1001','zabbix_trigger_id','100'),
  'actor',jsonb_build_object('login','synthetic.operator','customers',jsonb_build_array('Synthetic A'),'admin',false,'auth_type','user'),
  'route','/alert/'||alert_id||'/action','action','ack','cohort_revision','lab-v1','policy_revision','lab-v1',
  'issued_at',ts,'capture_expires_at',ts+interval '60 seconds','execution_expires_at',ts+interval '4 hours');
 -- MAC is a synthetic structural placeholder. SQL does not verify cryptography;
 -- Python tests prove the mandatory verify-before-promote trust boundary.
 mark:='[zabalack:v1 action='||action_id||' kid=lab mac='||repeat('0',64)||']';
 INSERT INTO public.alerts(id,resource,event,status,customer,attributes,history)
  VALUES(alert_id,'synthetic:1','stable-trigger','open','Synthetic A',
   jsonb_build_object('source_system','zabbix','zabbix_instance','lab-zabbix','zabbix_event_id','1001','zabbix_trigger_id','100','custom_preserved','yes','zabbix_ack_sync_intent',i),ARRAY[]::public.history[]);
 UPDATE public.alerts SET status='ack',history=ARRAY[ROW('change-1','stable-trigger',NULL,'ack',NULL,'private note '||mark,'ack',ts AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history],update_time=ts AT TIME ZONE 'UTC' WHERE id=alert_id;
 PERFORM set_config('ack_test.action_id',action_id::text,false);
END $$;
SET ROLE zabalack_owner;
DO $$ BEGIN
 IF (SELECT count(*) FROM zabalack.captures)<>1 THEN RAISE EXCEPTION 'T1 missing atomic capture'; END IF;
 IF (SELECT state FROM zabalack.captures LIMIT 1)<>'captured_unverified' THEN RAISE EXCEPTION 'T1 malformed valid fixture'; END IF;
 IF (SELECT marker FROM zabalack.captures LIMIT 1) LIKE '%private note%' THEN RAISE EXCEPTION 'capture leaked operator note'; END IF;
 IF has_column_privilege(current_user,'public.alerts','status','UPDATE') OR has_column_privilege(current_user,'public.alerts','history','UPDATE') OR has_column_privilege(current_user,'public.alerts','customer','UPDATE') OR has_column_privilege(current_user,'public.alerts','severity','UPDATE') THEN RAISE EXCEPTION 'T38 owner protected column privilege'; END IF;
 IF has_table_privilege(current_user,'public.alerts','TRIGGER') THEN RAISE EXCEPTION 'T39 zabalack_owner trigger privilege'; END IF;
 BEGIN ALTER TABLE public.alerts DISABLE TRIGGER zabalack_capture; RAISE EXCEPTION 'T39 owner unexpectedly disabled trigger'; EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SET ROLE zabalack_worker;
DO $$ DECLARE c jsonb; a jsonb; d jsonb; attempt jsonb; out jsonb;
BEGIN
 BEGIN PERFORM zabalack.capture_health(); RAISE EXCEPTION 'T38 worker has unnecessary direct health grant'; EXCEPTION WHEN insufficient_privilege THEN NULL; END;
 IF zabalack.worker_call('doctor','{}')->>'database_name' IS DISTINCT FROM current_database() THEN RAISE EXCEPTION 'T38 restricted worker doctor cannot read health'; END IF;
 c:=zabalack.worker_call('claim_verification','{"worker_id":"lab-worker"}');
 IF c IS NULL THEN RAISE EXCEPTION 'claim missing'; END IF;
 a:=zabalack.worker_call('verify_complete',jsonb_build_object('worker_id','lab-worker','capture_id',c->>'capture_id','lease_generation',c->'lease_generation','intent',c->'intent','actor_ref','opaque-lab-actor'));
 IF a->>'state'<>'authorised' THEN RAISE EXCEPTION 'verify promotion failed %',a->>'reason'; END IF;
 d:=zabalack.worker_call('claim_delivery','{"worker_id":"lab-worker"}');
 IF d IS NULL OR d->>'event_id'<>'1001' THEN RAISE EXCEPTION 'T1 target changed'; END IF;
 IF NOT (zabalack.worker_call('before_write',d||'{"worker_id":"lab-worker"}')->>'allowed')::boolean THEN RAISE EXCEPTION 'before write denied'; END IF;
 attempt:=zabalack.worker_call('start_attempt',d||'{"worker_id":"lab-worker"}');
 out:=zabalack.worker_call('complete',d||jsonb_build_object('worker_id','lab-worker','attempt_id',attempt->>'attempt_id','state','ok','reason','write_confirmed','completion_evidence','write_response','uncertain',false));
 IF out->>'state'<>'ok' THEN RAISE EXCEPTION 'completion failed'; END IF;
 out:=zabalack.project((a->>'action_id')::uuid);
 IF out->>'reason'<>'projected' THEN RAISE EXCEPTION 'T38 projection failed %',out; END IF;
 BEGIN UPDATE public.alerts SET attributes='{}'; RAISE EXCEPTION 'T38 worker directly wrote'; EXCEPTION WHEN insufficient_privilege THEN NULL; END;
 BEGIN PERFORM zabalack.operator_call('capture_bypass','{}'); RAISE EXCEPTION 'T39 worker operator API'; EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
SET ROLE alerta_app;
DO $$ BEGIN
 IF (SELECT attributes->>'custom_preserved' FROM public.alerts LIMIT 1)<>'yes' OR (SELECT attributes->>'zabbix_event_id' FROM public.alerts LIMIT 1)<>'1001' THEN RAISE EXCEPTION 'T38 projection overwrote source/custom'; END IF;
 UPDATE public.alerts SET attributes=attributes||'{"other":"update"}' WHERE id='10000000-0000-4000-8000-000000000001';
END $$;
SET ROLE zabalack_owner;
DO $$ BEGIN IF (SELECT count(*) FROM zabalack.captures)<>1 THEN RAISE EXCEPTION 'T6 attribute update recaptured old head'; END IF; END $$;
-- T14 malformed marker and scalar/extra-field intents: local ack must persist
-- with diagnostic evidence. No malformed candidate reaches delivery uniqueness.
SET ROLE alerta_app;
DO $$ DECLARE text_value text; counter integer:=0;
BEGIN
 FOREACH text_value IN ARRAY ARRAY['[zabalack:v1 action=invalid kid=x mac=bad] SYNTHETIC_PRIVATE_NOTE_SENTINEL','[zabalack:v1 action=','[zabalack:'||repeat('x',300)] LOOP
  counter:=counter+1;
  UPDATE public.alerts SET history=ARRAY[ROW('bad-'||counter,'stable-trigger',NULL,'ack',NULL,text_value,'ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id='10000000-0000-4000-8000-000000000001';
 END LOOP;
 UPDATE public.alerts SET attributes=attributes||'{"zabbix_ack_sync_intent":4}',history=ARRAY[ROW('bad-scalar','stable-trigger',NULL,'ack',NULL,
  '[zabalack:v1 action='||current_setting('ack_test.action_id')||' kid=lab mac='||repeat('0',64)||']','ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id='10000000-0000-4000-8000-000000000001';
END $$;
SET ROLE zabalack_owner;
DO $$ BEGIN
 IF (SELECT count(*) FROM zabalack.captures WHERE diagnostic_reason='marker_malformed')<>3 THEN RAISE EXCEPTION 'T14 marker diagnostic mismatch'; END IF;
 IF EXISTS(SELECT 1 FROM zabalack.captures WHERE diagnostic_reason='marker_malformed' AND marker IS NOT NULL) THEN RAISE EXCEPTION 'T14 retained malformed marker bytes'; END IF;
 IF EXISTS(SELECT 1 FROM zabalack.captures c WHERE to_jsonb(c)::text LIKE '%SYNTHETIC_PRIVATE_NOTE_SENTINEL%') THEN RAISE EXCEPTION 'T14 capture retained malformed operator free text'; END IF;
 IF (SELECT count(*) FROM zabalack.captures WHERE diagnostic_reason='intent_shape_invalid' AND intent IS NULL)<>1 THEN RAISE EXCEPTION 'T14 scalar diagnostic'; END IF;
 IF (SELECT count(*) FROM zabalack.actions)<>1 THEN RAISE EXCEPTION 'T8 malformed candidate reserved verified identity'; END IF;
END $$;
-- Mandatory diagnostic fault rolls back only candidate ack; marker-free works.
SET ROLE zabalack_owner;
ALTER TABLE zabalack.captures ADD CONSTRAINT lab_capture_fault CHECK(false) NOT VALID;
SET ROLE alerta_app;
DO $$ BEGIN
 BEGIN
  UPDATE public.alerts SET history=ARRAY[ROW('fault','stable-trigger',NULL,'ack',NULL,'[zabalack:bad','ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id='10000000-0000-4000-8000-000000000001';
  RAISE EXCEPTION 'T22 expected capture failure';
 EXCEPTION WHEN check_violation THEN NULL; END;
 UPDATE public.alerts SET attributes=attributes||'{"unrelated_ingest":"works"}' WHERE id='10000000-0000-4000-8000-000000000001';
 UPDATE public.alerts SET history=ARRAY[ROW('local','stable-trigger',NULL,'ack',NULL,'local-only','ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id='10000000-0000-4000-8000-000000000001';
END $$;
SET ROLE zabalack_owner;
ALTER TABLE zabalack.captures DROP CONSTRAINT lab_capture_fault;
SET ROLE zabalack_operator;
SELECT zabalack.operator_call('pause','{"instance":"lab-zabbix","actor_ref":"lab-engineer","change_ref":"LAB-bypass"}');
SELECT zabalack.operator_call('capture_bypass','{"enabled":true,"config_revision":"lab-bypass","actor_ref":"lab-engineer","change_ref":"LAB-bypass"}');
SET ROLE alerta_app;
UPDATE public.alerts SET history=ARRAY[ROW('bypass','stable-trigger',NULL,'ack',NULL,'[zabalack:bad','ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id='10000000-0000-4000-8000-000000000001';
SET ROLE zabalack_owner;
DO $$ BEGIN IF (SELECT count(*) FROM zabalack.captures WHERE diagnostic_reason='skipped_capture_bypass')<>1 THEN RAISE EXCEPTION 'T39 bypass diagnostic'; END IF; END $$;
RESET ROLE;
SET ROLE zabalack_installer;
SET ROLE alerta_owner;
ALTER TABLE public.alerts DISABLE TRIGGER zabalack_capture;
SET ROLE zabalack_probe;
DO $$ BEGIN IF (zabalack.capture_health()->>'trigger_ok')::boolean THEN RAISE EXCEPTION 'T21 disabled trigger healthy'; END IF; END $$;
SET ROLE alerta_owner;
ALTER TABLE public.alerts ENABLE TRIGGER zabalack_capture;
RESET ROLE;
