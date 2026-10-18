"""M22-07: app rows in search (`homeai.index`, `app.core.appindex`, `/files/search`, RPC `search`).

World: alice owns /spaces/family, bob edits it, carol views it, dave is out.
The notes template declares `{"table": "notes", "title": "title", "text": ["body"]}`.
"""

from __future__ import annotations

import json

import pytest

from app.core import appindex, manifest
from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.files_world import API, FILES, World, build_world
from tests.helpers import sql
from tests.test_app_build import FakeBuilder
from tests.test_file_search import FakeEmbedder


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


@pytest.fixture
def builder(world: World) -> FakeBuilder:
    fake = FakeBuilder(world.platform.app.state.builds.root)
    world.platform.app.state.builder = fake
    return fake


def index(world: World):
    return world.platform.app.state.file_index


async def _notes(world: World, user: str, space_id, **body) -> dict:
    response = await world.client.post(
        f"{API}/spaces/{space_id}/app-templates/notes/install",
        json=body,
        headers=world.headers[user],
    )
    assert response.status_code == 201, response.text
    assert response.json()["build"]["ok"], response.text
    return response.json()["instance"]


async def _rpc(world: World, headers: dict, inst: dict, body: dict, status: int = 200) -> dict:
    response = await world.client.post(
        f"{API}/apps/instances/{inst['id']}/rpc", json=body, headers=headers
    )
    assert response.status_code == status, response.text
    return response.json()


async def _run(world: World, user: str, inst: dict, sql_text: str, params: list) -> None:
    await _rpc(world, world.headers[user], inst, {"op": "run", "sql": sql_text, "params": params})


async def _add(world: World, user: str, inst: dict, title: str, body: str) -> None:
    await _run(world, user, inst, "INSERT INTO notes (title, body) VALUES (?, ?)", [title, body])


