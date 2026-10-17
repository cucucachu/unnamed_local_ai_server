"""The file index and `POST /api/platform/files/search` (docs/MEMORY.md §5).

A fake embedder stands in for the embeddings service: words in one
`CONCEPTS` group share a dimension, so a paraphrase lands near its source
while full-text matching can't see it; digits carry no meaning to it, so a
code only full-text matching finds.
"""

from __future__ import annotations

import math
import re
import zlib
from collections.abc import Sequence
from itertools import pairwise

import pytest

from app.core.fileindex import (
    CHUNK_CHARS,
    EMBEDDING_DIMS,
    MAX_FILE_BYTES,
    EmbeddingsUnavailable,
    chunk_text,
)
from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.files_world import API, FILES, World, build_world
from tests.helpers import sql

CONCEPTS = [
    {"wife", "partner", "maria"},
    {"allergic", "allergy", "peanuts", "foods", "food", "avoid", "eat"},
    {"garden", "tomatoes", "plants", "grow", "growing", "vegetables"},
]
_CONCEPT_DIM = {word: i for i, group in enumerate(CONCEPTS) for word in group}


class FakeEmbedder:
    def __init__(self) -> None:
        self.model_id = "fake-1"
        self.down = False
        self.documents: list[str] = []

    def vector(self, text: str) -> list[float]:
        v = [0.0] * EMBEDDING_DIMS
        for word in re.findall(r"[a-z]+", text.lower()):
            dim = _CONCEPT_DIM.get(word)
            v[dim if dim is not None else 16 + zlib.crc32(word.encode()) % 700] += 1.0
        norm = math.sqrt(sum(x * x for x in v))
        if not norm:
            v[-1], norm = 1.0, 1.0
        return [x / norm for x in v]

    async def model(self) -> str:
        if self.down:
            raise EmbeddingsUnavailable("down")
        return self.model_id

    async def embed_documents(self, docs: Sequence[tuple[str, str]]) -> list[list[float]]:
        if self.down:
            raise EmbeddingsUnavailable("down")
        self.documents.extend(text for _, text in docs)
        return [self.vector(text) for _, text in docs]

    async def embed_query(self, query: str) -> list[float]:
        if self.down:
            raise EmbeddingsUnavailable("down")
        return self.vector(query)

    async def aclose(self) -> None:
        pass


@pytest.fixture
async def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
async def world(pg_database, tmp_path, embedder):
    settings = make_settings(
        pg_database, tmp_path, file_index_enabled=True, file_index_scan_interval_s=3600
    )
    app = create_app(settings, embedder_override=embedder)
    async with running(app) as client:
        yield await build_world(Platform(app, client, pg_database, tmp_path))


def index(world: World):
    return world.platform.app.state.file_index


async def write(world: World, user: str, path: str, content: str) -> None:
    response = await world.client.post(
        f"{FILES}/write", json={"path": path, "content": content}, headers=world.headers[user]
    )
    assert response.status_code == 200, response.text


