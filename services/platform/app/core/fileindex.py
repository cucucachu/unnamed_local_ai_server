"""File search: the index of the spaces' text files and hybrid search over it.

docs/MEMORY.md §5, docs/PLATFORM.md §5 "File search". The index lives in
`indexed_files` and `file_chunks` (migration 0019) and is only ever a cache
of the files: every row can be rebuilt from disk.

`FileIndex` runs one background task. Writes through the files API queue
their paths (`changed`), and a scan every `scan_interval_s` compares every
indexable file's modification time and size with what was indexed, which
catches writes that bypass the API (`execute_code` writes straight to disk).
Chunks are stored first and embedded afterwards, so full-text search sees a
file before the embeddings service has looked at it, and the service being
down only delays the vector half. A changed embeddings model re-embeds every
chunk; search only uses vectors from the current one.

Search merges a vector and a full-text ranking by reciprocal rank fusion:
small embedding models are weak on names and exact words, which personal
notes are full of, and full-text search is blind to paraphrase.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import stat
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import batched
from typing import Protocol
from uuid import UUID

import anyio.to_thread
import httpx
from psycopg_pool import AsyncConnectionPool

from app.core import beneath, vfs
from app.core.errors import NotFound
from app.core.storage import SpaceStorage

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 1024 * 1024
CHUNK_CHARS = 1500
CHUNK_OVERLAP_CHARS = 200
# A chunk that is one very long line is cut here, so it fits the model's context.
MAX_CHUNK_CHARS = 1800
SNIPPET_CHARS = 400
EMBEDDING_DIMS = 768
EMBED_BATCH = 16
EMBED_RETRY_S = 60.0
# Candidates each ranking contributes before fusion, and the fusion constant.
CANDIDATES = 50
RRF_K = 60

MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdx")
SKIP_DIRS = frozenset(
    {"node_modules", "__pycache__", "dist", "build", "target", "venv", "site-packages"}
)
SKIP_FILES = frozenset(
    {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Cargo.lock"}
)

_OPEN_READ = os.O_RDONLY | os.O_NONBLOCK
_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# `path` is the file, or below the directory, `%s` names ('' is the whole space).
_UNDER = "(%s = '' OR path = %s OR starts_with(path, %s || '/'))"


# --- chunking -----------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    line_start: int
    line_end: int
    content: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


_HEADING = re.compile(r"^#{1,6}\s")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _sections(lines: list[str]) -> list[tuple[int, int]]:
    """Markdown: [start, end) line ranges, each starting at a heading outside a code fence."""
    starts = [0]
    fenced = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and i and _HEADING.match(line):
            starts.append(i)
    return list(zip(starts, [*starts[1:], len(lines)], strict=True))


def _size(lines: list[str], start: int, end: int) -> int:
    return sum(len(line) + 1 for line in lines[start:end])


def _windows(lines: list[str], start: int, end: int) -> Iterator[tuple[int, int]]:
    """~CHUNK_CHARS windows on line boundaries, each overlapping the last by a few lines."""
    i = start
    while i < end:
        j, size = i, 0
        while j < end and (j == i or size + len(lines[j]) + 1 <= CHUNK_CHARS):
            size += len(lines[j]) + 1
            j += 1
        yield i, j
        if j >= end:
            return
        k, back = j, 0
        while k - 1 > i and back + len(lines[k - 1]) + 1 <= CHUNK_OVERLAP_CHARS:
            k -= 1
            back += len(lines[k]) + 1
        i = k


def chunk_text(text: str, *, markdown: bool) -> list[Chunk]:
    """Markdown splits at headings, small sections packed together; anything else (and a
    section too big for one chunk) goes in overlapping windows. Line numbers are 1-based."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    pieces: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    current_size = 0
    for start, end in _sections(lines) if markdown else [(0, len(lines))]:
        size = _size(lines, start, end)
        if size > CHUNK_CHARS:
            if current:
                pieces.append(current)
                current = None
            pieces.extend(_windows(lines, start, end))
        elif current and current_size + size <= CHUNK_CHARS:
            current = (current[0], end)
            current_size += size
        else:
            if current:
                pieces.append(current)
            current, current_size = (start, end), size
    if current:
        pieces.append(current)

    chunks = []
    for start, end in pieces:
        while start < end and not lines[start].strip():
            start += 1
        while end > start and not lines[end - 1].strip():
            end -= 1
        if start < end:
            content = "\n".join(lines[start:end])[:MAX_CHUNK_CHARS]
            chunks.append(Chunk(start + 1, end, content))
    return chunks


