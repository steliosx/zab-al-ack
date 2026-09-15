-- A5 stage two (T41): retire staged intents from live alert rows, and move the
-- generation guard out of the Alerta API's reach. Preserve 001-007. Schema 3 -> 4.
--
-- The guard that project() carried since 002 compared the staged intent's
-- action_id against its own. Retiring the intent removes that comparison, and
-- nothing in public.alerts.attributes can replace it: PUT /alert/<id>/attributes
-- runs no plugin hook and replaces the whole object, so any attribute-borne
-- marker can be erased, forged or rolled back by a stale dictionary.
--
-- The replacement is zabalack.alert_generations, an append-only fence ordered by
-- an identity sequence the database assigns. Every staging records its
-- generation there in the same transaction that writes the staged attributes,
-- through zabalack.stage_generation(), the one function the Alerta application
-- role may execute. Nothing updates or deletes a fence row: not the sweep, not
-- projection, not this migration. A generation recorded at staging outranks any
-- recorded by observation (capture, migration), whatever their sequence, so a
-- capture arriving late cannot promote an older acknowledgement above a newer
-- staging. project() refuses unless its action is the newest generation.
--
-- Lock order, everywhere both are taken: the per-alert generation lock, then the
-- public.alerts row. project() takes its own action row before either.
-- capture_ack() runs inside the signed UPDATE, already holding the row, and
-- takes no generation lock; its insert is ON CONFLICT DO NOTHING.

CREATE TABLE zabalack.alert_generations(
 seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 alert_id text NOT NULL,
 action_id uuid NOT NULL UNIQUE,
 recorded_by text NOT NULL CHECK(recorded_by IN ('staging','capture','migration')),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX alert_generations_alert_seq ON zabalack.alert_generations(alert_id,seq DESC);
REVOKE ALL ON zabalack.alert_generations FROM PUBLIC;

-- The newest generation for an alert. A row recorded at staging outranks every
-- row recorded by observation; within a tier the highest sequence wins.
CREATE FUNCTION zabalack.newest_generation(p_alert text) RETURNS uuid LANGUAGE sql STABLE
SET search_path=pg_catalog,zabalack AS $$
 SELECT action_id FROM zabalack.alert_generations WHERE alert_id=p_alert
 ORDER BY (recorded_by='staging') DESC, seq DESC LIMIT 1;
$$;
REVOKE ALL ON FUNCTION zabalack.newest_generation(text) FROM PUBLIC;

-- The protected staging operation. Records the generation and writes the
-- reserved staging keys in one transaction; the caller commits. It writes only
-- the zabbix_ack_sync* keys the signer would have written, so Alerta's own
-- whole-object write of the same values afterwards changes nothing. A refused
-- staging stages nothing: the caller must fail the acknowledgement.
CREATE FUNCTION zabalack.stage_generation(p_alert text, p_action uuid, p_intent jsonb) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE s bigint; row_customer text;
BEGIN
 IF p_alert IS NULL OR length(p_alert) NOT BETWEEN 1 AND 128 OR p_action IS NULL THEN
  RAISE EXCEPTION 'staging identity required';
 END IF;
 IF jsonb_typeof(p_intent) IS DISTINCT FROM 'object' OR octet_length(p_intent::text)>16384
   OR p_intent->>'action_id' IS DISTINCT FROM p_action::text OR p_intent->>'alert_id' IS DISTINCT FROM p_alert THEN
  RAISE EXCEPTION 'staged intent does not bind to this staging';
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('zabalack.generation:'||p_alert,0));
 SELECT customer INTO row_customer FROM public.alerts WHERE id=p_alert FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'staging target missing'; END IF;
 IF p_intent->>'customer' IS DISTINCT FROM row_customer THEN
  RAISE EXCEPTION 'staged intent does not bind to the alert customer';
 END IF;
 -- The signer issues a fresh action_id per staging, so an existing one is a
 -- replay of something already recorded: refuse rather than re-stage it.
 INSERT INTO zabalack.alert_generations(alert_id,action_id,recorded_by) VALUES(p_alert,p_action,'staging')
  ON CONFLICT(action_id) DO NOTHING RETURNING seq INTO s;
 IF s IS NULL THEN RAISE EXCEPTION 'generation already recorded'; END IF;
 UPDATE public.alerts SET attributes=(coalesce(attributes,'{}'::jsonb)-'zabbix_ack_sync_local_at')
  ||jsonb_build_object('zabbix_ack_sync','pending','zabbix_ack_sync_reason','awaiting_capture',
   'zabbix_ack_sync_action_id',p_action::text,'zabbix_ack_sync_intent',p_intent)
  WHERE id=p_alert;
 INSERT INTO zabalack.audit(operation,actor_ref,object_ref,details)
  VALUES('stage_generation',session_user,p_alert,jsonb_build_object('action_id',p_action,'seq',s));
 RETURN s;
