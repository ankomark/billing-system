-- Run this ONCE, by hand, against a managed Postgres before the first migrate.
--
-- docker/postgres-init/10-app-role.sh does the same job for the compose stack,
-- but it only runs from docker-entrypoint-initdb.d, which a managed database
-- (Railway, RDS, Hetzner's managed offering) does not have. Skipping it is not
-- a cosmetic omission: Railway hands you the `postgres` role, a superuser, and
-- a superuser bypasses Row-Level Security unconditionally. Connect Django as
-- it and every tenant_isolation policy in the database is inert while still
-- listed in pg_policies exactly as though it were working. One operator's
-- dashboard would be one missing .filter() away from another's customers.
--
-- HOW TO RUN IT
--   Railway → your Postgres service → Data → Query, paste, execute.
--   Or:  psql "$DATABASE_URL" -f railway-app-role.sql   (the superuser URL)
--
-- BEFORE YOU RUN IT
--   1. Replace CHANGE-ME below with a generated password:
--        python -c "import secrets; print(secrets.token_urlsafe(32))"
--      That same value goes in POSTGRES_PASSWORD on every app service.
--   2. Check the database name. Railway calls it `railway`, not `wifi_billing`;
--      the ALTER DATABASE line must name the one you are connected to.
--   3. Run it BEFORE the first `migrate`. Migration 0030 assumes Django owns
--      the tables it applies FORCE ROW LEVEL SECURITY to, and tables are owned
--      by whoever created them. Migrate as `postgres` first and the tables
--      belong to a superuser, which is the situation this file exists to avoid.

CREATE ROLE wifi_app
    LOGIN
    PASSWORD 'CHANGE-ME'
    NOSUPERUSER
    NOBYPASSRLS
    NOCREATEROLE
    CREATEDB;

ALTER DATABASE railway OWNER TO wifi_app;
ALTER SCHEMA public OWNER TO wifi_app;

-- Proves the point of the whole file. Both columns must read `f`. If either is
-- `t`, Django is about to run with tenant isolation switched off at the
-- database, and nothing in the application will ever say so.
SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'wifi_app';
