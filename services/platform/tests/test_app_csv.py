"""M22-08: CSV export and import of app data (`app.core.appcsv`, `/export`, `/import`).

World: alice owns /spaces/family, bob edits it, carol views it, dave is out.
The notes template's table: `notes (id, title NOT NULL, body NOT NULL DEFAULT '',
pinned NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)), created_at, updated_at)`.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from tests.files_world import API, FILES, World
from tests.helpers import sql
from tests.test_app_build import FakeBuilder

TRICKY = 'Comma, "quotes" and\na new line'
UNICODE = "Crème brûlée — 東京 🍮"


@pytest.fixture
def builder(world: World) -> FakeBuilder:
    fake = FakeBuilder(world.platform.app.state.builds.root)
    world.platform.app.state.builder = fake
    return fake


async def _notes(world: World, user: str, space_id, slug: str = "notes") -> dict:
    response = await world.client.post(
        f"{API}/spaces/{space_id}/app-templates/notes/install",
        json={"slug": slug},
        headers=world.headers[user],
    )
    assert response.status_code == 201, response.text
    return response.json()["instance"]


async def _rpc(world: World, user: str, inst: dict, body: dict) -> dict:
    response = await world.client.post(
        f"{API}/apps/instances/{inst['id']}/rpc", json=body, headers=world.headers[user]
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _rows(world: World, inst: dict) -> list[dict]:
    sql_text = "SELECT id, title, body, pinned, _created_by FROM notes ORDER BY id"
    return (await _rpc(world, "alice", inst, {"op": "getAll", "sql": sql_text}))["rows"]


async def _write(world: World, path: str, content: str, user: str = "alice") -> None:
    response = await world.client.post(
        f"{FILES}/write", json={"path": path, "content": content}, headers=world.headers[user]
    )
    assert response.status_code == 200, response.text


async def _export(world: World, user: str, inst: dict, status: int = 201, **body):
    response = await world.client.post(
        f"{API}/apps/instances/{inst['id']}/export", json=body, headers=world.headers[user]
    )
    assert response.status_code == status, response.text
    return response.json()


async def _import(world: World, user: str, inst: dict, status: int = 200, **body):
    response = await world.client.post(
        f"{API}/apps/instances/{inst['id']}/import", json=body, headers=world.headers[user]
    )
    assert response.status_code == status, response.text
    return response.json()


def _disk(world: World, vpath: str) -> Path:
    parts = vpath.strip("/").split("/")
    if parts[0] == "personal":
        raise AssertionError("use a family path")
    return world.family_root.joinpath(*parts[2:])


async def test_export_then_import_round_trips_quoting_unicode_and_defaults(world, builder) -> None:
    family = await _notes(world, "alice", world.family["id"])
    for title, body, pinned in ((TRICKY, UNICODE, 1), ("Empty body", "", 0)):
        await _rpc(
            world,
            "alice",
            family,
            {
                "op": "run",
                "sql": "INSERT INTO notes (title, body, pinned) VALUES (?, ?, ?)",
                "params": [title, body, pinned],
            },
        )

    out = await _export(world, "bob", family)
    assert out["folder"].startswith("/spaces/family/Exports/notes-")
    assert out["tables"] == [{"name": "notes", "file": "notes.csv", "rows": 2}]
    folder = _disk(world, out["folder"])
    assert sorted(p.name for p in folder.iterdir()) == ["notes.csv", "schema.sql"]
    assert (folder / "schema.sql").read_text().startswith("-- One row per note.")
    exported = list(csv.DictReader(io.StringIO((folder / "notes.csv").read_text(), newline="")))
    assert [(r["title"], r["body"], r["pinned"]) for r in exported] == [
        (TRICKY, UNICODE, "1"),
        ("Empty body", "", "0"),
    ]
    assert exported[0]["_created_by"] == str(world.users["alice"]["id"])

    mine = await _notes(world, "alice", world.personal("alice")["id"])
    result = await _import(world, "alice", mine, table="notes", path=f"{out['folder']}/notes.csv")
    assert result["rows"] == 2 and result["dry_run"] is False
    assert set(result["skipped"]) == {"_created_by", "_created_at", "_updated_by", "_updated_at"}
    assert result["mapping"]["title"] == "title" and result["unmatched"] == []
    before = await _rows(world, family)
    after = await _rows(world, mine)
    assert [(r["id"], r["title"], r["body"], r["pinned"]) for r in after] == [
        (r["id"], r["title"], r["body"], r["pinned"]) for r in before
    ]
    # The platform stamps imported rows as the importer's.
    assert {r["_created_by"] for r in after} == {str(world.users["alice"]["id"])}


async def test_a_mapping_a_delimiter_and_a_bom(world, builder) -> None:
    inst = await _notes(world, "alice", world.family["id"])
    path = "/spaces/family/bank.csv"
    await _write(world, path, "\ufeffHeading;Text;Ignored\r\nOne;first;x\r\nTwo;;y\r\n")
    result = await _import(
        world, "bob", inst, table="notes", path=path, delimiter=";",
        mapping={"Heading": "title", "Text": "BODY"},
    )  # fmt: skip
    assert (result["rows"], result["mapping"], result["skipped"]) == (
        2,
        {"Heading": "title", "Text": "body"},
        ["Ignored"],
    )
    assert [(r["title"], r["body"]) for r in await _rows(world, inst)] == [
        ("One", "first"),
        ("Two", ""),
    ]


async def test_a_dry_run_reports_the_file_and_imports_nothing(world, builder) -> None:
    inst = await _notes(world, "alice", world.family["id"])
    path = "/spaces/family/in.csv"
    await _write(world, path, "Title,Notes\nA,b\nC,d\n")
    plan = await _import(world, "bob", inst, table="notes", path=path, dry_run=True)
    assert (plan["headers"], plan["mapping"], plan["unmatched"], plan["rows"]) == (
        ["Title", "Notes"],
        {"Title": "title"},
        ["Notes"],
        2,
    )
    assert "body" in plan["columns"] and "_created_by" not in plan["columns"]
    checked = await _import(
        world, "bob", inst, table="notes", path=path, dry_run=True,
        mapping={"Title": "title", "Notes": "body"},
    )  # fmt: skip
    assert (checked["rows"], checked["dry_run"]) == (2, True)
    assert await _rows(world, inst) == []
    failed = await _import(world, "bob", inst, 422, table="notes", path=path)
    assert failed["detail"] == "import_failed" and '"Notes"' in failed["message"]
    assert failed["line"] == 1


async def test_a_bad_row_rolls_back_the_whole_import_and_names_it(world, builder) -> None:
    inst = await _notes(world, "alice", world.family["id"])
    await _rpc(
        world, "alice", inst, {"op": "run", "sql": "INSERT INTO notes (title) VALUES ('kept')"}
    )
    path = "/spaces/family/bad.csv"
    await _write(world, path, 'title,pinned\nok,0\n"two\nlines",1\n,0\n')
    failed = await _import(world, "bob", inst, 422, table="notes", path=path)
    assert (failed["detail"], failed["line"], failed["column"]) == ("import_failed", 5, "title")
    assert "NOT NULL" in failed["message"]

    await _write(world, path, "title,pinned\nok,0\nnope,7\n")
    failed = await _import(world, "bob", inst, 422, table="notes", path=path)
    assert failed["line"] == 3 and "CHECK" in failed["message"]

    await _write(world, path, "title,pinned\nok\n")
    failed = await _import(world, "bob", inst, 422, table="notes", path=path)
    assert (failed["line"], failed["message"]) == (2, "1 fields, but the header has 2")
    assert [r["title"] for r in await _rows(world, inst)] == ["kept"]


async def test_bad_requests(world, builder) -> None:
    inst = await _notes(world, "alice", world.family["id"])
    await _write(world, "/spaces/family/x.csv", "title\na\n")
    await _write(world, "/spaces/family/latin1.csv", "title\n")
    (world.family_root / "latin1.csv").write_bytes("title\ncaf\xe9\n".encode("latin-1"))
    body = {"table": "notes", "path": "/spaces/family/x.csv"}
    assert (await _import(world, "bob", inst, 422, **{**body, "table": "nope"}))[
        "detail"
    ] == "unknown_table"
    assert (await _import(world, "bob", inst, 422, **{**body, "table": "_homeai_meta"}))[
        "detail"
    ] == "unknown_table"
    failed = await _import(world, "bob", inst, 422, **body, mapping={"title": "nope"})
    assert failed["column"] == "nope"
    failed = await _import(world, "bob", inst, 422, **body, mapping={"missing": "title"})
    assert '"missing"' in failed["message"]
    failed = await _import(world, "bob", inst, 422, table="notes", path="/spaces/family/latin1.csv")
    assert failed["message"] == "the file isn't UTF-8 text"
    assert (await _import(world, "bob", inst, 404, table="notes", path="/spaces/family/no.csv"))[
        "detail"
    ] == "not_found"


async def test_who_may_export_and_import(world, builder) -> None:
    inst = await _notes(world, "alice", world.family["id"])
    await _write(world, "/spaces/family/x.csv", "title\na\n")
    body = {"table": "notes", "path": "/spaces/family/x.csv"}

    # A viewer exports into their own space, can't write the shared one, and can't import.
    out = await _export(world, "carol", inst)
    assert out["folder"].startswith("/personal/Exports/notes-")
    assert (await _export(world, "carol", inst, 403, folder="/spaces/family/mine"))[
        "detail"
    ] == "insufficient_role"
    assert (await _import(world, "carol", inst, 403, **body))["detail"] == "insufficient_role"
    # A non-member doesn't see the instance at all.
    await _export(world, "dave", inst, 404)
    await _import(world, "dave", inst, 404, **body)
    # An editor can't read a file outside their spaces.
    await _write(world, "/personal/secret.csv", "title\nsecret\n", user="dave")
    await _import(world, "bob", inst, 404, table="notes", path="/personal/secret.csv")
    assert await _rows(world, inst) == []

    # An agent delegation has its user's rights.
    agent = await world.agent("bob")
    response = await world.client.post(
        f"{API}/apps/instances/{inst['id']}/import", json=body, headers=agent
    )
    assert response.status_code == 200, response.text


async def test_an_export_never_overwrites(world, builder) -> None:
    inst = await _notes(world, "alice", world.family["id"])
    await _write(world, "/spaces/family/taken/x.txt", "x")
    assert (await _export(world, "alice", inst, 409, folder="/spaces/family/taken"))[
        "detail"
    ] == "exists"
    assert (await _export(world, "alice", inst, 422, folder="/spaces/family"))[
        "detail"
    ] == "invalid_path"
    out = await _export(world, "alice", inst, folder="/spaces/family/backup/notes")
    assert out["folder"] == "/spaces/family/backup/notes"
    assert (_disk(world, out["folder"]) / "notes.csv").read_text().startswith("id,title,body")


async def test_imports_are_audited_and_change_events_fire(world, builder) -> None:
    inst = await _notes(world, "alice", world.family["id"])
    await _write(world, "/spaces/family/x.csv", "title\na\nb\n")
    changed = []
    world.platform.app.state.appdata.on_change = changed.append
    await _import(world, "bob", inst, table="notes", path="/spaces/family/x.csv", dry_run=True)
    assert changed == []
    await _import(world, "bob", inst, table="notes", path="/spaces/family/x.csv")
    assert [str(i) for i in changed] == [inst["id"]]
    (event,) = sql(
        world.platform,
        "SELECT summary, detail FROM audit_events WHERE kind = 'app_data.write'",
    )
    assert event["summary"] == "import in notes: 2 rows changed"
    assert (event["detail"]["table"], event["detail"]["path"]) == (
        "notes",
        "/spaces/family/x.csv",
    )
