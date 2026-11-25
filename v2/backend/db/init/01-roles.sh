#!/bin/sh
# Executed by the official PostgreSQL image on first initialization of PGDATA.
set -eu

require_env() {
    if [ -z "$2" ]; then
        printf 'Required environment variable is missing or empty: %s\n' "$1" >&2
        exit 1
    fi
}

require_env POSTGRES_USER "${POSTGRES_USER:-}"
require_env POSTGRES_DB "${POSTGRES_DB:-}"
require_env WEB_DB_PASSWORD "${WEB_DB_PASSWORD:-}"
require_env RESEARCH_DB_PASSWORD "${RESEARCH_DB_PASSWORD:-}"
require_env MIGRATION_DB_PASSWORD "${MIGRATION_DB_PASSWORD:-}"

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
