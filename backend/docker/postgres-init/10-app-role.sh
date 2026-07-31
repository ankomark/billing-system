#!/bin/bash
# Create the unprivileged role Django connects as.
#
# Why this exists: a superuser bypasses Row-Level Security unconditionally.
# FORCE ROW LEVEL SECURITY (billing/migrations/0030_row_level_security.py)
# closes the *owner* bypass, but nothing closes the superuser one — BYPASSRLS
# outranks FORCE. When the app connected as POSTGRES_USER, every
# tenant_isolation policy in the database was inert while still showing up in
# pg_policies exactly as if it were working. Four RLS tests caught it.
#
# The obvious fix — demote POSTGRES_USER — is impossible: Postgres rejects
# ALTER ROLE ... NOSUPERUSER on whichever role initdb created ("The bootstrap
# user must have the SUPERUSER attribute"). So POSTGRES_USER stays a superuser
# for administration, and the app gets its own role here.
#
# That role owns the database and schema, because migration 0030 assumes Django
# connects as the owner of these tables and relies on FORCE to make RLS apply
# to it. CREATEDB is granted only so `manage.py test` can build its test
# database. NOBYPASSRLS is the point of the whole file.
#
# NOTE: docker-entrypoint-initdb.d only runs when the data directory is empty.
# On a volume that already exists, create the role by hand once:
#     CREATE ROLE wifi_app LOGIN PASSWORD '...' NOSUPERUSER NOBYPASSRLS CREATEDB;
#     ALTER DATABASE wifi_billing OWNER TO wifi_app;
#     ALTER SCHEMA public OWNER TO wifi_app;
#     REASSIGN OWNED BY wifi_admin TO wifi_app;
# and confirm with:
#     select rolsuper, rolbypassrls from pg_roles where rolname = 'wifi_app';

set -euo pipefail

# Identifiers and literals are quoted by psql (:"name" / :'value') rather than
# interpolated by the shell, so a password containing a quote cannot break out
# into the surrounding statement.
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v app_user="$APP_DB_USER" \
     -v app_password="$APP_DB_PASSWORD" <<-'EOSQL'
	CREATE ROLE :"app_user"
	    LOGIN
	    PASSWORD :'app_password'
	    NOSUPERUSER
	    NOBYPASSRLS
	    NOCREATEROLE
	    CREATEDB;

	-- DBNAME is one of psql's own built-in variables: the database it is
	-- connected to, which is POSTGRES_DB.
	ALTER DATABASE :"DBNAME" OWNER TO :"app_user";
	ALTER SCHEMA public OWNER TO :"app_user";
EOSQL
