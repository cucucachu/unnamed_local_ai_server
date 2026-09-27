-- Spaces and memberships (docs/PLATFORM.md §5). Every user has exactly one
-- personal space (created with the user; pre-existing users are backfilled
-- at startup); shared spaces have owner/editor/viewer members. Slugs are
-- immutable. Each space's GID owns its directory tree under SPACES_DIR.

CREATE SEQUENCE space_gid_seq START WITH 30000 MINVALUE 30000;

CREATE TABLE spaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('personal', 'shared')),
    gid INTEGER NOT NULL UNIQUE DEFAULT nextval('space_gid_seq'),
    -- A user row can't be deleted while it has a personal space: deleting a
    -- user must archive the space, never silently drop it.
    owner_user_id UUID REFERENCES users (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ,
    CHECK ((kind = 'personal') = (owner_user_id IS NOT NULL))
);
ALTER SEQUENCE space_gid_seq OWNED BY spaces.gid;
CREATE UNIQUE INDEX spaces_personal_owner_key ON spaces (owner_user_id) WHERE kind = 'personal';

CREATE TABLE space_members (
    space_id UUID NOT NULL REFERENCES spaces (id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (space_id, user_id)
);
CREATE INDEX space_members_user_id_idx ON space_members (user_id);

-- A personal space's only member is its owner, as `owner`.
CREATE FUNCTION space_members_personal_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM spaces
        WHERE id = NEW.space_id AND kind = 'personal'
          AND (owner_user_id <> NEW.user_id OR NEW.role <> 'owner')
    ) THEN
        RAISE EXCEPTION 'personal spaces cannot gain members'
            USING ERRCODE = 'check_violation', CONSTRAINT = 'space_members_personal_guard';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER space_members_personal_guard
    BEFORE INSERT OR UPDATE ON space_members
    FOR EACH ROW EXECUTE FUNCTION space_members_personal_guard();
