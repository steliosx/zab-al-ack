-- F1 source binding at ingestion (docs/DESIGN_0.2.0_F1_SOURCE_BINDING.md r5).
-- Schema 4 -> 5. Forward-only; shipped migration bytes stay immutable.
--
-- Zabalack took the Zabbix source of an acknowledgement from the alert's
-- mutable attributes. Any principal with write access to an alert could change
-- zabbix_event_id / zabbix_trigger_id through PUT /alert/<id>/attributes and
-- then acknowledge, and Zabalack would deliver a validly signed acknowledgement
-- to a Zabbix event the alert never represented. From this migration an
-- acknowledgement is delivered only to a source that Zabalack's own
-- authenticated POST /alert ingestion recorded for the alert's exact current
-- ingestion epoch (last_receive_id, last_receive_time), and only while that
-- record is the alert's latest recorded binding (design 3.1). Attribute edits
-- alone can never select a source.
--
-- What it adds: zabalack.source_bindings and zabalack.binding_events, written
-- only by bind_source() and read only by read_binding(), both granted to the
-- Alerta application role by sql/install/12_application_grant.sql; a body-only
-- change to capture_ack() that refuses a capture whose intent source is not
-- the selected binding; capture_health() extended to assert the new routines'
-- grant map and the binding order; schema 5, so a plugin or worker that
-- predates this migration fails closed instead of mixing models. The
-- zabalack_capture trigger definition is unchanged.
--
-- Eligibility (Voalack/Astra review, 1 November 2026): Zabalack treats as its
-- own exactly the alerts that declare attributes.source_system = 'zabbix' AND
-- match an enabled zabalack.cohorts row (customer, zabbix_instance,
-- zabbix_trigger_id). It does not decide ownership by event or resource. Any
-- other alert returns from capture_ack() before any row is written, before and
-- after this migration. An alert that meets both conditions is not passed
-- through, whoever else considers it theirs; for it, nothing is delivered
-- unless it holds a usable binding written by Zabalack's authenticated
-- ingestion path and a valid Zabalack-signed intent and marker. Without them
-- its acknowledgement is recorded as a failed capture with no generation, no
-- verification admission and no Zabbix call. Overlap between that eligibility
-- and another owner's rule is a configuration conflict for the single-owner
-- rule of ack-core 3b; this migration does not resolve it.
--
-- Residual, accepted by the owner for 0.2.0 (F1-D1, 12 October 2026): the
-- latest recorded binding is not always the latest authenticated ingestion in
-- commit order; see design 3.1. Window, accepted with no backfill (F1-D2):
-- alerts ingested before this migration, and alerts ingested only through a
-- webhook, have no binding and are not acknowledgeable through Zabalack until
-- the next authenticated POST /alert ingestion.
DO $$
DECLARE installed integer;
BEGIN
 SELECT schema_version INTO STRICT installed FROM zabalack.installation WHERE singleton;
 IF installed<>4 THEN
  RAISE EXCEPTION '015 requires schema 4; found schema %',installed;
 END IF;
END $$;

-- Do not bless existing routine drift or compare a stale pre-upgrade seal, for
-- the reason 009 to 014 give: only the first migration of a pass may
-- compare against the seal 13_reseal.sql replaces afterwards.
DO $$
DECLARE recorded text; live jsonb;
BEGIN
 live:=zabalack.catalog_fingerprints();
 SELECT routine_bodies_md5 INTO recorded FROM zabalack.installation WHERE singleton;
 IF recorded IS NULL OR recorded<>live->>'routines' THEN
  RAISE EXCEPTION 'routine body drift before 015; restore the shipped routines before migrating';
 END IF;
END $$;

-- One row per recorded binding. The key is the ingestion epoch; the values are
-- never changed after insert. bind_seq is the order in which bindings were
-- recorded, assigned by the database and never by a caller: an identity column
-- (capture_health() refuses a sequence that generates for no column) that
-- counts upward by one without caching, for the reason generation_order_intact()
-- gives — above one, each backend draws its own block and a later binding can
-- take a number beneath an earlier one. bind_source() serialises the binds of
-- one alert, so for an alert the numbers follow the order of recording.
CREATE TABLE zabalack.source_bindings(
 alert_id text NOT NULL CHECK(length(alert_id) BETWEEN 1 AND 128),
 last_receive_id text NOT NULL CHECK(length(last_receive_id) BETWEEN 1 AND 128),
 last_receive_time timestamp without time zone NOT NULL,
 customer text NOT NULL CHECK(length(customer) BETWEEN 1 AND 128),
 zabbix_instance text NOT NULL CHECK(length(zabbix_instance) BETWEEN 1 AND 128),
 zabbix_event_id text NOT NULL CHECK(length(zabbix_event_id) BETWEEN 1 AND 20),
 zabbix_trigger_id text NOT NULL CHECK(length(zabbix_trigger_id) BETWEEN 1 AND 128),
 state text NOT NULL CHECK(state IN ('bound','poisoned')),
 bind_seq bigint GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1 MINVALUE 1 NO MAXVALUE CACHE 1 NO CYCLE) UNIQUE,
 bound_at timestamptz NOT NULL,
 PRIMARY KEY(alert_id,last_receive_id,last_receive_time)
);
CREATE INDEX source_bindings_alert_seq ON zabalack.source_bindings(alert_id,bind_seq DESC);
REVOKE ALL ON zabalack.source_bindings FROM PUBLIC;

-- What happened at each bind, as a fixed reason code. No source text, no SQL,
-- no attribute values.
CREATE TABLE zabalack.binding_events(
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 alert_id text NOT NULL,
 last_receive_id text NOT NULL,
 last_receive_time timestamp without time zone NOT NULL,
 reason text NOT NULL CHECK(reason IN ('bound','idempotent_repeat','conflict_poisoned')),
 recorded_at timestamptz NOT NULL
);
REVOKE ALL ON zabalack.binding_events FROM PUBLIC;

