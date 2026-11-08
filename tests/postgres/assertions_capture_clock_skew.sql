-- T48: verify_complete allows a bounded clock difference between the Alerta
-- host (which signs issued_at) and the database (which records captured_at).
-- An intent issued up to 5 s after the database recorded its capture is
-- verified; beyond that it is refused. Synthetic rows; no HMAC is checked in
-- SQL (the worker verifies the MAC first), and no real clock skew is induced:
-- issued_at is written ahead of the database clock instead.
BEGIN;
SET ROLE zabalack_operator;
SELECT zabalack.operator_call('configure_cohort','{"actor_ref":"lab-engineer","change_ref":"LAB-skew","cohort":{"customer":"Skew synthetic","zabbix_instance":"skew-zabbix","zabbix_trigger_id":"300","host_ids":["30"],"group_ids":["40"],"revision":"skew-v1","policy_revision":"skew-p1","enabled":true,"one_to_one":true,"comment_enabled":true,"allow_admin":false}}');
SET ROLE zabalack_owner;
UPDATE zabalack.outbox SET due_at=clock_timestamp()+interval '1 day' WHERE state IN ('captured_unverified','verifier_hold');
DO $$
DECLARE ts timestamptz; alertid uuid; aid uuid; i jsonb; mark text; c jsonb; result jsonb; n integer; ahead interval; refused boolean;
BEGIN
 -- 1 issued 3 s after the capture: verified.
 -- 2 issued 7 s after the capture: refused as an invalid capture lifetime.
 FOR n IN 1..2 LOOP
  ahead:=CASE n WHEN 1 THEN interval '3 seconds' ELSE interval '7 seconds' END;
  ts:=clock_timestamp(); alertid:=gen_random_uuid(); aid:=gen_random_uuid();
  SET ROLE alerta_app;
  INSERT INTO public.alerts(id,resource,event,status,customer,attributes,history) VALUES(alertid,'synthetic-skew','skew-trigger','open','Skew synthetic',
   jsonb_build_object('source_system','zabbix','zabbix_instance','skew-zabbix','zabbix_event_id','300'||n,'zabbix_trigger_id','300'),ARRAY[]::public.history[]);
  UPDATE public.alerts SET last_receive_id='receive-'||alertid,last_receive_time=ts AT TIME ZONE 'UTC' WHERE id=alertid::text;
  PERFORM zabalack.bind_source(alertid::text,'receive-'||alertid,ts AT TIME ZONE 'UTC','Skew synthetic','skew-zabbix','300'||n,'300');
  i:=jsonb_build_object('version',1,'action_id',aid,'alert_id',alertid,'customer','Skew synthetic',
   'source',jsonb_build_object('zabbix_instance','skew-zabbix','zabbix_event_id','300'||n,'zabbix_trigger_id','300'),
   'actor',jsonb_build_object('login','synthetic.operator','customers',jsonb_build_array('Skew synthetic'),'admin',false,'auth_type','user'),
   'route','/alert/'||alertid||'/action','action','ack','cohort_revision','skew-v1','policy_revision','skew-p1',
   'issued_at',ts+ahead,'capture_expires_at',ts+ahead+interval '60 seconds','execution_expires_at',ts+ahead+interval '4 hours');
  mark:='[zabalack:v1 action='||aid||' kid=lab mac='||repeat('0',64)||']';
  UPDATE public.alerts SET status='ack',attributes=attributes||jsonb_build_object('zabbix_ack_sync_intent',i),
   history=ARRAY[ROW('skew-'||n,'skew-trigger',NULL,'ack',NULL,mark,'ack',ts AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id=alertid::text;
  SET ROLE zabalack_worker;
  c:=zabalack.worker_call('claim_verification','{"worker_id":"skew-verifier"}');
  IF c IS NULL OR c->'intent'->>'action_id'<>aid::text THEN RAISE EXCEPTION 'T48 setup case %: claimed %',n,c; END IF;
  refused:=false;
  BEGIN
   result:=zabalack.worker_call('verify_complete',jsonb_build_object('worker_id','skew-verifier','capture_id',c->>'capture_id',
    'lease_generation',c->'lease_generation','intent',c->'intent','actor_ref','opaque-lab-actor'));
  EXCEPTION WHEN raise_exception THEN
   IF SQLERRM<>'invalid capture lifetime' THEN RAISE; END IF;
   refused:=true;
  END;
  IF n=1 AND (refused OR result->>'action_id' IS DISTINCT FROM aid::text) THEN
   RAISE EXCEPTION 'T48 case 1: an intent issued 3 s after its capture was not verified (refused %, result %)',refused,result;
  END IF;
  IF n=2 AND NOT refused THEN
   RAISE EXCEPTION 'T48 case 2: an intent issued 7 s after its capture was verified: %',result;
  END IF;
  SET ROLE zabalack_owner;
 END LOOP;
END $$;
ROLLBACK;
