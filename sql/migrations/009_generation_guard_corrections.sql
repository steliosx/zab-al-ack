-- Forward corrections to 008. Preserve 001-008.
--
-- 008 is published and its digest is registered, so it is immutable:
-- `sql/migrate.py` refuses to proceed when a recorded migration's bytes change,
-- and a database that already applied 008 would be stranded on the superseded
-- definitions. Every correction therefore lands here instead.
--
-- Three corrections, each with its own regression:
--
-- 1. `note_staging` and the sweep accepted whatever `capture_expires_at` the
--    staged attribute claimed. That attribute is API-writable — PUT
--    /alert/<id>/attributes runs no plugin hook — so `"infinity"` pinned the
--    ledger permanently: advances are monotonic, so no legitimate finite
--    generation could replace it, every later projection was refused as a
--    generation mismatch, and the forged intent exempted itself from retirement
--    because its window never expired.
-- 2. `project()` consulted the alert row's marker only when the ledger entry was
--    absent. Once a capture had seeded the ledger with an action, a newer
--    staging that had not yet been captured or swept existed only as that
--    marker, so the older projection was permitted, overwrote the newer
--    acknowledgement's state and deleted its intent.
-- 3. The sweep's cadence was process-local. The advisory lock excludes
--    simultaneous sweeps but is released with each `worker_call` transaction, so
--    every worker still ran its own unindexed scan once per its own interval.
--    Cadence is now deployment state, claimed atomically.
--
-- No grant change and no trigger change: `trigger_md5` is unaffected,
-- `functions_md5` and `structures_md5` move, and the installation must be
-- re-sealed with `sql/install/12_reseal.sql`.

-- Retirement cadence is deployment-wide state rather than each worker's own
-- clock. The claim below is a single atomic UPDATE, so exactly one worker per
-- interval does the scan and the rest return immediately.
ALTER TABLE zabalack.installation
 ADD COLUMN IF NOT EXISTS retirement_due_at timestamptz,
 ADD COLUMN IF NOT EXISTS retirement_interval_seconds integer NOT NULL DEFAULT 900;