async def _search(world: World, headers: dict, query: str, path: str = "/") -> dict:
    response = await world.client.post(
        f"{FILES}/search", json={"query": query, "path": path}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _rows(world: World, who: str | dict, query: str, path: str = "/") -> list[tuple]:
    found = await _search(world, world.headers[who] if isinstance(who, str) else who, query, path)
    return [
        (h["app_row"]["app"]["slug"], h["app_row"]["table"], h["app_row"]["title"])
        for h in found["results"]
        if h["kind"] == "app_row"
    ]


def _chunks(world: World) -> dict[tuple[str, int], str]:
    rows = sql(world.platform, "SELECT tbl, row_id, content FROM app_row_chunks")
    return {(r["tbl"], r["row_id"]): r["content"] for r in rows}


# --- rendering and validation -----------------------------------------------------------


def test_a_row_renders_as_title_fields_then_text() -> None:
    entry = {
        "table": "recipes",
        "title": "name",
        "text": ["steps"],
        "fields": ["cuisine", "serves"],
    }
    row = {
        "__homeai_rowid": 12,
        "name": "Dal",
        "cuisine": "Indian",
        "serves": 4,
        "steps": "Simmer.",
    }
    assert appindex.render(entry, row) == ("Dal", "Dal\ncuisine: Indian\nserves: 4\nSimmer.")
    empty = {"__homeai_rowid": 3, "name": "  ", "cuisine": None, "serves": "", "steps": None}
    assert appindex.render(entry, empty) == ("recipes #3", "recipes #3")


def test_the_declaration_is_checked_against_schema_sql() -> None:
    schema = (
        "CREATE TABLE recipes (id INTEGER PRIMARY KEY, name TEXT, steps TEXT);\n"
        "CREATE TABLE tags (name TEXT PRIMARY KEY) WITHOUT ROWID;"
    )
    doc = {
        "homeai": {
            "index": [
                {"table": "recipes", "title": "name", "text": ["steps", "notes"]},
                {"table": "recipes", "title": "name"},
                {"table": "tags", "title": "name"},
                {"table": "missing", "title": "x"},
            ]
        }
    }
    messages = [d.message for d in manifest.index_diagnostics(doc, schema)]
    assert len(messages) == 4, messages
    assert any("notes" in m for m in messages)
    assert any("missing" in m for m in messages)
    assert any("WITHOUT ROWID" in m for m in messages)
    ok = {"homeai": {"index": [{"table": "recipes", "title": "name", "text": ["steps"]}]}}
    assert manifest.index_diagnostics(ok, schema) == []


# --- freshness --------------------------------------------------------------------------


async def test_inserts_updates_and_deletes_keep_the_rows_current(world, builder, embedder) -> None:
    inst = await _notes(world, "alice", world.personal("alice")["id"])
    await _add(world, "alice", inst, "Tomatoes", "Plant them out after the last frost.")
    await _add(world, "alice", inst, "Boat", "The sailboat is named Ophelia.")
    await index(world).drain()
    assert _chunks(world) == {
        ("notes", 1): "Tomatoes\nPlant them out after the last frost.",
        ("notes", 2): "Boat\nThe sailboat is named Ophelia.",
    }
    # By words, and by meaning (the fake embedder puts "vegetables" next to "tomatoes").
    # The vector half ranks every row, so the best one is what counts.
    assert (await _rows(world, "alice", "Ophelia"))[0] == ("notes", "notes", "Boat")
    assert (await _rows(world, "alice", "growing vegetables"))[0] == ("notes", "notes", "Tomatoes")

    embedded = len(embedder.documents)
    await _run(world, "alice", inst, "UPDATE notes SET body = ? WHERE id = 2", ["Renamed Juno."])
    await index(world).drain()
    assert _chunks(world)[("notes", 2)] == "Boat\nRenamed Juno."
    assert _chunks(world)[("notes", 1)].startswith("Tomatoes")
    assert len(embedder.documents) == embedded + 1  # only the changed row

    await _run(world, "alice", inst, "DELETE FROM notes WHERE id = 1", [])
    await index(world).drain()
    assert list(_chunks(world)) == [("notes", 2)]
    assert await _rows(world, "alice", "tomatoes") == [("notes", "notes", "Boat")]


async def test_a_changed_declaration_reindexes_the_instance(world, builder) -> None:
    inst = await _notes(world, "alice", world.personal("alice")["id"])
    await _add(world, "alice", inst, "Boat", "Ophelia")
    await index(world).drain()
    assert _chunks(world) == {("notes", 1): "Boat\nOphelia"}

    declared = [{"table": "notes", "title": "title", "text": ["body"], "fields": ["pinned"]}]
    sql(
        world.platform,
        "UPDATE app_versions SET manifest = jsonb_set(manifest, '{homeai,index}', %s::jsonb) "
        "WHERE app_id = %s",
        (json.dumps(declared), inst["app_id"]),
    )
    index(world).app_changed(inst["id"])
    await index(world).drain()
    assert _chunks(world) == {("notes", 1): "Boat\npinned: 0\nOphelia"}

    sql(
        world.platform,
        "UPDATE app_versions SET manifest = manifest #- '{homeai,index}' WHERE app_id = %s",
        (inst["app_id"],),
    )
    index(world).app_changed(inst["id"])
    await index(world).drain()
    assert _chunks(world) == {}
    assert sql(world.platform, "SELECT * FROM indexed_instances") == []


async def test_a_rebuild_queues_the_instance(world, builder) -> None:
    inst = await _notes(world, "alice", world.personal("alice")["id"])
    await _add(world, "alice", inst, "Boat", "Ophelia")
    await index(world).drain()
    sql(world.platform, "DELETE FROM indexed_instances")
    response = await world.client.post(
        f"{API}/apps/{inst['app_id']}/build", headers=world.headers["alice"]
    )
    assert response.status_code == 200, response.text
    await index(world).drain()
    assert _chunks(world) == {("notes", 1): "Boat\nOphelia"}


async def test_the_scan_catches_writes_that_were_not_queued(world, builder) -> None:
    inst = await _notes(world, "alice", world.personal("alice")["id"])
    await index(world).drain()
    world.platform.app.state.appdata.on_change = None
    await _add(world, "alice", inst, "Boat", "Ophelia")
    await index(world).drain()
    assert _chunks(world) == {}
    await index(world).scan()
    assert _chunks(world) == {("notes", 1): "Boat\nOphelia"}


async def test_uninstalled_instances_and_archived_spaces_drop_out(world, builder) -> None:
    mine = await _notes(world, "alice", world.personal("alice")["id"])
    family = await _notes(world, "alice", world.family["id"])
    await _add(world, "alice", mine, "Mine", "quokka census")
    await _add(world, "alice", family, "Family", "quokka census")
    await index(world).drain()
    assert len(await _rows(world, "alice", "quokka")) == 2

    response = await world.client.delete(
        f"{API}/spaces/{world.personal('alice')['id']}/instances/{mine['id']}",
        headers=world.headers["alice"],
    )
    assert response.status_code == 204, response.text
    await index(world).drain()
    assert await _rows(world, "alice", "quokka") == [("notes", "notes", "Family")]

    sql(
        world.platform, "UPDATE spaces SET archived_at = now() WHERE id = %s", (world.family["id"],)
    )
    await index(world).scan()
    assert _chunks(world) == {}


async def test_an_unbuilt_instance_is_skipped(world, builder) -> None:
    response = await world.client.post(
        f"{API}/spaces/{world.family['id']}/app-templates/notes/install",
        json={"build": False},
        headers=world.headers["alice"],
    )
    assert response.status_code == 201, response.text
    await index(world).scan()
    assert _chunks(world) == {}


# --- search -----------------------------------------------------------------------------


async def test_rows_reach_only_those_who_can_read_the_space(world, builder) -> None:
    family = await _notes(world, "bob", world.family["id"])
    mine = await _notes(world, "alice", world.personal("alice")["id"])
    await _add(world, "bob", family, "Shared", "zanzibar trip")
    await _add(world, "alice", mine, "Private", "zanzibar diary")
    await index(world).drain()

    assert sorted(await _rows(world, "alice", "zanzibar")) == [
        ("notes", "notes", "Private"),
        ("notes", "notes", "Shared"),
    ]
    assert await _rows(world, "bob", "zanzibar") == [("notes", "notes", "Shared")]
    assert await _rows(world, "carol", "zanzibar") == [("notes", "notes", "Shared")]
    assert await _rows(world, "dave", "zanzibar") == []
    agent = await world.agent("bob")
    assert await _rows(world, agent, "zanzibar") == [("notes", "notes", "Shared")]

    (hit,) = (await _search(world, world.headers["carol"], "zanzibar"))["results"]
    assert hit["path"] is None and hit["line_start"] is None
    ref = hit["app_row"]
    assert ref["instance_id"] == family["id"]
    assert ref["app"] == {"slug": "notes", "name": "Notes"}
    assert (ref["space"]["slug"], ref["space"]["kind"]) == ("family", "shared")
    assert (ref["table"], ref["row_id"]) == ("notes", 1)
    assert hit["snippet"] == "zanzibar trip"  # from the first line with a query word


async def test_rows_only_come_with_whole_spaces(world, builder) -> None:
    inst = await _notes(world, "alice", world.family["id"])
    await _add(world, "alice", inst, "Trip", "zanzibar")
    await index(world).drain()
    for path in ("/", "/spaces", "/spaces/family"):
        assert len(await _rows(world, "alice", "zanzibar", path)) == 1, path
    assert await _rows(world, "alice", "zanzibar", "/spaces/family/Apps") == []
    assert await _rows(world, "alice", "zanzibar", "/personal") == []


async def test_rows_rank_together_with_files(world, builder) -> None:
    inst = await _notes(world, "alice", world.personal("alice")["id"])
    await _add(world, "alice", inst, "Allergies", "Maria is allergic to peanuts.")
    response = await world.client.post(
        f"{FILES}/write",
        json={"path": "/personal/garden.md", "content": "# Garden\nGrow tomatoes by the fence."},
        headers=world.headers["alice"],
    )
    assert response.status_code == 200, response.text
    await index(world).drain()

    (top, *_) = (await _search(world, world.headers["alice"], "what foods should my wife avoid"))[
        "results"
    ]
    assert top["kind"] == "app_row" and top["app_row"]["title"] == "Allergies"
    (top, *_) = (await _search(world, world.headers["alice"], "vegetables growing"))["results"]
    assert (top["kind"], top["path"]) == ("file", "/personal/garden.md")


async def test_the_rpc_searches_only_its_own_instance(world, builder) -> None:
    family = await _notes(world, "alice", world.family["id"])
    mine = await _notes(world, "alice", world.personal("alice")["id"])
    await _add(world, "alice", family, "Shared", "zanzibar trip")
    await _add(world, "alice", mine, "Private", "zanzibar diary")
    response = await world.client.post(
        f"{FILES}/write",
        json={"path": "/spaces/family/zanzibar.md", "content": "zanzibar"},
        headers=world.headers["alice"],
    )
    assert response.status_code == 200, response.text
    await index(world).drain()

    search = {"op": "search", "query": "zanzibar", "limit": 5}
    (hit,) = (await _rpc(world, world.headers["carol"], family, search))["results"]
    assert (hit["table"], hit["id"], hit["title"]) == ("notes", 1, "Shared")
    assert hit["snippet"] == "zanzibar trip" and hit["score"] > 0
    (hit,) = (await _rpc(world, world.headers["alice"], mine, search))["results"]
    assert hit["title"] == "Private"
    assert (await _rpc(world, world.headers["alice"], mine, {**search, "query": " "}))[
        "results"
    ] == []
    await _rpc(world, world.headers["dave"], family, search, status=404)
    await _rpc(world, world.headers["alice"], family, {"op": "search", "query": ""}, status=422)
