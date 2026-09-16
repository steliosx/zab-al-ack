-- Record the roles that may hold zabalack_owner. Run as zabalack_owner, after
-- the migrations and before the manifest is sealed (10_trigger.sql on a fresh
-- install, 13_reseal.sql on an upgrade), with :installer_roles set to a
-- comma-separated list of the installation principals that are members of
-- zabalack_owner — empty where the migrations run as a superuser and no role
-- holds it at all.
--
-- Migrations run as zabalack_owner, so a deployment has principals able to
-- assume it, and until this record existed every role granted zabalack_owner
-- was indistinguishable from one of them. That gap is not visible anywhere
-- else: a membership moves no ACL, the fingerprint hashes one only where the
-- *member* is itself one of the four zabalack_* roles, and a NOINHERIT login
-- resolves no privilege through any ACL — so an arbitrary login granted
-- zabalack_owner reaches zabalack.worker_call() with SET ROLE and records a
-- capture as verified with no signature check, while every grant and every
-- digest reads exactly right.
--
-- Recording does not make an installer less powerful: a role that can act as
-- the owner can rewrite this list. It makes the set of principals that can an
-- explicit, reviewable part of the installation instead of an exemption for
-- every role outside those four. capture_health() reports functions_ok false
-- while it is unset or while a role outside it holds one of the four, and
-- 13_reseal.sql refuses to seal such a tree.
--
-- This script creates, alters and drops nothing, grants no privilege and
-- changes no ownership. It records the topology it is shown, and refuses where
-- that is not the topology the catalog has.
BEGIN;
DO $$ BEGIN
 IF current_user<>'zabalack_owner' THEN RAISE EXCEPTION 'run as zabalack_owner'; END IF;
 IF (SELECT schema_version FROM zabalack.installation WHERE singleton)<4 THEN
  RAISE EXCEPTION 'apply migration 008 before recording the installer roles';
 END IF;
END $$;
-- The named list replaces whatever was recorded before: this is the whole
-- allowlist, not an addition to it, so a principal retired from the topology
-- stops being exempt the moment it is left out.
UPDATE zabalack.installation SET installer_roles=coalesce(
 (SELECT array_agg(DISTINCT btrim(x) ORDER BY btrim(x))
  FROM unnest(string_to_array(:'installer_roles',',')) x WHERE btrim(x)<>''),
 '{}'::text[]) WHERE singleton;
-- A name that is not a role records an exemption for nothing and hides a typo
-- that would otherwise leave a real installer refused.
DO $$
DECLARE missing text;
BEGIN
 SELECT string_agg(x,', ' ORDER BY x) INTO missing
  FROM zabalack.installation i CROSS JOIN LATERAL unnest(i.installer_roles) x
  WHERE i.singleton AND NOT EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname=x);
 IF missing IS NOT NULL THEN
  RAISE EXCEPTION 'named installer role(s) do not exist: %; name the roles that hold zabalack_owner',missing;
 END IF;
END $$;
-- None of the four may be named: the exemption is for principals outside them,
-- and a runtime login that can assume zabalack_owner reaches every owner-only
-- function. Allowlisting one of the four would be allowlisting exactly that.
DO $$
DECLARE named text;
BEGIN
 SELECT string_agg(x,', ' ORDER BY x) INTO named
  FROM zabalack.installation i CROSS JOIN LATERAL unnest(i.installer_roles) x
  WHERE i.singleton AND x IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator');
 IF named IS NOT NULL THEN
  RAISE EXCEPTION 'a zabalack role was named as an installer: %; the allowlist is for installation principals outside those four',named;
 END IF;
END $$;
-- And each named role must actually hold zabalack_owner. The record is the
-- topology written down, not a standing permission to grant later: a name here
-- that is not a member exempts a role the catalog does not show, which is the
-- unbounded exemption this script exists to close.
DO $$
DECLARE absent text;
BEGIN
 SELECT string_agg(x,', ' ORDER BY x) INTO absent
  FROM zabalack.installation i CROSS JOIN LATERAL unnest(i.installer_roles) x
  WHERE i.singleton AND NOT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
   WHERE m.roleid='zabalack_owner'::regrole AND r.rolname=x);
 IF absent IS NOT NULL THEN
  RAISE EXCEPTION 'named installer role(s) are not members of zabalack_owner: %; grant the membership first, or leave them out',absent;
 END IF;
END $$;
-- What is left must be nothing: every non-superuser role that can act as any of
-- the four, other than a named installer acting as zabalack_owner, is a second
-- credential on it. pg_has_role() follows the chain, so a member of a named
-- installer is refused rather than exempted along with it: a role that reaches
-- an installer reaches zabalack_owner. zabalack_owner holds no ADMIN OPTION on those memberships
-- and cannot revoke them, as 12_application_grant.sql cannot revoke a
-- membership of the application role: refuse, naming them, and let the role
-- administrator revoke them first. capture_health() and 13_reseal.sql apply the
-- same rule afterwards.
DO $$
DECLARE outside text;
BEGIN
 SELECT string_agg(DISTINCT r.rolname||' on '||z.rolname,', ') INTO outside
  FROM zabalack.installation i CROSS JOIN pg_roles r CROSS JOIN pg_roles z
  WHERE i.singleton
   AND z.rolname IN ('zabalack_owner','zabalack_worker','zabalack_probe','zabalack_operator')
   AND NOT r.rolsuper AND r.oid<>z.oid AND pg_has_role(r.oid,z.oid,'MEMBER')
   AND NOT (z.rolname='zabalack_owner'
            AND r.rolname<>ALL(ARRAY['zabalack_owner','zabalack_worker',
                                     'zabalack_probe','zabalack_operator'])
            AND r.rolname=ANY(i.installer_roles));
 IF outside IS NOT NULL THEN
  RAISE EXCEPTION 'role(s) hold a zabalack role outside the recorded installer topology: %; revoke those memberships, or name the installers among :installer_roles',outside;
 END IF;
END $$;
COMMIT;
