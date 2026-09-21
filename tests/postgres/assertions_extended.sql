-- Supplemental and native-engine SQL state-machine regressions. No remote calls.
SET ROLE zabalack_operator;
SELECT zabalack.operator_call('capture_bypass','{"enabled":false,"config_revision":"lab-v1","actor_ref":"lab-engineer","change_ref":"LAB-extended"}');
SELECT zabalack.operator_call('resume','{"instance":"lab-zabbix","actor_ref":"lab-engineer","change_ref":"LAB-extended"}');
SET ROLE alerta_app;
DO $$ DECLARE n integer; aid uuid; alertid uuid; i jsonb; ts timestamptz; mark text;
BEGIN
 FOR n IN 2..4 LOOP
  ts:=clock_timestamp(); aid:=gen_random_uuid(); alertid:=gen_random_uuid();
  i:=jsonb_build_object('version',1,'action_id',aid,'alert_id',alertid,'customer','Synthetic A',
   'source',jsonb_build_object('zabbix_instance','lab-zabbix','zabbix_event_id',(1000+n)::text,'zabbix_trigger_id','100'),
   'actor',jsonb_build_object('login','synthetic.operator','customers',jsonb_build_array('Synthetic A'),'admin',false,'auth_type','user'),
   'route','/alert/'||alertid||'/action','action','ack','cohort_revision','lab-v1','policy_revision','lab-v1',
   'issued_at',ts,'capture_expires_at',ts+interval '60 seconds','execution_expires_at',ts+interval '4 hours');
  mark:='[zabalack:v1 action='||aid||' kid=lab mac='||repeat('0',64)||']';
  INSERT INTO public.alerts(id,resource,event,status,customer,attributes,history) VALUES(alertid,'synthetic:'||n,'stable-trigger','open','Synthetic A',
   jsonb_build_object('source_system','zabbix','zabbix_instance','lab-zabbix','zabbix_event_id',(1000+n)::text,'zabbix_trigger_id','100','zabbix_ack_sync_intent',i),ARRAY[]::public.history[]);
  UPDATE public.alerts SET status='ack',history=ARRAY[ROW('change','stable-trigger',NULL,'ack',NULL,mark,'ack',ts AT TIME ZONE 'UTC','synthetic.operator',NULL)::public.history] WHERE id=alertid::text;
 END LOOP;
END $$;
SET ROLE zabalack_worker;
DO $$ DECLARE c jsonb; a jsonb; d1 jsonb; d2 jsonb; d3 jsonb; at1 jsonb; n integer;
BEGIN
 FOR n IN 1..3 LOOP
  c:=zabalack.worker_call('claim_verification','{"worker_id":"verify-extended"}');
  a:=zabalack.worker_call('verify_complete',jsonb_build_object('worker_id','verify-extended','capture_id',c->>'capture_id','lease_generation',c->'lease_generation','intent',c->'intent','actor_ref','opaque-lab-actor'));
 END LOOP;
 d1:=zabalack.worker_call('claim_delivery','{"worker_id":"delivery-a"}');
 d2:=zabalack.worker_call('claim_delivery','{"worker_id":"delivery-b"}');
 d3:=zabalack.worker_call('claim_delivery','{"worker_id":"delivery-c"}');
 IF d1 IS NULL OR d2 IS NULL OR d1->>'event_id'=d2->>'event_id' OR d3 IS NOT NULL THEN RAISE EXCEPTION 'T31 two slots/active lease fairness'; END IF;
 at1:=zabalack.worker_call('start_attempt',d1||'{"worker_id":"delivery-a"}');
 PERFORM set_config('ack_test.delivery_a',d1::text,false);
 PERFORM set_config('ack_test.delivery_b',d2::text,false);
 PERFORM set_config('ack_test.attempt_a',at1::text,false);
END $$;
SET ROLE zabalack_operator;
SELECT zabalack.operator_call('pause','{"instance":"lab-zabbix","actor_ref":"lab-engineer","change_ref":"LAB-pause"}');
SET ROLE zabalack_worker;
DO $$ DECLARE d jsonb:=current_setting('ack_test.delivery_a')::jsonb; b jsonb:=current_setting('ack_test.delivery_b')::jsonb;
BEGIN
 IF (zabalack.worker_call('before_write',d||'{"worker_id":"delivery-a"}')->>'allowed')::boolean THEN RAISE EXCEPTION 'T32 paused write allowed'; END IF;
 PERFORM zabalack.worker_call('complete',b||'{"worker_id":"delivery-b","state":"pending","reason":"endpoint_unreachable","uncertain":false,"retry_delay":1}');
