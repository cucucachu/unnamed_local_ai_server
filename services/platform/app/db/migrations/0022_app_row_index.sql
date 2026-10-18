-- App rows in search (M22-07, docs/APP_TEMPLATES.md §5.2, docs/PLATFORM.md
-- §5 "File search"). The rows an app declares in `homeai.index`, rendered
-- and chunked next to the files' chunks, searched together with them. Like
-- the file index, only ever a cache of the instances' databases.

-- One row per instance the indexer has read: `declaration_hash` is the
-- `homeai.index` (and table set) it indexed with; a different one re-indexes
-- the instance from scratch.
CREATE TABLE indexed_instances (
    instance_id UUID PRIMARY KEY REFERENCES app_instances (id) ON DELETE CASCADE,
    declaration_hash TEXT NOT NULL,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per indexed app row: `row_id` is SQLite's rowid, `row_updated_at`
-- the row's `_updated_at` when it was rendered (a different one re-renders it).
CREATE TABLE indexed_app_rows (
    instance_id UUID NOT NULL REFERENCES indexed_instances (instance_id) ON DELETE CASCADE,
    space_id UUID NOT NULL REFERENCES spaces (id) ON DELETE CASCADE,
    tbl TEXT NOT NULL,
    row_id BIGINT NOT NULL,
    row_updated_at TEXT,
    title TEXT NOT NULL,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instance_id, tbl, row_id)
);

-- As `file_chunks`: `model`/`embedding` are NULL until embedded.
CREATE TABLE app_row_chunks (
    instance_id UUID NOT NULL,
    space_id UUID NOT NULL,
    tbl TEXT NOT NULL,
    row_id BIGINT NOT NULL,
    chunk_no INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model TEXT,
    embedding vector(768),
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    PRIMARY KEY (instance_id, tbl, row_id, chunk_no),
    FOREIGN KEY (instance_id, tbl, row_id)
        REFERENCES indexed_app_rows (instance_id, tbl, row_id) ON DELETE CASCADE
);
CREATE INDEX app_row_chunks_embedding_idx ON app_row_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX app_row_chunks_tsv_idx ON app_row_chunks USING gin (tsv);
CREATE INDEX app_row_chunks_space_idx ON app_row_chunks (space_id);