-- Whether the sequence behind bind_seq still counts upward from where it
-- stands; the selection rule reads bind_seq DESC, so the whole ordering rests
-- on it, as generation_order_intact() explains for the generation fence.
CREATE FUNCTION zabalack.binding_order_intact() RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
DECLARE generator regclass; highest bigint; standing bigint;
BEGIN
 BEGIN
  generator:=pg_get_serial_sequence('zabalack.source_bindings','bind_seq')::regclass;
  IF generator IS NULL THEN
   RETURN false;
  END IF;
  IF NOT EXISTS(SELECT 1 FROM pg_sequence s
    WHERE s.seqrelid=generator
     AND s.seqincrement=1 AND s.seqstart=1 AND s.seqmin=1
     AND s.seqmax=9223372036854775807 AND NOT s.seqcycle AND s.seqcache=1) THEN
   RETURN false;
  END IF;
  SELECT max(b.bind_seq) INTO highest FROM zabalack.source_bindings b;
  standing:=pg_sequence_last_value(generator);
 EXCEPTION WHEN insufficient_privilege THEN RETURN false;
 END;
 RETURN coalesce(standing,0)>=coalesce(highest,0);
END $$;
REVOKE ALL ON FUNCTION zabalack.binding_order_intact() FROM PUBLIC;

-- The selection rule of design 3.1 for one alert and one exact epoch. Internal:
-- read_binding() answers the application with it and capture_ack() applies it
-- inside the signed UPDATE. A binding row is usable only when it matches the
-- epoch exactly, is 'bound', and its bind_seq is greater than that of every
-- other row for the alert, bound or poisoned. No row is a safe negative and
-- never an invitation to read the mutable attributes instead.
CREATE FUNCTION zabalack.select_binding(p_alert text, p_receive_id text, p_receive_time timestamp without time zone)
RETURNS jsonb LANGUAGE plpgsql STABLE SET search_path=pg_catalog,zabalack AS $$
DECLARE b zabalack.source_bindings; latest bigint;
BEGIN
 IF p_alert IS NULL OR p_receive_id IS NULL OR p_receive_time IS NULL THEN
  RETURN jsonb_build_object('outcome','source_binding_missing');
 END IF;
 SELECT * INTO b FROM zabalack.source_bindings
  WHERE alert_id=p_alert AND last_receive_id=p_receive_id AND last_receive_time=p_receive_time;
 IF NOT FOUND THEN RETURN jsonb_build_object('outcome','source_binding_missing'); END IF;
 IF b.state<>'bound' THEN RETURN jsonb_build_object('outcome','source_binding_conflict'); END IF;
 SELECT max(bind_seq) INTO latest FROM zabalack.source_bindings WHERE alert_id=p_alert;
 IF latest IS DISTINCT FROM b.bind_seq THEN RETURN jsonb_build_object('outcome','source_binding_not_latest'); END IF;
 RETURN jsonb_build_object('outcome','usable','customer',b.customer,
  'zabbix_instance',b.zabbix_instance,'zabbix_event_id',b.zabbix_event_id,'zabbix_trigger_id',b.zabbix_trigger_id);
END $$;
REVOKE ALL ON FUNCTION zabalack.select_binding(text,text,timestamp without time zone) FROM PUBLIC;

-- Write a binding for one authenticated POST /alert ingestion. Called once per
-- ingestion by ZabbixNormaliser.post_receive with values taken from the alert
-- object the ingestion statement returned; the caller commits. It reads and
-- locks nothing in public.alerts: the epoch is checked at use, not here, so a
-- bind whose epoch is no longer current records a row that never matches it.
-- Same key and same values: an idempotent repeat that changes nothing, a
-- poisoned row included. Same key and different values: the original values
-- stay and the row is poisoned, which fails every check for that epoch. The
-- only update ever made is state bound -> poisoned.
CREATE FUNCTION zabalack.bind_source(p_alert text, p_receive_id text, p_receive_time timestamp without time zone,
 p_customer text, p_instance text, p_event_id text, p_trigger_id text) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE existing zabalack.source_bindings; event_id text; outcome text; inserted bigint;
BEGIN
 event_id:=left(p_event_id,20);
 IF p_alert IS NULL OR length(p_alert) NOT BETWEEN 1 AND 128
   OR p_receive_id IS NULL OR length(p_receive_id) NOT BETWEEN 1 AND 128 OR p_receive_time IS NULL
   OR p_customer IS NULL OR length(p_customer) NOT BETWEEN 1 AND 128
   OR p_instance IS NULL OR length(p_instance) NOT BETWEEN 1 AND 128
   OR event_id IS NULL OR length(event_id)<1
   OR p_trigger_id IS NULL OR length(p_trigger_id) NOT BETWEEN 1 AND 128 THEN
  RAISE EXCEPTION 'binding identity required';
 END IF;
 -- Serialise the binds of one alert, so bind_seq follows the order of recording.
 PERFORM pg_advisory_xact_lock(hashtextextended('zabalack.binding:'||p_alert,0));
 INSERT INTO zabalack.source_bindings(alert_id,last_receive_id,last_receive_time,customer,
  zabbix_instance,zabbix_event_id,zabbix_trigger_id,state,bound_at)
  VALUES(p_alert,p_receive_id,p_receive_time,p_customer,p_instance,event_id,p_trigger_id,'bound',clock_timestamp())
  ON CONFLICT(alert_id,last_receive_id,last_receive_time) DO NOTHING RETURNING bind_seq INTO inserted;
 IF inserted IS NOT NULL THEN
  outcome:='bound';
 ELSE
  SELECT * INTO STRICT existing FROM zabalack.source_bindings
   WHERE alert_id=p_alert AND last_receive_id=p_receive_id AND last_receive_time=p_receive_time FOR UPDATE;
  IF existing.customer=p_customer AND existing.zabbix_instance=p_instance
    AND existing.zabbix_event_id=event_id AND existing.zabbix_trigger_id=p_trigger_id THEN
   outcome:='idempotent_repeat';
  ELSE
   UPDATE zabalack.source_bindings SET state='poisoned'
    WHERE alert_id=p_alert AND last_receive_id=p_receive_id AND last_receive_time=p_receive_time;
   outcome:='conflict_poisoned';
  END IF;
 END IF;
 INSERT INTO zabalack.binding_events(alert_id,last_receive_id,last_receive_time,reason,recorded_at)
  VALUES(p_alert,p_receive_id,p_receive_time,outcome,clock_timestamp());
 RETURN outcome;