END $$;
SET ROLE zabalack_owner;
UPDATE zabalack.delivery_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE owner='delivery-a';
UPDATE zabalack.delivery_slots SET expires_at=clock_timestamp()-interval '1 second' WHERE owner='delivery-a';
SET ROLE zabalack_worker;
DO $$ DECLARE d jsonb:=current_setting('ack_test.delivery_a')::jsonb; result jsonb; bg jsonb;
BEGIN
 result:=zabalack.worker_call('reconcile_due','{"limit":200}');
 IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(result) x WHERE x->>'action_id'=d->>'action_id' AND (x->>'uncertain')::boolean) THEN RAISE EXCEPTION 'T31 abandoned attempt not uncertain'; END IF;
 IF (zabalack.worker_call('before_write',d||'{"worker_id":"delivery-a"}')->>'allowed')::boolean THEN RAISE EXCEPTION 'T31 expired old writer allowed'; END IF;
 result:=zabalack.worker_call('complete',d||jsonb_build_object('worker_id','delivery-a','attempt_id',current_setting('ack_test.attempt_a')::jsonb->>'attempt_id','state','ok','reason','late_write_response','completion_evidence','write_response'));
 IF result->>'state'<>'late_response' THEN RAISE EXCEPTION 'T31 late response overwrote active action'; END IF;
 bg:=zabalack.worker_call('claim_background','{"instance":"lab-zabbix","worker_id":"reconciler"}');
 IF NOT (bg->>'allowed')::boolean THEN RAISE EXCEPTION 'background claim denied'; END IF;
 IF (zabalack.worker_call('claim_background','{"instance":"lab-zabbix","worker_id":"another"}')->>'allowed')::boolean THEN RAISE EXCEPTION 'background concurrency not bounded'; END IF;
 -- 013: a reconciliation 'ok' with no completion_evidence key is refused like one with the wrong evidence.
 BEGIN
  PERFORM zabalack.worker_call('record_reconciliation',jsonb_build_object('action_id',d->>'action_id','instance','lab-zabbix','worker_id','reconciler','background_generation',bg->'lease_generation','state','ok','reason','no_evidence_supplied'));
  RAISE EXCEPTION 'T29 reconciliation ok accepted without completion_evidence';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM<>'invalid reconciliation evidence' THEN RAISE; END IF;
 END;
 result:=zabalack.worker_call('inspect',jsonb_build_object('action_id',d->>'action_id'));
 IF result->0->>'state'='ok' THEN RAISE EXCEPTION 'T29 evidence-free reconciliation became durable'; END IF;
 PERFORM zabalack.worker_call('record_reconciliation',jsonb_build_object('action_id',d->>'action_id','instance','lab-zabbix','worker_id','reconciler','background_generation',bg->'lease_generation','state','ok','reason','reference_observed','completion_evidence','reference_observed','uncertain',false));
 PERFORM zabalack.worker_call('record_reconciliation',jsonb_build_object('action_id',d->>'action_id','instance','lab-zabbix','worker_id','reconciler','background_generation',bg->'lease_generation','preserve_delivery',true,'check_failure','tls_failure'));
 result:=zabalack.worker_call('inspect',jsonb_build_object('action_id',d->>'action_id'));
 IF result->0->>'state'<>'ok' OR result->0->>'completion_evidence'<>'reference_observed' THEN RAISE EXCEPTION 'T29 read failure erased durable success'; END IF;
 -- Unknown host IDs flag drift. A later clean batch cannot silently approve them.
 PERFORM zabalack.worker_call('record_host_inventory',jsonb_build_object('instance','lab-zabbix','worker_id','reconciler','background_generation',bg->'lease_generation','customer','Synthetic A','trigger_id','100','cohort_revision','lab-v1','reason','cohort_host_missing_or_not_visible','missing_ids',jsonb_build_array('10'),'next_cursor',0));
 PERFORM zabalack.worker_call('record_host_inventory',jsonb_build_object('instance','lab-zabbix','worker_id','reconciler','background_generation',bg->'lease_generation','customer','Synthetic A','trigger_id','100','cohort_revision','lab-v1','reason','host_inventory_ok','missing_ids','[]'::jsonb,'next_cursor',0));
 PERFORM zabalack.worker_call('finish_background',jsonb_build_object('instance','lab-zabbix','worker_id','reconciler','lease_generation',bg->'lease_generation'));
 -- 013: with the lease released, a payload that names no worker cannot pass the
 -- background fence: the three leased operations require worker_id, and an
 -- absent lease is refused rather than compared NULL-for-NULL.
 BEGIN
  PERFORM zabalack.worker_call('record_reconciliation',jsonb_build_object('action_id',d->>'action_id','instance','lab-zabbix','background_generation',bg->'lease_generation','state','failed','reason','anonymous_caller'));
  RAISE EXCEPTION 'T29 anonymous reconciliation passed the background fence';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM<>'worker_id required' THEN RAISE; END IF;
 END;
 BEGIN
  PERFORM zabalack.worker_call('record_host_inventory',jsonb_build_object('instance','lab-zabbix','background_generation',bg->'lease_generation','customer','Synthetic A','trigger_id','100','cohort_revision','lab-v1','reason','host_inventory_ok','missing_ids','[]'::jsonb,'next_cursor',7));
  RAISE EXCEPTION 'T29 anonymous host inventory passed the background fence';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM<>'worker_id required' THEN RAISE; END IF;
 END;
 BEGIN
  PERFORM zabalack.worker_call('record_host_check',jsonb_build_object('instance','lab-zabbix','background_generation',bg->'lease_generation','customer','Synthetic A','trigger_id','100','cohort_revision','lab-v1','reason','host_check_ok','next_cursor',7));
  RAISE EXCEPTION 'T29 anonymous host check passed the background fence';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM<>'worker_id required' THEN RAISE; END IF;
 END;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(zabalack.worker_call('host_cohorts','{"instance":"lab-zabbix"}')) cohort WHERE cohort->>'customer'='Synthetic A' AND cohort->>'trigger_id'='100' AND (cohort->>'host_cursor')::int=7) THEN RAISE EXCEPTION 'T29 anonymous host inventory moved the cohort cursor'; END IF;
 -- 013: with the lease released, the fence refuses the former holder too; NULL is not a held lease.
 BEGIN
  PERFORM zabalack.worker_call('record_reconciliation',jsonb_build_object('action_id',d->>'action_id','instance','lab-zabbix','worker_id','reconciler','background_generation',bg->'lease_generation','state','failed','reason','after_release'));
  RAISE EXCEPTION 'T29 released lease still admitted its former holder';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM<>'background lease lost' THEN RAISE; END IF;
 END;
 PERFORM zabalack.worker_call('host_inventory_due','{}');
 PERFORM zabalack.worker_call('nightly_due','{"limit":200}');
 PERFORM zabalack.worker_call('worker_stopped','{"worker_id":"delivery-c"}');
END $$;
SET ROLE zabalack_owner;
DO $$ BEGIN IF NOT (SELECT host_drift FROM zabalack.cohorts WHERE customer='Synthetic A' AND trigger_id='100') THEN RAISE EXCEPTION 'T33 host drift auto-cleared'; END IF; END $$;
-- Explicit source-generation drift refuses projection, even though the old
-- source effect remains durably successful and is never sent a second time.
SET ROLE alerta_app;
UPDATE public.alerts SET attributes=attributes||'{"zabbix_event_id":"9999"}' WHERE id=(current_setting('ack_test.delivery_a')::jsonb)->>'alert_id';
SET ROLE zabalack_worker;
DO $$ DECLARE d jsonb:=current_setting('ack_test.delivery_a')::jsonb; result jsonb;
BEGIN
 result:=zabalack.project((d->>'action_id')::uuid);
 IF result->>'reason'<>'projection_source_generation_mismatch' THEN RAISE EXCEPTION 'T10 stale projection applied'; END IF;
END $$;
RESET ROLE;
