-- Bookkeeping for app/db/migrate.py. The runner tolerates this table not
-- existing yet (it's what this very file creates).
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Singleton platform-wide values (e.g. `bootstrap_admin_id`, M10-03).
CREATE TABLE platform_state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
