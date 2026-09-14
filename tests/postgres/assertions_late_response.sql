-- T29/T31: isolated lease-expiry regressions. Synthetic ledger rows establish
-- internal coordination cases; they are not HMAC, real-source or HA evidence.
-- Every runtime function is called with current_user=zabalack_worker/operator.
BEGIN;
DO $$
DECLARE
 n integer; aid uuid; cid uuid; eid uuid; target text; first_claim jsonb; newer_claim jsonb;
 attempt jsonb; response jsonb; observed jsonb; data jsonb; audit_payload jsonb; role_name text; denied boolean;
BEGIN
 FOR n IN 1..7 LOOP
  aid:=gen_random_uuid(); cid:=gen_random_uuid(); eid:=gen_random_uuid(); target:='late-response-'||aid::text;
  SET ROLE zabalack_owner;
  UPDATE zabalack.installation SET external_writes_enabled=true,capture_disabled=false WHERE singleton;
  INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,host_ids,group_ids,enabled)
   VALUES('Late response synthetic',target,'9100','r1','1','["9200"]','["9300"]',true);
  INSERT INTO zabalack.instance_control(instance,paused) VALUES(target,false);
  INSERT INTO zabalack.captures(capture_id,alert_id,customer,snapshot,history_type,resulting_status,state)
   VALUES(cid,aid::text,'Late response synthetic','{}','ack','ack','verified');
  INSERT INTO zabalack.effects(effect_id,instance,event_id,generation,primary_action_id,latest_action_time)
   VALUES(eid,target,'9400',1,aid,clock_timestamp());
  INSERT INTO zabalack.actions(action_id,capture_id,capture_order,effect_id,alert_id,customer,instance,event_id,trigger_id,
   actor_ref,cohort_revision,policy_version,issued_at,execute_before)
   VALUES(aid,cid,1,eid,aid::text,'Late response synthetic',target,'9400','9100','synthetic','r1','1',clock_timestamp(),clock_timestamp()+interval '4 hours');
  SET ROLE zabalack_worker;
  first_claim:=zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','old-worker','action_id',aid));
  data:=jsonb_build_object('worker_id','old-worker','action_id',aid,'lease_generation',first_claim->'lease_generation');
  attempt:=zabalack.worker_call('start_attempt',data);
  IF attempt->>'attempt_id' IS NULL THEN RAISE EXCEPTION 'T31 setup failed to record first attempt'; END IF;
  data:=data||jsonb_build_object('attempt_id',attempt->'attempt_id');
  IF n=6 THEN
   -- A duplicate callback after an already committed valid completion must not
   -- overwrite success just because complete expired its lease on completion.
   PERFORM zabalack.worker_call('complete',data||'{"state":"ok","reason":"acknowledged","completion_evidence":"write_response","uncertain":false}');
  ELSE
   SET ROLE zabalack_owner;
   -- Test-only clock advancement avoids sleeping; native elapsed/failover gates stay open.
   UPDATE zabalack.delivery_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=aid;
   UPDATE zabalack.delivery_slots SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=aid;
   SET ROLE zabalack_worker;
  END IF;
  IF n=7 THEN
   -- Retry may requeue an in-flight action before its old callback returns.
   -- Pause may then convert the queued state to held; neither settles the send.
   SET ROLE zabalack_operator;
   PERFORM zabalack.operator_call('retry',jsonb_build_object('action_id',aid,'actor_ref','synthetic-reviewer','change_ref','LAB-IN-FLIGHT-RETRY'));
   PERFORM zabalack.operator_call('pause',jsonb_build_object('instance',target,'actor_ref','synthetic-reviewer','change_ref','LAB-IN-FLIGHT-PAUSE'));
   SET ROLE zabalack_worker;
   PERFORM zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','paused-worker','action_id',aid));
  END IF;
  IF n IN (4,5) THEN
   newer_claim:=zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','new-worker','action_id',aid));
   IF NOT (newer_claim->>'uncertain')::boolean THEN RAISE EXCEPTION 'T31 replacement did not detect abandoned attempt'; END IF;
  END IF;
  response:=zabalack.worker_call('complete',data||CASE
   WHEN n IN (2,5,6) THEN '{"state":"ok","reason":"acknowledged","completion_evidence":"write_response","uncertain":false}'::jsonb
   WHEN n=3 THEN '{"state":"failed","reason":"permission_denied","uncertain":false}'::jsonb
   ELSE '{"state":"pending","reason":"outcome_unknown","uncertain":true}'::jsonb END);
  IF response->>'state'<>'late_response' THEN RAISE EXCEPTION 'T31 late callback not fenced'; END IF;
  observed:=zabalack.worker_call('inspect',jsonb_build_object('action_id',aid))->0;
  IF n=6 THEN
   IF observed->>'state'<>'ok' OR (observed->>'uncertain')::boolean THEN RAISE EXCEPTION 'T31 duplicate late callback overwrote completed success'; END IF;
   -- Defensive fixture: an active lease must not make a settled terminal result
   -- eligible for shutdown requeue. Normal completion expires this lease.
   SET ROLE zabalack_owner;
   UPDATE zabalack.delivery_leases SET expires_at=clock_timestamp()+interval '30 seconds' WHERE action_id=aid;
   SET ROLE zabalack_worker;
   PERFORM zabalack.worker_call('worker_stopped','{"worker_id":"old-worker"}');
   observed:=zabalack.worker_call('inspect',jsonb_build_object('action_id',aid))->0;
   IF observed->>'state'<>'ok' OR (observed->>'uncertain')::boolean THEN RAISE EXCEPTION 'T31 shutdown overwrote terminal success'; END IF;
   CONTINUE;
  END IF;
  IF observed->>'state'<>'unknown' OR NOT (observed->>'uncertain')::boolean THEN
   RAISE EXCEPTION 'T31 late response cleared unresolved send (case %)',n;
  END IF;
  PERFORM zabalack.worker_call('reconcile_due','{}');
  IF zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','third-worker','action_id',aid)) IS NOT NULL THEN
   RAISE EXCEPTION 'T31 late response allowed blind delivery reclaim (case %)',n;
  END IF;
  IF n IN (4,5) THEN
   response:=zabalack.worker_call('before_write',jsonb_build_object('worker_id','new-worker','action_id',aid,'lease_generation',newer_claim->'lease_generation'));
   IF (response->>'allowed')::boolean THEN RAISE EXCEPTION 'T31 replacement generation can write uncertain action'; END IF;
   PERFORM zabalack.worker_call('worker_stopped','{"worker_id":"new-worker"}');
   observed:=zabalack.worker_call('inspect',jsonb_build_object('action_id',aid))->0;
   IF NOT (observed->>'uncertain')::boolean OR observed->>'state'<>'unknown' THEN RAISE EXCEPTION 'T31 replacement shutdown cleared prior-owner uncertainty'; END IF;
   IF zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','after-shutdown','action_id',aid)) IS NOT NULL THEN RAISE EXCEPTION 'T31 replacement shutdown permits blind resend'; END IF;
   CONTINUE;
  END IF;
  SET ROLE zabalack_operator;
  IF n=7 THEN
   PERFORM zabalack.operator_call('resume',jsonb_build_object('instance',target,'actor_ref','synthetic-reviewer','change_ref','LAB-IN-FLIGHT-RESUME'));
  END IF;
  audit_payload:=jsonb_build_object('action_id',aid,'actor_ref','synthetic-reviewer','change_ref','LAB-LATE-RESPONSE',
   'disposition','no-effect-confirmed','confirmation','REMOTE_ATTEMPT_FENCED_AND_SETTLED','evidence_ref','synthetic-no-effect-evidence');
  PERFORM zabalack.operator_call('settle_unknown',audit_payload);
  SET ROLE zabalack_worker;
  PERFORM zabalack.worker_call('complete',data||'{"state":"pending","reason":"duplicate_callback","uncertain":true}');
  observed:=zabalack.worker_call('inspect',jsonb_build_object('action_id',aid))->0;
  IF observed->>'state'<>'failed' OR (observed->>'uncertain')::boolean THEN RAISE EXCEPTION 'T31 duplicate callback overwrote explicit settlement'; END IF;
  IF zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','retry-worker','action_id',aid)) IS NOT NULL THEN RAISE EXCEPTION 'T31 settlement automatically queued write'; END IF;
  SET ROLE zabalack_operator;
  PERFORM zabalack.operator_call('retry',audit_payload);
  SET ROLE zabalack_worker;
  PERFORM zabalack.worker_call('complete',data||'{"state":"pending","reason":"duplicate_after_retry","uncertain":true}');
  observed:=zabalack.worker_call('inspect',jsonb_build_object('action_id',aid))->0;
  IF observed->>'state'<>'pending' OR (observed->>'uncertain')::boolean THEN RAISE EXCEPTION 'T31 duplicate callback overwrote explicitly settled retry'; END IF;
  newer_claim:=zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','retry-worker','action_id',aid));
  response:=zabalack.worker_call('before_write',jsonb_build_object('worker_id','retry-worker','action_id',aid,'lease_generation',newer_claim->'lease_generation'));
  IF NOT (response->>'allowed')::boolean THEN RAISE EXCEPTION 'T31 explicit fenced settlement and retry was blocked'; END IF;
 END LOOP;
 FOREACH role_name IN ARRAY ARRAY['zabalack_worker','zabalack_probe','zabalack_operator'] LOOP
  EXECUTE format('SET ROLE %I',role_name);
  denied:=false;
  BEGIN PERFORM zabalack.attempt_unsettled(gen_random_uuid());
  EXCEPTION WHEN insufficient_privilege THEN denied:=true;
  END;
  IF NOT denied THEN RAISE EXCEPTION 'T38 helper directly executable by %',role_name; END IF;
 END LOOP;
 RESET ROLE;
END $$;
ROLLBACK;
