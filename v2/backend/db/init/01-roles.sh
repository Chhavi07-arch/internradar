#!/bin/sh
# Executed by the official PostgreSQL image on first initialization of PGDATA.
set -eu

: "${POSTGRES_USER:?Set POSTGRES_USER to the bootstrap administrator}"
: "${POSTGRES_DB:?Set POSTGRES_DB to the application database}"
: "${WEB_DB_PASSWORD:?Set WEB_DB_PASSWORD}"
: "${RESEARCH_DB_PASSWORD:?Set RESEARCH_DB_PASSWORD}"
: "${MIGRATION_DB_PASSWORD:?Set MIGRATION_DB_PASSWORD}"

# Read passwords from the environment rather than exposing them in psql's argv.
PGDATABASE="$POSTGRES_DB" psql -X --no-password --username="$POSTGRES_USER" --set=ON_ERROR_STOP=1 <<'SQL'
\getenv db POSTGRES_DB
\getenv web_password WEB_DB_PASSWORD
\getenv research_password RESEARCH_DB_PASSWORD
\getenv migration_password MIGRATION_DB_PASSWORD

BEGIN;
SET LOCAL password_encryption = 'scram-sha-256';
CREATE ROLE internradar_web LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'web_password';
CREATE ROLE internradar_research LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'research_password';
CREATE ROLE internradar_migrate LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'migration_password';

ALTER DATABASE :"db" OWNER TO internradar_migrate;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO internradar_web, internradar_research;

-- Reserve public for Flyway's history table, not runtime users.
ALTER SCHEMA public OWNER TO internradar_migrate;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
COMMIT;
SQL
