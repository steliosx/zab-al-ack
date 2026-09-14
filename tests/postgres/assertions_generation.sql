-- Same issuance timestamp, reversed verification, stale attribute restoration.
-- Database capture order, not wall-clock ordering, controls UI generation.
SET ROLE zabalack_operator;
SELECT zabalack.operator_call('configure_cohort','{"actor_ref":"lab-engineer","change_ref":"LAB-generation","cohort":{"customer":"Synthetic A","zabbix_instance":"lab-zabbix","zabbix_trigger_id":"100","host_ids":["10"],"group_ids":["20"],"revision":"lab-v2","policy_revision":"lab-v1","enabled":true,"one_to_one":true,"comment_enabled":true,"allow_admin":false}}');
SET ROLE zabalack_owner;
UPDATE zabalack.outbox SET due_at=clock_timestamp()+interval '1 day' WHERE state IN ('captured_unverified','verifier_hold');
SET ROLE alerta_app;
DO $$ DECLARE ts timestamptz:=clock_timestamp(); alertid uuid:=gen_random_uuid(); aid uuid; i jsonb; mark text; n integer;
BEGIN
 INSERT INTO public.alerts(id,resource,event,status,customer,attributes,history) VALUES(alertid,'synthetic-generation','stable-trigger','open','Synthetic A',
  '{"source_system":"zabbix","zabbix_instance":"lab-zabbix","zabbix_event_id":"2000","zabbix_trigger_id":"100"}',ARRAY[]::public.history[]);
 FOR n IN 1..2 LOOP
  aid:=gen_random_uuid();
  i:=jsonb_build_object('version',1,'action_id',aid,'alert_id',alertid,'customer','Synthetic A',
   'source',jsonb_build_object('zabbix_instance','lab-zabbix','zabbix_event_id','2000','zabbix_trigger_id','100'),
   'actor',jsonb_build_object('login','synthetic.operator','customers',jsonb_build_array('Synthetic A'),'admin',false,'auth_type','user'),
   'route','/alert/'||alertid||'/action','action','ack','cohort_revision','lab-v2','policy_revision','lab-v1',
   'issued_at',ts,'capture_expires_at',ts+interval '60 seconds','execution_expires_at',ts+interval '4 hours');
  mark:='[zabalack:v1 action='||aid||' kid=lab mac='||repeat('0',64)||']';
  UPDATE public.alerts SET status='ack',attributes=attributes||jsonb_build_object('zabbix_ack_sync_intent',i),
   history=ARRAY[ROW('generation-'||n,'stable-trigger',NULL,'ack',NULL,mark,'ack',ts AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id=alertid::text;
  PERFORM set_config('ack_test.generation_intent_'||n,i::text,false);
 END LOOP;
END $$;
SET ROLE zabalack_owner;
UPDATE zabalack.outbox o SET due_at=clock_timestamp()-CASE WHEN c.intent->>'action_id'=current_setting('ack_test.generation_intent_2')::jsonb->>'action_id' THEN interval '2 seconds' ELSE interval '1 second' END
 FROM zabalack.captures c WHERE o.capture_id=c.capture_id AND c.alert_id=current_setting('ack_test.generation_intent_2')::jsonb->>'alert_id';
UPDATE zabalack.installation SET verification_turn=0 WHERE singleton;
SET ROLE zabalack_worker;
DO $$ DECLARE c jsonb; action jsonb; n integer;
BEGIN
 FOR n IN 1..2 LOOP
  c:=zabalack.worker_call('claim_verification','{"worker_id":"generation-verifier"}');
  IF n=1 AND c->'intent'->>'action_id'<>current_setting('ack_test.generation_intent_2')::jsonb->>'action_id' THEN RAISE EXCEPTION 'regression setup did not reverse verification'; END IF;
  action:=zabalack.worker_call('verify_complete',jsonb_build_object('worker_id','generation-verifier','capture_id',c->>'capture_id','lease_generation',c->'lease_generation','intent',c->'intent','actor_ref','opaque-lab-actor'));
 END LOOP;
END $$;
SET ROLE alerta_app;
UPDATE public.alerts SET attributes=attributes||jsonb_build_object('zabbix_ack_sync_intent',current_setting('ack_test.generation_intent_1')::jsonb)
 WHERE id=current_setting('ack_test.generation_intent_1')::jsonb->>'alert_id';
SET ROLE zabalack_worker;
DO $$ DECLARE result jsonb;
BEGIN
 result:=zabalack.project((current_setting('ack_test.generation_intent_1')::jsonb->>'action_id')::uuid);
 IF result->>'reason'<>'projection_action_generation_mismatch' THEN RAISE EXCEPTION 'T7/T38 same-time earlier projection defeated generation guard'; END IF;
END $$;
SET ROLE alerta_app;
UPDATE public.alerts SET attributes=attributes||jsonb_build_object('zabbix_ack_sync_intent',current_setting('ack_test.generation_intent_2')::jsonb,'zabbix_ack_sync_local_at',(clock_timestamp()+interval '1 second')::text)
 WHERE id=current_setting('ack_test.generation_intent_2')::jsonb->>'alert_id';
SET ROLE zabalack_worker;
DO $$ DECLARE result jsonb;
BEGIN
 result:=zabalack.project((current_setting('ack_test.generation_intent_2')::jsonb->>'action_id')::uuid);
 IF result->>'reason'<>'projection_local_generation_mismatch' THEN RAISE EXCEPTION 'new local-only skip overwritten by old source effect'; END IF;
END $$;
SET ROLE alerta_app;
DELETE FROM public.alerts WHERE id=current_setting('ack_test.generation_intent_2')::jsonb->>'alert_id';
SET ROLE zabalack_worker;
DO $$ DECLARE result jsonb;
BEGIN
 result:=zabalack.project((current_setting('ack_test.generation_intent_2')::jsonb->>'action_id')::uuid);
 IF result->>'reason'<>'projection_target_missing' THEN RAISE EXCEPTION 'absent projection target mishandled'; END IF;
END $$;
RESET ROLE;
