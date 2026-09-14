CREATE FUNCTION zabalack.operator_call(op text,p jsonb DEFAULT '{}'::jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
DECLARE target text:=p->>'instance'; actor text:=p->>'actor_ref'; ref text:=p->>'change_ref'; c jsonb; a zabalack.actions;
BEGIN
 IF jsonb_typeof(p)<>'object' OR octet_length(p::text)>65536 OR length(coalesce(actor,'')) NOT BETWEEN 1 AND 128
  OR length(coalesce(ref,'')) NOT BETWEEN 1 AND 256 THEN RAISE EXCEPTION 'operator actor_ref and change_ref required'; END IF;
 IF op IN ('pause','resume') THEN
  IF target IS NULL OR length(target)>128 THEN RAISE EXCEPTION 'explicit instance required'; END IF;
  INSERT INTO zabalack.instance_control(instance,paused,owner_ref,change_ref,reason,expected_end)
   VALUES(target,op='pause',actor,ref,left(p->>'reason',128),(p->>'expected_end')::timestamptz)
   ON CONFLICT(instance) DO UPDATE SET paused=excluded.paused,owner_ref=excluded.owner_ref,change_ref=excluded.change_ref,
    reason=excluded.reason,expected_end=excluded.expected_end,changed_at=clock_timestamp();
  IF op='pause' THEN
   UPDATE zabalack.actions SET state='held',reason='maintenance_hold',projection_due_at=clock_timestamp()
    WHERE instance=target AND state IN ('authorised','pending','held');
  ELSE
   UPDATE zabalack.actions SET state=CASE WHEN execute_before<=clock_timestamp() AND NOT uncertain THEN 'expired' ELSE CASE WHEN uncertain THEN 'unknown' ELSE 'pending' END END,
    reason=CASE WHEN execute_before<=clock_timestamp() AND NOT uncertain THEN 'execution_lifetime_expired' ELSE 'resume_validation_required' END,
    next_due=clock_timestamp(),projection_due_at=clock_timestamp() WHERE instance=target AND state='held';
  END IF;
 ELSIF op='capture_bypass' THEN
  IF (p->>'enabled')::boolean AND EXISTS(SELECT 1 FROM zabalack.instance_control WHERE NOT paused) THEN RAISE EXCEPTION 'pause every instance before capture bypass'; END IF;
  UPDATE zabalack.installation SET capture_disabled=(p->>'enabled')::boolean,config_revision=p->>'config_revision' WHERE singleton;
 ELSIF op='configure_installation' THEN
  IF coalesce(p->>'deployment_id','') IN ('','REQUIRED_FOR_DEPLOYMENT') OR coalesce(p->>'config_revision','')='' THEN RAISE EXCEPTION 'explicit installation identity/revision required'; END IF;
  UPDATE zabalack.installation SET deployment_id=p->>'deployment_id',config_revision=p->>'config_revision' WHERE singleton;
 ELSIF op='set_external_writes' THEN
  IF (p->>'enabled')::boolean AND (p->>'confirmation' IS DISTINCT FROM 'ENABLE_EXTERNAL_WRITES' OR NOT coalesce((p->>'gates_reviewed')::boolean,false)) THEN RAISE EXCEPTION 'explicit external-write confirmation and approved gate evidence required'; END IF;
  IF (p->>'enabled')::boolean AND (SELECT capture_disabled OR deployment_id='REQUIRED_FOR_DEPLOYMENT' FROM zabalack.installation WHERE singleton) THEN RAISE EXCEPTION 'installation fenced'; END IF;
  IF (p->>'enabled')::boolean AND NOT (SELECT external_writes_enabled FROM zabalack.installation WHERE singleton) AND EXISTS(SELECT 1 FROM zabalack.instance_control WHERE NOT paused) THEN RAISE EXCEPTION 'pause all instances before global write enable'; END IF;
  UPDATE zabalack.installation SET external_writes_enabled=(p->>'enabled')::boolean WHERE singleton;
 ELSIF op='configure_cohort' THEN
  c:=p->'cohort';
  IF EXISTS(SELECT 1 FROM zabalack.cohorts old WHERE old.customer=c->>'customer' AND old.instance=c->>'zabbix_instance' AND old.trigger_id=c->>'zabbix_trigger_id' AND old.revision=c->>'revision' AND (zabalack.cohort_json(old) IS DISTINCT FROM c OR old.host_drift)) THEN RAISE EXCEPTION 'changed cohort/drift approval requires new revision'; END IF;
  IF jsonb_typeof(c)<>'object' OR coalesce((c->>'one_to_one')::boolean,false)=false
   OR jsonb_typeof(c->'host_ids')<>'array' OR jsonb_array_length(c->'host_ids')=0
   OR jsonb_typeof(c->'group_ids')<>'array' OR jsonb_array_length(c->'group_ids')=0
   OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(c->'host_ids') h WHERE h !~ '^[1-9][0-9]{0,19}$')
   OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(c->'group_ids') h WHERE h !~ '^[1-9][0-9]{0,19}$') THEN RAISE EXCEPTION 'invalid one-to-one cohort'; END IF;
  INSERT INTO zabalack.cohorts(customer,instance,trigger_id,revision,policy_version,host_ids,group_ids,enabled,allow_comment,admin_exception)
   VALUES(c->>'customer',c->>'zabbix_instance',c->>'zabbix_trigger_id',c->>'revision',c->>'policy_revision',c->'host_ids',c->'group_ids',
    coalesce((c->>'enabled')::boolean,false),coalesce((c->>'comment_enabled')::boolean,true),coalesce((c->>'allow_admin')::boolean,false))
   ON CONFLICT(customer,instance,trigger_id) DO UPDATE SET revision=excluded.revision,policy_version=excluded.policy_version,
    host_ids=excluded.host_ids,group_ids=excluded.group_ids,enabled=excluded.enabled,allow_comment=excluded.allow_comment,
    admin_exception=excluded.admin_exception,host_drift=false,host_checked_at=NULL;
  INSERT INTO zabalack.instance_control(instance) VALUES(c->>'zabbix_instance') ON CONFLICT DO NOTHING;
 ELSIF op='reset_circuit' THEN
  IF target IS NULL THEN RAISE EXCEPTION 'explicit instance required'; END IF;
  UPDATE zabalack.instance_control SET circuit_open=false,circuit_reason=NULL WHERE instance=target;
  UPDATE zabalack.incidents SET active=false WHERE instance=target;
 ELSIF op='settle_unknown' THEN
  IF p->>'disposition' IS DISTINCT FROM 'no-effect-confirmed'
    OR p->>'confirmation' IS DISTINCT FROM 'REMOTE_ATTEMPT_FENCED_AND_SETTLED'
    OR length(coalesce(p->>'evidence_ref','')) NOT BETWEEN 1 AND 512 THEN RAISE EXCEPTION 'explicit fenced-and-settled no-effect evidence required'; END IF;
  SELECT * INTO STRICT a FROM zabalack.actions WHERE action_id=(p->>'action_id')::uuid FOR UPDATE;
  IF NOT a.uncertain THEN RAISE EXCEPTION 'action has no unknown outcome'; END IF;
  IF EXISTS(SELECT 1 FROM zabalack.delivery_leases l WHERE l.action_id=a.action_id AND l.expires_at>clock_timestamp()) THEN RAISE EXCEPTION 'delivery lease still active'; END IF;
  UPDATE zabalack.actions SET state=CASE WHEN execute_before<=clock_timestamp() THEN 'expired' ELSE 'failed' END,
   reason='operator_no_effect_confirmed',uncertain=false,updated_at=clock_timestamp(),projection_due_at=clock_timestamp() WHERE action_id=a.action_id;
  INSERT INTO zabalack.outcomes(action_id,state,reason,completion_evidence,uncertain) VALUES(a.action_id,
   CASE WHEN a.execute_before<=clock_timestamp() THEN 'expired' ELSE 'failed' END,'operator_no_effect_confirmed','operator_review',false);
  IF a.execute_before<=clock_timestamp() THEN
   UPDATE zabalack.effects SET state='expired',reason='operator_no_effect_confirmed',completed_at=clock_timestamp() WHERE effect_id=a.effect_id;
   UPDATE zabalack.actions SET state='expired',reason='operator_no_effect_confirmed',uncertain=false,projection_due_at=clock_timestamp() WHERE effect_id=a.effect_id;
  END IF;
  INSERT INTO zabalack.audit(operation,actor_ref,change_ref,object_ref,details) VALUES('settle_unknown_evidence',actor,ref,a.action_id::text,
    jsonb_build_object('evidence_ref',p->>'evidence_ref','disposition','no-effect-confirmed','remote_settlement_asserted_by_operator',true));
 ELSIF op='retry' THEN
  SELECT * INTO STRICT a FROM zabalack.actions WHERE action_id=(p->>'action_id')::uuid FOR UPDATE;
  IF a.state IN ('ok','skipped','expired') OR a.execute_before<=clock_timestamp() THEN RAISE EXCEPTION 'terminal/expired action cannot be replayed'; END IF;
  UPDATE zabalack.actions SET state=CASE WHEN uncertain THEN 'unknown' ELSE 'pending' END,reason='operator_retry',next_due=clock_timestamp(),projection_due_at=clock_timestamp() WHERE action_id=a.action_id;
 ELSIF op='repair_projection' THEN
  INSERT INTO zabalack.audit(operation,actor_ref,change_ref,object_ref) VALUES(op,actor,ref,p->>'action_id');
  RETURN zabalack.project((p->>'action_id')::uuid);
 ELSE RAISE EXCEPTION 'unsupported operator operation: %',op;
 END IF;
 INSERT INTO zabalack.audit(operation,actor_ref,change_ref,object_ref,details)
  VALUES(op,actor,ref,coalesce(target,p->>'action_id','installation'),jsonb_build_object('session_principal',session_user));
 RETURN jsonb_build_object('operation',op,'recorded',true);
END $$;
REVOKE ALL ON FUNCTION zabalack.operator_call(text,jsonb) FROM PUBLIC;
-- Worker: claim/completion (worker_call) and projection only. Health is read
-- through worker_call('doctor') inside the SECURITY DEFINER boundary, so no
-- direct capture_health() grant is needed (design baseline section 12).
GRANT EXECUTE ON FUNCTION zabalack.worker_call(text,jsonb),zabalack.project(uuid) TO zabalack_worker;
GRANT EXECUTE ON FUNCTION zabalack.capture_health() TO zabalack_probe;
GRANT EXECUTE ON FUNCTION zabalack.operator_call(text,jsonb),zabalack.capture_health() TO zabalack_operator;

