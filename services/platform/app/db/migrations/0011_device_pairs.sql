-- Host-app device pairing (M15-06, docs/PLATFORM.md §4 / §8). Hardware-
-- backed ECDSA P-256 public keys live here; private keys never leave the
-- phone. Challenges live in Postgres (like webauthn_challenges) so they
-- survive a process restart. Enroll tokens are stored only as SHA-256
-- digests; the plaintext is in the LAN QR once.
--
-- sessions.host_device_id tags a host-app login to a pair so revoking that
-- phone can drop only those sessions (distinct from sessions.device_id,
-- which tags a WireGuard peer).

CREATE TABLE device_pairs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    public_key BYTEA NOT NULL,
    name TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    UNIQUE (user_id, public_key)
);
CREATE INDEX device_pairs_user_id_idx ON device_pairs (user_id);

CREATE TABLE device_challenges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    purpose TEXT NOT NULL CHECK (purpose IN ('enroll', 'login')),
    user_id UUID REFERENCES users (id) ON DELETE CASCADE,
    device_id UUID REFERENCES device_pairs (id) ON DELETE CASCADE,
    token_hash BYTEA UNIQUE,
    challenge BYTEA NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    CHECK (
        (purpose = 'enroll' AND user_id IS NOT NULL AND device_id IS NULL
         AND token_hash IS NOT NULL)
        OR
        (purpose = 'login' AND device_id IS NOT NULL AND token_hash IS NULL)
    )
);
CREATE INDEX device_challenges_expires_idx ON device_challenges (expires_at);
CREATE INDEX device_challenges_device_id_idx ON device_challenges (device_id)
    WHERE purpose = 'login';

ALTER TABLE sessions
    ADD COLUMN host_device_id UUID REFERENCES device_pairs (id) ON DELETE SET NULL;
CREATE INDEX sessions_host_device_id_idx ON sessions (host_device_id)
    WHERE host_device_id IS NOT NULL;