CREATE OR REPLACE FUNCTION zabalack.note_staging(p_alert text, p_action uuid, p_until timestamptz, p_by text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
DECLARE advanced boolean;
BEGIN
 -- A staged window is bounded by construction: build_intent sets
 -- capture_expires_at to issued_at + 60 seconds, and verify_complete refuses any
 -- capture whose intent claims more. Anything outside that bound did not come
 -- from the signer, so it is refused here rather than allowed to become the
 -- authority. The bound holds at the ledger, not only at its callers.
 IF p_until IS NULL OR isfinite(p_until) IS NOT TRUE
    OR p_until>clock_timestamp()+interval '60 seconds' THEN
  RETURN false;
 END IF;
 INSERT INTO zabalack.alert_staging(alert_id,staged_action_id,staged_until,observed_by)
  VALUES(p_alert,p_action,p_until,p_by)
 ON CONFLICT(alert_id) DO UPDATE SET staged_action_id=excluded.staged_action_id,
  staged_until=excluded.staged_until,observed_at=clock_timestamp(),observed_by=excluded.observed_by
  WHERE zabalack.alert_staging.staged_until<excluded.staged_until
 RETURNING true INTO advanced;
 RETURN coalesce(advanced,false);
END $$;
REVOKE ALL ON FUNCTION zabalack.note_staging(text,uuid,timestamptz,text) FROM PUBLIC;

-- Claim the deployment-wide retirement slot. Returns true to exactly one caller
-- per configured interval; the UPDATE is atomic, so concurrent workers serialise
-- on the row and only the winner proceeds to the scan.
CREATE FUNCTION zabalack.claim_retirement_slot() RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
DECLARE claimed boolean;
BEGIN
 UPDATE zabalack.installation SET retirement_due_at=clock_timestamp()
  +make_interval(secs=>least(86400,greatest(1,retirement_interval_seconds)))
  WHERE singleton AND (retirement_due_at IS NULL OR retirement_due_at<=clock_timestamp())
  RETURNING true INTO claimed;
 RETURN coalesce(claimed,false);
END $$;
REVOKE ALL ON FUNCTION zabalack.claim_retirement_slot() FROM PUBLIC;

-- The sweep itself, with the staged attribute validated before anything from
-- it reaches the ledger. The advisory lock stays: it prevents an overlapping
-- sweep, which the cadence claim alone would not.
CREATE OR REPLACE FUNCTION zabalack.retire_intents(p_limit integer DEFAULT 200, p_grace_seconds integer DEFAULT 300)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE
 candidate record; intent jsonb; action uuid; until_at timestamptz; issued_at timestamptz; grace interval;
 now_at timestamptz:=clock_timestamp(); examined integer:=0; noted integer:=0; retired integer:=0;
BEGIN
 -- public.alerts carries no index for this predicate — zabalack_owner does not
 -- own the table and cannot create one — so LIMIT bounds matching rows, not rows
 -- scanned. One uncoordinated sweep per worker per cycle would mean every worker
 -- scanning the whole table even with nothing to retire. Exactly one sweep runs
 -- at a time across the deployment; the rest return immediately. See
 -- docs/deployment_acceptance.md for the optional partial index the alerts owner
 -- can add to make the scan itself cheap.
 IF NOT pg_try_advisory_xact_lock(hashtextextended('zabalack.retire_intents',0)) THEN
  RETURN jsonb_build_object('examined',0,'noted',0,'retired',0,'skipped','sweep_in_progress');
 END IF;
 grace:=make_interval(secs=>least(86400,greatest(0,coalesce(p_grace_seconds,300))));
 -- No ORDER BY: zabalack_owner cannot create an index on public.alerts, so an
 -- ordered scan would sort the whole table for no benefit. Each pass removes
 -- the intents it processes, so the candidate set shrinks and repeated passes
 -- converge. SKIP LOCKED leaves rows another session holds to the next pass.
 FOR candidate IN SELECT id,attributes FROM public.alerts WHERE attributes ? 'zabbix_ack_sync_intent'
   LIMIT least(1000,greatest(1,coalesce(p_limit,200))) FOR UPDATE SKIP LOCKED
 LOOP
  examined:=examined+1;
  intent:=candidate.attributes->'zabbix_ack_sync_intent';
  action:=NULL; until_at:=NULL; issued_at:=NULL;
  -- This attribute is API-writable: PUT /alert/<id>/attributes runs no plugin
  -- hook, so a write:alerts client can put any JSON here. Nothing from it
  -- reaches the ledger without passing the signer's own bounds, and a value
  -- that fails them is treated as unvalidatable plaintext: retired, never
  -- promoted. Promoting it unchecked would let a forged far-future window both
  -- pin the ledger and exempt itself from retirement.
  IF jsonb_typeof(intent)='object' THEN
   BEGIN
    action:=(intent->>'action_id')::uuid;
    issued_at:=(intent->>'issued_at')::timestamptz;
    until_at:=(intent->>'capture_expires_at')::timestamptz;
    IF issued_at IS NULL OR until_at IS NULL
       OR isfinite(issued_at) IS NOT TRUE OR isfinite(until_at) IS NOT TRUE
       -- The same lifetime rule verify_complete enforces on a capture.
       OR until_at>issued_at+interval '60 seconds' OR until_at<=issued_at
       OR issued_at>now_at THEN
     action:=NULL; until_at:=NULL;
    END IF;
   EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format OR datetime_field_overflow THEN
    action:=NULL; until_at:=NULL;
   END;
  END IF;
  IF action IS NOT NULL AND zabalack.note_staging(candidate.id,action,until_at,'sweep') THEN
   noted:=noted+1;
  END IF;
  -- Only a generation that passed those bounds may defer its own retirement.
  CONTINUE WHEN action IS NOT NULL AND until_at+grace>now_at;
  -- The generation marker is retained for the lifetime of the alert and is
  -- restored here when the row never carried one. staged_until governed this
  -- decision and nothing else, so it goes with the intent.
  UPDATE public.alerts SET attributes=
   (coalesce(attributes,'{}'::jsonb)-'zabbix_ack_sync_intent'-'zabbix_ack_sync_staged_until')
   || CASE WHEN action IS NOT NULL AND zabalack.staged_marker(attributes) IS NULL
      THEN jsonb_build_object('zabbix_ack_sync_staged_action_id',action::text) ELSE '{}'::jsonb END
   -- `action` is NULL unless the generation passed the bounds above, so an
   -- unvalidatable intent never leaves a marker behind.
   WHERE id=candidate.id;
  retired:=retired+1;
 END LOOP;
 IF retired>0 OR noted>0 THEN
  INSERT INTO zabalack.audit(operation,actor_ref,details) VALUES('retire_intents',session_user,
   jsonb_build_object('examined',examined,'noted',noted,'retired',retired));
 END IF;
 RETURN jsonb_build_object('examined',examined,'noted',noted,'retired',retired);
END $$;
REVOKE ALL ON FUNCTION zabalack.retire_intents(integer,integer) FROM PUBLIC;

-- project(), with either authority able to refuse and neither able to permit
-- alone.
CREATE OR REPLACE FUNCTION zabalack.project(p_action uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE
 a zabalack.actions; attrs jsonb; target_customer text; why text; display_state text; local_at timestamptz;
 alert_present boolean; staging zabalack.alert_staging; marker uuid; marker_present boolean;
BEGIN
 SELECT * INTO STRICT a FROM zabalack.actions WHERE action_id=p_action FOR UPDATE;
 -- UPDATE(column) does not give SELECT(other columns); this list matches explicit grants.
 SELECT attributes,customer INTO attrs,target_customer FROM public.alerts WHERE id=a.alert_id FOR UPDATE;
 alert_present:=FOUND;
 SELECT * INTO staging FROM zabalack.alert_staging WHERE alert_id=a.alert_id FOR UPDATE;
 marker:=zabalack.staged_marker(attrs);
 marker_present:=coalesce(attrs ? 'zabbix_ack_sync_staged_action_id',false);
 IF attrs->>'zabbix_ack_sync_local_at' IS NOT NULL THEN
  BEGIN local_at:=(attrs->>'zabbix_ack_sync_local_at')::timestamptz;
  EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN local_at:='infinity'::timestamptz; END;
 END IF;
 IF NOT alert_present THEN why := 'projection_target_missing';
 ELSIF local_at>a.issued_at THEN why := 'projection_local_generation_mismatch';
 ELSIF target_customer IS DISTINCT FROM a.customer THEN why := 'projection_customer_mismatch';
 ELSIF attrs->>'zabbix_instance' IS DISTINCT FROM a.instance OR attrs->>'zabbix_event_id' IS DISTINCT FROM a.event_id
  OR attrs->>'zabbix_trigger_id' IS DISTINCT FROM a.trigger_id THEN why := 'projection_source_generation_mismatch';
 ELSIF EXISTS(SELECT 1 FROM zabalack.actions newer WHERE newer.alert_id=a.alert_id AND newer.capture_order>a.capture_order) THEN
  why := 'projection_action_generation_mismatch';
 ELSIF staging.alert_id IS NOT NULL AND staging.staged_action_id<>a.action_id THEN
  -- The ledger cannot be reached from the Alerta API, so it can refuse.
  why := 'projection_action_generation_mismatch';
 ELSIF marker_present AND (marker IS NULL OR marker<>a.action_id) THEN
  -- Either authority may refuse; neither alone may permit. Gating this on an
  -- absent ledger reopened the exact window the guard exists for: once a
  -- capture has seeded the ledger with this action, a newer staging that has
  -- not yet been captured or swept exists *only* as this marker, and ignoring
  -- it let the older projection overwrite the newer acknowledgement's state and
  -- delete its intent. A marker that is present but unparseable is mismatched,
  -- never absent.
  why := 'projection_action_generation_mismatch';
 ELSIF staging.alert_id IS NULL AND NOT marker_present THEN
  -- Neither authority present. Migration backfills the ledger for every alert
  -- carrying an action row, so this is not a pre-existing deployment reaching
  -- an upgraded schema; it is a row whose generation evidence was removed.
  -- Refuse rather than let an arbitrarily old projection through.
  why := 'projection_generation_unknown';
 ELSE
  display_state := CASE WHEN a.state IN ('ok','failed','expired','skipped') THEN a.state ELSE 'pending' END;
  -- Caller cannot supply a patch; every value is derived from the authoritative row.
  -- The intent is retired here on the path that reaches it; the marker is left
  -- exactly as staging wrote it, and staged_until goes with the intent.
  UPDATE public.alerts SET attributes=
   (coalesce(attributes,'{}'::jsonb)-'zabbix_ack_sync_intent'-'zabbix_ack_sync_staged_until')||jsonb_build_object(
   'zabbix_ack_sync',display_state,'zabbix_ack_sync_reason',coalesce(a.reason,a.state),
   'zabbix_ack_sync_action_id',a.action_id::text,'zabbix_ack_sync_generation',a.capture_order,
   'zabbix_ack_sync_effect_id',a.effect_id::text) WHERE id=a.alert_id;
  why := 'projected';
 END IF;
 UPDATE zabalack.actions SET projection_at=clock_timestamp(),projection_reason=why,projection_due_at=NULL WHERE action_id=p_action;
 INSERT INTO zabalack.audit(operation,actor_ref,object_ref,details) VALUES('projection',session_user,p_action::text,jsonb_build_object('result',why));
 RETURN jsonb_build_object('action_id',p_action,'reason',why);
END $$;
REVOKE ALL ON FUNCTION zabalack.project(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zabalack.project(uuid) TO zabalack_worker;

-- worker_call, whose retirement branch now claims the deployment-wide slot
-- before scanning. Nothing else in it changes.
CREATE OR REPLACE FUNCTION zabalack.worker_call(op text,p jsonb DEFAULT '{}'::jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE
 c zabalack.captures; a zabalack.actions; co zabalack.cohorts; ctl zabalack.instance_control; ef zabalack.effects;
 vl zabalack.verification_leases; dl zabalack.delivery_leases; inst zabalack.installation;
 worker text:=p->>'worker_id'; now_at timestamptz:=clock_timestamp(); cid uuid; aid uuid;
 generation bigint; turn bigint; slot_no integer; attempt uuid; why text; target text;
 i jsonb; result jsonb; wanted_state text; effective_state text; until_at timestamptz;
BEGIN
 IF jsonb_typeof(p)<>'object' OR octet_length(p::text)>65536 THEN RAISE EXCEPTION 'invalid command'; END IF;
 IF op IN ('claim_verification','verify_complete','reject_capture','hold_capture','claim_delivery','before_write','start_attempt','complete','claim_background','finish_background')
  AND (worker IS NULL OR length(worker) NOT BETWEEN 1 AND 128) THEN RAISE EXCEPTION 'worker_id required'; END IF;
 IF op='claim_verification' THEN
  -- One short DB lease, no target resolution. Rotate FIFO/newest to reserve progress
  -- for newly arrived genuine work under an existing invalid-candidate backlog.
  UPDATE zabalack.installation SET verification_turn=verification_turn+1 WHERE singleton RETURNING verification_turn INTO turn;
  IF (SELECT count(*) FROM zabalack.verification_leases WHERE expires_at>now_at)>=32
    OR (SELECT count(*) FROM zabalack.verification_leases WHERE owner=worker AND expires_at>now_at)>=4 THEN RETURN NULL; END IF;
  SELECT o.capture_id INTO cid FROM zabalack.outbox o LEFT JOIN zabalack.verification_leases l USING(capture_id)
   WHERE o.state IN ('captured_unverified','verifier_hold') AND o.due_at<=now_at
    AND (l.capture_id IS NULL OR l.expires_at<=now_at)
   ORDER BY CASE WHEN turn%4=0 THEN -extract(epoch FROM o.due_at) ELSE extract(epoch FROM o.due_at) END,o.capture_id
   FOR UPDATE OF o SKIP LOCKED LIMIT 1;
  IF cid IS NULL THEN RETURN NULL; END IF;
  INSERT INTO zabalack.verification_leases(capture_id,owner,expires_at) VALUES(cid,worker,now_at+interval '10 seconds')
   ON CONFLICT(capture_id) DO UPDATE SET owner=excluded.owner,generation=zabalack.verification_leases.generation+1,expires_at=excluded.expires_at
   RETURNING * INTO vl;
  UPDATE zabalack.outbox SET verification_count=verification_count+1 WHERE capture_id=cid;
  SELECT * INTO c FROM zabalack.captures WHERE capture_id=cid;
  RETURN (to_jsonb(c)-'snapshot')||jsonb_build_object('source',c.snapshot,'lease_generation',vl.generation);
 ELSIF op IN ('verify_complete','reject_capture','hold_capture') THEN
  cid:=(p->>'capture_id')::uuid;
  SELECT * INTO STRICT vl FROM zabalack.verification_leases WHERE capture_id=cid FOR UPDATE;
  IF vl.owner<>worker OR vl.generation<>(p->>'lease_generation')::bigint OR vl.expires_at<=now_at THEN RAISE EXCEPTION 'verification lease lost'; END IF;
  SELECT * INTO STRICT c FROM zabalack.captures WHERE capture_id=cid;
  IF op<>'verify_complete' THEN
   why:=left(coalesce(p->>'reason','failed_intent_invalid'),128);
   wanted_state:=CASE WHEN op='hold_capture' THEN 'verifier_hold' WHEN p->>'state'='expired' THEN 'expired' ELSE 'failed' END;
   UPDATE zabalack.outbox SET state=wanted_state,reason=why,due_at=now_at+interval '60 seconds' WHERE capture_id=cid;
   UPDATE zabalack.captures SET state=wanted_state,diagnostic_reason=why WHERE capture_id=cid;
   DELETE FROM zabalack.verification_leases WHERE capture_id=cid;
   INSERT INTO zabalack.verifier_health(worker_id,healthy,reason) VALUES(worker,op<>'hold_capture',why)
    ON CONFLICT(worker_id) DO UPDATE SET healthy=excluded.healthy,reason=excluded.reason,last_seen=now_at;
   RETURN jsonb_build_object('state',wanted_state,'reason',why);
  END IF;
  PERFORM zabalack.expire_due_effects(200);
  i:=p->'intent';
  IF i IS DISTINCT FROM c.intent THEN RAISE EXCEPTION 'verified intent differs from capture'; END IF;
  IF i->>'alert_id' IS DISTINCT FROM c.alert_id OR i->>'customer' IS DISTINCT FROM c.customer
    OR i->'source' IS DISTINCT FROM c.snapshot OR i#>>'{actor,login}' IS DISTINCT FROM c.history_user
    OR i->>'action'<>'ack' OR i->>'route'<>'/alert/'||c.alert_id||'/action'
    OR i#>>'{actor,auth_type}'<>'user' THEN RAISE EXCEPTION 'verified binding mismatch'; END IF;
  SELECT * INTO co FROM zabalack.cohorts WHERE customer=c.customer AND instance=c.snapshot->>'zabbix_instance' AND trigger_id=c.snapshot->>'zabbix_trigger_id';
  IF NOT FOUND OR NOT co.enabled OR co.host_drift OR co.revision IS DISTINCT FROM i->>'cohort_revision'
    OR co.policy_version IS DISTINCT FROM i->>'policy_revision'
    OR (coalesce((i#>>'{actor,admin}')::boolean,false) AND NOT co.admin_exception)
    OR (NOT coalesce((i#>>'{actor,admin}')::boolean,false) AND NOT (i#>'{actor,customers}' ? c.customer)) THEN
   UPDATE zabalack.outbox SET state='failed',reason='failed_actor_or_cohort_policy' WHERE capture_id=cid;
   UPDATE zabalack.captures SET state='failed',diagnostic_reason='failed_actor_or_cohort_policy' WHERE capture_id=cid;
   DELETE FROM zabalack.verification_leases WHERE capture_id=cid;
   RETURN jsonb_build_object('state','failed','reason','failed_actor_or_cohort_policy');
  END IF;
  IF c.captured_at<(i->>'issued_at')::timestamptz OR c.captured_at>(i->>'capture_expires_at')::timestamptz
    OR (i->>'capture_expires_at')::timestamptz>(i->>'issued_at')::timestamptz+interval '60 seconds'
    OR (i->>'execution_expires_at')::timestamptz>(i->>'issued_at')::timestamptz+interval '4 hours' THEN RAISE EXCEPTION 'invalid capture lifetime'; END IF;
  aid:=(i->>'action_id')::uuid;
  SELECT * INTO a FROM zabalack.actions WHERE action_id=aid;
  IF FOUND THEN
   IF a.alert_id<>c.alert_id OR a.customer<>c.customer OR a.event_id<>c.snapshot->>'zabbix_event_id' THEN RAISE EXCEPTION 'verified action collision'; END IF;
   UPDATE zabalack.captures SET state='verified_replay',verified_action_id=aid WHERE capture_id=cid;
   UPDATE zabalack.outbox SET state='verified_replay',reason='action_already_verified' WHERE capture_id=cid;
   DELETE FROM zabalack.verification_leases WHERE capture_id=cid;
   RETURN zabalack.action_json(a)||jsonb_build_object('replay',true);
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(co.instance||':'||(c.snapshot->>'zabbix_event_id'),0));
  SELECT * INTO ef FROM zabalack.effects WHERE instance=co.instance AND event_id=c.snapshot->>'zabbix_event_id' ORDER BY zabalack.effects.generation DESC LIMIT 1 FOR UPDATE;
  -- Concurrent distinct human actions coalesce while a source effect is unresolved.
  -- Only a new explicit action AFTER completed evidence receives a new generation.
  IF ef.effect_id IS NULL OR (ef.completed_at IS NOT NULL AND (i->>'issued_at')::timestamptz>ef.completed_at) THEN
   generation:=coalesce(ef.generation,0)+1;
   INSERT INTO zabalack.effects(instance,event_id,generation,primary_action_id,latest_action_time)
    VALUES(co.instance,c.snapshot->>'zabbix_event_id',generation,aid,(i->>'issued_at')::timestamptz) RETURNING * INTO ef;
  END IF;
  wanted_state:=CASE WHEN (i->>'execution_expires_at')::timestamptz<=now_at THEN 'expired'
   WHEN ef.primary_action_id<>aid THEN CASE WHEN ef.completed_at IS NULL THEN 'held' ELSE ef.state END ELSE 'authorised' END;
  INSERT INTO zabalack.actions(action_id,capture_id,capture_order,effect_id,alert_id,customer,instance,event_id,trigger_id,actor_ref,
   cohort_revision,policy_version,issued_at,execute_before,state,reason,completion_evidence)
   VALUES(aid,cid,c.capture_order,ef.effect_id,c.alert_id,c.customer,co.instance,c.snapshot->>'zabbix_event_id',co.trigger_id,
    p->>'actor_ref',co.revision,co.policy_version,(i->>'issued_at')::timestamptz,(i->>'execution_expires_at')::timestamptz,
    wanted_state,CASE WHEN ef.primary_action_id<>aid THEN 'shared_source_effect' WHEN wanted_state='expired' THEN 'execution_lifetime_expired' ELSE NULL END,ef.completion_evidence)
    RETURNING * INTO a;
  INSERT INTO zabalack.actor_identity(actor_ref,login) VALUES(a.actor_ref,c.history_user) ON CONFLICT(actor_ref) DO NOTHING;
  PERFORM zabalack.expire_due_effects(200);
  SELECT * INTO a FROM zabalack.actions WHERE action_id=aid;
  UPDATE zabalack.captures SET state='verified',verified_action_id=aid WHERE capture_id=cid;
  UPDATE zabalack.outbox SET state='verified',reason=NULL WHERE capture_id=cid;
  DELETE FROM zabalack.verification_leases WHERE capture_id=cid;
  INSERT INTO zabalack.verifier_health(worker_id,healthy) VALUES(worker,true) ON CONFLICT(worker_id) DO UPDATE SET healthy=true,reason=NULL,last_seen=now_at;
  RETURN zabalack.action_json(a);
 ELSIF op='claim_delivery' THEN
  PERFORM zabalack.expire_due_effects(200);
  -- The instance row serialises slot allocation across every process/site.
  SELECT ac.* INTO a FROM zabalack.actions ac JOIN zabalack.effects e USING(effect_id)
   WHERE ac.action_id=e.primary_action_id AND ac.state IN ('authorised','pending','unknown','held','delivery_claimed')
    AND NOT ac.uncertain
    AND NOT EXISTS(SELECT 1 FROM zabalack.delivery_leases active WHERE active.instance=ac.instance AND active.event_id=ac.event_id AND active.expires_at>now_at)
    AND ac.next_due<=now_at AND (p->>'action_id' IS NULL OR ac.action_id=(p->>'action_id')::uuid)
   ORDER BY ac.next_due,ac.action_id FOR UPDATE OF ac SKIP LOCKED LIMIT 1;
  IF a.action_id IS NULL THEN RETURN NULL; END IF;
  SELECT * INTO ctl FROM zabalack.instance_control WHERE instance=a.instance FOR UPDATE;
  SELECT * INTO co FROM zabalack.cohorts WHERE customer=a.customer AND instance=a.instance AND trigger_id=a.trigger_id;
  SELECT * INTO inst FROM zabalack.installation WHERE singleton;
  IF ctl.instance IS NULL OR ctl.paused OR ctl.circuit_open THEN
   UPDATE zabalack.actions SET state='held',reason=CASE WHEN ctl.circuit_open THEN 'instance_circuit_open' ELSE 'maintenance_hold' END,next_due=now_at+interval '60 seconds',projection_due_at=now_at WHERE action_id=a.action_id;
   RETURN NULL;
  END IF;
  IF NOT co.enabled OR co.host_drift OR co.revision<>a.cohort_revision OR co.policy_version<>a.policy_version THEN
   UPDATE zabalack.actions SET state='failed',reason='cohort_policy_changed',updated_at=now_at,projection_due_at=now_at WHERE action_id=a.action_id; RETURN NULL;
  END IF;
  -- Outbound write-disable still permits read-only reconciliation of an ambiguous send.
  IF (a.execute_before<=now_at OR NOT inst.external_writes_enabled OR inst.capture_disabled) AND NOT a.uncertain THEN
   UPDATE zabalack.actions SET state=CASE WHEN a.execute_before<=now_at THEN 'expired' ELSE 'held' END,
    reason=CASE WHEN a.execute_before<=now_at THEN 'execution_lifetime_expired' ELSE 'external_writes_disabled' END,
    next_due=now_at+interval '60 seconds',updated_at=now_at,projection_due_at=now_at WHERE action_id=a.action_id; RETURN NULL;
  END IF;
  SELECT * INTO dl FROM zabalack.delivery_leases WHERE instance=a.instance AND event_id=a.event_id FOR UPDATE;
  IF dl.instance IS NOT NULL AND dl.expires_at>now_at THEN RETURN NULL; END IF;
  IF dl.instance IS NOT NULL AND EXISTS(SELECT 1 FROM zabalack.attempts t WHERE t.action_id=dl.action_id AND t.lease_generation=dl.generation AND t.operation='write'
     AND zabalack.attempt_unsettled(t.attempt_id)) THEN
   UPDATE zabalack.actions SET uncertain=true,state='unknown',reason='abandoned_attempt',updated_at=now_at,projection_due_at=now_at WHERE action_id=dl.action_id;
   IF dl.action_id=a.action_id THEN a.uncertain:=true; a.state:='unknown'; a.reason:='abandoned_attempt'; END IF;
  END IF;
  DELETE FROM zabalack.delivery_slots WHERE instance=a.instance AND expires_at<=now_at;
  SELECT n INTO slot_no FROM generate_series(1,ctl.delivery_slots) n WHERE NOT EXISTS(SELECT 1 FROM zabalack.delivery_slots s WHERE s.instance=a.instance AND s.slot=n) ORDER BY n LIMIT 1;
  IF slot_no IS NULL THEN RETURN NULL; END IF;
  generation:=coalesce(dl.generation,0)+1;
  INSERT INTO zabalack.delivery_leases(instance,event_id,action_id,owner,generation,expires_at)
   VALUES(a.instance,a.event_id,a.action_id,worker,generation,now_at+interval '30 seconds')
   ON CONFLICT(instance,event_id) DO UPDATE SET action_id=excluded.action_id,owner=excluded.owner,generation=excluded.generation,expires_at=excluded.expires_at;
  INSERT INTO zabalack.delivery_slots(instance,slot,action_id,owner,generation,expires_at)
   VALUES(a.instance,slot_no,a.action_id,worker,generation,now_at+interval '30 seconds');
  UPDATE zabalack.actions SET state=CASE WHEN uncertain THEN 'unknown' ELSE 'delivery_claimed' END,updated_at=now_at WHERE action_id=a.action_id RETURNING * INTO a;
  RETURN zabalack.action_json(a)||jsonb_build_object('lease_generation',generation,'slot',slot_no);
 ELSIF op IN ('before_write','start_attempt','complete') THEN
  aid:=(p->>'action_id')::uuid;
  SELECT * INTO STRICT a FROM zabalack.actions WHERE action_id=aid FOR UPDATE;
  SELECT * INTO dl FROM zabalack.delivery_leases WHERE instance=a.instance AND event_id=a.event_id FOR UPDATE;
  IF dl.action_id IS DISTINCT FROM aid OR dl.owner IS DISTINCT FROM worker OR dl.generation IS DISTINCT FROM (p->>'lease_generation')::bigint OR dl.expires_at<=now_at THEN
   IF op='before_write' THEN RETURN jsonb_build_object('allowed',false,'reason','delivery_lease_lost'); END IF;
   -- A late response cannot overwrite a new owner's state. It remains immutable evidence.
   IF op='complete' AND p->>'attempt_id' IS NOT NULL AND EXISTS(SELECT 1 FROM zabalack.attempts t WHERE t.attempt_id=(p->>'attempt_id')::uuid AND t.action_id=aid AND t.owner=worker AND t.lease_generation=(p->>'lease_generation')::bigint) THEN
    -- If no replacement owner has acquired this expired lease, preserve the
    -- unresolved send on the action before the evidence row makes the attempt
    -- appear handled. A late response cannot settle or requeue the source effect.
    -- Terminal/manual-settlement states and a replacement generation are kept.
    IF dl.action_id=aid AND dl.owner=worker AND dl.generation=(p->>'lease_generation')::bigint
       AND a.state IN ('authorised','pending','held','delivery_claimed','unknown')
       AND zabalack.attempt_unsettled((p->>'attempt_id')::uuid) THEN
     UPDATE zabalack.actions SET uncertain=true,state='unknown',reason='late_response_requires_reconciliation',
      next_due=now_at,updated_at=now_at,projection_due_at=now_at WHERE action_id=aid;
    END IF;
    INSERT INTO zabalack.outcomes(action_id,attempt_id,state,reason,completion_evidence,uncertain)
     VALUES(aid,(p->>'attempt_id')::uuid,'late_response',left(p->>'reason',128),left(p->>'completion_evidence',128),true);
    RETURN jsonb_build_object('state','late_response','reason','delivery_lease_lost');
   END IF;
   RAISE EXCEPTION 'delivery lease lost';
  END IF;
  SELECT * INTO ctl FROM zabalack.instance_control WHERE instance=a.instance;
  SELECT * INTO inst FROM zabalack.installation WHERE singleton;
  SELECT * INTO co FROM zabalack.cohorts WHERE customer=a.customer AND instance=a.instance AND trigger_id=a.trigger_id;
  why:=CASE WHEN a.uncertain THEN 'unknown_outcome_requires_reconciliation'
   WHEN a.execute_before<=now_at THEN 'execution_lifetime_expired'
   WHEN ctl.paused THEN 'maintenance_hold' WHEN ctl.circuit_open THEN 'instance_circuit_open' WHEN NOT inst.external_writes_enabled OR inst.capture_disabled THEN 'external_writes_disabled'
   WHEN NOT co.enabled OR co.host_drift OR co.revision<>a.cohort_revision OR co.policy_version<>a.policy_version THEN 'cohort_policy_changed' ELSE NULL END;
  IF op='before_write' THEN RETURN jsonb_build_object('allowed',why IS NULL,'reason',why); END IF;
  IF op='start_attempt' THEN
   IF why IS NOT NULL THEN RETURN jsonb_build_object('allowed',false,'reason',why); END IF;
   -- Committed before sending. Abandonment becomes uncertain, never proof of non-send.
   INSERT INTO zabalack.attempts(action_id,owner,lease_generation,operation,public_reference,frozen_event_id)
    VALUES(aid,worker,dl.generation,'write',a.public_reference,a.event_id) RETURNING attempt_id INTO attempt;
   UPDATE zabalack.actions SET attempts=attempts+1 WHERE action_id=aid;
   RETURN jsonb_build_object('allowed',true,'attempt_id',attempt);
  END IF;
  wanted_state:=p->>'state';
  IF wanted_state NOT IN ('ok','pending','failed','held','skipped','expired','unknown') THEN RAISE EXCEPTION 'invalid outcome state'; END IF;
  IF p->>'attempt_id' IS NOT NULL AND NOT EXISTS(SELECT 1 FROM zabalack.attempts t WHERE t.attempt_id=(p->>'attempt_id')::uuid AND t.action_id=aid AND t.owner=worker AND t.lease_generation=dl.generation) THEN RAISE EXCEPTION 'attempt binding mismatch'; END IF;
  IF wanted_state='ok' AND coalesce(p->>'completion_evidence','') NOT IN ('write_response','source_ack_observed','reference_observed','reference_found') THEN RAISE EXCEPTION 'invalid success evidence'; END IF;
  INSERT INTO zabalack.outcomes(action_id,attempt_id,state,reason,completion_evidence,uncertain)
   VALUES(aid,(p->>'attempt_id')::uuid,wanted_state,left(p->>'reason',128),left(p->>'completion_evidence',128),coalesce((p->>'uncertain')::boolean,false));
  UPDATE zabalack.actions SET state=wanted_state,reason=left(p->>'reason',128),completion_evidence=left(p->>'completion_evidence',128),
   uncertain=coalesce((p->>'uncertain')::boolean,false),updated_at=now_at,
   next_due=now_at+make_interval(secs=>least(3600,greatest(1,coalesce((p->>'retry_delay')::int,60)))),projection_due_at=now_at WHERE action_id=aid;
  IF wanted_state IN ('ok','failed','skipped','expired') AND NOT coalesce((p->>'uncertain')::boolean,false) THEN
   UPDATE zabalack.effects SET state=wanted_state,reason=left(p->>'reason',128),completion_evidence=left(p->>'completion_evidence',128),completed_at=now_at WHERE effect_id=a.effect_id;
   UPDATE zabalack.actions SET state=wanted_state,reason='shared_source_effect',completion_evidence=left(p->>'completion_evidence',128),updated_at=now_at,projection_due_at=now_at
    WHERE effect_id=a.effect_id AND action_id<>aid;
  END IF;
  IF wanted_state='failed' AND coalesce(p->>'reason','') ~ '(token|credential|permission|auth|tls|certificate)' THEN
   UPDATE zabalack.instance_control SET circuit_open=true,circuit_reason=left(p->>'reason',128) WHERE instance=a.instance;
  END IF;
  IF wanted_state='failed' THEN INSERT INTO zabalack.incidents(instance,failure_class) VALUES(a.instance,coalesce(left(p->>'reason',128),'delivery_failure'))
   ON CONFLICT(instance,failure_class) DO UPDATE SET active=true,last_seen=now_at; END IF;
  DELETE FROM zabalack.delivery_slots s WHERE s.action_id=aid AND s.owner=worker AND s.generation=dl.generation;
  UPDATE zabalack.delivery_leases l SET expires_at=now_at WHERE l.action_id=aid AND l.owner=worker AND l.generation=dl.generation;
  SELECT * INTO a FROM zabalack.actions WHERE action_id=aid;
  RETURN zabalack.action_json(a);
 ELSIF op='claim_background' THEN
  target:=p->>'instance'; SELECT * INTO ctl FROM zabalack.instance_control WHERE instance=target FOR UPDATE;
  IF NOT FOUND OR ctl.next_background_at>now_at OR ctl.background_until>now_at THEN RETURN jsonb_build_object('allowed',false); END IF;
  UPDATE zabalack.instance_control SET background_generation=background_generation+1,background_owner=worker,background_until=now_at+interval '25 seconds',next_background_at=now_at+interval '1 second' WHERE instance=target;
  RETURN jsonb_build_object('allowed',true,'lease_generation',ctl.background_generation+1,'instance',target,'owner',worker,'expires_at',now_at+interval '25 seconds');
 ELSIF op='finish_background' THEN
  UPDATE zabalack.instance_control SET background_owner=NULL,background_until=NULL WHERE instance=p->>'instance' AND background_owner=worker AND background_generation=(p->>'lease_generation')::bigint;
  RETURN jsonb_build_object('released',FOUND);
 ELSIF op='record_reconciliation' THEN
  SELECT * INTO STRICT ctl FROM zabalack.instance_control WHERE instance=p->>'instance' FOR UPDATE;
  IF ctl.background_owner IS DISTINCT FROM worker OR ctl.background_generation IS DISTINCT FROM (p->>'background_generation')::bigint OR ctl.background_until<=now_at THEN RAISE EXCEPTION 'background lease lost'; END IF;
  SELECT * INTO STRICT a FROM zabalack.actions WHERE action_id=(p->>'action_id')::uuid AND instance=ctl.instance FOR UPDATE;
  IF EXISTS(SELECT 1 FROM zabalack.delivery_leases l WHERE l.action_id=a.action_id AND l.expires_at>now_at) THEN RETURN jsonb_build_object('recorded',false,'reason','delivery_in_flight'); END IF;
  IF coalesce((p->>'preserve_delivery')::boolean,false) THEN
   UPDATE zabalack.actions SET drift_due_at=now_at+interval '1 day' WHERE action_id=a.action_id;
   INSERT INTO zabalack.audit(operation,actor_ref,object_ref,details) VALUES('reconciliation_check_failed',worker,a.action_id::text,jsonb_build_object('reason',left(p->>'check_failure',128)));
   RETURN jsonb_build_object('recorded',true,'state',a.state);
  END IF;
  wanted_state:=p->>'state';
  IF wanted_state NOT IN ('ok','failed','held','pending','unknown','expired') THEN RAISE EXCEPTION 'invalid reconciliation state'; END IF;
  IF wanted_state='ok' AND p->>'completion_evidence' NOT IN ('reference_observed','source_ack_observed') THEN RAISE EXCEPTION 'invalid reconciliation evidence'; END IF;
  INSERT INTO zabalack.outcomes(action_id,state,reason,completion_evidence,uncertain)
   VALUES(a.action_id,wanted_state,left(p->>'reason',128),left(p->>'completion_evidence',128),coalesce((p->>'uncertain')::boolean,false));
  UPDATE zabalack.actions SET state=wanted_state,reason=left(p->>'reason',128),completion_evidence=left(p->>'completion_evidence',128),
   uncertain=coalesce((p->>'uncertain')::boolean,false),next_due=now_at+make_interval(secs=>least(3600,greatest(1,coalesce((p->>'retry_delay')::int,60)))),
   updated_at=now_at,projection_due_at=now_at,drift_due_at=now_at+interval '1 day' WHERE action_id=a.action_id;
  IF wanted_state IN ('ok','failed','expired') AND NOT coalesce((p->>'uncertain')::boolean,false) THEN
   UPDATE zabalack.effects SET state=wanted_state,reason=left(p->>'reason',128),completion_evidence=left(p->>'completion_evidence',128),completed_at=now_at WHERE effect_id=a.effect_id;
   UPDATE zabalack.actions SET state=wanted_state,reason='shared_source_effect',completion_evidence=left(p->>'completion_evidence',128),uncertain=false,updated_at=now_at,projection_due_at=now_at WHERE effect_id=a.effect_id AND action_id<>a.action_id;
  END IF;
  RETURN jsonb_build_object('recorded',true,'state',wanted_state);
 ELSIF op='worker_stopped' THEN
  -- A replacement worker may hold a lease for an older owner's unknown send.
  -- Shutdown cannot infer settlement from the absence of its own attempts.
  UPDATE zabalack.actions stopped_action SET state=CASE WHEN (stopped_action.uncertain OR EXISTS(SELECT 1 FROM zabalack.attempts t WHERE t.action_id=stopped_action.action_id AND zabalack.attempt_unsettled(t.attempt_id))) THEN 'unknown' ELSE 'pending' END,
   uncertain=(stopped_action.uncertain OR EXISTS(SELECT 1 FROM zabalack.attempts t WHERE t.action_id=stopped_action.action_id AND zabalack.attempt_unsettled(t.attempt_id))),
   reason='worker_stopped',next_due=now_at,updated_at=now_at,projection_due_at=now_at
   WHERE stopped_action.state IN ('authorised','pending','held','delivery_claimed','unknown')
    AND stopped_action.action_id IN (SELECT action_id FROM zabalack.delivery_leases WHERE owner=worker AND expires_at>now_at);
  DELETE FROM zabalack.delivery_slots WHERE owner=worker;
  UPDATE zabalack.delivery_leases SET expires_at=now_at WHERE owner=worker;
  INSERT INTO zabalack.audit(operation,actor_ref) VALUES('worker_stopped',coalesce(worker,session_user));
  RETURN jsonb_build_object('recorded',true);
 ELSIF op='reconcile_due' THEN
  PERFORM zabalack.expire_due_effects(200);
  -- Capture reclaims happen through claim_verification; only indexed due work here.
  UPDATE zabalack.actions ac SET uncertain=true,state='unknown',reason='abandoned_attempt',next_due=now_at,updated_at=now_at,projection_due_at=now_at
   WHERE ac.action_id IN (SELECT l.action_id FROM zabalack.delivery_leases l JOIN zabalack.actions aa ON aa.action_id=l.action_id
    WHERE l.expires_at<=now_at AND aa.state='delivery_claimed'
      AND EXISTS(SELECT 1 FROM zabalack.attempts t WHERE t.action_id=l.action_id AND t.lease_generation=l.generation AND zabalack.attempt_unsettled(t.attempt_id)) LIMIT least(200,coalesce((p->>'limit')::int,200)));
  UPDATE zabalack.actions SET state='expired',reason='execution_lifetime_expired',updated_at=now_at,projection_due_at=now_at
   WHERE action_id IN (SELECT action_id FROM zabalack.actions WHERE state IN ('authorised','pending','held') AND NOT uncertain AND execute_before<=now_at AND next_due<=now_at ORDER BY next_due LIMIT least(200,coalesce((p->>'limit')::int,200)));
  SELECT coalesce(jsonb_agg(zabalack.action_json(x)),'[]') INTO result FROM (SELECT * FROM zabalack.actions WHERE state IN ('authorised','pending','unknown','held','delivery_claimed') AND next_due<=now_at ORDER BY next_due,action_id LIMIT least(200,coalesce((p->>'limit')::int,200))) x;
  RETURN result;
 ELSIF op='projection_due' THEN
  SELECT coalesce(jsonb_agg(jsonb_build_object('action_id',x.action_id)),'[]') INTO result FROM
   (SELECT action_id FROM zabalack.actions WHERE projection_due_at<=now_at ORDER BY projection_due_at,action_id LIMIT least(200,coalesce((p->>'limit')::int,200))) x;
  RETURN result;
 ELSIF op='project' THEN RETURN zabalack.project((p->>'action_id')::uuid);
 ELSIF op='retire_intents' THEN
  -- Cadence is deployment state, not each worker's own clock: the advisory
  -- lock inside retire_intents is released with this transaction, so it
  -- excludes an overlapping sweep but not a repeated one.
  IF NOT zabalack.claim_retirement_slot() THEN
   RETURN jsonb_build_object('examined',0,'noted',0,'retired',0,'skipped','not_due');
  END IF;
  RETURN zabalack.retire_intents(coalesce((p->>'limit')::int,200),coalesce((p->>'grace_seconds')::int,300));
 ELSIF op IN ('status','inspect','doctor','verifier_health') THEN
  IF op='doctor' THEN RETURN zabalack.capture_health(); END IF;
  IF op='verifier_health' THEN SELECT coalesce(jsonb_agg(to_jsonb(v)),'[]') INTO result FROM zabalack.verifier_health v; RETURN result; END IF;
  IF op='inspect' THEN
   SELECT coalesce(jsonb_agg(zabalack.action_json(x)),'[]') INTO result FROM (SELECT * FROM zabalack.actions WHERE (p->>'state' IS NULL OR state=p->>'state') AND (p->>'action_id' IS NULL OR action_id=(p->>'action_id')::uuid) ORDER BY updated_at DESC LIMIT least(200,coalesce((p->>'limit')::int,50))) x; RETURN result;
  END IF;
  RETURN jsonb_build_object('health',zabalack.capture_health(),'actions',(SELECT coalesce(jsonb_object_agg(state,n),'{}') FROM (SELECT state,count(*) n FROM zabalack.actions GROUP BY state)x),
   'captures',(SELECT coalesce(jsonb_object_agg(state,n),'{}') FROM (SELECT state,count(*) n FROM zabalack.outbox GROUP BY state)x),
   'instances',(SELECT coalesce(jsonb_agg(to_jsonb(x)-'background_owner'),'[]') FROM zabalack.instance_control x),
   'oldest_pending_at',(SELECT min(issued_at) FROM zabalack.actions WHERE state IN ('authorised','pending','held','unknown','delivery_claimed')),
   'per_instance',(SELECT coalesce(jsonb_agg(summary),'[]') FROM (SELECT control.instance,
    count(ac.action_id) FILTER(WHERE ac.state IN ('authorised','pending','held','unknown','delivery_claimed')) AS pending_count,
    coalesce(extract(epoch FROM now_at-min(ac.issued_at) FILTER(WHERE ac.state IN ('authorised','pending','held','unknown','delivery_claimed'))),0) AS oldest_pending_seconds,
    count(ac.action_id) FILTER(WHERE ac.state='ok') AS success_count,count(ac.action_id) FILTER(WHERE ac.state='failed') AS failure_count,
    count(ac.action_id) FILTER(WHERE ac.state='skipped') AS skipped_count,coalesce(sum(greatest(ac.attempts-1,0)),0) AS retries_sum,
    count(ac.action_id) FILTER(WHERE ac.uncertain) AS uncertain_count
    FROM zabalack.instance_control control LEFT JOIN zabalack.actions ac ON ac.instance=control.instance GROUP BY control.instance) summary),
   'verifier_unhealthy',(SELECT count(*) FROM zabalack.verifier_health WHERE NOT healthy),
   'active_incidents',(SELECT coalesce(jsonb_agg(jsonb_build_object('instance',incident.instance,'failure_class',incident.failure_class)),'[]') FROM zabalack.incidents incident WHERE incident.active));
 ELSIF op IN ('host_cohorts','host_inventory_due') THEN
  SELECT coalesce(jsonb_agg(zabalack.cohort_json(cohort_row)||jsonb_build_object('host_cursor',cohort_row.host_cursor)),'[]') INTO result FROM (SELECT * FROM zabalack.cohorts WHERE enabled AND (p->>'instance' IS NULL OR instance=p->>'instance') AND (host_cursor>0 OR host_checked_at IS NULL OR host_checked_at<=now_at-interval '15 minutes') ORDER BY customer,instance,trigger_id LIMIT least(200,coalesce((p->>'limit')::int,200))) cohort_row; RETURN result;
 ELSIF op IN ('record_host_check','record_host_inventory') THEN
  SELECT * INTO STRICT ctl FROM zabalack.instance_control WHERE instance=p->>'instance' FOR UPDATE;
  IF ctl.background_owner IS DISTINCT FROM worker OR ctl.background_generation IS DISTINCT FROM (p->>'background_generation')::bigint OR ctl.background_until<=now_at THEN RAISE EXCEPTION 'background lease lost'; END IF;
  UPDATE zabalack.cohorts SET host_cursor=coalesce((p->>'next_cursor')::int,0),host_checked_at=now_at,
   host_drift=CASE WHEN p->>'reason'='host_inventory_failed' THEN host_drift ELSE host_drift OR coalesce((p->>'drift')::boolean,false) OR coalesce(jsonb_array_length(p->'missing_ids'),0)>0 OR coalesce(jsonb_array_length(p->'group_mismatch_ids'),0)>0 OR coalesce(jsonb_array_length(p->'unknown_ids'),0)>0 END
   WHERE customer=p->>'customer' AND instance=p->>'instance' AND trigger_id=p->>'trigger_id' AND revision=p->>'cohort_revision';
  INSERT INTO zabalack.audit(operation,actor_ref,object_ref,details) VALUES('host_check',coalesce(worker,session_user),p->>'instance',jsonb_build_object('drift',p->'drift','reason',left(p->>'reason',128)));
  RETURN jsonb_build_object('recorded',true);
 ELSIF op IN ('drift_due','nightly_due') THEN
  SELECT coalesce(jsonb_agg(zabalack.action_json(x)),'[]') INTO result FROM (SELECT * FROM zabalack.actions WHERE state='ok' AND drift_due_at<=now_at AND (updated_at>now_at-make_interval(secs=>least(2592000,greatest(1,coalesce((p->>'recent_terminal_seconds')::int,604800)))) OR EXISTS(SELECT 1 FROM public.alerts mapped WHERE mapped.id=zabalack.actions.alert_id AND mapped.customer=zabalack.actions.customer AND mapped.attributes->>'zabbix_instance'=zabalack.actions.instance AND mapped.attributes->>'zabbix_event_id'=zabalack.actions.event_id AND mapped.attributes->>'zabbix_trigger_id'=zabalack.actions.trigger_id)) AND (p->>'cursor' IS NULL OR action_id>(p->>'cursor')::uuid) ORDER BY action_id LIMIT least(200,coalesce((p->>'limit')::int,200)))x; IF op='nightly_due' THEN RETURN jsonb_build_object('actions',result,'next_cursor',NULL); END IF; RETURN result;
 ELSIF op='record_drift' THEN
  UPDATE zabalack.actions SET drift_due_at=now_at+interval '1 day',reason=CASE WHEN coalesce((p->>'drift')::boolean,false) THEN 'source_state_drift' ELSE reason END,projection_due_at=now_at WHERE action_id=(p->>'action_id')::uuid AND state='ok';
  INSERT INTO zabalack.audit(operation,actor_ref,object_ref,details) VALUES('read_only_drift',coalesce(worker,session_user),p->>'action_id',jsonb_build_object('drift',p->'drift'));
  RETURN jsonb_build_object('recorded',true);
 ELSE RAISE EXCEPTION 'unsupported worker operation: %',op;
 END IF;
END $$;
REVOKE ALL ON FUNCTION zabalack.worker_call(text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zabalack.worker_call(text,jsonb) TO zabalack_worker;

UPDATE zabalack.installation SET schema_version=5 WHERE singleton;
