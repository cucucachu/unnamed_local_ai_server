-- App registry (docs/PLATFORM.md §7 "Registry, lifecycle, sharing"). An app's
-- source is the folder /<space>/Apps/<slug>/ of its source space; each app
-- has one `working` version (the source as last validated) and, from M14,
-- published ones. An instance is an app installed in a space, with its data
-- under SPACES_DIR/<space_id>/apps/<instance_id>/. Nothing is hard-deleted
-- by the product: apps are archived and uninstalled instances keep their row
-- (the ON DELETE clauses only serve test and e2e cleanup).

CREATE TABLE apps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,39}$'),
    name TEXT NOT NULL,
    source_space_id UUID NOT NULL REFERENCES spaces (id) ON DELETE CASCADE,
    -- Virtual path as the source space's members see it (/personal/... for a
    -- personal space, whose only member is its owner).
    source_path TEXT NOT NULL,
    created_by UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ,
    UNIQUE (source_space_id, slug)
);

CREATE TABLE app_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_id UUID NOT NULL REFERENCES apps (id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('working', 'published')),
    commit TEXT,
    manifest JSONB NOT NULL,
    bundle_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ,
    CHECK ((kind = 'published') = (published_at IS NOT NULL))
);
CREATE UNIQUE INDEX app_versions_working_key ON app_versions (app_id) WHERE kind = 'working';
CREATE UNIQUE INDEX app_versions_published_key ON app_versions (app_id, version)
    WHERE kind = 'published';

CREATE TABLE app_instances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_id UUID NOT NULL REFERENCES apps (id) ON DELETE CASCADE,
    space_id UUID NOT NULL REFERENCES spaces (id) ON DELETE CASCADE,
    -- `working` follows the source; `version` pins version_id.
    tracks TEXT NOT NULL CHECK (tracks IN ('working', 'version')),
    version_id UUID REFERENCES app_versions (id),
    installed_by UUID REFERENCES users (id) ON DELETE SET NULL,
    granted_permissions JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    uninstalled_at TIMESTAMPTZ,
    CHECK ((tracks = 'version') = (version_id IS NOT NULL))
);
-- At most one live instance of an app per space (for now).
CREATE UNIQUE INDEX app_instances_app_space_key ON app_instances (app_id, space_id)
    WHERE uninstalled_at IS NULL;
CREATE INDEX app_instances_space_id_idx ON app_instances (space_id) WHERE uninstalled_at IS NULL;