END $$;
REVOKE ALL ON FUNCTION zabalack.stage_generation(text,uuid,jsonb) FROM PUBLIC;
-- EXECUTE is granted to the application role by sql/install/12_application_grant.sql,
-- run as zabalack_owner with that role named; a migration cannot know it.

-- Retirement cadence is deployment-wide state rather than each worker's clock:
-- one worker per interval performs the bounded scan of public.alerts, however
-- many workers and sites run. The claim never blocks a loser.
ALTER TABLE zabalack.installation
 ADD COLUMN IF NOT EXISTS retirement_due_at timestamptz,
 ADD COLUMN IF NOT EXISTS retirement_interval_seconds integer NOT NULL DEFAULT 900,
 -- The one role granted zabalack.stage_generation(), recorded by
 -- sql/install/12_application_grant.sql; capture_health() reports functions_ok
 -- false while the function has any other non-owner grantee.
 ADD COLUMN IF NOT EXISTS application_role text;

CREATE FUNCTION zabalack.claim_retirement_slot() RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
DECLARE claimed boolean;
BEGIN
 IF NOT pg_try_advisory_xact_lock(hashtextextended('zabalack.retire_intents',0)) THEN
  RETURN false;
 END IF;
 UPDATE zabalack.installation SET retirement_due_at=clock_timestamp()
  +make_interval(secs=>least(86400,greatest(1,retirement_interval_seconds)))
  WHERE singleton AND (retirement_due_at IS NULL OR retirement_due_at<=clock_timestamp())
  RETURNING true INTO claimed;
 RETURN coalesce(claimed,false);
END $$;
REVOKE ALL ON FUNCTION zabalack.claim_retirement_slot() FROM PUBLIC;

-- Whether a staged intent is due for retirement. The attribute is
-- API-writable: its timestamps decide only whether the intent is retired now
-- or on a later pass, and a value that fails the signer's own bounds — or one
-- the cast rejects, a bad zone displacement as much as a bad format — is
-- unvalidatable plaintext, retired now. Nothing here raises, so a broken
-- value never aborts a sweep that serves every other row. capture_ack()
-- performs no lifetime check of its own — a capture can still be recorded
-- after the window, and verify_complete is what rejects it — so the grace
-- margin keeps a capture in flight at expiry from becoming intent_missing.
CREATE FUNCTION zabalack.intent_retirable(p_intent jsonb, p_grace interval, p_now timestamptz) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE until_at timestamptz; issued_at timestamptz;
BEGIN
 IF p_intent IS NULL OR jsonb_typeof(p_intent)<>'object' THEN RETURN true; END IF;
 BEGIN
  issued_at:=(p_intent->>'issued_at')::timestamptz;
  until_at:=(p_intent->>'capture_expires_at')::timestamptz;
  IF issued_at IS NULL OR until_at IS NULL
     OR isfinite(issued_at) IS NOT TRUE OR isfinite(until_at) IS NOT TRUE
     -- The same lifetime rule verify_complete enforces on a capture.
     OR until_at>issued_at+interval '60 seconds' OR until_at<=issued_at
     OR issued_at>p_now THEN
   RETURN true;
  END IF;
  RETURN until_at+p_grace<=p_now;
 EXCEPTION WHEN OTHERS THEN
  RETURN true;
 END;
END $$;
REVOKE ALL ON FUNCTION zabalack.intent_retirable(jsonb,interval,timestamptz) FROM PUBLIC;

