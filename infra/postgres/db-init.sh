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
#   - role `agent` (M11-04; same attributes, password from AGENT_DB_PASSWORD)
#     is what agent-server connects as. Its database is PGDATABASE (compose:
#     POSTGRES_DB, `homeai`), which stays owned by the superuser: `agent`
#     gets CONNECT/TEMPORARY on it and USAGE/CREATE on schema `public`, and
#     owns every table, sequence, view, type and routine in `public` — the
#     ones agent-server's own startup DDL and LangGraph's migrations created
#     as the superuser before this role existed are handed over here, rows
#     untouched. Each run hands over whatever isn't `agent`'s yet.
#   - role `agent_rls_bypass` (#194; NOLOGIN, BYPASSRLS) is the one way past
#     the row-level security agent-server puts on its tables: `agent` may
#     only `SET ROLE` to it (no inherited privileges), and agent-server does
#     so inside a transaction just for handing pre-Stage-3 data to the
#     bootstrap admin. It owns nothing; agent-server's startup DDL grants it
#     the few table privileges that needs. Only a superuser can create a
#     BYPASSRLS role, hence here.
#   - PUBLIC loses CONNECT on both application databases and on `postgres`
#     and `template1`, so each role reaches only its own database.
#
# Env (set by docker-compose.yml): PGHOST, PGUSER, PGPASSWORD, PGDATABASE —
# the existing superuser from POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB —
# plus PLATFORM_DB_PASSWORD and AGENT_DB_PASSWORD.
#
# Manual re-run: docker compose run --rm db-init
set -euo pipefail

: "${PGHOST:?PGHOST must be set}"
: "${PGUSER:?PGUSER must be set}"
: "${PGPASSWORD:?PGPASSWORD must be set}"
: "${PLATFORM_DB_PASSWORD:?PLATFORM_DB_PASSWORD must be set (see .env.example)}"
: "${AGENT_DB_PASSWORD:?AGENT_DB_PASSWORD must be set (see .env.example)}"
export PGDATABASE="${PGDATABASE:-postgres}"
AGENT_DB="$PGDATABASE"
case "$AGENT_DB" in
  postgres | template0 | template1 | homeai_platform)
    echo "db-init: POSTGRES_DB must name agent-server's own database, not '$AGENT_DB'" >&2
    exit 1
    ;;
esac

for _ in $(seq 1 60); do
  pg_isready -q && break
  sleep 1
done
pg_isready

psql -X -q -v ON_ERROR_STOP=1 -v platform_password="${PLATFORM_DB_PASSWORD}" \
  -v agent_password="${AGENT_DB_PASSWORD}" -v agent_db="${AGENT_DB}" -d postgres <<'SQL'
SELECT 'CREATE ROLE platform'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'platform')\gexec
ALTER ROLE platform WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'platform_password';

SELECT 'CREATE DATABASE homeai_platform OWNER platform'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'homeai_platform')\gexec
ALTER DATABASE homeai_platform OWNER TO platform;
REVOKE ALL ON DATABASE homeai_platform FROM PUBLIC;

SELECT 'CREATE ROLE agent'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent')\gexec
ALTER ROLE agent WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'agent_password';

SELECT 'CREATE ROLE agent_rls_bypass'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_rls_bypass')\gexec
ALTER ROLE agent_rls_bypass WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION
  BYPASSRLS;
GRANT agent_rls_bypass TO agent WITH INHERIT FALSE, SET TRUE, ADMIN FALSE;

SELECT format('CREATE DATABASE %I', :'agent_db')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'agent_db')\gexec
REVOKE ALL ON DATABASE :"agent_db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"agent_db" TO agent;

REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;
SQL

psql -X -q -v ON_ERROR_STOP=1 -d "$AGENT_DB" <<'SQL'
GRANT USAGE, CREATE ON SCHEMA public TO agent;

-- ALTER ... OWNER waits for an ACCESS EXCLUSIVE lock; a running agent-server
-- holds only brief ones, so a long wait means something is wrong.
SET lock_timeout = '60s';
DO $$
DECLARE
  obj record;
BEGIN
  FOR obj IN
    SELECT CASE c.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
             WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE'
             ELSE 'TABLE' END AS kind,
           c.oid::regclass::text AS name
    FROM pg_class c
    WHERE c.relnamespace = 'public'::regnamespace
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
      AND c.relowner <> 'agent'::regrole
      -- Owned/identity sequences move with their table.
      AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                      AND d.objid = c.oid
                      AND (d.deptype = 'e' OR (c.relkind = 'S' AND d.deptype IN ('a', 'i'))))
    UNION ALL
    SELECT 'TYPE', t.oid::regtype::text
    FROM pg_type t
    WHERE t.typnamespace = 'public'::regnamespace
      AND t.typowner <> 'agent'::regrole
      AND (t.typtype IN ('e', 'd', 'r')
           OR (t.typtype = 'c' AND (SELECT relkind FROM pg_class WHERE oid = t.typrelid) = 'c'))
      AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid = 'pg_type'::regclass
                      AND d.objid = t.oid AND d.deptype = 'e')
    UNION ALL
    SELECT 'ROUTINE', p.oid::regprocedure::text
    FROM pg_proc p
    WHERE p.pronamespace = 'public'::regnamespace
      AND p.proowner <> 'agent'::regrole
      AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass
                      AND d.objid = p.oid AND d.deptype = 'e')
  LOOP
    EXECUTE format('ALTER %s %s OWNER TO agent', obj.kind, obj.name);
    RAISE NOTICE 'db-init: % % is now owned by agent', lower(obj.kind), obj.name;
  END LOOP;
END
$$;
SQL

echo "db-init: roles 'platform', 'agent' and 'agent_rls_bypass', databases 'homeai_platform' and '${AGENT_DB}' are in place"