# --- the filesystem side (blocking; run in a worker thread) ---------------------------


def _skipped_dir(name: str) -> bool:
    return name.startswith(".") or name in SKIP_DIRS


def _indexable_file(name: str, st: os.stat_result) -> bool:
    return (
        stat.S_ISREG(st.st_mode)
        and not name.startswith(".")
        and name not in SKIP_FILES
        and st.st_size <= MAX_FILE_BYTES
    )


def _walk(dir_fd: int, rel: tuple[str, ...], out: dict[str, tuple[int, int]]) -> None:
    try:
        with os.scandir(dir_fd) as it:
            entries = list(it)
    except OSError:
        return
    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            if _skipped_dir(entry.name):
                continue
            try:
                fd = os.open(entry.name, _OPEN_DIR, dir_fd=dir_fd)
            except OSError:
                continue
            try:
                _walk(fd, (*rel, entry.name), out)
            finally:
                os.close(fd)
        elif _indexable_file(entry.name, st):
            out["/".join((*rel, entry.name))] = (st.st_mtime_ns, st.st_size)


def stat_tree(
    storage: SpaceStorage, space_id: UUID, rel: tuple[str, ...]
) -> dict[str, tuple[int, int]] | None:
    """{path: (mtime_ns, size)} of the indexable files at or below `rel` (symlinks aren't
    followed); None if the space's `files/` dir can't be opened (so nothing is dropped)."""
    if any(_skipped_dir(part) for part in rel[:-1]):
        return {}
    found: dict[str, tuple[int, int]] = {}
    try:
        with vfs.open_files_root(storage, space_id) as root:
            try:
                fd = beneath.open(root, rel, _OPEN_READ, follow=False, symlinks=False)
            except OSError:
                return found
            try:
                st = os.fstat(fd)
                if stat.S_ISDIR(st.st_mode):
                    if not (rel and _skipped_dir(rel[-1])):
                        _walk(fd, rel, found)
                elif rel and _indexable_file(rel[-1], st):
                    found["/".join(rel)] = (st.st_mtime_ns, st.st_size)
            finally:
                os.close(fd)
    except NotFound:
        return None
    return found


@dataclass(frozen=True)
class FileText:
    # None: binary (a NUL byte, or not UTF-8); indexed with no chunks.
    text: str | None
    mtime_ns: int
    size: int


def read_text(storage: SpaceStorage, space_id: UUID, path: str) -> FileText | None:
    """The file's text, or None if it's gone or no longer indexable."""
    parts = tuple(path.split("/"))
    try:
        with vfs.open_files_root(storage, space_id) as root:
            fd = beneath.open(root, parts, _OPEN_READ, follow=False, symlinks=False)
            with os.fdopen(fd, "rb") as f:
                st = os.fstat(f.fileno())
                if not _indexable_file(parts[-1], st):
                    return None
                data = f.read(MAX_FILE_BYTES + 1)
    except (NotFound, OSError):
        return None
    if len(data) > MAX_FILE_BYTES:
        return None
    text: str | None = None
    if b"\x00" not in data:
        try:
            text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            pass
    return FileText(text, st.st_mtime_ns, st.st_size)


# --- the embeddings service --------------------------------------------------------


class EmbeddingsUnavailable(Exception):
    """The service is down or unconfigured: try again later."""


class EmbeddingRejected(Exception):
    """The service refused these inputs (e.g. too long): retrying them won't help."""


class Embedder(Protocol):
    async def model(self) -> str: ...

    async def embed_documents(self, docs: Sequence[tuple[str, str]]) -> list[list[float]]:
        """One vector per (title, text)."""
        ...

    async def embed_query(self, query: str) -> list[float]: ...

    async def aclose(self) -> None: ...


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


