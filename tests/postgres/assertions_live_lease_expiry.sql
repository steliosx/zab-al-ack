-- T43: the execution-lifetime expiry statement reads the delivery facts too.
-- A send committed before sending, under a lease that is still live, is not
-- proof of non-send when the deadline passes. Synthetic ledger rows establish
-- internal coordination cases only; no HMAC, real-source, concurrency or HA
-- evidence follows from them. Every runtime function is called as
-- zabalack_worker/zabalack_operator.
BEGIN;
DO $$
DECLARE
 n integer; aid uuid; cid uuid; eid uuid; target text; claim jsonb; data jsonb; attempt jsonb; observed jsonb;
BEGIN
 -- 1 the minimized reproduction (nightly profile, 28 October 2026): retry
 --   requeues a claimed action while its lease is live, the replacement send
 --   is recorded against that lease, the execution deadline passes while the
 --   lease is still live, and the worker then stops. The outcome of that send
 --   is unknown; the ledger must say so, and the operator must be able to
 --   settle it.
 -- 2 negative control: the same, but the attempt was settled and the lease has
 --   expired. Ordinary expiry still expires it.
 -- 3 negative control: no attempt was ever started and the lease has expired.
 --   Ordinary expiry still expires it.
 -- 4 the same send as case 1 under maintenance: pause holds the requeued
 --   action, resume runs its own expiry; the send is still outstanding.
 FOR n IN 1..4 LOOP
  aid:=gen_random_uuid(); cid:=gen_random_uuid(); eid:=gen_random_uuid(); target:='live-lease-expiry-'||aid::text;
  SET ROLE zabalack_owner;
  UPDATE zabalack.installation SET external_writes_enabled=true,capture_disabled=false WHERE singleton;
  INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,host_ids,group_ids,enabled)
   VALUES('Live lease expiry synthetic',target,'9100','r1','1','["9200"]','["9300"]',true);
  INSERT INTO zabalack.instance_control(instance,paused) VALUES(target,false);
  INSERT INTO zabalack.captures(capture_id,alert_id,customer,snapshot,history_type,resulting_status,state)
   VALUES(cid,aid::text,'Live lease expiry synthetic','{}','ack','ack','verified');
  INSERT INTO zabalack.effects(effect_id,instance,event_id,generation,primary_action_id,latest_action_time)
   VALUES(eid,target,'9400',1,aid,clock_timestamp());
  INSERT INTO zabalack.actions(action_id,capture_id,capture_order,effect_id,alert_id,customer,instance,event_id,trigger_id,
   actor_ref,cohort_revision,policy_version,issued_at,execute_before)
   VALUES(aid,cid,1,eid,aid::text,'Live lease expiry synthetic',target,'9400','9100','synthetic','r1','1',clock_timestamp(),clock_timestamp()+interval '4 hours');

  SET ROLE zabalack_worker;
  claim:=zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','live-worker','action_id',aid));
  IF claim IS NULL THEN RAISE EXCEPTION 'T43 setup could not claim delivery (case %)',n; END IF;
  data:=jsonb_build_object('worker_id','live-worker','action_id',aid,'lease_generation',claim->'lease_generation');

  -- The documented retry requeues the claimed action; its lease stays live.
  SET ROLE zabalack_operator;
  PERFORM zabalack.operator_call('retry',jsonb_build_object('action_id',aid,'actor_ref','synthetic-reviewer','change_ref','LAB-LIVE-LEASE-RETRY'));
  SET ROLE zabalack_worker;
  IF n<>3 THEN
   attempt:=zabalack.worker_call('start_attempt',data);
   IF attempt->>'attempt_id' IS NULL THEN RAISE EXCEPTION 'T43 setup could not record the attempt (case %): %',n,attempt; END IF;
   data:=data||jsonb_build_object('attempt_id',attempt->'attempt_id');
  END IF;
  IF n=2 THEN
   PERFORM zabalack.worker_call('complete',data||'{"state":"pending","reason":"source_busy","uncertain":false}');
  END IF;

  SET ROLE zabalack_owner;
  -- Test-only clock advancement, never a sleep. The lease is left live in
  -- cases 1 and 4; that is the whole case.
  UPDATE zabalack.actions SET execute_before=clock_timestamp()-interval '1 second',next_due=clock_timestamp()-interval '1 second' WHERE action_id=aid;
  IF n IN (2,3) THEN
   UPDATE zabalack.delivery_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=aid;
   UPDATE zabalack.delivery_slots SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=aid;
  END IF;

  IF n=4 THEN
   SET ROLE zabalack_operator;
   PERFORM zabalack.operator_call('pause',jsonb_build_object('instance',target,'reason','synthetic_hold','actor_ref','synthetic-reviewer','change_ref','LAB-LIVE-LEASE-PAUSE'));
   SET ROLE zabalack_owner;
   IF (SELECT state FROM zabalack.actions WHERE action_id=aid)<>'held' THEN
    RAISE EXCEPTION 'T43 case 4 setup: expected a held action, found %',(SELECT state FROM zabalack.actions WHERE action_id=aid);
   END IF;
   SET ROLE zabalack_operator;
   PERFORM zabalack.operator_call('resume',jsonb_build_object('instance',target,'reason','synthetic_release','actor_ref','synthetic-reviewer','change_ref','LAB-LIVE-LEASE-RESUME'));
   SET ROLE zabalack_owner;
   -- resume runs its own expiry over held rows; it must read the same facts.
   IF (SELECT state FROM zabalack.actions WHERE action_id=aid)='expired' THEN
    RAISE EXCEPTION 'T43 case 4: resume recorded a send in flight under a live lease as terminally expired';
   END IF;
  END IF;

  SET ROLE zabalack_worker;
  PERFORM zabalack.worker_call('reconcile_due','{}');
  SET ROLE zabalack_owner;
  SELECT jsonb_build_object('state',state,'uncertain',uncertain,'reason',reason) INTO observed FROM zabalack.actions WHERE action_id=aid;

  IF n IN (1,4) THEN
   -- The send is outstanding under a live lease: not expired, and not yet
   -- uncertain either, because nobody has abandoned it.
   IF observed->>'state'='expired' THEN
    RAISE EXCEPTION 'T43 case %: a send in flight under a live lease was recorded terminally expired: %',n,observed;
   END IF;
   IF (observed->>'uncertain')::boolean IS NOT FALSE THEN
    RAISE EXCEPTION 'T43 case %: a live send was fenced before anything abandoned it: %',n,observed;
   END IF;
   -- The worker stops. Now the send is abandoned and the ledger must say so.
   SET ROLE zabalack_worker;
   PERFORM zabalack.worker_call('worker_stopped',jsonb_build_object('worker_id','live-worker'));
   PERFORM zabalack.worker_call('reconcile_due','{}');
   SET ROLE zabalack_owner;
   SELECT jsonb_build_object('state',state,'uncertain',uncertain,'reason',reason) INTO observed FROM zabalack.actions WHERE action_id=aid;
   IF observed->>'state'<>'unknown' OR (observed->>'uncertain')::boolean IS NOT TRUE THEN
    RAISE EXCEPTION 'T43 case %: the abandoned send was left as %',n,observed;
   END IF;
   -- What the uncertainty is for: the operator can settle it under the
   -- fenced-and-settled evidence rule.
   SET ROLE zabalack_operator;
   PERFORM zabalack.operator_call('settle_unknown',jsonb_build_object('action_id',aid,'disposition','no-effect-confirmed',
    'confirmation','REMOTE_ATTEMPT_FENCED_AND_SETTLED','evidence_ref','synthetic-fenced-no-effect','actor_ref','synthetic-reviewer','change_ref','LAB-LIVE-LEASE-SETTLE'));
   SET ROLE zabalack_owner;
   SELECT jsonb_build_object('state',state,'uncertain',uncertain,'reason',reason) INTO observed FROM zabalack.actions WHERE action_id=aid;
   IF (observed->>'uncertain')::boolean IS NOT FALSE OR observed->>'state' NOT IN ('failed','expired') THEN
    RAISE EXCEPTION 'T43 case %: settlement left %',n,observed;
   END IF;
  ELSE
   IF observed->>'state'<>'expired' OR (observed->>'uncertain')::boolean IS NOT FALSE OR observed->>'reason'<>'execution_lifetime_expired' THEN
    RAISE EXCEPTION 'T43 case %: ordinary expiry no longer expires: %',n,observed;
   END IF;
  END IF;
 END LOOP;
END $$;
ROLLBACK;
