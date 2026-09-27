#!/usr/bin/env bash
# db-init.sh — idempotently create the Postgres roles/databases that the
# postgres image's own /docker-entrypoint-initdb.d can't: that hook only runs
# on an EMPTY data volume, and the live `pgdata` volume was initialized long
# before the platform service existed (docs/PLATFORM.md §3, `db-init`).
#
# Run by the one-shot `db-init` compose service (postgres:17 image, so psql
# matches the server) on every `docker compose up`; safe to re-run any
# number of times against an already-initialized volume:
#   - role `platform` (LOGIN, no other attributes) is created if missing,
#     and its password is (re)set from PLATFORM_DB_PASSWORD every run, so
#     rotating the value in .env and re-running is enough;
#   - database `homeai_platform` is created owned by `platform` if missing
#     (CREATE DATABASE can't run in a DO block/transaction, hence \gexec),
#     and its owner/CONNECT privileges are re-asserted every run.
#
# Env (set by docker-compose.yml): PGHOST, PGUSER, PGPASSWORD, PGDATABASE —
# the existing superuser from POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB —
# plus PLATFORM_DB_PASSWORD.
#
# Manual re-run: docker compose run --rm db-init
set -euo pipefail

: "${PGHOST:?PGHOST must be set}"
: "${PGUSER:?PGUSER must be set}"
: "${PGPASSWORD:?PGPASSWORD must be set}"
: "${PLATFORM_DB_PASSWORD:?PLATFORM_DB_PASSWORD must be set (see .env.example)}"
export PGDATABASE="${PGDATABASE:-postgres}"

for _ in $(seq 1 60); do
  pg_isready -q && break
  sleep 1
done
pg_isready

psql -X -q -v ON_ERROR_STOP=1 -v platform_password="${PLATFORM_DB_PASSWORD}" <<'SQL'
SELECT 'CREATE ROLE platform'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'platform')\gexec
ALTER ROLE platform WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'platform_password';

SELECT 'CREATE DATABASE homeai_platform OWNER platform'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'homeai_platform')\gexec
ALTER DATABASE homeai_platform OWNER TO platform;
REVOKE ALL ON DATABASE homeai_platform FROM PUBLIC;
SQL

echo "db-init: role 'platform' and database 'homeai_platform' are in place"
