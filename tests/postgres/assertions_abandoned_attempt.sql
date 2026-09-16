-- T42: the reconciliation sweep reads the delivery facts, not the state the
-- action was left in. Synthetic ledger rows establish internal coordination
-- cases only; no HMAC, real-source, concurrency or HA evidence follows from
-- them. Every runtime function is called as zabalack_worker/zabalack_operator.
BEGIN;
DO $$
DECLARE
 n integer; aid uuid; cid uuid; eid uuid; target text; claim jsonb; data jsonb; attempt jsonb;
 observed jsonb; touched timestamptz; fenced timestamptz;
BEGIN
 -- 1 the minimized reproduction: retry requeues a claimed action while its
 --   lease is still live, the replacement send is recorded against that lease,
 --   and the lease then expires with nobody holding the outcome.
 -- 2 the same, with the execution deadline already past: the expiry sweep in
 --   the statement below this one takes ('authorised','pending','held') AND NOT
 --   uncertain, so an unfenced action is recorded terminally expired -- an
 --   outcome nobody observed, written as though someone had.
 -- 3 pause converts the requeued state to held; still abandoned, still fenced.
 -- 4 negative control: the attempt on the expired lease was settled, so this
 --   action must be left exactly where it is. The sweep is widened, not blind.
 -- 5 an action fenced on an earlier pass is not rewritten by a later one.
 FOR n IN 1..5 LOOP
  aid:=gen_random_uuid(); cid:=gen_random_uuid(); eid:=gen_random_uuid(); target:='abandoned-attempt-'||aid::text;
  SET ROLE zabalack_owner;
  UPDATE zabalack.installation SET external_writes_enabled=true,capture_disabled=false WHERE singleton;
  INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,host_ids,group_ids,enabled)
   VALUES('Abandoned attempt synthetic',target,'9100','r1','1','["9200"]','["9300"]',true);
  INSERT INTO zabalack.instance_control(instance,paused) VALUES(target,false);
  INSERT INTO zabalack.captures(capture_id,alert_id,customer,snapshot,history_type,resulting_status,state)
   VALUES(cid,aid::text,'Abandoned attempt synthetic','{}','ack','ack','verified');
  INSERT INTO zabalack.effects(effect_id,instance,event_id,generation,primary_action_id,latest_action_time)
   VALUES(eid,target,'9400',1,aid,clock_timestamp());
  INSERT INTO zabalack.actions(action_id,capture_id,capture_order,effect_id,alert_id,customer,instance,event_id,trigger_id,
   actor_ref,cohort_revision,policy_version,issued_at,execute_before)
   VALUES(aid,cid,1,eid,aid::text,'Abandoned attempt synthetic',target,'9400','9100','synthetic','r1','1',clock_timestamp(),clock_timestamp()+interval '4 hours');

  SET ROLE zabalack_worker;
  claim:=zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','abandoning-worker','action_id',aid));
  IF claim IS NULL THEN RAISE EXCEPTION 'T42 setup could not claim delivery (case %)',n; END IF;
  data:=jsonb_build_object('worker_id','abandoning-worker','action_id',aid,'lease_generation',claim->'lease_generation');

  -- The operator requeues while the delivery lease is still live. This is the
  -- documented retry, and it is what leaves the action outside the state the
  -- unrepaired sweep looked for.
  SET ROLE zabalack_operator;
  PERFORM zabalack.operator_call('retry',jsonb_build_object('action_id',aid,'actor_ref','synthetic-reviewer','change_ref','LAB-ABANDONED-RETRY'));
  SET ROLE zabalack_worker;
  attempt:=zabalack.worker_call('start_attempt',data);
  IF attempt->>'attempt_id' IS NULL THEN RAISE EXCEPTION 'T42 setup could not record the replacement attempt (case %): %',n,attempt; END IF;
  data:=data||jsonb_build_object('attempt_id',attempt->'attempt_id');
  IF n=4 THEN
   -- The control the widened predicate has to survive: this action is left in
   -- 'pending' too, on an expired lease, with an attempt against that same
   -- lease generation -- and its outcome is known. Only the attempt tells the
   -- two apart, which is the whole point of reading the facts.
   PERFORM zabalack.worker_call('complete',data||'{"state":"pending","reason":"source_busy","uncertain":false}');
  END IF;

  SET ROLE zabalack_owner;
  -- Test-only clock advancement, never a sleep: native elapsed-time and
  -- failover gates stay open.
  UPDATE zabalack.delivery_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=aid;
  UPDATE zabalack.delivery_slots SET expires_at=clock_timestamp()-interval '1 second' WHERE action_id=aid;
  IF n=2 THEN
   UPDATE zabalack.actions SET execute_before=clock_timestamp()-interval '1 second',next_due=clock_timestamp()-interval '1 second' WHERE action_id=aid;
  END IF;
  IF n=3 THEN
   -- Maintenance arrives while the send is outstanding. The next claim records
   -- 'held' before it ever reaches the abandoned-attempt guard, so the action
   -- sits in a third state with the same unresolved send underneath it.
   SET ROLE zabalack_operator;
   PERFORM zabalack.operator_call('pause',jsonb_build_object('instance',target,'reason','synthetic_hold','actor_ref','synthetic-reviewer','change_ref','LAB-ABANDONED-PAUSE'));
   SET ROLE zabalack_worker;
   PERFORM zabalack.worker_call('claim_delivery',jsonb_build_object('worker_id','held-worker','action_id',aid));
   SET ROLE zabalack_owner;
   IF (SELECT state FROM zabalack.actions WHERE action_id=aid)<>'held' THEN
    RAISE EXCEPTION 'T42 case 3 setup: expected a held action, found %',(SELECT state FROM zabalack.actions WHERE action_id=aid);
   END IF;
  END IF;
  SET ROLE zabalack_worker;
  PERFORM zabalack.worker_call('reconcile_due','{}');
  SET ROLE zabalack_owner;
  SELECT jsonb_build_object('state',state,'uncertain',uncertain,'reason',reason) INTO observed FROM zabalack.actions WHERE action_id=aid;
  SELECT updated_at INTO fenced FROM zabalack.actions WHERE action_id=aid;

  IF n IN (1,2,3) THEN
   IF observed->>'state'<>'unknown' OR (observed->>'uncertain')::boolean IS NOT TRUE THEN
    RAISE EXCEPTION 'T42 case %: an abandoned send was left as %',n,observed;
   END IF;
   IF observed->>'reason'<>'abandoned_attempt' THEN RAISE EXCEPTION 'T42 case %: fenced for the wrong reason: %',n,observed; END IF;
  ELSIF n=4 THEN
   IF (observed->>'uncertain')::boolean IS NOT FALSE OR observed->>'state'<>'pending' THEN
    RAISE EXCEPTION 'T42 case 4: a settled attempt was swept as abandoned: %',observed;
   END IF;
  ELSE
   -- Fenced once by the pass above; a second pass must leave the row alone
   -- rather than rewrite next_due and updated_at on every reconciliation.
   IF observed->>'state'<>'unknown' OR (observed->>'uncertain')::boolean IS NOT TRUE THEN
    RAISE EXCEPTION 'T42 case 5: the first pass did not fence: %',observed;
   END IF;
   touched:=fenced;
   SET ROLE zabalack_worker;
   PERFORM zabalack.worker_call('reconcile_due','{}');
   SET ROLE zabalack_owner;
   SELECT updated_at INTO fenced FROM zabalack.actions WHERE action_id=aid;
   IF fenced<>touched THEN RAISE EXCEPTION 'T42 case 5: a fenced action was rewritten by the next pass'; END IF;
  END IF;

  IF n=1 THEN
   -- What the fence is for: the operator can now settle the unknown outcome
   -- under the fenced-and-settled evidence rule, and could not before.
   SET ROLE zabalack_operator;
   PERFORM zabalack.operator_call('settle_unknown',jsonb_build_object('action_id',aid,'disposition','no-effect-confirmed',
    'confirmation','REMOTE_ATTEMPT_FENCED_AND_SETTLED','evidence_ref','synthetic-fenced-no-effect','actor_ref','synthetic-reviewer','change_ref','LAB-ABANDONED-SETTLE'));
   SET ROLE zabalack_owner;
   SELECT jsonb_build_object('state',state,'uncertain',uncertain) INTO observed FROM zabalack.actions WHERE action_id=aid;
   IF (observed->>'uncertain')::boolean IS NOT FALSE OR observed->>'state'<>'failed' THEN
    RAISE EXCEPTION 'T42 case 1: settlement after fencing left %',observed;
   END IF;
  END IF;
 END LOOP;
END $$;
ROLLBACK;
