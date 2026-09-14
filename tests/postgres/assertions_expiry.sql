-- A known never-sent expired effect must not trap a later explicit human action.
SET ROLE zabalack_owner;
UPDATE zabalack.outbox SET due_at=clock_timestamp()+interval '1 day' WHERE state IN ('captured_unverified','verifier_hold');
UPDATE zabalack.actions SET execute_before=clock_timestamp()-interval '1 hour' WHERE event_id='2000';
UPDATE zabalack.effects SET completed_at=NULL,state='authorised' WHERE event_id='2000';
SET ROLE zabalack_worker;
SELECT zabalack.worker_call('reconcile_due','{"limit":200}');
SET ROLE zabalack_owner;
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM zabalack.effects WHERE event_id='2000' AND completed_at IS NULL) THEN RAISE EXCEPTION 'expired primary left unresolved effect'; END IF;
END $$;
SET ROLE alerta_app;
DO $$ DECLARE ts timestamptz:=clock_timestamp(); alertid uuid:=gen_random_uuid(); aid uuid:=gen_random_uuid(); i jsonb; mark text;
BEGIN
 i:=jsonb_build_object('version',1,'action_id',aid,'alert_id',alertid,'customer','Synthetic A',
  'source',jsonb_build_object('zabbix_instance','lab-zabbix','zabbix_event_id','2000','zabbix_trigger_id','100'),
  'actor',jsonb_build_object('login','synthetic.operator','customers',jsonb_build_array('Synthetic A'),'admin',false,'auth_type','user'),
  'route','/alert/'||alertid||'/action','action','ack','cohort_revision','lab-v2','policy_revision','lab-v1',
  'issued_at',ts,'capture_expires_at',ts+interval '60 seconds','execution_expires_at',ts+interval '4 hours');
 mark:='[zabalack:v1 action='||aid||' kid=lab mac='||repeat('0',64)||']';
 INSERT INTO public.alerts(id,resource,event,status,customer,attributes,history) VALUES(alertid,'synthetic-expiry','stable-trigger','open','Synthetic A',
  jsonb_build_object('source_system','zabbix','zabbix_instance','lab-zabbix','zabbix_event_id','2000','zabbix_trigger_id','100','zabbix_ack_sync_intent',i),ARRAY[]::public.history[]);
 UPDATE public.alerts SET status='ack',history=ARRAY[ROW('new-explicit-action','stable-trigger',NULL,'ack',NULL,mark,'ack',ts AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id=alertid::text;
 PERFORM set_config('ack_test.expiry_new_action',aid::text,false);
END $$;
SET ROLE zabalack_owner;
UPDATE zabalack.installation SET verification_turn=0 WHERE singleton;
SET ROLE zabalack_worker;
DO $$ DECLARE capture jsonb; action jsonb;
BEGIN
 capture:=zabalack.worker_call('claim_verification','{"worker_id":"expiry-verifier"}');
 action:=zabalack.worker_call('verify_complete',jsonb_build_object('worker_id','expiry-verifier','capture_id',capture->>'capture_id','lease_generation',capture->'lease_generation','intent',capture->'intent','actor_ref','opaque-lab-actor'));
 IF action->>'state'<>'authorised' OR action->>'action_id'<>current_setting('ack_test.expiry_new_action') THEN RAISE EXCEPTION 'new explicit human action coalesced behind expired work'; END IF;
END $$;
SET ROLE zabalack_owner;
DO $$ BEGIN IF (SELECT max(generation) FROM zabalack.effects WHERE event_id='2000')<>2 THEN RAISE EXCEPTION 'explicit later action missed next effect generation'; END IF; END $$;
RESET ROLE;
-- T7: a committed proof replay with stale attributes cannot reserve a new action
-- or effect. It is captured separately, then recognized at the verified boundary.
SET ROLE zabalack_owner;
SELECT set_config('ack_test.replay_actions',(SELECT count(*)::text FROM zabalack.actions),false);
SELECT set_config('ack_test.replay_effects',(SELECT count(*)::text FROM zabalack.effects),false);
UPDATE zabalack.installation SET verification_turn=0 WHERE singleton;
SET ROLE alerta_app;
UPDATE public.alerts SET attributes=attributes||jsonb_build_object('zabbix_ack_sync_intent',attributes->'zabbix_ack_sync_intent'),
 history=ARRAY[ROW('replayed-capture','stable-trigger',NULL,'ack',NULL,
 '[zabalack:v1 action='||(attributes#>>'{zabbix_ack_sync_intent,action_id}')||' kid=lab mac='||repeat('0',64)||']',
 'ack',clock_timestamp() AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history]
 WHERE attributes#>>'{zabbix_ack_sync_intent,action_id}'=current_setting('ack_test.expiry_new_action');
SET ROLE zabalack_worker;
DO $$ DECLARE capture jsonb; action jsonb;
BEGIN
 capture:=zabalack.worker_call('claim_verification','{"worker_id":"replay-verifier"}');
 action:=zabalack.worker_call('verify_complete',jsonb_build_object('worker_id','replay-verifier','capture_id',capture->>'capture_id','lease_generation',capture->'lease_generation','intent',capture->'intent','actor_ref','opaque-lab-actor'));
 IF NOT (action->>'replay')::boolean OR action->>'action_id'<>current_setting('ack_test.expiry_new_action') THEN RAISE EXCEPTION 'T7 replay minted new action'; END IF;
END $$;
SET ROLE zabalack_owner;
DO $$ BEGIN
 IF (SELECT count(*) FROM zabalack.actions)<>current_setting('ack_test.replay_actions')::int OR (SELECT count(*) FROM zabalack.effects)<>current_setting('ack_test.replay_effects')::int THEN RAISE EXCEPTION 'T7 duplicate verified effect'; END IF;
END $$;
RESET ROLE;
