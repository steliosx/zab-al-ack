-- Test-only pre-006 state, for a fresh isolated database or rolled-back fixture.
-- The caller installs the shipped 003 worker function before invoking this file.
DO $$
DECLARE aid uuid; cid uuid; eid uuid; n integer; replacement jsonb;
 target text; claim jsonb; attempt jsonb; data jsonb; observed jsonb;
BEGIN
 FOR n IN 1..2 LOOP
 aid:=gen_random_uuid(); cid:=gen_random_uuid(); eid:=gen_random_uuid(); target:='upgrade-late-'||aid::text;
 SET ROLE zabalack_owner;
 UPDATE zabalack.installation SET external_writes_enabled=true,capture_disabled=false,schema_version=1 WHERE singleton;
 INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,host_ids,group_ids,enabled)
  VALUES('Upgrade synthetic',target,'9510','r1','1','["9520"]','["9530"]',true);
 INSERT INTO zabalack.instance_control(instance,paused) VALUES(target,false);
 INSERT INTO zabalack.captures(capture_id,alert_id,customer,snapshot,history_type,resulting_status,state)
  VALUES(cid,aid::text,'Upgrade synthetic','{}','ack','ack','verified');
 INSERT INTO zabalack.effects(effect_id,instance,event_id,generation,primary_action_id,latest_action_time)
  VALUES(eid,target,'9540',1,aid,clock_timestamp());
 INSERT INTO zabalack.actions(action_id,capture_id,capture_order,effect_id,alert_id,customer,instance,event_id,trigger_id,
  actor_ref,cohort_revision,policy_version,issued_at,execute_before)
  VALUES(aid,cid,1,eid,aid::text,'Upgrade synthetic',target,'9540','9510','synthetic','r1','1',clock_timestamp(),clock_timestamp()+interval '4 hours');
 SET ROLE zabalack_worker;
 claim:=zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','upgrade-old','action_id',aid));
 data:=jsonb_build_object('worker_id','upgrade-old','action_id',aid,'lease_generation',claim->'lease_generation');
 attempt:=zabalack.worker_call('start_attempt',data);
 SET ROLE zabalack_owner;
 UPDATE zabalack.delivery_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=aid;
 UPDATE zabalack.delivery_slots SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=aid;
 SET ROLE zabalack_worker;
 IF n=2 THEN
  replacement:=zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','upgrade-replacement','action_id',aid));
  IF NOT (replacement->>'uncertain')::boolean THEN RAISE EXCEPTION 'upgrade fixture failed to acquire abandoned generation'; END IF;
 END IF;
 PERFORM zabalack.worker_call('complete',data||jsonb_build_object('attempt_id',attempt->'attempt_id','state','pending','reason','outcome_unknown','uncertain',true));
 IF n=2 THEN PERFORM zabalack.worker_call('worker_stopped','{"worker_id":"upgrade-replacement"}'); END IF;
 observed:=zabalack.worker_call('inspect',jsonb_build_object('action_id',aid))->0;
 IF observed->>'state'<>(CASE WHEN n=1 THEN 'delivery_claimed' ELSE 'pending' END) OR (observed->>'uncertain')::boolean THEN RAISE EXCEPTION 'upgrade fixture must reproduce pre-006 defect'; END IF;
 PERFORM set_config('ack_test.late_upgrade_id_'||n,aid::text,true);
 END LOOP;
 RESET ROLE;
END $$;
