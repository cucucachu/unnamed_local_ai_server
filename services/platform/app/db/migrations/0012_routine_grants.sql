-- Routine grants (M17-03, docs/PLATFORM.md §4 "Routine grant"). A grant is a
-- session row bound to one agent-server routine and one space: delegations
-- minted from it go through the same per-request session re-check as a
-- chat's, and every session revocation path (sign-out, password change,
-- disable, Settings -> Sessions) kills it too. It is never a login: the
-- interactive token lookup skips these rows, and its secret has its own
-- `hr_` prefix.

ALTER TABLE sessions
    ADD COLUMN routine_id TEXT CHECK (routine_id ~ '^[A-Za-z0-9_-]{1,64}$'),
    ADD COLUMN routine_space_id UUID REFERENCES spaces (id) ON DELETE CASCADE,
    ADD CONSTRAINT sessions_routine_scope_check
        CHECK ((routine_id IS NULL) = (routine_space_id IS NULL));

CREATE UNIQUE INDEX sessions_routine_idx ON sessions (user_id, routine_id)
    WHERE routine_id IS NOT NULL AND revoked_at IS NULL;
