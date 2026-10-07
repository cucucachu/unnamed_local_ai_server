-- The audit log (docs/PLATFORM.md §4 "Audit log"): one row per thing that
-- happened on someone's behalf - an app-data write, a migration decision, an
-- install, a membership change - recorded by the platform from the verified
-- principal, never from anything an app or agent supplies. Append-only: rows
-- are never updated, deleted or truncated (the triggers refuse), so history
-- survives anything done through the API. No foreign keys on purpose: an
-- event outlives the user, space or instance it names; `actor_name` keeps
-- who it was at the time.

CREATE TABLE audit_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind TEXT NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('user', 'agent', 'system')),
    actor_user_id UUID,
    actor_name TEXT,
    session_id UUID,
    thread_id TEXT,
    space_id UUID,
    target_type TEXT,
    target_id TEXT,
    summary TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK ((actor_kind = 'system') = (actor_user_id IS NULL))
);
CREATE INDEX audit_events_space_idx ON audit_events (space_id, id DESC);
CREATE INDEX audit_events_target_idx ON audit_events (target_type, target_id, id DESC);
CREATE INDEX audit_events_actor_idx ON audit_events (actor_user_id, id DESC);

CREATE FUNCTION audit_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END
$$;
CREATE TRIGGER audit_events_no_change BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only();
