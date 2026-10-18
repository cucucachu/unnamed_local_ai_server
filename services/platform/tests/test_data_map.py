"""M22-06: the data map (`appschema.table_docs`, `app.core.datamap`, `GET /data-map`).

World: alice owns /spaces/family, bob edits it, carol views it, dave is out.
"""

from __future__ import annotations

import shutil

import pytest

from app.core import appschema
from tests.files_world import API, World
from tests.helpers import sql
from tests.test_app_build import FakeBuilder

DATA_MAP = f"{API}/data-map"


# --- parsing schema.sql comments --------------------------------------------------------

SCHEMA = """-- Money spent.
-- One row per purchase.
CREATE TABLE expenses (
  id INTEGER PRIMARY KEY,
  amount_cents INTEGER NOT NULL, -- in cents, positive
  "category id" INTEGER REFERENCES categories(id), -- which category
  note TEXT DEFAULT (lower('a,b')) -- free text
  -- on its own line: not a column's
  , day TEXT,
  UNIQUE (day, note) -- a constraint's: dropped
); -- after the statement: not above the next one
CREATE TABLE categories (name TEXT -- the label
);

-- above an index

CREATE INDEX expenses_day ON expenses (day);
-- with a blank line between

CREATE TABLE IF NOT EXISTS [plain] (x);
/* a block comment isn't a description */
CREATE TABLE blocked (y);
"""


def test_table_docs_reads_the_comment_above_and_trailing_column_comments() -> None:
    assert appschema.table_docs(SCHEMA) == [
        {
            "name": "expenses",
            "description": "Money spent. One row per purchase.",
            "columns": {
                "amount_cents": "in cents, positive",
                "category id": "which category",
                "note": "free text",
            },
        },
        {"name": "categories", "description": None, "columns": {"name": "the label"}},
        {"name": "plain", "description": None, "columns": {}},
        {"name": "blocked", "description": None, "columns": {}},
    ]


def test_table_docs_cuts_long_descriptions_and_ignores_what_isnt_a_table() -> None:
    long = "x" * 500
    (doc,) = appschema.table_docs(f"-- {long}\nCREATE TABLE t (a TEXT -- {long}\n);")
    assert len(doc["description"]) == appschema.MAX_DOC_CHARS
    assert doc["description"].endswith("…")
    assert len(doc["columns"]["a"]) == appschema.MAX_DOC_CHARS
    assert appschema.table_docs("") == []
    assert appschema.table_docs("-- only a comment\nCREATE INDEX i ON t (a);") == []


# --- the endpoint -----------------------------------------------------------------------


@pytest.fixture
def builder(world: World) -> FakeBuilder:
    fake = FakeBuilder(world.platform.app.state.builds.root)
    world.platform.app.state.builder = fake
    return fake


async def _from_template(world: World, user: str, space_id, template: str, **body) -> dict:
    response = await world.client.post(
        f"{API}/spaces/{space_id}/app-templates/{template}/install",
        json=body,
        headers=world.headers[user],
    )
    assert response.status_code == 201, response.text
    assert body.get("build") is False or response.json()["build"]["ok"], response.text
    return response.json()["instance"]


async def _action(world: World, user: str, inst: dict, name: str, params: dict) -> None:
    response = await world.client.post(
        f"{API}/apps/instances/{inst['id']}/rpc",
        json={"op": "action", "name": name, "params": params},
        headers=world.headers[user],
    )
    assert response.status_code == 200, response.text


async def _map(world: World, headers: dict[str, str]) -> list[dict]:
    response = await world.client.get(DATA_MAP, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["instances"]


async def test_the_map_lists_instances_with_descriptions_and_row_counts(world, builder) -> None:
    tracker = await _from_template(world, "alice", world.personal("alice")["id"], "tracker")
    await _action(world, "alice", tracker, "addHabit", {"name": "Stretch"})
    await _action(world, "alice", tracker, "addHabit", {"name": "Read"})
    await _action(world, "alice", tracker, "checkIn", {"habit_id": 1, "day": "2026-10-18"})
    chores = await _from_template(
        world, "bob", world.family["id"], "list", slug="chores", name="Chores"
    )

    (mine, family) = await _map(world, world.headers["alice"])
    assert mine["id"] == tracker["id"]
    assert mine["app"]["slug"] == "tracker"
    assert mine["space"]["kind"] == "personal" and mine["space"]["role"] == "owner"
    assert mine["description"] == "Habit tracker: add habits and check them off each day."
    habits, checkins = mine["tables"]
    assert (habits["name"], habits["rows"], checkins["name"], checkins["rows"]) == (
        "habits",
        2,
        "checkins",
        1,
    )
    assert habits["description"].startswith("One row per habit.")
    assert checkins["description"] == "One row per day a habit was done."
    assert checkins["columns"]["habit_id"] == "which habit"
    assert "_created_by" not in habits["columns"]

    assert (family["id"], family["app"]["name"]) == (chores["id"], "Chores")
    assert (family["space"]["slug"], family["space"]["role"]) == ("family", "owner")
    assert [(t["name"], t["rows"]) for t in family["tables"]] == [("items", 0)]


async def test_each_user_sees_only_their_spaces(world, builder) -> None:
    await _from_template(world, "alice", world.personal("alice")["id"], "notes")
    await _from_template(world, "alice", world.family["id"], "list")
    carol = await _map(world, world.headers["carol"])
    assert [(i["app"]["slug"], i["space"]["role"]) for i in carol] == [("list", "viewer")]
    assert await _map(world, world.headers["dave"]) == []
    assert (await world.client.get(DATA_MAP)).status_code == 401


async def test_an_agent_delegation_sees_its_users_map(world, builder) -> None:
    await _from_template(world, "bob", world.family["id"], "list")
    assert [i["app"]["slug"] for i in await _map(world, await world.agent("bob"))] == ["list"]


async def test_unbuilt_and_uninstalled_instances(world, builder) -> None:
    unbuilt = await _from_template(
        world, "alice", world.family["id"], "notes", slug="draft", build=False
    )
    gone = await _from_template(world, "alice", world.family["id"], "list")
    response = await world.client.delete(
        f"{API}/spaces/{world.family['id']}/instances/{gone['id']}", headers=world.headers["alice"]
    )
    assert response.status_code == 204, response.text
    (entry,) = await _map(world, world.headers["alice"])
    assert entry["id"] == unbuilt["id"]
    # Never built: no tables in its database and none described yet.
    assert entry["tables"] == []


async def test_without_a_readable_database_the_tables_come_from_the_build(world, builder) -> None:
    inst = await _from_template(world, "alice", world.family["id"], "list")
    storage = world.platform.app.state.storage
    shutil.rmtree(storage.space_dir(world.family["id"]) / "apps" / inst["id"])
    (entry,) = await _map(world, world.headers["alice"])
    (items,) = entry["tables"]
    assert (items["name"], items["rows"]) == ("items", None)
    assert items["description"].startswith("One row per checklist item.")


async def test_publishing_keeps_the_descriptions(world, builder) -> None:
    inst = await _from_template(world, "alice", world.personal("alice")["id"], "list")
    response = await world.client.post(
        f"{API}/apps/{inst['app_id']}/publish",
        json={"space_ids": [str(world.family["id"])]},
        headers=world.headers["alice"],
    )
    assert response.status_code == 200, response.text
    (published,) = sql(
        world.platform,
        "SELECT tables_doc FROM app_versions WHERE app_id = %s AND kind = 'published'",
        (inst["app_id"],),
    )
    assert published["tables_doc"][0]["columns"]["done"] == "1 once it's ticked off"