-- The bounded sweep. It removes the staged intent from live public.alerts rows
-- once its capture window has closed by a grace margin, on every lifecycle path
-- and independently of whether verification or delivery ever succeeds. It
-- never reads or writes zabalack.captures.intent, and never touches the fence.
CREATE FUNCTION zabalack.retire_intents(p_limit integer DEFAULT 200, p_grace_seconds integer DEFAULT 300)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE
 candidate record; intent jsonb; grace interval; batch integer; candidates text[]; taken text[];
 examined integer:=0; retired integer:=0; deferred integer:=0;
BEGIN
 -- public.alerts carries no index for this predicate — zabalack_owner does not
 -- own the table and cannot create one — so LIMIT bounds matching rows, not rows
 -- scanned. Exactly one sweep runs at a time across the deployment.
 IF NOT pg_try_advisory_xact_lock(hashtextextended('zabalack.retire_intents',0)) THEN
  RETURN jsonb_build_object('examined',0,'retired',0,'deferred',0,'skipped','sweep_in_progress');
 END IF;
 grace:=make_interval(secs=>least(86400,greatest(0,coalesce(p_grace_seconds,300))));
 batch:=least(1000,greatest(1,coalesce(p_limit,200)));
 -- No ORDER BY: an ordered scan would sort the whole table for no benefit. The
 -- batch is chosen from the rows that are due, so a staging still inside its
 -- window takes no place in it: a burst of fresh acknowledgements ahead of the
 -- due rows in heap order cannot hold them back. Each pass removes what it
 -- processes, so the candidate set shrinks and repeated passes converge.
 -- The predicate is evaluated against the clock at that moment — here, in the
 -- top-up below and under the lock — never against a time saved when the sweep
 -- began: the scan of a large table is long enough for an acknowledgement to
 -- be staged on a row it has yet to reach, and against a saved time that
 -- staging would read as issued in the future, unvalidatable, retired at once.
 SELECT coalesce(array_agg(id),'{}') INTO candidates FROM (SELECT id FROM public.alerts
  WHERE attributes ? 'zabbix_ack_sync_intent'
   AND zabalack.intent_retirable(attributes->'zabbix_ack_sync_intent',grace,clock_timestamp())
  LIMIT batch) chosen;
 -- The candidates are chosen first, without locking, so a row another session
 -- holds — an acknowledgement staging it, a projection — is counted as
 -- deferred to the next pass rather than silently omitted, and the caller
 -- keeps the slot due while any is; SKIP LOCKED takes the rest.
 SELECT coalesce(array_agg(id),'{}') INTO taken FROM (SELECT id FROM public.alerts
  WHERE id=ANY(candidates) FOR UPDATE SKIP LOCKED) locked;
 deferred:=coalesce(array_length(candidates,1),0)-coalesce(array_length(taken,1),0);
 -- A held candidate costs the pass nothing: its place is filled from the due
 -- rows beyond the candidates, locking as it goes so that whatever else is
 -- held is skipped as well. A pass therefore makes progress whenever any due
 -- row is free, however many held rows precede it in heap order. Before this
 -- the batch was the first rows that were due, held or not, and enough held
 -- rows at the front of the heap had every pass choosing them again.
 IF deferred>0 THEN
  SELECT taken||coalesce(array_agg(id),'{}') INTO taken FROM (SELECT id FROM public.alerts
   WHERE attributes ? 'zabbix_ack_sync_intent' AND NOT (id=ANY(candidates))
    AND zabalack.intent_retirable(attributes->'zabbix_ack_sync_intent',grace,clock_timestamp())
   LIMIT batch-coalesce(array_length(taken,1),0) FOR UPDATE SKIP LOCKED) filled;
 END IF;
 FOR candidate IN SELECT id,attributes FROM public.alerts WHERE id=ANY(taken) FOR UPDATE
 LOOP
  examined:=examined+1;
  intent:=candidate.attributes->'zabbix_ack_sync_intent';
  -- Retired (a projection), or staged again, between the choice and the lock:
  -- the row is read under its lock against the clock now, and left alone.
  CONTINUE WHEN intent IS NULL OR NOT zabalack.intent_retirable(intent,grace,clock_timestamp());
  UPDATE public.alerts SET attributes=coalesce(attributes,'{}'::jsonb)-'zabbix_ack_sync_intent'
   WHERE id=candidate.id;
  retired:=retired+1;
 END LOOP;
 IF retired>0 THEN
  INSERT INTO zabalack.audit(operation,actor_ref,details) VALUES('retire_intents',session_user,
   jsonb_build_object('examined',examined,'retired',retired,'deferred',deferred));
 END IF;
 RETURN jsonb_build_object('examined',examined,'retired',retired,'deferred',deferred);
