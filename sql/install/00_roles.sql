-- Reviewed DBA provisioning ONLY. Not part of the zabalack_owner migration.
-- Prerequisites: existing :database_name; existing :alert_owner owns public.alerts.
-- Creates no passwords; use approved credential manager and pg_hba/TLS separately.
-- These fixed role names are part of the v1 reference privilege contract.
BEGIN;
DO $$ BEGIN
 IF pg_catalog.to_regnamespace('ackline') IS NOT NULL THEN
  RAISE EXCEPTION 'legacy_ackline_schema_present: fresh database required; see docs/RENAME.md';
 END IF;
END $$;
CREATE ROLE zabalack_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE zabalack_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE zabalack_probe LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE zabalack_operator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT CONNECT ON DATABASE :"database_name" TO zabalack_owner,zabalack_worker,zabalack_probe,zabalack_operator;
CREATE SCHEMA zabalack AUTHORIZATION zabalack_owner;
REVOKE ALL ON SCHEMA zabalack FROM PUBLIC;
GRANT USAGE ON SCHEMA zabalack TO zabalack_worker,zabalack_probe,zabalack_operator,:"alert_owner";
GRANT USAGE ON SCHEMA public TO zabalack_owner;
GRANT SELECT(id,customer,status,attributes,update_time),UPDATE(attributes) ON public.alerts TO zabalack_owner;
-- No TRIGGER grant, no alerts ownership, no role inheritance to zabalack_owner.
COMMIT;
