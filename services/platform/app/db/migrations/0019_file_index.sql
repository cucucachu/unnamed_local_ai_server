-- The file search index (M21-03, docs/MEMORY.md §5, docs/PLATFORM.md §5
-- "File search"). Rebuildable from the files at any time: nothing here is
-- the only copy of anything.
--
-- `vector` is created by db-init (it needs the superuser); IF NOT EXISTS is
-- a no-op then, and creates it where the migrating role is a superuser.

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per file the indexer has looked at, chunked or not (a binary or
-- empty file has no chunks), so the scan only rereads files whose
-- modification time or size changed. `path` is below the space's `files/`
-- dir, '/'-joined, without a leading slash.
CREATE TABLE indexed_files (
    space_id UUID NOT NULL REFERENCES spaces (id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    file_mtime_ns BIGINT NOT NULL,
    file_size BIGINT NOT NULL,
    indexed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (space_id, path)
);

-- `model`/`embedding` are NULL until the chunk is embedded, and are replaced
-- when the embeddings model changes; search uses only rows whose `model` is
-- the current one. The dimension is EmbeddingGemma's: a model with another
-- needs a new migration.
CREATE TABLE file_chunks (
    space_id UUID NOT NULL,
    path TEXT NOT NULL,
    chunk_no INTEGER NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    model TEXT,
    embedding vector(768),
    tsv tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', translate(path, '/-_.', '    ')), 'A')
        || to_tsvector('english', content)
    ) STORED,
    PRIMARY KEY (space_id, path, chunk_no),
    FOREIGN KEY (space_id, path) REFERENCES indexed_files (space_id, path) ON DELETE CASCADE
);
CREATE INDEX file_chunks_embedding_idx ON file_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX file_chunks_tsv_idx ON file_chunks USING gin (tsv);
