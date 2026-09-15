-- Re-seal the readiness manifest after a migration that changes the zabalack
-- catalog but not the trigger. Run as zabalack_owner — the principal that
-- changed the catalog, the owner of seal_installation() and the only
-- non-superuser that can read the tables the checks below consult — while
-- eligible acknowledgements are fenced, and after 12_application_grant.sql:
-- the grant changes ACLs the manifest hashes. 10_trigger.sql, which changes
-- the trigger, still seals under the alerts owner.
--
-- This script never creates, alters or drops an object, never grants a
-- privilege and never changes ownership. It refuses rather than sealing a tree
-- that is not the one it was written for.
BEGIN;
DO $$ BEGIN
 IF current_user<>'zabalack_owner' THEN RAISE EXCEPTION 'run as zabalack_owner'; END IF;
END $$;
-- pg_get_triggerdef renders names according to search_path, so the fingerprint
-- is only comparable under the same path seal_installation() itself uses.
SET LOCAL search_path=pg_catalog,zabalack;
DO $$
DECLARE installed integer;
BEGIN
 SELECT schema_version INTO STRICT installed FROM zabalack.installation WHERE singleton;
 IF installed<>4 THEN
  RAISE EXCEPTION 'expected zabalack schema version 4, found %; apply the migrations first',installed;
 END IF;
 IF to_regprocedure('zabalack.stage_generation(text,uuid,jsonb)') IS NULL
  OR to_regprocedure('zabalack.newest_generation(text)') IS NULL
  OR to_regprocedure('zabalack.retire_intents(integer,integer)') IS NULL
  OR to_regprocedure('zabalack.intent_retirable(jsonb,interval,timestamptz)') IS NULL
  OR to_regprocedure('zabalack.intent_due_at(jsonb,interval)') IS NULL
  OR to_regprocedure('zabalack.claim_retirement_slot()') IS NULL
  OR to_regclass('zabalack.alert_generations') IS NULL THEN
  RAISE EXCEPTION 'schema version 4 objects absent; refusing to seal an incomplete tree';
 END IF;
 -- The application role's USAGE on the schema rests on the facts checked here,
 -- which capture_health() asserts under functions_ok. A seal over a tree that
 -- breaks any of them would make the digests agree with a real
 -- misconfiguration: refuse.
 IF EXISTS(SELECT 1 FROM pg_proc p WHERE p.pronamespace='zabalack'::regnamespace
   AND (p.proacl IS NULL OR EXISTS(SELECT 1 FROM aclexplode(p.proacl) e WHERE e.grantee=0))) THEN
  RAISE EXCEPTION 'a zabalack function is executable by PUBLIC; revoke that before sealing';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) e
   WHERE c.oid='zabalack.alert_generations'::regclass AND e.grantee<>c.relowner) THEN
  RAISE EXCEPTION 'zabalack.alert_generations admits a grantee other than its owner; revoke that before sealing';
 END IF;
 -- The staging entry point has exactly one non-owner grantee, the application
 -- role 12_application_grant.sql recorded. Any other is a second credential
 -- able to stage; a grant that was never recorded did not come from that
 -- script; and none at all — the grant revoked, the role dropped — would seal
 -- a tree on which every eligible acknowledgement fails behind a passing probe.
 IF NOT EXISTS(SELECT 1 FROM zabalack.installation i WHERE i.singleton AND i.application_role IS NOT NULL
    AND (SELECT count(*) FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
     WHERE p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure AND e.grantee<>p.proowner)=1
    AND EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
     WHERE p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure AND e.grantee<>p.proowner
      AND e.grantee<>0 AND pg_get_userbyid(e.grantee)=i.application_role)) THEN
  RAISE EXCEPTION 'zabalack.stage_generation() must have exactly one grantee, the recorded application role; run sql/install/12_application_grant.sql for that role, or revoke any other, before sealing';
 END IF;
 -- That role must also hold USAGE on the schema: without it the fully
 -- qualified call fails at schema access, and a seal over that would leave
 -- every eligible acknowledgement failing behind a passing probe.
 IF NOT EXISTS(SELECT 1 FROM zabalack.installation i JOIN pg_roles r ON r.rolname=i.application_role
    WHERE i.singleton AND has_schema_privilege(r.oid,'zabalack','USAGE')) THEN
  RAISE EXCEPTION 'the recorded application role does not hold USAGE on schema zabalack; run sql/install/12_application_grant.sql for that role before sealing';
 END IF;
 -- And no role may be a member of it: a member inherits its privileges, or can
 -- SET ROLE to it, and stages through the entry point while the ACL above
 -- still reads as one grantee. A membership changes no ACL, so the digests
 -- this seals would agree with that second credential. Superusers excepted:
 -- they execute it whatever the ACL says.
 IF EXISTS(SELECT 1 FROM zabalack.installation i JOIN pg_roles r ON r.rolname=i.application_role
    JOIN pg_auth_members a ON a.roleid=r.oid JOIN pg_roles m ON m.oid=a.member
    WHERE i.singleton AND NOT m.rolsuper) THEN
  RAISE EXCEPTION 'the recorded application role has members, each a credential able to stage through it; revoke those memberships before sealing';
 END IF;
 -- Nor may it be, or be able to assume, a zabalack role: zabalack_worker
 -- carries worker_call(), so an application role that reaches it verifies,
 -- holds and rejects captures without the verifier's signature check. A seal
 -- over that would agree with it, since a membership moves no ACL here.
 IF EXISTS(SELECT 1 FROM zabalack.installation i JOIN pg_roles a ON a.rolname=i.application_role
    CROSS JOIN pg_roles z WHERE i.singleton
     AND z.rolname IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')
     AND pg_has_role(a.oid,z.oid,'MEMBER')) THEN
  RAISE EXCEPTION 'the recorded application role is, or is a member of, a zabalack role and can act as it; revoke that membership or name a separate application role before sealing';
 END IF;
 -- And its effective rights here are the entry point and nothing more, as
 -- itself or as any role it can assume. Every ACL in this schema is hashed, so
 -- a stray grant shows through the digests until a re-seal — this one — agrees
 -- with it: refuse instead, or the seal would certify it.
 IF EXISTS(SELECT 1 FROM zabalack.installation i JOIN pg_roles a ON a.rolname=i.application_role
    JOIN pg_roles r ON pg_has_role(a.oid,r.oid,'MEMBER') CROSS JOIN pg_proc p
    WHERE i.singleton AND p.pronamespace='zabalack'::regnamespace AND p.prokind='f'
     AND p.oid<>'zabalack.stage_generation(text,uuid,jsonb)'::regprocedure
     AND has_function_privilege(r.oid,p.oid,'EXECUTE')
     AND NOT (r.rolname=i.application_role
              AND r.oid=(SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass)
              AND p.oid IN ('zabalack.capture_ack()'::regprocedure,
                            'zabalack.seal_installation()'::regprocedure))) THEN
  RAISE EXCEPTION 'the recorded application role executes a zabalack function other than the staging entry point; worker_call() alone is a signature check skipped — revoke those grants before sealing';
 END IF;
 IF EXISTS(SELECT 1 FROM zabalack.installation i JOIN pg_roles a ON a.rolname=i.application_role
    JOIN pg_roles r ON pg_has_role(a.oid,r.oid,'MEMBER') CROSS JOIN pg_class c
    WHERE i.singleton AND c.relnamespace='zabalack'::regnamespace AND c.relkind IN ('r','p','v','m')
     AND has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')) THEN
  RAISE EXCEPTION 'the recorded application role holds a privilege on a zabalack table; revoke it before sealing';
 END IF;
 -- And the verification path answers to zabalack_worker alone, whoever the
 -- application role is: worker_call() carries the verification operations and
 -- project() writes the acknowledgement through. A seal over a stray grant on
 -- either would certify a second credential able to record a capture as
 -- verified with no signature check.
 -- Every non-owner grant in this schema must be one the installation makes.
 -- Sealing is what would otherwise bless a stray one: the digests catch it, and
 -- this seal would then agree with it. The map is the whole schema, not one
 -- function — worker_call() and project() carry the verification path,
 -- operator_call() the controls — and no relation here admits a grantee at all.
 IF EXISTS(SELECT 1 FROM zabalack.installation i CROSS JOIN pg_proc p
    CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
    WHERE i.singleton AND p.pronamespace='zabalack'::regnamespace AND p.prokind='f'
     AND e.grantee<>p.proowner AND e.grantee<>0
     AND NOT ((p.oid IN ('zabalack.worker_call(text,jsonb)'::regprocedure,'zabalack.project(uuid)'::regprocedure)
               AND e.grantee='zabalack_worker'::regrole)
           OR (p.oid='zabalack.operator_call(text,jsonb)'::regprocedure AND e.grantee='zabalack_operator'::regrole)
           OR (p.oid='zabalack.capture_health()'::regprocedure
               AND e.grantee IN ('zabalack_probe'::regrole,'zabalack_operator'::regrole))
           OR (p.oid IN ('zabalack.capture_ack()'::regprocedure,'zabalack.seal_installation()'::regprocedure)
               AND e.grantee=(SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass))
           OR (p.oid='zabalack.stage_generation(text,uuid,jsonb)'::regprocedure
               AND pg_get_userbyid(e.grantee)=i.application_role))) THEN
  RAISE EXCEPTION 'a zabalack function has a grantee the installation does not make; revoke it before sealing';
 END IF;
 -- And every grant the installation makes is still there. The rule above only
 -- refuses an entry that should not be; a missing one it cannot see, and a
 -- revoked EXECUTE on operator_call() leaves every control failing with
 -- permission denied behind a probe reporting healthy, with nothing to restore
 -- it — the migrations do not re-make a grant they already made. Exactly these
 -- pairs, no more and no fewer, as already held for stage_generation().
 IF EXISTS(SELECT 1 FROM (
    SELECT 'zabalack.worker_call(text,jsonb)'::regprocedure proc,'zabalack_worker'::regrole::oid grantee
    UNION ALL SELECT 'zabalack.project(uuid)'::regprocedure,'zabalack_worker'::regrole::oid
    UNION ALL SELECT 'zabalack.operator_call(text,jsonb)'::regprocedure,'zabalack_operator'::regrole::oid
    UNION ALL SELECT 'zabalack.capture_health()'::regprocedure,'zabalack_probe'::regrole::oid
    UNION ALL SELECT 'zabalack.capture_health()'::regprocedure,'zabalack_operator'::regrole::oid
    UNION ALL SELECT 'zabalack.capture_ack()'::regprocedure,(SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass)
    UNION ALL SELECT 'zabalack.seal_installation()'::regprocedure,(SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass)
   ) x WHERE NOT EXISTS(SELECT 1 FROM pg_proc p
     CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) e
     WHERE p.oid=x.proc AND e.grantee=x.grantee AND e.privilege_type='EXECUTE')) THEN
  RAISE EXCEPTION 'a zabalack function is missing a grant the installation makes; the role it answers to cannot call it — restore the grant before sealing';
 END IF;
 -- Every relation, whatever its kind: tables, views and the identity sequence
 -- behind the fence, where UPDATE is setval() and a sequence moved backwards
 -- inverts the ordering the fence rests on.
 IF EXISTS(SELECT 1 FROM pg_class c
    CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,
     acldefault((CASE WHEN c.relkind='S' THEN 's' ELSE 'r' END)::"char",c.relowner))) e
    WHERE c.relnamespace='zabalack'::regnamespace AND e.grantee<>c.relowner) THEN
  RAISE EXCEPTION 'a zabalack relation or sequence admits a grantee other than its owner; revoke it before sealing';
 END IF;
 -- The schema's own ACL is the gate in front of every rule above. Without
 -- USAGE on zabalack, zabalack_worker's fully qualified worker_call() fails at
 -- schema access while every function grant below still reads right, and a seal
 -- over that leaves verification and delivery stopped behind a passing probe;
 -- CREATE here to anyone but the owner puts foreign objects in the schema the
 -- manifest describes. USAGE and nothing else, to the three runtime roles, the
 -- alerts owner and the recorded application role, and to no one else.
 IF EXISTS(SELECT 1 FROM zabalack.installation i CROSS JOIN pg_namespace n
    CROSS JOIN LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) e
    WHERE i.singleton AND n.nspname='zabalack' AND e.grantee<>n.nspowner
     AND (e.grantee=0 OR e.privilege_type<>'USAGE'
          OR (e.grantee NOT IN ('zabalack_worker'::regrole,'zabalack_probe'::regrole,
                                'zabalack_operator'::regrole)
              AND e.grantee<>(SELECT c.relowner FROM pg_class c WHERE c.oid='public.alerts'::regclass)
              AND pg_get_userbyid(e.grantee)<>i.application_role))) THEN
  RAISE EXCEPTION 'schema zabalack admits a grant the installation does not make; USAGE alone, to the runtime roles, the alerts owner and the recorded application role — revoke the rest before sealing';
 END IF;
 IF NOT (has_schema_privilege('zabalack_worker','zabalack','USAGE')
     AND has_schema_privilege('zabalack_probe','zabalack','USAGE')
     AND has_schema_privilege('zabalack_operator','zabalack','USAGE')) THEN
  RAISE EXCEPTION 'a zabalack runtime role has lost USAGE on schema zabalack; its calls fail at schema access behind a passing probe — restore the grant before sealing';
 END IF;
 -- Ownership is the other half of the map: a SECURITY DEFINER function runs as
 -- its owner, so reassigning one hands it that role's rights, and the grantee
 -- rules above exclude the owner by definition. Sealing would record the
 -- changed owner as the baseline.
 IF NOT EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname='zabalack'
    AND n.nspowner='zabalack_owner'::regrole)
  OR EXISTS(SELECT 1 FROM pg_proc p WHERE p.pronamespace='zabalack'::regnamespace
    AND p.proowner<>'zabalack_owner'::regrole)
  OR EXISTS(SELECT 1 FROM pg_class c WHERE c.relnamespace='zabalack'::regnamespace
    AND c.relowner<>'zabalack_owner'::regrole) THEN
  RAISE EXCEPTION 'the zabalack schema, or an object in it, is not owned by zabalack_owner; a SECURITY DEFINER function runs as its owner — restore ownership before sealing';
 END IF;
 -- No role outside the recorded installer topology may assume any of the four,
 -- whichever it is and whoever the member: a NOINHERIT member resolves no
 -- privilege through any ACL above while SET ROLE hands it the role whole, and
 -- the fingerprint hashes the membership only where the member is itself one of
 -- the four. sql/install/09_installer_roles.sql records the principals that may
 -- hold zabalack_owner; an unrecorded installation seals nothing.
 IF NOT EXISTS(SELECT 1 FROM zabalack.installation i WHERE i.singleton AND i.installer_roles IS NOT NULL) THEN
  RAISE EXCEPTION 'the installer roles are not recorded; run sql/install/09_installer_roles.sql, naming the principals that hold zabalack_owner, before sealing';
 END IF;
 -- Reach, not the edge: pg_has_role() follows the chain, so a member of a
 -- recorded installer — outside login, installer, zabalack_owner — is refused
 -- rather than exempted along with it.
 IF EXISTS( SELECT 1 FROM zabalack.installation i CROSS JOIN pg_roles r CROSS JOIN pg_roles z
    WHERE i.singleton
     AND z.rolname IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')
     AND NOT r.rolsuper AND r.oid<>z.oid AND pg_has_role(r.oid,z.oid,'MEMBER')
     AND NOT (z.rolname='zabalack_owner'
              AND r.rolname<>ALL(ARRAY['zabalack_owner','zabalack_worker',
                                       'zabalack_probe','zabalack_operator'])
              AND r.rolname=ANY(i.installer_roles))) THEN
  RAISE EXCEPTION 'a role outside the recorded installer topology can act as a zabalack role, directly or through a role it can assume; revoke that membership, or record the installer, before sealing';
 END IF;
 -- And the four keep the attributes 00_roles.sql declares. SUPERUSER or
 -- BYPASSRLS on a runtime login walks past every rule above, and a seal would
 -- record the escalation as the baseline.
 IF EXISTS(SELECT 1 FROM pg_roles r
    WHERE r.rolname IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')
     AND (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls
          OR r.rolreplication OR r.rolinherit
          OR r.rolcanlogin<>(r.rolname<>'zabalack_owner'))) THEN
  RAISE EXCEPTION 'a zabalack role no longer carries the attributes 00_roles.sql declares (NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT, no BYPASSRLS or REPLICATION, LOGIN only for the three runtime roles); restore them before sealing';
 END IF;
 -- A fresh installation has no manifest yet: it is sealed by 10_trigger.sql
 -- under the alerts owner, not re-sealed here.
 IF NOT EXISTS(SELECT 1 FROM zabalack.readiness_manifest m WHERE m.singleton) THEN
  RAISE EXCEPTION 'readiness manifest not yet sealed; a fresh installation seals with sql/install/10_trigger.sql as the alerts owner';
 END IF;
 -- 008 does not touch the trigger definition, so its fingerprint must still
 -- match the sealed one. A mismatch here means something else moved.
 IF NOT EXISTS(SELECT 1 FROM zabalack.readiness_manifest m JOIN pg_trigger t
   ON t.tgrelid='public.alerts'::regclass AND t.tgname='zabalack_capture'
   WHERE m.singleton AND md5(pg_get_triggerdef(t.oid))=m.trigger_md5) THEN
  RAISE EXCEPTION 'trigger fingerprint changed; this is not an 008 upgrade';
 END IF;
END $$;
SELECT zabalack.seal_installation();
COMMIT;