class HttpEmbedder:
    """llama.cpp's `llama-server --embedding` (the `embeddings` service).

    The model id is the served file's name, so swapping the weights re-embeds
    the index. EmbeddingGemma wants task prompts on both sides; other models
    get the raw text.
    """

    MODEL_TTL_S = 60.0

    def __init__(self, url: str, *, timeout_s: float = 60.0, query_timeout_s: float = 10.0):
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=timeout_s)
        self._query_timeout_s = query_timeout_s
        self._model: str | None = None
        self._model_at = 0.0

    async def model(self) -> str:
        if self._model is not None and time.monotonic() - self._model_at < self.MODEL_TTL_S:
            return self._model
        try:
            response = await self._client.get("/v1/models", timeout=self._query_timeout_s)
            response.raise_for_status()
            model = os.path.basename(response.json()["data"][0]["id"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise EmbeddingsUnavailable(f"model lookup failed: {exc!r}") from exc
        self._model, self._model_at = model, time.monotonic()
        return model

    def _gemma(self) -> bool:
        return "embeddinggemma" in (self._model or "").lower()

    async def _embed(self, texts: list[str], timeout_s: float | None = None) -> list[list[float]]:
        try:
            response = await self._client.post(
                "/v1/embeddings",
                json={"input": texts},
                timeout=timeout_s or httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingsUnavailable(repr(exc)) from exc
        if response.status_code == 400:
            raise EmbeddingRejected(response.text[:200])
        if response.status_code != 200:
            raise EmbeddingsUnavailable(f"HTTP {response.status_code}: {response.text[:200]}")
        try:
            data = sorted(response.json()["data"], key=lambda d: d["index"])
            vectors = [d["embedding"] for d in data]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingsUnavailable(f"bad response: {exc!r}") from exc
        if len(vectors) != len(texts) or any(len(v) != EMBEDDING_DIMS for v in vectors):
            raise EmbeddingsUnavailable(
                f"expected {len(texts)} vectors of {EMBEDDING_DIMS} dimensions"
            )
        return vectors

    async def embed_documents(self, docs: Sequence[tuple[str, str]]) -> list[list[float]]:
        await self.model()
        if self._gemma():
            texts = [f"title: {title or 'none'} | text: {text}" for title, text in docs]
        else:
            texts = [text for _, text in docs]
        return await self._embed(texts)

    async def embed_query(self, query: str) -> list[float]:
        await self.model()
        text = f"task: search result | query: {query}" if self._gemma() else query
        return (await self._embed([text], self._query_timeout_s))[0]

    async def aclose(self) -> None:
        await self._client.aclose()


# --- the indexer ----------------------------------------------------------------------


class FileIndex:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        storage: SpaceStorage,
        embedder: Embedder | None,
        *,
        scan_interval_s: float = 300.0,
        enabled: bool = True,
    ) -> None:
        self.pool = pool
        self.storage = storage
        self.embedder = embedder
        self.scan_interval_s = scan_interval_s
        self.enabled = enabled
        self._pending: set[tuple[UUID, tuple[str, ...]]] = set()
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._embed_retry_at = 0.0
        self._embed_failing = False

    def changed(self, space_id: UUID, rel: tuple[str, ...]) -> None:
        """Queue a file or directory for re-indexing; returns at once."""
        if self.enabled:
            self._pending.add((space_id, rel))
            self._wake.set()

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._run(), name="file-index")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.embedder is not None:
            await self.embedder.aclose()

    async def _run(self) -> None:
        next_scan = time.monotonic()
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), max(0.0, next_scan - time.monotonic()))
            except TimeoutError:
                pass
            self._wake.clear()
            try:
                if time.monotonic() >= next_scan:
                    next_scan = time.monotonic() + self.scan_interval_s
                    await self.scan()
                else:
                    await self.drain()
            except Exception:
                logger.exception("file index: indexing failed")
                await asyncio.sleep(5)

    async def drain(self) -> None:
        """Index everything queued, then embed whatever isn't yet."""
        async with self._lock:
            await self._process_pending()
            await self._embed_pending()

    async def scan(self) -> None:
        """Reconcile every active space with disk, drop archived spaces' rows, then drain."""
        async with self._lock:
            async with self.pool.connection() as conn:
                await conn.execute(
                    "DELETE FROM indexed_files WHERE space_id IN "
                    "(SELECT id FROM spaces WHERE archived_at IS NOT NULL)"
                )
                cur = await conn.execute(
                    "SELECT id FROM spaces WHERE archived_at IS NULL ORDER BY created_at, id"
                )
                space_ids = [row["id"] for row in await cur.fetchall()]
            for space_id in space_ids:
                await self._reconcile(space_id, ())
            await self._process_pending()
            await self._embed_pending()

    async def _process_pending(self) -> None:
        while self._pending:
            space_id, rel = self._pending.pop()
            await self._reconcile(space_id, rel)

    async def _reconcile(self, space_id: UUID, rel: tuple[str, ...]) -> None:
        found = await anyio.to_thread.run_sync(stat_tree, self.storage, space_id, rel)
        if found is None:
            return
        prefix = "/".join(rel)
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT path, file_mtime_ns, file_size FROM indexed_files "
                f"WHERE space_id = %s AND {_UNDER}",
                (space_id, prefix, prefix, prefix),
            )
            known = {r["path"]: (r["file_mtime_ns"], r["file_size"]) for r in await cur.fetchall()}
            gone = [path for path in known if path not in found]
            if gone:
                await conn.execute(
                    "DELETE FROM indexed_files WHERE space_id = %s AND path = ANY(%s)",
                    (space_id, gone),
                )
        for path, signature in sorted(found.items()):
            if known.get(path) != signature:
                await self._index_file(space_id, path)

    async def _index_file(self, space_id: UUID, path: str) -> None:
        read = await anyio.to_thread.run_sync(read_text, self.storage, space_id, path)
        async with self.pool.connection() as conn, conn.transaction():
            if read is None:
                await conn.execute(
                    "DELETE FROM indexed_files WHERE space_id = %s AND path = %s", (space_id, path)
                )
                return
            chunks = []
            if read.text:
                chunks = chunk_text(read.text, markdown=path.lower().endswith(MARKDOWN_SUFFIXES))
            cur = await conn.execute(
                "SELECT content_hash, model, embedding::text AS embedding FROM file_chunks "
                "WHERE space_id = %s AND path = %s AND model IS NOT NULL",
                (space_id, path),
            )
            reuse = {r["content_hash"]: (r["model"], r["embedding"]) for r in await cur.fetchall()}
            await conn.execute(
                "INSERT INTO indexed_files (space_id, path, file_mtime_ns, file_size) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (space_id, path) DO UPDATE SET "
                "file_mtime_ns = EXCLUDED.file_mtime_ns, file_size = EXCLUDED.file_size, "
                "indexed_at = now()",
                (space_id, path, read.mtime_ns, read.size),
            )
            await conn.execute(
                "DELETE FROM file_chunks WHERE space_id = %s AND path = %s", (space_id, path)
            )
            rows = []
            for chunk_no, chunk in enumerate(chunks):
                model, embedding = reuse.get(chunk.content_hash, (None, None))
                rows.append((
                    space_id, path, chunk_no, chunk.line_start, chunk.line_end,
                    chunk.content, chunk.content_hash, model, embedding,
                ))  # fmt: skip
            if rows:
                async with conn.cursor() as cur:
                    await cur.executemany(
                        "INSERT INTO file_chunks (space_id, path, chunk_no, line_start, line_end, "
                        "content, content_hash, model, embedding) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)",
                        rows,
                    )

    async def _embed_pending(self) -> None:
        if self.embedder is None or time.monotonic() < self._embed_retry_at:
            return
        try:
            model = await self.embedder.model()
            while True:
                async with self.pool.connection() as conn:
                    cur = await conn.execute(
                        "SELECT space_id, path, chunk_no, content, content_hash FROM file_chunks "
                        "WHERE model IS DISTINCT FROM %s ORDER BY space_id, path, chunk_no "
                        "LIMIT 256",
                        (model,),
                    )
                    todo = await cur.fetchall()
                if not todo:
                    break
                for batch in batched(todo, EMBED_BATCH):
                    await self._embed_batch(model, list(batch))
                    if self._pending:
                        await self._process_pending()
        except EmbeddingsUnavailable as exc:
            self._embed_retry_at = time.monotonic() + EMBED_RETRY_S
            if not self._embed_failing:
                logger.warning("file index: embeddings unavailable, will retry: %s", exc)
            self._embed_failing = True
            return
        if self._embed_failing:
            logger.info("file index: embeddings available again")
        self._embed_failing = False

    async def _embed_batch(self, model: str, rows: list[dict]) -> None:
        try:
            vectors = await self.embedder.embed_documents([(r["path"], r["content"]) for r in rows])
        except EmbeddingRejected as exc:
            if len(rows) > 1:
                for row in rows:
                    await self._embed_batch(model, [row])
                return
            # Marked done without a vector: full-text search still finds it.
            logger.warning("file index: %s chunk %d not embedded: %s",
                           rows[0]["path"], rows[0]["chunk_no"], exc)  # fmt: skip
            vectors = [None]
        updates = [
            (
                model,
                vector_literal(v) if v is not None else None,
                *(r["space_id"], r["path"], r["chunk_no"], r["content_hash"]),
            )
            for r, v in zip(rows, vectors, strict=True)
        ]
        async with self.pool.connection() as conn, conn.cursor() as cur:
            await cur.executemany(
                "UPDATE file_chunks SET model = %s, embedding = %s::vector "
                "WHERE space_id = %s AND path = %s AND chunk_no = %s AND content_hash = %s",
                updates,
            )