END $$;
REVOKE ALL ON FUNCTION zabalack.bind_source(text,text,timestamp without time zone,text,text,text,text) FROM PUBLIC;

-- Read the binding the selection rule selects for an alert's exact epoch. The
-- plugin calls it before staging and takes the intent's source from it, never
-- from attributes. Source keys are returned only when usable.
CREATE FUNCTION zabalack.read_binding(p_alert text, p_receive_id text, p_receive_time timestamp without time zone)
RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
 SELECT zabalack.select_binding(p_alert,p_receive_id,p_receive_time);
$$;
REVOKE ALL ON FUNCTION zabalack.read_binding(text,text,timestamp without time zone) FROM PUBLIC;
-- EXECUTE on bind_source() and read_binding() is granted to the application
-- role by sql/install/12_application_grant.sql, as for stage_generation().

-- capture_ack(): 008's body with the F1 binding check added last in the
-- reason chain. The zabalack_capture trigger definition is unchanged.
CREATE OR REPLACE FUNCTION zabalack.capture_ack() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,zabalack SET timezone='UTC' AS $$
DECLARE
 i jsonb; s jsonb; m text; tail text; matches text[]; why text; intent_reason text; cid uuid;
 disabled boolean; eligible boolean; bound jsonb;
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
 ELSE
  -- 015 (F1): the intent's source must be the binding the selection rule
  -- selects for this row's exact ingestion epoch, recorded by Zabalack's
  -- authenticated ingestion. Applied only after both early returns above, the
  -- capture bypass and every existing marker and intent check, so an alert
  -- outside Zabalack's eligibility never reaches it and an existing reason
  -- keeps its place. No usable binding is a failure, never a fallback to the
  -- mutable attributes.
  bound := zabalack.select_binding(NEW.id,NEW.last_receive_id,NEW.last_receive_time);
  IF bound->>'outcome' IS DISTINCT FROM 'usable' THEN why := bound->>'outcome';
  ELSIF bound->>'customer' IS DISTINCT FROM NEW.customer
    OR i->'source' IS DISTINCT FROM jsonb_build_object('zabbix_instance',bound->>'zabbix_instance',
     'zabbix_event_id',bound->>'zabbix_event_id','zabbix_trigger_id',bound->>'zabbix_trigger_id')
    THEN why := 'source_binding_mismatch';
  END IF;
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

