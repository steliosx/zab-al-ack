-- Catalog fingerprints are installation evidence, not executable branch tests.
-- The separate installer seals them only after trigger installation and tests.
CREATE TABLE zabalack.readiness_manifest (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), functions_md5 text NOT NULL,
 structures_md5 text NOT NULL, trigger_md5 text NOT NULL, sealed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE FUNCTION zabalack.catalog_fingerprints() RETURNS jsonb LANGUAGE sql STABLE
SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
 SELECT jsonb_build_object(
 'functions', (SELECT md5(string_agg(pg_get_functiondef(p.oid)||p.proowner::text||coalesce(p.proacl::text,''),E'\n' ORDER BY p.oid))
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='zabalack' AND p.prokind='f'),
 'structures',(SELECT md5(string_agg(v,E'\n' ORDER BY v)) FROM (
  SELECT 'column:'||c.relname||':'||a.attname||':'||a.atttypid::text||':'||a.attnotnull::text AS v
   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
   WHERE n.nspname='zabalack' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
  UNION ALL SELECT 'relation_acl:'||c.relname||':'||c.relowner::text||':'||coalesce(c.relacl::text,'') FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='zabalack'
  UNION ALL SELECT 'schema_acl:'||n.nspowner::text||':'||coalesce(n.nspacl::text,'') FROM pg_namespace n WHERE n.nspname='zabalack'
  UNION ALL SELECT 'role:'||r.rolname||':'||r.rolsuper::text||':'||r.rolcreaterole::text||':'||r.rolcreatedb::text||':'||r.rolinherit::text||':'||r.rolbypassrls::text FROM pg_roles r WHERE r.rolname IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')
  UNION ALL SELECT 'membership:'||am.roleid::text||':'||am.member::text||':'||am.admin_option::text FROM pg_auth_members am WHERE am.member IN ('zabalack_owner'::regrole,'zabalack_worker'::regrole,'zabalack_probe'::regrole,'zabalack_operator'::regrole)
  UNION ALL SELECT 'constraint:'||c.conrelid::text||':'||pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname='zabalack'
  UNION ALL SELECT 'index:'||i.indexdef FROM pg_indexes i WHERE i.schemaname='zabalack'
 ) x),
 'trigger',(SELECT md5(pg_get_triggerdef(t.oid)) FROM pg_trigger t WHERE t.tgrelid='public.alerts'::regclass AND t.tgname='zabalack_capture'));
$$;
REVOKE ALL ON FUNCTION zabalack.catalog_fingerprints() FROM PUBLIC;
CREATE FUNCTION zabalack.seal_installation() RETURNS void LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,zabalack AS $$
DECLARE hashes jsonb;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.alerts'::regclass AND tgname='zabalack_capture'
  AND tgfoid='zabalack.capture_ack()'::regprocedure AND tgtype=17 AND tgenabled='O') THEN RAISE EXCEPTION 'expected enabled trigger absent'; END IF;
 hashes:=zabalack.catalog_fingerprints();
 INSERT INTO zabalack.readiness_manifest(singleton,functions_md5,structures_md5,trigger_md5)
  VALUES(true,hashes->>'functions',hashes->>'structures',hashes->>'trigger')
 ON CONFLICT(singleton) DO UPDATE SET functions_md5=excluded.functions_md5,structures_md5=excluded.structures_md5,
 trigger_md5=excluded.trigger_md5,sealed_at=clock_timestamp();
 INSERT INTO zabalack.audit(operation,actor_ref) VALUES('seal_installation',session_user);
END $$;
REVOKE ALL ON FUNCTION zabalack.seal_installation() FROM PUBLIC;
-- Grant only to the observed owner of the upstream alerts table. This does not
-- confer that ownership or the right to administer its trigger on zabalack_owner.
DO $$ DECLARE principal name; BEGIN
 SELECT r.rolname INTO STRICT principal FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner WHERE c.oid='public.alerts'::regclass;
 EXECUTE format('GRANT EXECUTE ON FUNCTION zabalack.capture_ack(),zabalack.seal_installation() TO %I',principal);
END $$;
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
  AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member='zabalack_probe'::regrole),
 'checked_at',clock_timestamp()) FROM zabalack.installation i LEFT JOIN zabalack.readiness_manifest m USING(singleton)
 CROSS JOIN LATERAL (SELECT zabalack.catalog_fingerprints() hashes) h WHERE i.singleton;
$$;
REVOKE ALL ON FUNCTION zabalack.capture_health() FROM PUBLIC;
