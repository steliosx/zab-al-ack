CREATE FUNCTION zabalack.capture_ack() RETURNS trigger
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

CREATE FUNCTION zabalack.capture_health() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
 SELECT jsonb_build_object('in_recovery',pg_is_in_recovery(),'database_name',current_database(),
 'deployment_id',i.deployment_id,'zabalack_schema_version',i.schema_version,'config_revision',i.config_revision,
 'capture_disabled',i.capture_disabled,'external_writes_enabled',i.external_writes_enabled,
 'trigger_ok',EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid='public.alerts'::regclass
  AND t.tgname='zabalack_capture' AND t.tgfoid='zabalack.capture_ack()'::regprocedure
  AND t.tgtype=17 AND t.tgenabled='O' AND NOT t.tgisinternal),
 'functions_ok',EXISTS(SELECT 1 FROM pg_proc p WHERE p.oid='zabalack.capture_ack()'::regprocedure
   AND p.prosecdef AND p.proowner='zabalack_owner'::regrole)
  AND to_regprocedure('zabalack.worker_call(text,jsonb)') IS NOT NULL
  AND to_regprocedure('zabalack.project(uuid)') IS NOT NULL,
 'checked_at',clock_timestamp()) FROM zabalack.installation i WHERE singleton;
$$;
REVOKE ALL ON FUNCTION zabalack.capture_health() FROM PUBLIC;

CREATE FUNCTION zabalack.cohort_json(c zabalack.cohorts) RETURNS jsonb LANGUAGE sql IMMUTABLE
SET search_path=pg_catalog,zabalack AS $$ SELECT jsonb_build_object(
 'customer',c.customer,'zabbix_instance',c.instance,'zabbix_trigger_id',c.trigger_id,
 'revision',c.revision,'policy_revision',c.policy_version,'host_ids',c.host_ids,'group_ids',c.group_ids,
 'enabled',c.enabled,'one_to_one',true,'comment_enabled',c.allow_comment,'allow_admin',c.admin_exception); $$;
REVOKE ALL ON FUNCTION zabalack.cohort_json(zabalack.cohorts) FROM PUBLIC;

CREATE FUNCTION zabalack.action_json(a zabalack.actions) RETURNS jsonb LANGUAGE sql STABLE
SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
 SELECT (to_jsonb(a)-'policy_version'-'execute_before') || jsonb_build_object(
 'source',jsonb_build_object('zabbix_instance',a.instance,'zabbix_event_id',a.event_id,'zabbix_trigger_id',a.trigger_id),
 'policy_revision',a.policy_version,'execution_expires_at',a.execute_before,
 'cohort',(SELECT zabalack.cohort_json(c) FROM zabalack.cohorts c WHERE c.customer=a.customer AND c.instance=a.instance AND c.trigger_id=a.trigger_id));
$$;
REVOKE ALL ON FUNCTION zabalack.action_json(zabalack.actions) FROM PUBLIC;

CREATE FUNCTION zabalack.project(p_action uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE a zabalack.actions; attrs jsonb; target_customer text; why text; display_state text; local_at timestamptz;
BEGIN
 SELECT * INTO STRICT a FROM zabalack.actions WHERE action_id=p_action FOR UPDATE;
 -- UPDATE(column) does not give SELECT(other columns); this list matches explicit grants.
 SELECT attributes,customer INTO attrs,target_customer FROM public.alerts WHERE id=a.alert_id FOR UPDATE;
 IF attrs->>'zabbix_ack_sync_local_at' IS NOT NULL THEN
  BEGIN local_at:=(attrs->>'zabbix_ack_sync_local_at')::timestamptz;
  EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN local_at:='infinity'::timestamptz; END;
 END IF;
 IF NOT FOUND THEN why := 'projection_target_missing';
 ELSIF local_at>a.issued_at THEN why := 'projection_local_generation_mismatch';
 ELSIF target_customer IS DISTINCT FROM a.customer THEN why := 'projection_customer_mismatch';
 ELSIF attrs->>'zabbix_instance' IS DISTINCT FROM a.instance OR attrs->>'zabbix_event_id' IS DISTINCT FROM a.event_id
  OR attrs->>'zabbix_trigger_id' IS DISTINCT FROM a.trigger_id THEN why := 'projection_source_generation_mismatch';
 ELSIF EXISTS(SELECT 1 FROM zabalack.actions newer WHERE newer.alert_id=a.alert_id AND newer.capture_order>a.capture_order) THEN
  why := 'projection_action_generation_mismatch';
 ELSIF attrs#>>'{zabbix_ack_sync_intent,action_id}' IS NOT NULL
  AND attrs#>>'{zabbix_ack_sync_intent,action_id}'<>a.action_id::text THEN why := 'projection_action_generation_mismatch';
 ELSE
  display_state := CASE WHEN a.state IN ('ok','failed','expired','skipped') THEN a.state ELSE 'pending' END;
  -- Caller cannot supply a patch; every value is derived from the authoritative row.
  UPDATE public.alerts SET attributes=coalesce(attributes,'{}'::jsonb)||jsonb_build_object(
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
