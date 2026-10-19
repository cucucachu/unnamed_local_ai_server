-- The nightly Memory run reads app changes (M22-12, docs/APP_TEMPLATES.md
-- §6.1): each indexed row also keeps its rendered text and who created and
-- last changed it (the row's `_created_by` / `_updated_by`, user ids as
-- text). Rows indexed before this have neither until they're rendered
-- again: `attributed` is false for them, and the indexer treats them as
-- changed (their embeddings are reused, the content being the same).
ALTER TABLE indexed_app_rows
    ADD COLUMN text TEXT NOT NULL DEFAULT '',
    ADD COLUMN created_by TEXT,
    ADD COLUMN updated_by TEXT,
    ADD COLUMN attributed BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX indexed_app_rows_changes_idx ON indexed_app_rows (space_id, row_updated_at);