# --- search ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    space_id: UUID
    path: str
    line_start: int
    line_end: int
    snippet: str
    score: float


_FTS = f"""
fts AS (
    SELECT space_id, path, chunk_no, row_number() OVER (ORDER BY r DESC) AS rank
    FROM (
        SELECT space_id, path, chunk_no, ts_rank_cd(tsv, q) AS r
        FROM file_chunks,
             (SELECT replace(plainto_tsquery('english', %(query)s)::text, ' & ', ' | ')::tsquery
              AS q) tq
        WHERE tsv @@ q AND space_id = ANY(%(ids)s)
          AND (%(prefix)s = '' OR path = %(prefix)s OR starts_with(path, %(prefix)s || '/'))
        ORDER BY r DESC LIMIT {CANDIDATES}
    ) f
)"""

_VEC = f"""
vec AS (
    SELECT space_id, path, chunk_no, row_number() OVER (ORDER BY d) AS rank
    FROM (
        SELECT space_id, path, chunk_no, embedding <=> %(vector)s::vector AS d
        FROM file_chunks
        WHERE model = %(model)s AND embedding IS NOT NULL AND space_id = ANY(%(ids)s)
          AND (%(prefix)s = '' OR path = %(prefix)s OR starts_with(path, %(prefix)s || '/'))
        ORDER BY embedding <=> %(vector)s::vector LIMIT {CANDIDATES}
    ) v
)"""

