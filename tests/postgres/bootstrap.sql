-- ISOLATED LAB ONLY. Administrator provisions synthetic roles once. Assertions
-- and migrations SET ROLE into the listed non-superuser principals.
CREATE ROLE alerta_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE alerta_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE zabalack_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE zabalack_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE zabalack_probe LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE zabalack_operator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
CREATE ROLE zabalack_installer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
-- A second application login, for the change-of-application-role case: no
-- members, no memberships, exactly what the rules require of one.
CREATE ROLE alerta_app_next LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
GRANT alerta_owner TO zabalack_installer;
GRANT USAGE,CREATE ON SCHEMA public TO alerta_owner;
CREATE SCHEMA zabalack AUTHORIZATION zabalack_owner;
REVOKE ALL ON SCHEMA zabalack FROM PUBLIC;
GRANT USAGE ON SCHEMA zabalack TO zabalack_worker,zabalack_probe,zabalack_operator,alerta_owner;
GRANT USAGE ON SCHEMA public TO zabalack_owner,alerta_app,alerta_app_next;
-- Load actual pinned upstream alerta/sql/schema.sql as SET ROLE alerta_owner;
-- then apply bootstrap_after_schema.sql and run migrations SET ROLE zabalack_owner.
