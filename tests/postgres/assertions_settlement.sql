-- Explicit operator uncertainty settlement: no automatic source effects.
SET ROLE zabalack_owner;
UPDATE zabalack.actions SET uncertain=true,state='unknown',execute_before=clock_timestamp()+interval '1 hour' WHERE action_id=(current_setting('ack_test.delivery_b')::jsonb->>'action_id')::uuid;
SET ROLE zabalack_operator;
DO $$ DECLARE aid text:=current_setting('ack_test.delivery_b')::jsonb->>'action_id'; denied boolean:=false; p jsonb;
BEGIN
 p:=jsonb_build_object('action_id',aid,'actor_ref','lab-engineer','change_ref','LAB-settle','disposition','no-effect-confirmed');
 BEGIN PERFORM zabalack.operator_call('settle_unknown',p); EXCEPTION WHEN raise_exception THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'settlement accepted without confirmation/evidence'; END IF;
 PERFORM zabalack.operator_call('settle_unknown',p||'{"confirmation":"REMOTE_ATTEMPT_FENCED_AND_SETTLED","evidence_ref":"synthetic-evidence-no-effect"}');
END $$;
SET ROLE zabalack_worker;
DO $$ DECLARE result jsonb; denied boolean:=false;
BEGIN
 result:=zabalack.worker_call('inspect',jsonb_build_object('action_id',current_setting('ack_test.delivery_b')::jsonb->>'action_id'));
 IF result->0->>'state'<>'failed' OR (result->0->>'uncertain')::boolean THEN RAISE EXCEPTION 'settlement did not hold without auto-retry'; END IF;
 BEGIN PERFORM zabalack.operator_call('settle_unknown','{}'); EXCEPTION WHEN insufficient_privilege THEN denied:=true; END;
 IF NOT denied THEN RAISE EXCEPTION 'worker obtained uncertainty settlement authority'; END IF;
END $$;
SET ROLE zabalack_operator;
SELECT zabalack.operator_call('retry',jsonb_build_object('action_id',current_setting('ack_test.delivery_b')::jsonb->>'action_id','actor_ref','lab-engineer','change_ref','LAB-retry'));
SET ROLE zabalack_owner;
UPDATE zabalack.actions SET uncertain=true,state='unknown',execute_before=clock_timestamp()-interval '1 second' WHERE action_id=(current_setting('ack_test.delivery_b')::jsonb->>'action_id')::uuid;
SET ROLE zabalack_operator;
SELECT zabalack.operator_call('settle_unknown',jsonb_build_object('action_id',current_setting('ack_test.delivery_b')::jsonb->>'action_id','actor_ref','lab-engineer','change_ref','LAB-expired-settle','disposition','no-effect-confirmed','confirmation','REMOTE_ATTEMPT_FENCED_AND_SETTLED','evidence_ref','synthetic-expired-evidence'));
SET ROLE zabalack_worker;
DO $$ DECLARE result jsonb;
BEGIN
 result:=zabalack.worker_call('inspect',jsonb_build_object('action_id',current_setting('ack_test.delivery_b')::jsonb->>'action_id'));
 IF result->0->>'state'<>'expired' OR (result->0->>'uncertain')::boolean THEN RAISE EXCEPTION 'expired settlement can write'; END IF;
END $$;
SET ROLE zabalack_owner;
DO $$ BEGIN
 IF (SELECT count(*) FROM zabalack.audit WHERE operation='settle_unknown_evidence')<>2 THEN RAISE EXCEPTION 'settlement audit evidence missing'; END IF;
END $$;
RESET ROLE;