async def search(world: World, headers: dict, query: str, path: str = "/", **body) -> dict:
    response = await world.client.post(
        f"{FILES}/search", json={"query": query, "path": path, **body}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def paths(world: World, user: str, query: str, path: str = "/") -> list[str]:
    found = await search(world, world.headers[user], query, path)
    return [hit["path"] for hit in found["results"]]


def chunks(world: World) -> dict[str, str]:
    """{path: content} of every chunk (each test's files are one chunk each)."""
    rows = sql(world.platform, "SELECT path, content FROM file_chunks")
    return {r["path"]: r["content"] for r in rows}


def chunk_paths(world: World) -> set[str]:
    return set(chunks(world))


# --- chunking ---------------------------------------------------------------------


def test_markdown_splits_at_headings_and_packs_small_sections():
    big = "word " * 250  # ~1250 chars: two don't fit one chunk
    text = f"# One\n{big}\n## Two\n{big}\n## Three\nshort\n## Four\nalso short\n"
    chunks = chunk_text(text, markdown=True)
    assert [(c.line_start, c.line_end) for c in chunks] == [(1, 2), (3, 8)]
    assert chunks[1].content.startswith("## Two\n")
    assert chunks[1].content.endswith("## Three\nshort\n## Four\nalso short")


def test_a_heading_inside_a_code_fence_does_not_split():
    text = "# Doc\n```\n# not a heading\n```\n"
    assert len(chunk_text(text, markdown=True)) == 1


def test_plain_text_windows_overlap_on_line_boundaries():
    lines = [f"line {i} " + "x" * 90 for i in range(60)]
    chunks = chunk_text("\n".join(lines), markdown=False)
    assert len(chunks) > 3
    assert all(len(c.content) <= CHUNK_CHARS for c in chunks)
    assert chunks[0].line_start == 1 and chunks[-1].line_end == 60
    for prev, nxt in pairwise(chunks):
        assert prev.line_start < nxt.line_start <= prev.line_end


# --- indexing ---------------------------------------------------------------------


async def test_write_edit_move_and_delete_keep_the_index_current(world):
    await write(world, "alice", "/personal/notes/boat.md", "# Boat\nThe sailboat is named Ophelia.")
    await index(world).drain()
    assert await paths(world, "alice", "Ophelia") == ["/personal/notes/boat.md"]

    response = await world.client.post(
        f"{FILES}/edit",
        json={"path": "/personal/notes/boat.md", "old_string": "Ophelia", "new_string": "Juno"},
        headers=world.headers["alice"],
    )
    assert response.status_code == 200, response.text
    await index(world).drain()
    assert chunks(world) == {"notes/boat.md": "# Boat\nThe sailboat is named Juno."}

    response = await world.client.post(
        f"{FILES}/move",
        json={"src": "/personal/notes", "dst": "/spaces/family/notes"},
        headers=world.headers["alice"],
    )
    assert response.status_code == 200, response.text
    await index(world).drain()
    assert await paths(world, "alice", "Juno") == ["/spaces/family/notes/boat.md"]

    response = await world.client.delete(
        FILES, params={"path": "/spaces/family/notes/boat.md"}, headers=world.headers["alice"]
    )
    assert response.status_code == 204, response.text
    await index(world).drain()
    assert chunks(world) == {}
    assert await paths(world, "alice", "Juno") == []


async def test_the_scan_picks_up_files_written_straight_to_disk(world):
    (world.home("alice") / "direct.txt").write_text("Written by execute_code: quokka census.\n")
    assert await paths(world, "alice", "quokka") == []
    await index(world).scan()
    assert await paths(world, "alice", "quokka") == ["/personal/direct.txt"]

    (world.home("alice") / "direct.txt").unlink()
    await index(world).scan()
    assert await paths(world, "alice", "quokka") == []


async def test_binaries_oversized_files_and_skipped_folders_are_not_indexed(world):
    home = world.home("alice")
    (home / "blob.bin").write_bytes(b"zanzibar\x00\x01\x02")
    (home / "latin1.txt").write_bytes("zanzibar caf\xe9".encode("latin-1"))
    (home / "huge.txt").write_text("zanzibar\n" + "y" * MAX_FILE_BYTES)
    for folder in ("node_modules", ".trash", "build", "__pycache__"):
        (home / folder).mkdir()
        (home / folder / "x.md").write_text("zanzibar")
    (home / ".env").write_text("zanzibar")
    (home / "package-lock.json").write_text('{"zanzibar": 1}')
    (home / "kept.md").write_text("zanzibar is kept")
    await index(world).scan()
    assert chunk_paths(world) == {"kept.md"}
    assert await paths(world, "alice", "zanzibar") == ["/personal/kept.md"]
    # The binaries are remembered (no chunks), so the next scan doesn't reread them.
    indexed = {r["path"] for r in sql(world.platform, "SELECT path FROM indexed_files")}
    assert indexed == {"blob.bin", "latin1.txt", "kept.md"}


async def test_unchanged_chunks_are_not_reembedded(world, embedder):
    sections = [f"## Part {n}\n" + f"part{n} " * 200 for n in range(3)]
    await write(world, "alice", "/personal/long.md", "\n".join(sections))
    await index(world).drain()
    assert len(embedder.documents) == 3

    sections[2] = "## Part 2\n" + "new " * 200
    await write(world, "alice", "/personal/long.md", "\n".join(sections))
    await index(world).drain()
    assert len(embedder.documents) == 4
    assert embedder.documents[-1].startswith("## Part 2\nnew")


async def test_a_model_change_reembeds_everything(world, embedder):
    await write(world, "alice", "/personal/a.md", "Maria is allergic to peanuts.")
    await write(world, "bob", "/spaces/family/b.md", "We grow tomatoes.")
    await index(world).drain()
    assert len(embedder.documents) == 2

    embedder.model_id = "fake-2"
    await index(world).drain()
    assert len(embedder.documents) == 4
    models = {r["model"] for r in sql(world.platform, "SELECT model FROM file_chunks")}
    assert models == {"fake-2"}


async def test_embeddings_down_delays_embedding_but_not_indexing(world, embedder):
    embedder.down = True
    await write(world, "alice", "/personal/a.md", "Maria is allergic to peanuts.")
    await index(world).drain()
    (row,) = sql(world.platform, "SELECT model, embedding FROM file_chunks")
    assert row["model"] is None and row["embedding"] is None
    found = await search(world, world.headers["alice"], "peanuts")
    assert found["semantic"] is False
    assert [hit["path"] for hit in found["results"]] == ["/personal/a.md"]

    embedder.down = False
    index(world)._embed_retry_at = 0.0
    await index(world).drain()
    (row,) = sql(world.platform, "SELECT model FROM file_chunks")
    assert row["model"] == "fake-1"


async def test_archived_spaces_drop_out_of_the_index(world):
    await write(world, "alice", "/spaces/family/plan.md", "The zeppelin plan.")
    await index(world).drain()
    response = await world.client.delete(
        f"{API}/spaces/{world.family['id']}", headers=world.headers["alice"]
    )
    assert response.status_code == 204, response.text
    assert await paths(world, "alice", "zeppelin") == []
    await index(world).scan()
    assert chunk_paths(world) == set()


# --- ranking --------------------------------------------------------------------------


async def test_hybrid_ranking_finds_both_paraphrases_and_exact_codes(world):
    await write(world, "alice", "/personal/people/maria.md", "Maria is allergic to peanuts.")
    await write(world, "alice", "/personal/gate.md", "The workshop gate code is 4471.")
    await write(world, "alice", "/personal/garden.md", "We grow tomatoes every summer.")
    await index(world).drain()

    found = await search(world, world.headers["alice"], "which foods does my wife avoid")
    assert found["semantic"] is True
    assert found["results"][0]["path"] == "/personal/people/maria.md"

    found = await search(world, world.headers["alice"], "4471")
    assert found["results"][0]["path"] == "/personal/gate.md"
    hit = found["results"][0]
    assert (hit["line_start"], hit["line_end"]) == (1, 1)
    assert "4471" in hit["snippet"] and hit["score"] > 0


async def test_embeddings_down_falls_back_to_full_text(world, embedder):
    await write(world, "alice", "/personal/people/maria.md", "Maria is allergic to peanuts.")
    await write(world, "alice", "/personal/gate.md", "The workshop gate code is 4471.")
    await index(world).drain()
    embedder.down = True

    found = await search(world, world.headers["alice"], "which foods does my wife avoid")
    assert found == {"results": [], "semantic": False}
    found = await search(world, world.headers["alice"], "4471")
    assert [hit["path"] for hit in found["results"]] == ["/personal/gate.md"]


async def test_limit_and_snippet_length(world):
    for n in range(5):
        await write(world, "alice", f"/personal/n{n}.md", "albatross " * 100)
    await index(world).drain()
    found = await search(world, world.headers["alice"], "albatross", limit=3)
    assert len(found["results"]) == 3
    assert all(len(hit["snippet"]) <= 401 for hit in found["results"])


# --- access ---------------------------------------------------------------------------


async def test_search_reaches_only_what_the_caller_can_read(world):
    await write(world, "alice", "/personal/secret.md", "alice private walrus")
    await write(world, "bob", "/personal/secret.md", "bob private walrus")
    await write(world, "alice", "/spaces/family/shared.md", "family walrus")
    await index(world).drain()

    assert sorted(await paths(world, "alice", "walrus")) == [
        "/personal/secret.md",
        "/spaces/family/shared.md",
    ]
    for user in ("bob", "carol"):
        for headers in (world.headers[user], await world.agent(user)):
            found = await search(world, headers, "walrus")
            got = sorted(hit["path"] for hit in found["results"])
            expected = ["/spaces/family/shared.md"]
            if user == "bob":
                expected = ["/personal/secret.md", *expected]
            assert got == expected
            if user == "bob":
                bob_hit = next(h for h in found["results"] if h["path"] == "/personal/secret.md")
                assert "bob private" in bob_hit["snippet"]
    assert await paths(world, "dave", "walrus") == []

    response = await world.client.post(
        f"{FILES}/search",
        json={"query": "walrus", "path": "/spaces/family"},
        headers=await world.agent("dave"),
    )
    assert response.status_code == 404


async def test_a_member_who_leaves_stops_seeing_the_space(world):
    await write(world, "alice", "/spaces/family/shared.md", "family walrus")
    await index(world).drain()
    assert await paths(world, "bob", "walrus") == ["/spaces/family/shared.md"]
    response = await world.client.delete(
        f"{API}/spaces/{world.family['id']}/members/{world.users['bob']['id']}",
        headers=world.headers["alice"],
    )
    assert response.status_code == 204, response.text
    assert await paths(world, "bob", "walrus") == []


async def test_path_narrows_the_search(world):
    await write(world, "alice", "/personal/Memory/people/maria.md", "walrus memory")
    await write(world, "alice", "/personal/Memorybook.md", "walrus lookalike prefix")
    await write(world, "alice", "/personal/other.md", "walrus other")
    await write(world, "alice", "/spaces/family/shared.md", "walrus family")
    await index(world).drain()

    assert await paths(world, "alice", "walrus", "/personal/Memory") == [
        "/personal/Memory/people/maria.md"
    ]
    assert await paths(world, "alice", "walrus", "/personal/Memory/people/maria.md") == [
        "/personal/Memory/people/maria.md"
    ]
    assert await paths(world, "alice", "walrus", "/spaces") == ["/spaces/family/shared.md"]
    assert len(await paths(world, "alice", "walrus", "/personal")) == 3


async def test_a_blank_query_is_rejected(world):
    response = await world.client.post(
        f"{FILES}/search", json={"query": "   "}, headers=world.headers["alice"]
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_query"
