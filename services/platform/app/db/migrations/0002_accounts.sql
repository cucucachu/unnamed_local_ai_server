-- Users, sessions, invites (docs/PLATFORM.md §4). Session and invite tokens
-- are stored only as SHA-256 digests; the plaintext is returned once and
-- never persisted.

CREATE SEQUENCE user_uid_seq START WITH 20000 MINVALUE 20000;

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT NOT NULL UNIQUE CHECK (username ~ '^[a-z0-9][a-z0-9._-]{0,31}$'),
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
    uid INTEGER NOT NULL UNIQUE DEFAULT nextval('user_uid_seq'),
    password_hash TEXT NOT NULL,
    totp_secret TEXT,
    -- Enrolled but not yet confirmed with a valid code.
    totp_pending_secret TEXT,
    -- Last accepted TOTP time step, so a code can't be replayed.
    totp_last_step BIGINT,
    disabled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER SEQUENCE user_uid_seq OWNED BY users.uid;

CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash BYTEA NOT NULL UNIQUE,
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    device_label TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    stepped_up_until TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX sessions_user_id_idx ON sessions (user_id);

CREATE TABLE invites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash BYTEA NOT NULL UNIQUE,
    label TEXT,
    created_by UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    used_by UUID REFERENCES users (id) ON DELETE SET NULL,
    revoked_at TIMESTAMPTZ
);