END $$;
REVOKE ALL ON FUNCTION zabalack.retire_intents(integer,integer) FROM PUBLIC;

-- capture_ack(): 002's body with the fallback generation record. The trigger
-- definition is unchanged, so trigger_md5 is unaffected.
CREATE OR REPLACE FUNCTION zabalack.capture_ack() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE
 i jsonb; s jsonb; m text; tail text; matches text[]; why text; intent_reason text; cid uuid;
 disabled boolean; eligible boolean;
BEGIN
 -- The WHEN predicate performs no configuration lookup. Only enrolled Zabbix rows
 -- have mandatory evidence; missing configuration does not invent an enrolment.
 IF NEW.attributes->>'source_system' IS DISTINCT FROM 'zabbix' THEN RETURN NEW; END IF;
 SELECT EXISTS(SELECT 1 FROM zabalack.cohorts c WHERE c.customer=NEW.customer
  AND c.instance=NEW.attributes->>'zabbix_instance'
  AND c.trigger_id=NEW.attributes->>'zabbix_trigger_id' AND c.enabled) INTO eligible;
 IF NOT eligible THEN RETURN NEW; END IF;
 SELECT capture_disabled INTO STRICT disabled FROM zabalack.installation WHERE singleton;
 s := jsonb_build_object('zabbix_instance',NEW.attributes->>'zabbix_instance',
  'zabbix_event_id',left(NEW.attributes->>'zabbix_event_id',20),'zabbix_trigger_id',NEW.attributes->>'zabbix_trigger_id');
 i := NEW.attributes->'zabbix_ack_sync_intent';
 tail := left(substring((NEW.history[1]).text FROM position('[zabalack:' IN left((NEW.history[1]).text,65536))),257);
 m := left(tail,256);
 matches := regexp_match(m,'^\[zabalack:v1 action=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) kid=([A-Za-z0-9_.-]{1,32}) mac=([0-9a-f]{64})\]$');
 -- A well-formed marker is exactly the bounded proof string. Anything else may
 -- contain the operator's free text after the reserved prefix, which the capture
 -- allowlist excludes: keep only the diagnostic reason, not the malformed bytes.
 IF matches IS NULL THEN m := NULL; END IF;
 -- Apply the capture allowlist even when the marker itself is malformed.
 IF i IS NULL OR i='null'::jsonb THEN intent_reason := 'intent_missing';
 ELSIF jsonb_typeof(i)<>'object' OR octet_length(i::text)>16384 THEN intent_reason := 'intent_shape_invalid'; i := NULL;
 ELSIF (SELECT count(*) FROM jsonb_object_keys(i))<>13
   OR i - ARRAY['version','action_id','alert_id','customer','source','actor','route','action','cohort_revision','policy_revision','issued_at','capture_expires_at','execution_expires_at'] <> '{}'::jsonb
   OR jsonb_typeof(i->'source') IS DISTINCT FROM 'object'
   OR jsonb_typeof(i->'actor') IS DISTINCT FROM 'object' THEN intent_reason := 'intent_shape_invalid'; i := NULL;
 ELSIF (i->'source') - ARRAY['zabbix_instance','zabbix_event_id','zabbix_trigger_id'] <> '{}'::jsonb
   OR (i->'actor') - ARRAY['login','customers','admin','auth_type'] <> '{}'::jsonb
   OR (SELECT count(*) FROM jsonb_object_keys(i->'source'))<>3
   OR (SELECT count(*) FROM jsonb_object_keys(i->'actor'))<>4
   OR jsonb_typeof(i#>'{actor,login}') IS DISTINCT FROM 'string'
   OR octet_length(i#>>'{actor,login}')>256
   OR jsonb_typeof(i#>'{actor,admin}') IS DISTINCT FROM 'boolean'
   OR i#>>'{actor,auth_type}' IS DISTINCT FROM 'user'
   OR jsonb_typeof(i#>'{actor,customers}') IS DISTINCT FROM 'array'
   OR EXISTS(SELECT 1 FROM jsonb_each(i->'source') field WHERE jsonb_typeof(field.value)<>'string')
   OR EXISTS(SELECT 1 FROM jsonb_each(i) field WHERE field.key NOT IN ('version','source','actor') AND jsonb_typeof(field.value)<>'string')
   OR i->'version' IS DISTINCT FROM '1'::jsonb THEN intent_reason := 'intent_shape_invalid'; i := NULL;
 ELSIF jsonb_array_length(i#>'{actor,customers}')>100
   OR EXISTS(SELECT 1 FROM jsonb_array_elements(i#>'{actor,customers}') element WHERE jsonb_typeof(element)<>'string' OR octet_length(element::text)>130) THEN intent_reason := 'intent_shape_invalid'; i := NULL;
 END IF;
 IF disabled THEN why := 'skipped_capture_bypass';
 ELSIF octet_length(tail)>256 OR octet_length((NEW.history[1]).text)>65536 OR matches IS NULL THEN why := 'marker_malformed';
 ELSIF intent_reason IS NOT NULL THEN why := intent_reason;
 ELSIF i->>'action_id' IS DISTINCT FROM matches[1]
   OR i->>'alert_id' IS DISTINCT FROM NEW.id
   OR i->>'customer' IS DISTINCT FROM NEW.customer
   OR i->'source' IS DISTINCT FROM s
   OR i#>>'{actor,login}' IS DISTINCT FROM (NEW.history[1])."user" THEN why := 'binding_mismatch';
 END IF;
 -- Fallback generation record. A staging made through zabalack.stage_generation()
 -- is already recorded; this covers acknowledgements staged before that existed
 -- or by a plugin that predates it. It records only a capture that bound —
 -- marker, intent and row agree, with the proof still to be verified. An
 -- unbound, malformed or bypassed capture can never be verified into an
 -- action, so it records nothing, and cannot displace the newest generation
 -- on an alert that holds no staging-recorded row. newest_generation() never
 -- lets a row recorded here outrank one recorded at staging, and an action
 -- already recorded is left exactly where it is, so a capture arriving late
 -- can never promote an older acknowledgement above a newer staging.
 IF why IS NULL THEN
  BEGIN
   INSERT INTO zabalack.alert_generations(alert_id,action_id,recorded_by)
    VALUES(NEW.id,(i->>'action_id')::uuid,'capture') ON CONFLICT(action_id) DO NOTHING;
  EXCEPTION WHEN OTHERS THEN NULL;
  END;
 END IF;
 -- capture_id has no uniqueness relationship to the untrusted action ID.
 INSERT INTO zabalack.captures(alert_id,customer,snapshot,intent,marker,history_type,history_user,
  history_update_time,resulting_status,diagnostic_reason,state)
 VALUES(NEW.id,NEW.customer,s,i,m,(NEW.history[1]).type,(NEW.history[1])."user",
  (NEW.history[1]).update_time AT TIME ZONE 'UTC',NEW.status,why,
  CASE WHEN why='skipped_capture_bypass' THEN 'skipped' WHEN why IS NULL THEN 'captured_unverified' ELSE 'failed' END)
 RETURNING capture_id INTO cid;
 INSERT INTO zabalack.outbox(capture_id,state,reason) VALUES(cid,
  CASE WHEN why='skipped_capture_bypass' THEN 'skipped' WHEN why IS NULL THEN 'captured_unverified' ELSE 'failed' END,
  CASE WHEN why IS NULL THEN NULL WHEN why='skipped_capture_bypass' THEN why ELSE 'failed_intent_invalid:'||why END);
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION zabalack.capture_ack() FROM PUBLIC;

-- project(): every 002 check kept; the one that read the staged intent is
-- replaced by the fence. The intent is retired on the path that reaches it.
CREATE OR REPLACE FUNCTION zabalack.project(p_action uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE a zabalack.actions; attrs jsonb; target_customer text; why text; display_state text; local_at timestamptz;
 alert_present boolean; newest uuid;
BEGIN
 SELECT * INTO STRICT a FROM zabalack.actions WHERE action_id=p_action FOR UPDATE;
 -- The generation lock, then the alert row: the order stage_generation() takes
 -- them, so a staging and a projection of one alert serialise rather than deadlock.
 PERFORM pg_advisory_xact_lock(hashtextextended('zabalack.generation:'||a.alert_id,0));
 -- UPDATE(column) does not give SELECT(other columns); this list matches explicit grants.
 SELECT attributes,customer INTO attrs,target_customer FROM public.alerts WHERE id=a.alert_id FOR UPDATE;
 alert_present:=FOUND;
 newest:=zabalack.newest_generation(a.alert_id);
 IF attrs->>'zabbix_ack_sync_local_at' IS NOT NULL THEN
  -- API-writable: any value the cast rejects refuses, as 002 refused the
  -- malformed formats it named, rather than raising out of the projection.
  BEGIN local_at:=(attrs->>'zabbix_ack_sync_local_at')::timestamptz;
  EXCEPTION WHEN OTHERS THEN local_at:='infinity'::timestamptz; END;
 END IF;
 IF NOT alert_present THEN why := 'projection_target_missing';
 ELSIF local_at>a.issued_at THEN why := 'projection_local_generation_mismatch';
 ELSIF target_customer IS DISTINCT FROM a.customer THEN why := 'projection_customer_mismatch';
 ELSIF attrs->>'zabbix_instance' IS DISTINCT FROM a.instance OR attrs->>'zabbix_event_id' IS DISTINCT FROM a.event_id
  OR attrs->>'zabbix_trigger_id' IS DISTINCT FROM a.trigger_id THEN why := 'projection_source_generation_mismatch';
 -- 002's capture_order comparison is not kept: the fence is the one ordering.
 -- Every action row follows a capture whose intent validated, and that capture
 -- (or the backfill) recorded its generation, so the fence sees everything
 -- capture_order saw; and under two concurrent acknowledgements the two orders
 -- can disagree — B staged after A but captured first — and refuse both forever.
 ELSIF newest IS NULL THEN
  -- No generation recorded anywhere. Migration records one for every alert
  -- with an action row or a staged intent, so this is a row whose generation
  -- evidence never existed; refuse rather than let an arbitrarily old
  -- projection through.
  why := 'projection_generation_unknown';
 ELSIF newest<>a.action_id THEN why := 'projection_action_generation_mismatch';
 ELSE
  display_state := CASE WHEN a.state IN ('ok','failed','expired','skipped') THEN a.state ELSE 'pending' END;
  -- Caller cannot supply a patch; every value is derived from the authoritative row.
  UPDATE public.alerts SET attributes=(coalesce(attributes,'{}'::jsonb)-'zabbix_ack_sync_intent')||jsonb_build_object(
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

-- worker_call(): 006's body with one dispatch branch, so retirement runs through
-- the same coordination boundary as every other worker operation.
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
 ELSIF op='retire_intents' THEN
  -- Asked every reconciler cycle. The installation row decides whether it is
  -- due, so one worker per interval scans public.alerts however many are running.
  IF NOT zabalack.claim_retirement_slot() THEN RETURN jsonb_build_object('examined',0,'retired',0,'deferred',0,'skipped','not_due'); END IF;
  result := zabalack.retire_intents(coalesce((p->>'limit')::int,200),coalesce((p->>'grace_seconds')::int,300));
  -- A batch that reached its limit may have left more behind, and a batch
  -- that had to leave a locked row to the next pass certainly did. The slot
  -- then stays due, so the next cycle — this worker's or another's — continues
  -- draining at the fast cadence rather than waiting out the interval: a
  -- backlog is bounded by the worker rate, never by the interval times the
  -- batch size, and a row held during one pass waits one cycle, not one
  -- interval. Only a batch below the limit that deferred nothing closes the
  -- slot for the interval.
  IF (result->>'examined')::int>=least(1000,greatest(1,coalesce((p->>'limit')::int,200)))
     OR coalesce((result->>'deferred')::int,0)>0 THEN
   UPDATE zabalack.installation SET retirement_due_at=now_at WHERE singleton;
   result := result||jsonb_build_object('continues',true);
  ELSIF coalesce((p->>'report_remaining')::boolean,false) THEN
   -- The standalone drain asks: a pass that left nothing due behind it is a
   -- cleanup only while no row still carries the key. A staging inside its
   -- window and grace is left to a later pass by design, and the adapter
   -- that keeps the key from readers must outlive it; the count says whether
   -- it may go. The worker's periodic call does not ask and pays no second scan.
   result := result||jsonb_build_object('remaining',
    (SELECT count(*) FROM public.alerts WHERE attributes ? 'zabbix_ack_sync_intent'));
   -- A count above zero is rows inside their windows, due within the window's
   -- bound (60 seconds) plus the grace this caller applies. The slot is
   -- brought forward to then, so the retry the rollback documents is not
   -- refused as not due by the interval this pass claimed; the periodic call,
   -- which does not ask, keeps the interval. The bound is taken from the
   -- clock after the count, not from this call's entry: a staging committed
   -- while the pass was in flight is counted, and its window runs from its
   -- own issue, later than the entry — a bound from the entry would schedule
   -- the retry before that row can be due, and the retry would find it still
   -- inside its window.
   IF (result->>'remaining')::int>0 THEN
    UPDATE zabalack.installation SET retirement_due_at=least(retirement_due_at,
     clock_timestamp()+interval '60 seconds'+make_interval(secs=>least(86400,greatest(0,coalesce((p->>'grace_seconds')::int,300)))))
     WHERE singleton;
    result := result||jsonb_build_object('slot_due_at',
     (SELECT retirement_due_at FROM zabalack.installation WHERE singleton));
   END IF;
  END IF;
  RETURN result;
 ELSE RAISE EXCEPTION 'unsupported worker operation: %',op;
 END IF;
END $$;
REVOKE ALL ON FUNCTION zabalack.worker_call(text,jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zabalack.worker_call(text,jsonb) TO zabalack_worker;

-- capture_health(): 005's body with two conjuncts on functions_ok.
CREATE OR REPLACE FUNCTION zabalack.capture_health() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
 SELECT jsonb_build_object('in_recovery',pg_is_in_recovery(),'database_name',current_database(),
 'deployment_id',i.deployment_id,'zabalack_schema_version',i.schema_version,'config_revision',i.config_revision,
 'capture_disabled',i.capture_disabled,'external_writes_enabled',i.external_writes_enabled,
 'trigger_ok',EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid='public.alerts'::regclass
  AND t.tgname='zabalack_capture' AND t.tgfoid='zabalack.capture_ack()'::regprocedure
  AND t.tgtype=17 AND t.tgenabled='O' AND NOT t.tgisinternal AND md5(pg_get_triggerdef(t.oid))=m.trigger_md5),
 'functions_ok',m.functions_md5=h.hashes->>'functions' AND m.structures_md5=h.hashes->>'structures'
  AND NOT has_table_privilege('zabalack_worker','public.alerts','UPDATE')
  AND NOT has_column_privilege('zabalack_worker','public.alerts','attributes','UPDATE')
  AND NOT has_column_privilege('zabalack_owner','public.alerts','status','UPDATE')
  AND NOT has_column_privilege('zabalack_owner','public.alerts','history','UPDATE')
  AND NOT has_column_privilege('zabalack_owner','public.alerts','severity','UPDATE')
  AND NOT has_column_privilege('zabalack_owner','public.alerts','customer','UPDATE')
  AND NOT has_table_privilege('zabalack_owner','public.alerts','TRIGGER')
  AND NOT has_table_privilege('zabalack_probe','public.alerts','SELECT')
  AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member='zabalack_probe'::regrole)
  -- USAGE on the schema is granted to the application role for one function.
  -- That confers nothing only while no function here is executable by PUBLIC,
  -- and while nothing but the owner can touch the generation fence.
  AND NOT EXISTS(SELECT 1 FROM pg_proc p WHERE p.pronamespace='zabalack'::regnamespace
   AND (p.proacl IS NULL OR EXISTS(SELECT 1 FROM aclexplode(p.proacl) e WHERE e.grantee=0)))
  AND NOT EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) e
   WHERE c.oid='zabalack.alert_generations'::regclass AND e.grantee<>c.relowner)
  -- The staging entry point has exactly one non-owner grantee: the application
  -- role the grant recorded. A former application role, or any other, must not
  -- keep it; and a tree whose grant was revoked, or whose role was dropped, is
  -- not healthy either — every eligible acknowledgement would fail behind a
  -- passing probe.
  AND i.application_role IS NOT NULL
  AND (SELECT count(*) FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
   WHERE p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure AND e.grantee<>p.proowner)=1
  AND EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
   WHERE p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure AND e.grantee<>p.proowner
    AND e.grantee<>0 AND pg_get_userbyid(e.grantee)=i.application_role)
  -- ...and that role holds USAGE on the schema, without which its fully
  -- qualified call fails at schema access behind a passing probe.
  AND EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname=i.application_role
   AND has_schema_privilege(r.oid,'zabalack','USAGE')),
 'checked_at',clock_timestamp()) FROM zabalack.installation i LEFT JOIN zabalack.readiness_manifest m USING(singleton)
 CROSS JOIN LATERAL (SELECT zabalack.catalog_fingerprints() hashes) h WHERE i.singleton;
$$;
REVOKE ALL ON FUNCTION zabalack.capture_health() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zabalack.capture_health() TO zabalack_probe,zabalack_operator;
-- The trigger fires capture_ack() as the alerts owner; keep that grant explicit.
DO $$ DECLARE principal name; BEGIN
 SELECT r.rolname INTO STRICT principal FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner WHERE c.oid='public.alerts'::regclass;
 EXECUTE format('GRANT EXECUTE ON FUNCTION zabalack.capture_ack() TO %I',principal);
END $$;

-- Backfill, once. Every capture that produced an action or may still produce
-- one — verified, or awaiting verification in the outbox (captured_unverified,
-- verifier_hold) — in capture order, assigned by the database at capture, the
-- best pre-fence order there is. A capture awaiting verification whose live
-- intent has since left its row has no other record; left out, an older
-- verified acknowledgement would be the newest generation and the newer one,
-- once verified, refused. Its place is taken here, in that order; verification
-- appends nothing later. A capture that failed, was rejected or expired never
-- becomes an action and records nothing. Then the intent currently on each
-- row, recorded as newest: it is the only evidence an uncaptured staging has,
-- and one that already captured is a no-op. All are observation-tier rows, so
-- the first staging through stage_generation() outranks them.
DO $$
DECLARE row record;
BEGIN
 FOR row IN
  SELECT c.alert_id,a.action_id,c.intent->>'action_id' AS intent_action
  FROM zabalack.captures c
  LEFT JOIN zabalack.actions a ON a.capture_id=c.capture_id
  LEFT JOIN zabalack.outbox o ON o.capture_id=c.capture_id
  WHERE a.action_id IS NOT NULL
   OR (o.state IN ('captured_unverified','verifier_hold') AND jsonb_typeof(c.intent)='object'
       AND c.intent->>'alert_id'=c.alert_id AND c.intent->>'action_id' IS NOT NULL)
  ORDER BY c.capture_order
 LOOP
  BEGIN
   INSERT INTO zabalack.alert_generations(alert_id,action_id,recorded_by)
    VALUES(row.alert_id,coalesce(row.action_id,row.intent_action::uuid),'migration') ON CONFLICT(action_id) DO NOTHING;
  -- A capture whose action id the cast rejects can never verify; it records
  -- nothing and never aborts the upgrade.
  EXCEPTION WHEN OTHERS THEN NULL;
  END;
 END LOOP;
 FOR row IN SELECT id,attributes->'zabbix_ack_sync_intent' AS intent FROM public.alerts
   WHERE attributes ? 'zabbix_ack_sync_intent' LOOP
  BEGIN
   IF jsonb_typeof(row.intent)='object' AND row.intent->>'alert_id'=row.id AND row.intent->>'action_id' IS NOT NULL THEN
    INSERT INTO zabalack.alert_generations(alert_id,action_id,recorded_by) VALUES(row.id,(row.intent->>'action_id')::uuid,'migration')
     ON CONFLICT(action_id) DO NOTHING;
   END IF;
  -- A malformed intent on a live row is not the migration's to judge; it is
  -- skipped here and retired by the sweep, never allowed to abort the upgrade.
  EXCEPTION WHEN OTHERS THEN NULL;
  END;
 END LOOP;
END $$;

UPDATE zabalack.installation SET schema_version=4 WHERE singleton;
