-- M14-01: published versions are listed in chosen spaces' catalogs, and each
-- published version keeps a snapshot of the package it was built from
-- (schema, actions, source) so pinned installs don't follow the working copy.

CREATE TABLE app_catalog (
    app_id UUID NOT NULL REFERENCES apps (id) ON DELETE CASCADE,
    space_id UUID NOT NULL REFERENCES spaces (id) ON DELETE CASCADE,
    listed_by UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (app_id, space_id)
);
CREATE INDEX app_catalog_space_id_idx ON app_catalog (space_id);

ALTER TABLE app_versions ADD COLUMN source_snapshot TEXT;