-- capture_health(): 008's body extended for 015. The application role's grant
-- map now names three entry points, and binding_order_intact() and the
-- routine-body digest are asserted at schema 5.
CREATE OR REPLACE FUNCTION zabalack.capture_health() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
 SELECT jsonb_build_object('in_recovery',pg_is_in_recovery(),'database_name',current_database(),
 'deployment_id',i.deployment_id,'zabalack_schema_version',i.schema_version,'config_revision',i.config_revision,
 'capture_disabled',i.capture_disabled,'external_writes_enabled',i.external_writes_enabled,
 'trigger_ok',EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid='public.alerts'::regclass
  AND t.tgname='zabalack_capture' AND t.tgfoid='zabalack.capture_ack()'::regprocedure
  AND t.tgtype=17 AND t.tgenabled='O' AND NOT t.tgisinternal AND md5(pg_get_triggerdef(t.oid))=m.trigger_md5)
  -- ...and it is the only one. PostgreSQL dispatches a trigger by its
  -- definition, not by its name, so a second trigger on public.alerts calling
  -- capture_ack() fires alongside this one and every acknowledgement records
  -- two captures and two outbox rows. Rename the installed trigger and
  -- 10_trigger.sql creates a replacement under the expected name, leaving both:
  -- the sealed fingerprint names one trigger and matches it, and the digest
  -- names it too, so nothing here saw a second before this counted them.
  AND (SELECT count(*) FROM pg_trigger t WHERE t.tgrelid='public.alerts'::regclass
   AND NOT t.tgisinternal AND t.tgfoid='zabalack.capture_ack()'::regprocedure
   AND t.tgenabled<>'D')=1,
 'functions_ok',m.functions_md5=h.hashes->>'functions' AND m.structures_md5=h.hashes->>'structures'
  AND NOT has_table_privilege('zabalack_worker','public.alerts','UPDATE')
  AND NOT has_column_privilege('zabalack_worker','public.alerts','attributes','UPDATE')
  AND NOT has_column_privilege('zabalack_owner','public.alerts','status','UPDATE')
  AND NOT has_column_privilege('zabalack_owner','public.alerts','history','UPDATE')
  AND NOT has_column_privilege('zabalack_owner','public.alerts','severity','UPDATE')
  AND NOT has_column_privilege('zabalack_owner','public.alerts','customer','UPDATE')
  AND NOT has_table_privilege('zabalack_owner','public.alerts','TRIGGER')
  AND NOT has_table_privilege('zabalack_probe','public.alerts','SELECT')
  -- ...and the three runtime roles can still open a session at all. Neither
  -- CONNECT nor a connection limit is an ACL this schema hashes and nothing
  -- else here looks at either: take one away and capture_health() answers
  -- normally through the probe's existing session while the worker never
  -- connects again, so verification and delivery stop behind a probe that
  -- passes. A limit of zero refuses every new session as flatly as a revoked
  -- CONNECT does, which is why both are asked, as they are of the application
  -- role. LOGIN itself is covered by the declared-attributes rule below.
  -- zabalack_owner is NOLOGIN and connects as nobody; the migration principal
  -- assumes it after connecting as itself.
  AND NOT EXISTS(SELECT 1 FROM pg_roles r
   WHERE r.rolname IN ('zabalack_worker','zabalack_probe','zabalack_operator')
    AND (r.rolconnlimit=0 OR NOT has_database_privilege(r.oid,current_database(),'CONNECT')))
  -- ...and hold nothing else on the database. That check is positive only, so
  -- a runtime role granted CREATE on the database — arbitrary schemas and
  -- objects — still passed it, and so did a grantable CONNECT, which delegates
  -- admission to the database. Database ACLs are in no fingerprint. Read as
  -- entries again: a zabalack role's own entry here may be CONNECT and must
  -- not be grantable. A null datacl carries no entry for anyone and leaves
  -- CONNECT to PUBLIC's default, which this does not disturb.
  AND NOT EXISTS(SELECT 1 FROM pg_database d CROSS JOIN LATERAL aclexplode(d.datacl) e
   WHERE d.datname=current_database() AND d.datacl IS NOT NULL
    AND ((pg_get_userbyid(e.grantee) IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')
          AND (e.privilege_type<>'CONNECT' OR e.is_grantable))
      -- PUBLIC again: CREATE granted to it lets every identity in the database
      -- make schemas and objects, and no name matches grantee zero. What it may
      -- hold is what a database is created with — CONNECT and TEMPORARY, neither
      -- grantable — which is also what a null datacl means.
      OR (e.grantee=0
          AND (e.privilege_type NOT IN ('CONNECT','TEMPORARY') OR e.is_grantable))))
  -- ...and zabalack_owner still holds the grants 00_roles.sql makes it on
  -- public.alerts, exactly those and no more. Every rule above about this table
  -- is a prohibition, and a prohibition cannot see a grant that is *missing*.
  -- Revoke UPDATE(attributes) and stage_generation(), SECURITY DEFINER and
  -- running as this owner, fails with "permission denied for table alerts": every
  -- eligible acknowledgement is refused with zabalack_generation_unavailable,
  -- and retirement and projection lose their access too. public.alerts is
  -- outside the schema catalog_fingerprints() hashes, so no digest moves either
  -- — not even before a re-seal. This is the only thing that sees it.
  AND has_column_privilege('zabalack_owner','public.alerts','id','SELECT')
  AND has_column_privilege('zabalack_owner','public.alerts','customer','SELECT')
  AND has_column_privilege('zabalack_owner','public.alerts','status','SELECT')
  AND has_column_privilege('zabalack_owner','public.alerts','attributes','SELECT')
  AND has_column_privilege('zabalack_owner','public.alerts','update_time','SELECT')
  AND has_column_privilege('zabalack_owner','public.alerts','attributes','UPDATE')
  -- ...and nothing beyond them, stated as a map rather than as the four column
  -- prohibitions above: no SELECT on any other column, no UPDATE on any other,
  -- no INSERT or REFERENCES anywhere in the table, and none of DELETE, TRUNCATE
  -- or TRIGGER. The owner reads five columns and writes one.
  AND NOT EXISTS(SELECT 1 FROM pg_attribute a
   WHERE a.attrelid='public.alerts'::regclass AND a.attnum>0 AND NOT a.attisdropped
    AND ((a.attname<>ALL(ARRAY['id','customer','status','attributes','update_time'])
          AND has_column_privilege('zabalack_owner','public.alerts',a.attname,'SELECT'))
      OR (a.attname<>'attributes'
          AND has_column_privilege('zabalack_owner','public.alerts',a.attname,'UPDATE'))
      OR has_column_privilege('zabalack_owner','public.alerts',a.attname,'INSERT')
      OR has_column_privilege('zabalack_owner','public.alerts',a.attname,'REFERENCES')))
  AND NOT has_table_privilege('zabalack_owner','public.alerts','DELETE,TRUNCATE,TRIGGER')
  -- ...and the other three hold nothing on that table at all, stated as a map
  -- for the same reason. The worker's UPDATE and the probe's SELECT were named
  -- above one pair at a time, which left zabalack_operator unmentioned: a
  -- direct GRANT UPDATE(attributes) ON public.alerts TO zabalack_operator lets
  -- the operator credential rewrite the reserved sync keys and the source
  -- identity on any row without going through project() at all. public.alerts
  -- lies outside the schema catalog_fingerprints() hashes, so no digest moves,
  -- before a re-seal or after one. Only zabalack_owner holds anything here.
  -- ...read from the ACL entries themselves rather than from a list of
  -- privilege names, because such a list is a map with one row per name and
  -- goes stale: PostgreSQL 18's MAINTAIN — VACUUM, CLUSTER, REINDEX — was in
  -- neither the runtime-role list nor the owner's DELETE/TRUNCATE/TRIGGER one,
  -- so GRANT MAINTAIN ON public.alerts TO zabalack_operator read healthy and
  -- sealed. Enumerated, the rule needs no name at all: the four zabalack roles
  -- hold no table-level entry here, the three runtime roles hold no entry of
  -- any kind, and the owner's column entries are exactly the six 00_roles.sql
  -- makes it.
  -- PUBLIC is grantee zero and carries no role name, so a map scoped by name
  -- steps over it while every role in the database holds what it grants:
  -- GRANT SELECT(attributes) ON public.alerts TO PUBLIC hands the worker, the
  -- probe and the operator the live attributes and every staged intent in them.
  -- The installation grants PUBLIC nothing here, so the rule is none.
  AND NOT EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) e
   WHERE c.oid='public.alerts'::regclass AND c.relacl IS NOT NULL
    AND (e.grantee=0
      OR pg_get_userbyid(e.grantee) IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')))
  AND NOT EXISTS(SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) e
   WHERE a.attrelid='public.alerts'::regclass AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL
    AND (e.grantee=0
      OR pg_get_userbyid(e.grantee) IN ('zabalack_worker','zabalack_probe','zabalack_operator')
      OR (pg_get_userbyid(e.grantee)='zabalack_owner'
       AND NOT ((e.privilege_type='SELECT' AND a.attname IN ('id','customer','status','attributes','update_time'))
             OR (e.privilege_type='UPDATE' AND a.attname='attributes')))))
  AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member='zabalack_probe'::regrole)
  -- USAGE on the schema is granted to the application role for one function.
  -- That confers nothing only while no function here is executable by PUBLIC,
  -- and while nothing but the owner can touch the generation fence.
  AND NOT EXISTS(SELECT 1 FROM pg_proc p WHERE p.pronamespace='zabalack'::regnamespace
   AND (p.proacl IS NULL OR EXISTS(SELECT 1 FROM aclexplode(p.proacl) e WHERE e.grantee=0)))
  AND NOT EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) e
   WHERE c.oid IN ('zabalack.alert_generations'::regclass,'zabalack.source_bindings'::regclass,
                   'zabalack.binding_events'::regclass) AND e.grantee<>c.relowner)
  -- ...and while the sequence behind it still counts upward from where it
  -- stands, which no digest can see at all. See generation_order_intact().
  AND zabalack.generation_order_intact()
  AND zabalack.binding_order_intact()
  -- Row-level security filters what a role may see and write, and no ACL, no
  -- ownership and no digest records it. Enabled on public.alerts without a
  -- policy for zabalack_owner, stage_generation() — SECURITY DEFINER as that
  -- role, which does not own that table and holds no BYPASSRLS — writes
  -- nothing and every eligible acknowledgement answers 503, while the grant
  -- map, the ownership rules and both sealed digests read exactly right.
  -- Inside this schema the owner bypasses RLS, so only FORCE bites there, and
  -- it bites every routine here at once.
  AND NOT (SELECT c.relrowsecurity OR c.relforcerowsecurity FROM pg_class c
   WHERE c.oid='public.alerts'::regclass)
  AND NOT EXISTS(SELECT 1 FROM pg_class c WHERE c.relnamespace='zabalack'::regnamespace
   AND c.relkind IN ('r','p') AND (c.relrowsecurity OR c.relforcerowsecurity))
  -- ...and no sequence here is attached to nothing. Every sequence this schema
  -- ships is the identity generator of a column in it; one that generates for
  -- no column was created by hand, and its only use is to stand under a name a
  -- check might read. Refusing it closes that for all four, not just the fence's.
  AND NOT EXISTS(SELECT 1 FROM pg_class c
   WHERE c.relnamespace='zabalack'::regnamespace AND c.relkind='S'
    AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
     AND d.objid=c.oid AND d.refclassid='pg_class'::regclass AND d.deptype='i'))
  -- The staging entry point has exactly one non-owner grantee: the application
  -- role the grant recorded. A former application role, or any other, must not
  -- keep it; and a tree whose grant was revoked, or whose role was dropped, is
  -- not healthy either — every eligible acknowledgement would fail behind a
  -- passing probe.
  AND i.application_role IS NOT NULL
  -- 015: the same holds for bind_source() and read_binding(), the application
  -- role's two binding entry points: exactly one non-owner grantee each, the
  -- recorded application role.
  AND NOT EXISTS(SELECT 1 FROM unnest(ARRAY['zabalack.stage_generation(text,uuid,jsonb)'::regprocedure,
    'zabalack.bind_source(text,text,timestamp without time zone,text,text,text,text)'::regprocedure,
    'zabalack.read_binding(text,text,timestamp without time zone)'::regprocedure]) entry(proc)
   WHERE (SELECT count(*) FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
           WHERE p.oid=entry.proc AND e.grantee<>p.proowner)<>1
      OR NOT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
           WHERE p.oid=entry.proc AND e.grantee<>p.proowner
            AND e.grantee<>0 AND pg_get_userbyid(e.grantee)=i.application_role))
  -- ...and it can still open a session as itself. Alerta connects as this role
  -- and no member of it is permitted, so there is no other way in: a role
  -- altered to NOLOGIN, given a connection limit of zero, or stripped of
  -- CONNECT leaves every eligible acknowledgement refused with
  -- zabalack_generation_unavailable while every grant below still reads exactly
  -- right. None of these is an ACL this schema hashes. rolvaliduntil is not
  -- checked: it binds password authentication only, and a certificate-
  -- authenticated role outlives it.
  AND EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname=i.application_role
   AND r.rolcanlogin AND r.rolconnlimit<>0
   AND has_database_privilege(r.oid,current_database(),'CONNECT'))
  -- ...and that role holds USAGE on the schema, without which its fully
  -- qualified call fails at schema access behind a passing probe.
  AND EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname=i.application_role
   AND has_schema_privilege(r.oid,'zabalack','USAGE'))
  -- ...and no role is a member of it. A member inherits its privileges, or can
  -- SET ROLE to it, and is a second credential able to stage. A membership is
  -- not an ACL: neither the grantee count above nor the manifest digests move
  -- with it, so nothing else here would see it. A superuser member is not
  -- counted; a superuser executes this function whatever its ACL says, which
  -- no probe can change.
  AND NOT EXISTS(SELECT 1 FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid
   JOIN pg_roles m ON m.oid=a.member WHERE r.rolname=i.application_role AND NOT m.rolsuper)
  -- ...and it is none of the zabalack roles and can assume none of them. A role
  -- it can assume is a role it acts as: zabalack_worker carries worker_call(),
  -- so an application role that reaches it verifies, holds and rejects captures
  -- without the verifier's signature check. pg_has_role() follows the chain, so
  -- an indirect membership is caught too, and it holds whether the membership
  -- inherits or is reached with SET ROLE. The ACLs of those roles are hashed,
  -- this membership is not.
  AND NOT EXISTS(SELECT 1 FROM pg_roles a CROSS JOIN pg_roles z
   WHERE a.rolname=i.application_role
    AND z.rolname IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')
    AND pg_has_role(a.oid,z.oid,'MEMBER'))
  -- ...and, as itself or as any role it can assume, it executes nothing else
  -- here and reads no table here. The ACL of every function and relation in
  -- this schema is hashed, so a stray grant reads unhealthy through the digests
  -- alone — until someone re-seals, which every upgrade does, and the seal then
  -- agrees with it. This is the standing assertion that survives that seal:
  -- worker_call() carries the verification operations, and a grant of it to the
  -- application is a signature check skipped. Where the application connects as
  -- the alerts owner, the upstream default, that role keeps 005's capture_ack()
  -- and seal_installation() grants, which the trigger and the installer need.
  AND NOT EXISTS(SELECT 1 FROM pg_roles a JOIN pg_roles r ON pg_has_role(a.oid,r.oid,'MEMBER')
   CROSS JOIN pg_proc p WHERE a.rolname=i.application_role
    AND p.pronamespace='zabalack'::regnamespace
    AND p.oid NOT IN ('zabalack.stage_generation(text,uuid,jsonb)'::regprocedure,
     'zabalack.bind_source(text,text,timestamp without time zone,text,text,text,text)'::regprocedure,
     'zabalack.read_binding(text,text,timestamp without time zone)'::regprocedure)
    AND has_function_privilege(r.oid,p.oid,'EXECUTE')
    AND NOT (r.rolname=i.application_role
             AND r.oid=(SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass)
             AND p.oid IN ('zabalack.capture_ack()'::regprocedure,
                           'zabalack.seal_installation()'::regprocedure)))
  AND NOT EXISTS(SELECT 1 FROM pg_roles a JOIN pg_roles r ON pg_has_role(a.oid,r.oid,'MEMBER')
   CROSS JOIN pg_class c WHERE a.rolname=i.application_role
    AND c.relnamespace='zabalack'::regnamespace AND c.relkind IN ('r','p','v','m')
    AND has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
  -- Every non-owner grant in this schema is one the installation makes, and no
  -- other. The ACLs are hashed, so a stray grant reads unhealthy through the
  -- digests — until the re-seal every upgrade performs makes them agree with
  -- it. This is the assertion that survives that seal, and it is the whole map
  -- rather than one function at a time: worker_call() and project() carry the
  -- verification path, operator_call() the controls (external writes, resume,
  -- cohorts, capture bypass), and the rest answer to their own role or to no
  -- one. PUBLIC is refused above; a superuser or the owner executes what it
  -- owns whatever an ACL says.
  AND NOT EXISTS(SELECT 1 FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
   WHERE p.pronamespace='zabalack'::regnamespace
    AND e.grantee<>p.proowner AND e.grantee<>0
    AND NOT ((p.oid IN ('zabalack.worker_call(text,jsonb)'::regprocedure,'zabalack.project(uuid)'::regprocedure)
              AND e.grantee='zabalack_worker'::regrole)
          OR (p.oid='zabalack.operator_call(text,jsonb)'::regprocedure AND e.grantee='zabalack_operator'::regrole)
          OR (p.oid='zabalack.capture_health()'::regprocedure
              AND e.grantee IN ('zabalack_probe'::regrole,'zabalack_operator'::regrole))
          OR (p.oid IN ('zabalack.capture_ack()'::regprocedure,'zabalack.seal_installation()'::regprocedure)
              AND e.grantee=(SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass))
          OR (p.oid IN ('zabalack.stage_generation(text,uuid,jsonb)'::regprocedure,
               'zabalack.bind_source(text,text,timestamp without time zone,text,text,text,text)'::regprocedure,
               'zabalack.read_binding(text,text,timestamp without time zone)'::regprocedure)
              AND pg_get_userbyid(e.grantee)=i.application_role)))
  -- ...and every grant the installation makes is still there. The rule above
  -- only refuses an entry that should not be; a missing one it cannot see, and
  -- revoking EXECUTE on operator_call() from zabalack_operator leaves no
  -- unexpected row for it to find while every control — pause, resume, cohorts,
  -- external writes — fails with permission denied behind a probe reporting
  -- healthy. The migrations do not restore a grant they already made, so the
  -- re-seal records the grantless ACL as the baseline. The map is therefore
  -- two-sided, as it already was for stage_generation(): exactly these pairs,
  -- no more and no fewer.
  AND NOT EXISTS(SELECT 1 FROM (
   SELECT 'zabalack.worker_call(text,jsonb)'::regprocedure proc,'zabalack_worker'::regrole::oid grantee
   UNION ALL SELECT 'zabalack.project(uuid)'::regprocedure,'zabalack_worker'::regrole::oid
   UNION ALL SELECT 'zabalack.operator_call(text,jsonb)'::regprocedure,'zabalack_operator'::regrole::oid
   UNION ALL SELECT 'zabalack.capture_health()'::regprocedure,'zabalack_probe'::regrole::oid
   UNION ALL SELECT 'zabalack.capture_health()'::regprocedure,'zabalack_operator'::regrole::oid
   UNION ALL SELECT 'zabalack.capture_ack()'::regprocedure,(SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass)
   UNION ALL SELECT 'zabalack.seal_installation()'::regprocedure,(SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass)
  ) x WHERE NOT EXISTS(SELECT 1 FROM pg_proc p
    CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
    WHERE p.oid=x.proc AND e.grantee=x.grantee AND e.privilege_type='EXECUTE'))
  -- No relation here admits a non-owner grantee at all — tables, views and the
  -- identity sequence behind the fence alike: the migrations grant none, and
  -- every runtime role reaches its data through a function. The sequence is not
  -- an afterthought: UPDATE on it is setval(), and a sequence moved backwards
  -- gives a later staging a lower seq than an earlier one, which is the fence's
  -- ordering inverted. acldefault() takes 's' for a sequence and 'r' for the
  -- rest, and relkind is not filtered so that nothing in the schema is outside
  -- this rule.
  AND NOT EXISTS(SELECT 1 FROM pg_class c
   CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,
    acldefault((CASE WHEN c.relkind='S' THEN 's' ELSE 'r' END)::"char",c.relowner))) e
   WHERE c.relnamespace='zabalack'::regnamespace AND e.grantee<>c.relowner)
  -- The schema's own ACL is the gate in front of every rule above, and it is
  -- the one they all assume rather than assert. Revoke USAGE on zabalack from
  -- zabalack_worker and every function ACL below still reads exactly right
  -- while worker_call() fails at schema access, so verification and delivery
  -- stop behind a probe that reports healthy; grant CREATE here to a role and
  -- it puts its own objects in the schema the manifest describes. nspacl is
  -- hashed, so either reads unhealthy through the digests — until the re-seal
  -- every upgrade performs agrees with it, which is why the map is asserted
  -- here too. It is exact: USAGE and no other privilege, to the three runtime
  -- roles, the alerts owner (whose trigger calls capture_ack()) and the
  -- recorded application role, and to no one else; CREATE to the owner alone;
  -- nothing at all to PUBLIC.
  AND NOT EXISTS(SELECT 1 FROM pg_namespace n
   CROSS JOIN LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) e
   WHERE n.nspname='zabalack' AND e.grantee<>n.nspowner
    AND (e.grantee=0 OR e.privilege_type<>'USAGE'
         OR (e.grantee NOT IN ('zabalack_worker'::regrole,'zabalack_probe'::regrole,
                               'zabalack_operator'::regrole)
             AND e.grantee<>(SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass)
             AND pg_get_userbyid(e.grantee)<>i.application_role)))
  AND has_schema_privilege('zabalack_worker','zabalack','USAGE')
  AND has_schema_privilege('zabalack_probe','zabalack','USAGE')
  AND has_schema_privilege('zabalack_operator','zabalack','USAGE')
  -- ...and the alerts owner, which is a separate role wherever the deployment
  -- splits the application from it, as docs/deployment.md prefers. Without it
  -- that role cannot execute the capture_ack() and seal_installation() grants
  -- 005 makes to it, and 10_trigger.sql — the trigger's installation and
  -- repair — fails at schema access.
  AND has_schema_privilege((SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass),
                           'zabalack','USAGE')
  -- ...and every routine body is the one this migration shipped. The seal
  -- cannot answer this: a re-seal records whatever it finds, so the expected
  -- value is kept where no seal writes — installation.routine_bodies_md5, set
  -- by 008 itself. Redefine operator_call() to call worker_call() and the
  -- signature, owner, ACL, SECURITY DEFINER flag and pinned search_path all
  -- still read right; only the body says otherwise. Definitions alone, so the
  -- grant 12_application_grant.sql makes afterwards does not disturb it.
  -- Asserted for the version that recorded it, and only that one: the digest
  -- describes the routine set 008 ships, so on a tree at another schema version
  -- — a rolled-back fixture, a future migration's own bodies — it says nothing.
  -- A worker pinned to 4 reports unhealthy on such a tree for its own reasons.
  AND (i.schema_version<>5
       OR (i.routine_bodies_md5 IS NOT NULL AND i.routine_bodies_md5=h.hashes->>'routines'))
  -- ...and no routine here has drifted in how it runs. Every one is created
  -- with its search_path pinned to pg_catalog, zabalack, and ALTER FUNCTION ...
  -- SET search_path can move it afterwards: a SECURITY DEFINER body resolving
  -- an unqualified name through a schema someone else writes is the classic
  -- way in. The stored form carries a space after the comma, and some routines
  -- also pin TimeZone, so the element is matched whitespace-insensitively
  -- rather than the array compared whole.
  AND NOT EXISTS(SELECT 1 FROM pg_proc p
   WHERE p.pronamespace='zabalack'::regnamespace
    AND NOT EXISTS(SELECT 1 FROM unnest(coalesce(p.proconfig,'{}'::text[])) x
                   WHERE replace(x,' ','')='search_path=pg_catalog,zabalack'))
  -- ...and every routine anyone but its owner can execute runs as its owner.
  -- That is what these grants are for: worker_call() and operator_call() carry
  -- operations the caller has no rights of its own to perform, so ALTER
  -- FUNCTION ... SECURITY INVOKER does not weaken the boundary, it removes the
  -- path — the operator's controls fail under the unprivileged caller — while
  -- every ACL still reads exactly right. The rule needs no list of names: it is
  -- the granted routines, which the map above already fixes.
  AND NOT EXISTS(SELECT 1 FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
   WHERE p.pronamespace='zabalack'::regnamespace AND e.grantee<>p.proowner
    AND NOT p.prosecdef)
  -- ...and no grant here carries the option to pass it on. A grantee with GRANT
  -- OPTION is a grantee that can make more of them: zabalack_worker holding
  -- EXECUTE WITH GRANT OPTION on worker_call() delegates the verification path
  -- to a credential the map never named, and the map reads the grantee alone.
  -- Relations admit no non-owner grantee at all, so this is the function ACLs,
  -- the schema's own, and the owner's columns on public.alerts.
  AND NOT EXISTS(SELECT 1 FROM pg_proc p
   CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
   WHERE p.pronamespace='zabalack'::regnamespace AND e.grantee<>p.proowner AND e.is_grantable)
  AND NOT EXISTS(SELECT 1 FROM pg_namespace n
   CROSS JOIN LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) e
   WHERE n.nspname='zabalack' AND e.grantee<>n.nspowner AND e.is_grantable)
  AND NOT EXISTS(
   SELECT 1 FROM (SELECT c.relacl FROM pg_class c
                  WHERE c.oid='public.alerts'::regclass AND c.relacl IS NOT NULL) t
    CROSS JOIN LATERAL aclexplode(t.relacl) e
    WHERE e.grantee='zabalack_owner'::regrole AND e.is_grantable
   UNION ALL
   SELECT 1 FROM (SELECT a.attacl FROM pg_attribute a
                  WHERE a.attrelid='public.alerts'::regclass AND a.attnum>0
                   AND NOT a.attisdropped AND a.attacl IS NOT NULL) t
    CROSS JOIN LATERAL aclexplode(t.attacl) e
    WHERE e.grantee='zabalack_owner'::regrole AND e.is_grantable)
  -- Ownership is the other half of the map, and for a SECURITY DEFINER function
  -- it is the privilege itself: the body runs as the owner. Reassign one and
  -- the grantee rules above skip the new owner, which they exclude by
  -- definition. proowner, relowner and nspowner are all hashed, so a
  -- reassignment reads unhealthy through the digests — until the re-seal agrees
  -- with it. Everything the migrations create here is owned by zabalack_owner
  -- and stays that way.
  AND EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname='zabalack'
   AND n.nspowner='zabalack_owner'::regrole)
  AND NOT EXISTS(SELECT 1 FROM pg_proc p WHERE p.pronamespace='zabalack'::regnamespace
   AND p.proowner<>'zabalack_owner'::regrole)
  AND NOT EXISTS(SELECT 1 FROM pg_class c WHERE c.relnamespace='zabalack'::regnamespace
   AND c.relowner<>'zabalack_owner'::regrole)
  -- ...and no role outside the installation's recorded topology may assume any
  -- of these four. This is the membership rule entire, rather than the corner
  -- of it each earlier round named: the member may be any role, and the role
  -- assumed may be any of the four. A NOINHERIT member resolves no privilege
  -- through any ACL above, so every has_*_privilege() assertion here reads
  -- clean while SET ROLE hands it the role whole — an outside login granted
  -- zabalack_owner calls worker_call() and records a capture as verified with
  -- no signature check, and one granted zabalack_operator enables external
  -- writes. The membership is hashed only where the *member* is itself one of
  -- the four, so an outside one moves no digest at all: nothing but this sees
  -- it, sealed or not.
  --
  -- The installer topology is the single exception, and it is now named rather
  -- than assumed. Migrations run as zabalack_owner, so a deployment does have
  -- principals that can assume it; sql/install/09_installer_roles.sql records
  -- which, and they may hold zabalack_owner and none of the other three.
  -- Recording does not make an installer less powerful — it can already act as
  -- the owner, and could widen this list as easily — it makes the set of
  -- principals that can an explicit, reviewable part of the installation
  -- instead of an exemption for every role outside the four. An unrecorded
  -- installation is not healthy: the column starts NULL and the script is the
  -- act that sets it, empty where migrations run as a superuser and no role
  -- holds zabalack_owner at all. Superusers are never counted; they assume any
  -- role whatever this says.
  -- Reach, not the edge: pg_has_role() follows the chain, so a member of a
  -- recorded installer — outside login → installer → zabalack_owner — is
  -- refused as the installer's own member rather than exempted along with it.
  -- Only the roles named here reach zabalack_owner; anything that reaches them
  -- must be named too.
  AND i.installer_roles IS NOT NULL
  AND NOT EXISTS(SELECT 1 FROM pg_roles r CROSS JOIN pg_roles z
   WHERE z.rolname IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')
    AND NOT r.rolsuper AND r.oid<>z.oid AND pg_has_role(r.oid,z.oid,'MEMBER')
    AND NOT (z.rolname='zabalack_owner'
             AND r.rolname<>ALL(ARRAY['zabalack_owner','zabalack_worker',
                                      'zabalack_probe','zabalack_operator'])
             AND r.rolname=ANY(i.installer_roles)))
  -- ...and the four are members of nothing themselves. Every rule above reads
  -- inward — who can act as one of these four — and this one reads outward:
  -- what one of them can act as. GRANT alerta_owner TO zabalack_worker and the
  -- worker does SET ROLE alerta_owner and writes public.alerts directly, past
  -- the projection boundary the whole design rests on, while
  -- has_table_privilege('zabalack_worker','public.alerts','UPDATE') stays false
  -- throughout — that function resolves inherited privilege, and a NOINHERIT
  -- role inherits none of what SET ROLE still hands it. The membership is
  -- hashed, the member being one of the four, so the digests catch it until the
  -- re-seal every upgrade performs agrees with it. There is no exception here:
  -- 00_roles.sql grants these four no role at all, the installer topology runs
  -- the other way, and a role one of them could assume is a role it acts as.
  AND NOT EXISTS(SELECT 1 FROM pg_roles z CROSS JOIN pg_roles r
   WHERE z.rolname IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')
    AND r.oid<>z.oid AND pg_has_role(z.oid,r.oid,'MEMBER'))
  -- And the four keep the attributes 00_roles.sql declares. A role attribute is
  -- not an ACL and not an ownership: SUPERUSER or BYPASSRLS on a runtime login
  -- walks past every rule above. The digests hash five of these; LOGIN on the
  -- owner and REPLICATION on any of them they do not hash at all.
  AND NOT EXISTS(SELECT 1 FROM pg_roles r
   WHERE r.rolname IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')
    AND (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls
         OR r.rolreplication OR r.rolinherit
         OR r.rolcanlogin<>(r.rolname<>'zabalack_owner'))),
 'checked_at',clock_timestamp()) FROM zabalack.installation i LEFT JOIN zabalack.readiness_manifest m USING(singleton)
 CROSS JOIN LATERAL (SELECT zabalack.catalog_fingerprints() hashes) h WHERE i.singleton;
$$;
REVOKE ALL ON FUNCTION zabalack.capture_health() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION zabalack.capture_health() TO zabalack_probe,zabalack_operator;

-- Recorded last, when every routine this migration ships is in place, for the
-- reason 008 records it: capture_health() and 13_reseal.sql compare the live
-- routine digest against this column, which no seal replaces. Schema 5: the
-- worker and the plugin pin it, so a component that predates the binding model
-- fails closed instead of mixing models.
UPDATE zabalack.installation
 SET routine_bodies_md5=(SELECT zabalack.catalog_fingerprints()->>'routines'),
     schema_version=5
 WHERE singleton;
