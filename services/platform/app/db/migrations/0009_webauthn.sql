-- Passkeys / WebAuthn (M15-04). Challenges live in Postgres so they survive
-- a process restart (unlike HITL's in-memory TTL). RP ID is config, not a
-- column: empty WEBAUTHN_RP_ID and HOMEAI_DOMAIN means passkeys are off.

ALTER TABLE users
    ADD COLUMN require_passkeys BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE webauthn_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    credential_id BYTEA NOT NULL UNIQUE,
    public_key BYTEA NOT NULL,
    sign_count BIGINT NOT NULL,
    transports TEXT[],
    name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ
);
CREATE INDEX webauthn_credentials_user_id_idx ON webauthn_credentials (user_id);

CREATE TABLE webauthn_challenges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    purpose TEXT NOT NULL CHECK (purpose IN ('register', 'login', 'step_up')),
    user_id UUID REFERENCES users (id) ON DELETE CASCADE,
    challenge BYTEA NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX webauthn_challenges_expires_idx ON webauthn_challenges (expires_at);
