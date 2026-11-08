-- T45: claim_delivery fences an abandoned send before its cohort-policy branch
-- records a failure. A send recorded against a lease that has since expired is
-- not proof of non-send when the cohort revision, policy or enablement changed
-- in between. Synthetic ledger rows establish internal coordination cases
-- only; no HMAC, real-source, concurrency or HA evidence follows from them.
-- Every runtime function is called as zabalack_worker/zabalack_operator.
BEGIN;
DO $$
DECLARE
 n integer; aid uuid; cid uuid; eid uuid; target text; claim jsonb; data jsonb; attempt jsonb; observed jsonb; again jsonb;
BEGIN
 -- 1 the send is recorded under a live lease, the lease expires before the
 --   deadline, the cohort revision changes, and the next claim arrives before
 --   any reconciliation pass.
 -- 2 the same, with the cohort disabled instead.
 -- 3 negative control: the attempt was settled before the lease expired. The
 --   claim still records the ordinary cohort-policy failure.
 FOR n IN 1..3 LOOP
  aid:=gen_random_uuid(); cid:=gen_random_uuid(); eid:=gen_random_uuid(); target:='cohort-policy-fence-'||aid::text;
  SET ROLE zabalack_owner;
  UPDATE zabalack.installation SET external_writes_enabled=true,capture_disabled=false WHERE singleton;
  INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,host_ids,group_ids,enabled)
   VALUES('Cohort policy fence synthetic',target,'9100','r1','1','["9200"]','["9300"]',true);
  INSERT INTO zabalack.instance_control(instance,paused) VALUES(target,false);
  INSERT INTO zabalack.captures(capture_id,alert_id,customer,snapshot,history_type,resulting_status,state)
   VALUES(cid,aid::text,'Cohort policy fence synthetic','{}','ack','ack','verified');
  INSERT INTO zabalack.effects(effect_id,instance,event_id,generation,primary_action_id,latest_action_time)
   VALUES(eid,target,'9400',1,aid,clock_timestamp());
  INSERT INTO zabalack.actions(action_id,capture_id,capture_order,effect_id,alert_id,customer,instance,event_id,trigger_id,
   actor_ref,cohort_revision,policy_version,issued_at,execute_before)
   VALUES(aid,cid,1,eid,aid::text,'Cohort policy fence synthetic',target,'9400','9100','synthetic','r1','1',clock_timestamp(),clock_timestamp()+interval '4 hours');

  SET ROLE zabalack_worker;
  claim:=zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','first-worker','action_id',aid));
  IF claim IS NULL THEN RAISE EXCEPTION 'T45 setup could not claim delivery (case %)',n; END IF;
  data:=jsonb_build_object('worker_id','first-worker','action_id',aid,'lease_generation',claim->'lease_generation');
  attempt:=zabalack.worker_call('start_attempt',data);
  IF attempt->>'attempt_id' IS NULL THEN RAISE EXCEPTION 'T45 setup could not record the attempt (case %): %',n,attempt; END IF;
  data:=data||jsonb_build_object('attempt_id',attempt->'attempt_id');
  IF n=3 THEN
   PERFORM zabalack.worker_call('complete',data||'{"state":"pending","reason":"source_busy","uncertain":false}');
  END IF;

  SET ROLE zabalack_owner;
  -- Test-only clock advancement, never a sleep. The lease expires well before
  -- the execution deadline; then the cohort changes. No reconciliation pass
  -- runs in between.
  UPDATE zabalack.actions SET next_due=clock_timestamp()-interval '1 second' WHERE action_id=aid;
  UPDATE zabalack.delivery_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=aid;
  UPDATE zabalack.delivery_slots SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=aid;
  IF n=2 THEN
   UPDATE zabalack.cohorts SET enabled=false WHERE instance=target;
  ELSE
   UPDATE zabalack.cohorts SET revision='r2' WHERE instance=target;
  END IF;

  SET ROLE zabalack_worker;
  again:=zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','second-worker','action_id',aid));
  SET ROLE zabalack_owner;
  SELECT jsonb_build_object('state',state,'uncertain',uncertain,'reason',reason) INTO observed FROM zabalack.actions WHERE action_id=aid;

  IF n IN (1,2) THEN
   IF observed->>'state'='failed' OR (observed->>'uncertain')::boolean IS NOT TRUE OR observed->>'reason'<>'abandoned_attempt' THEN
    RAISE EXCEPTION 'T45 case %: a send abandoned on an expired lease was recorded by the next claim as %',n,observed;
   END IF;
   -- Under the changed policy the action is never handed a new lease.
   IF again IS NOT NULL THEN
    RAISE EXCEPTION 'T45 case %: a claim under a changed cohort handed out a new lease: %',n,again;
   END IF;
   -- What the uncertainty is for: the operator can settle it.
   SET ROLE zabalack_operator;
   PERFORM zabalack.operator_call('settle_unknown',jsonb_build_object('action_id',aid,'disposition','no-effect-confirmed',
    'confirmation','REMOTE_ATTEMPT_FENCED_AND_SETTLED','evidence_ref','synthetic-fenced-no-effect','actor_ref','synthetic-reviewer','change_ref','LAB-COHORT-FENCE-SETTLE'));
   SET ROLE zabalack_owner;
   SELECT jsonb_build_object('state',state,'uncertain',uncertain) INTO observed FROM zabalack.actions WHERE action_id=aid;
   IF (observed->>'uncertain')::boolean IS NOT FALSE OR observed->>'state' NOT IN ('failed','expired') THEN
    RAISE EXCEPTION 'T45 case %: settlement after fencing left %',n,observed;
   END IF;
  ELSE
   IF again IS NOT NULL OR observed->>'state'<>'failed' OR (observed->>'uncertain')::boolean IS NOT FALSE OR observed->>'reason'<>'cohort_policy_changed' THEN
    RAISE EXCEPTION 'T45 case 3: an ordinary cohort-policy change no longer fails the action: % (claim %)',observed,again;
   END IF;
  END IF;
 END LOOP;
END $$;
ROLLBACK;