_FUSE = f"""
SELECT c.space_id, c.path, c.line_start, c.line_end, c.content,
       sum(1.0 / ({RRF_K} + r.rank))::float8 AS score
FROM ranked r JOIN file_chunks c USING (space_id, path, chunk_no)
GROUP BY c.space_id, c.path, c.chunk_no
ORDER BY score DESC, c.path, c.chunk_no
LIMIT %(limit)s"""


def snippet(content: str, query: str) -> str:
    """Up to SNIPPET_CHARS of the chunk, from the first line mentioning a query word."""
    words = {w for w in re.findall(r"\w+", query.lower()) if len(w) >= 3}
    lines = content.split("\n")
    first = next((i for i, line in enumerate(lines) if any(w in line.lower() for w in words)), 0)
    text = "\n".join(lines[first:]).strip()
    if len(text) <= SNIPPET_CHARS:
        return text
    cut = text.rfind(" ", 0, SNIPPET_CHARS)
    return text[: cut if cut > SNIPPET_CHARS // 2 else SNIPPET_CHARS].rstrip() + "…"


async def search(
    pool: AsyncConnectionPool,
    embedder: Embedder | None,
    space_ids: list[UUID],
    prefix: str,
    query: str,
    limit: int,
) -> tuple[list[Hit], bool]:
    """(hits best first, whether the vector half ran) over `space_ids`, under `prefix`."""
    if not space_ids:
        return [], False
    params: dict = {"query": query, "ids": space_ids, "prefix": prefix, "limit": limit}
    semantic = False
    if embedder is not None:
        try:
            params["model"] = await embedder.model()
            params["vector"] = vector_literal(await embedder.embed_query(query))
            semantic = True
        except (EmbeddingsUnavailable, EmbeddingRejected) as exc:
            logger.warning("file search: full-text only: %s", exc)
    ctes = [_FTS, _VEC] if semantic else [_FTS]
    union = "SELECT * FROM fts UNION ALL SELECT * FROM vec" if semantic else "SELECT * FROM fts"
    sql = f"WITH {','.join(ctes)}, ranked AS ({union}) {_FUSE}"
    async with pool.connection() as conn, conn.transaction():
        if semantic:
            await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    hits = [
        Hit(
            space_id=r["space_id"],
            path=r["path"],
            line_start=r["line_start"],
            line_end=r["line_end"],
            snippet=snippet(r["content"], query),
            score=round(r["score"], 6),
        )
        for r in rows
    ]
    return hits, semantic
