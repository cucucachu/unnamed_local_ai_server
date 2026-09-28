-- App data migrations (docs/PLATFORM.md §7 "Data"): every plan the platform
-- applied to an instance's SQLite database, or holds for approval. A plan
-- with a destructive step waits as `pending` (at most one per instance; a
-- newer plan supersedes it) until approved or rejected; `schema_sql` is
-- what approving applies. `snapshot` names the file in
-- apps/<instance_id>/snapshots/ taken just before applying.

CREATE TABLE app_migrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instance_id UUID NOT NULL REFERENCES app_instances (id) ON DELETE CASCADE,
    status TEXT NOT NULL
        CHECK (status IN ('pending', 'applied', 'failed', 'rejected', 'superseded')),
    schema_sql TEXT NOT NULL,
    steps JSONB NOT NULL,
    summary JSONB NOT NULL,
    needs_approval BOOLEAN NOT NULL,
    snapshot TEXT,
    error TEXT,
    created_by UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by UUID REFERENCES users (id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX app_migrations_pending_key ON app_migrations (instance_id)
    WHERE status = 'pending';
CREATE INDEX app_migrations_instance_idx ON app_migrations (instance_id, created_at);
