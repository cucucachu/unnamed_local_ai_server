"""M22-05: installable app templates (`app.core.apptemplates`).

`GET /api/platform/app-templates` and `POST /spaces/{id}/app-templates/{slug}/install`,
plus a check over every template the image ships (`examples/apps/`): it validates, its
schema runs, and each of its actions runs against a scratch database. The real
builder builds and renders each one in services/app-builder's tests.

World: alice owns /spaces/family, bob edits it, carol views it, dave is out.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from app.core import appdb, appschema, manifest
from tests.app_packages import write_package
from tests.conftest import REPO_TEMPLATES
from tests.files_world import API, World
from tests.helpers import sql
from tests.test_app_build import FakeBuilder

TEMPLATES = f"{API}/app-templates"
SHIPPED = sorted(p.name for p in REPO_TEMPLATES.iterdir() if (p / "app.json").is_file())


def _install_url(space_id, template: str) -> str:
    return f"{API}/spaces/{space_id}/app-templates/{template}/install"


async def _install(world: World, user: str, space_id, template: str = "notes", **body):
    return await world.client.post(
        _install_url(space_id, template),
        json={"build": False} | body,
        headers=world.headers[user],
    )


# --- the list ---------------------------------------------------------------------------


async def test_the_shipped_templates_are_listed_with_their_agent_md(world: World) -> None:
    response = await world.client.get(TEMPLATES, headers=world.headers["dave"])
    assert response.status_code == 200, response.text
    templates = response.json()["templates"]
    assert [t["slug"] for t in templates] == SHIPPED
    notes = next(t for t in templates if t["slug"] == "notes")
    doc = json.loads((REPO_TEMPLATES / "notes" / "app.json").read_text())
    assert notes["name"] == doc["name"]
    assert notes["version"] == doc["version"]
    assert notes["icon"] == doc["homeai"]["icon"]
    assert notes["description"] == doc["homeai"]["description"]
    assert notes["agent_md"] == (REPO_TEMPLATES / "notes" / "AGENT.md").read_text()


async def test_listing_needs_a_session(world: World) -> None:
    assert (await world.client.get(TEMPLATES)).status_code == 401


async def test_an_invalid_template_is_left_out(world: World, tmp_path: Path) -> None:
    root = tmp_path / "templates"
    write_package(root / "good")
    write_package(root / "bad", doc={"name": "Bad"})
    (root / "not-a-template").mkdir()
    os.symlink(root / "good", root / "linked")
    world.platform.app.state.settings.app_templates_dir = root
    response = await world.client.get(TEMPLATES, headers=world.headers["alice"])
    assert [t["slug"] for t in response.json()["templates"]] == ["good"]
    assert (await _install(world, "alice", world.family["id"], "bad")).status_code == 404
    assert (await _install(world, "alice", world.family["id"], "linked")).status_code == 404


# --- install ----------------------------------------------------------------------------


async def test_install_copies_patches_registers_and_installs(world: World) -> None:
    personal = world.personal("alice")
    response = await _install(world, "alice", personal["id"], "notes")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["app"]["slug"] == "notes"
    assert body["app"]["source_path"] == "/personal/Apps/notes"
    assert body["instance"]["tracks"] == "working"
    assert body["instance"]["space_id"] == str(personal["id"])
    assert body["build"] is None

    copy = world.home("alice") / "Apps" / "notes"
    shipped = REPO_TEMPLATES / "notes"
    files = sorted(str(p.relative_to(shipped)) for p in shipped.rglob("*") if p.is_file())
    assert sorted(str(p.relative_to(copy)) for p in copy.rglob("*") if p.is_file()) == files
    assert (copy / "schema.sql").read_bytes() == (shipped / "schema.sql").read_bytes()
    doc = json.loads((copy / "app.json").read_text())
    original = json.loads((shipped / "app.json").read_text())
    assert doc["homeai"]["template"] == {"slug": "notes", "version": original["version"]}
    assert {k: v for k, v in doc["homeai"].items() if k != "template"} == original["homeai"]
    assert body["app"]["working_version"]["manifest"]["homeai"]["template"]["slug"] == "notes"

    (event,) = sql(
        world.platform,
        "SELECT kind, detail FROM audit_events WHERE kind = 'app.installed' AND target_id = %s",
        (body["instance"]["id"],),
    )
    assert event["detail"]["app"] == "notes"


async def test_slug_and_name_can_be_chosen(world: World) -> None:
    response = await _install(
        world, "bob", world.family["id"], "list", slug="chores", name="  Chores  "
    )
    assert response.status_code == 201, response.text
    assert (response.json()["app"]["slug"], response.json()["app"]["name"]) == ("chores", "Chores")
    doc = json.loads((world.family_root / "Apps" / "chores" / "app.json").read_text())
    assert (doc["slug"], doc["name"], doc["homeai"]["template"]["slug"]) == (
        "chores",
        "Chores",
        "list",
    )


async def test_a_taken_slug_is_a_conflict(world: World) -> None:
    family = world.family["id"]
    assert (await _install(world, "alice", family, "notes")).status_code == 201
    again = await _install(world, "bob", family, "notes")
    assert (again.status_code, again.json()) == (409, {"detail": "app_exists"})
    # A folder that isn't an app yet is taken too.
    (world.family_root / "Apps" / "jotter").mkdir()
    taken = await _install(world, "alice", family, "notes", slug="jotter")
    assert (taken.status_code, taken.json()) == (409, {"detail": "app_exists"})
    assert sorted(os.listdir(world.family_root / "Apps" / "jotter")) == []


@pytest.mark.parametrize(
    ("user", "status"),
    [("alice", 201), ("bob", 201), ("carol", 403), ("dave", 404)],
)
async def test_installing_needs_write_on_the_space(world: World, user: str, status: int) -> None:
    response = await _install(world, user, world.family["id"])
    assert response.status_code == status, response.text
    if status != 201:
        assert not (world.family_root / "Apps" / "notes").exists()


@pytest.mark.parametrize(
    ("template", "body", "status", "detail"),
    [
        ("nope", {}, 404, "unknown_template"),
        (".hidden", {}, 404, "unknown_template"),
        ("notes", {"slug": "Bad Slug"}, 422, "invalid_source_path"),
        ("notes", {"slug": "files"}, 422, "reserved_slug"),
        ("notes", {"name": "   "}, 422, "invalid_name"),
    ],
)
async def test_bad_requests(world: World, template, body, status, detail) -> None:
    response = await _install(world, "alice", world.family["id"], template, **body)
    assert (response.status_code, response.json()["detail"]) == (status, detail), response.text
    assert not (world.family_root / "Apps").exists() or os.listdir(world.family_root / "Apps") == []


async def test_install_builds_by_default(world: World) -> None:
    builder = FakeBuilder(world.platform.app.state.builds.root)
    world.platform.app.state.builder = builder
    response = await world.client.post(
        _install_url(world.family["id"], "tracker"), json={}, headers=world.headers["alice"]
    )
    assert response.status_code == 201, response.text
    build = response.json()["build"]
    assert build["ok"] is True, build
    assert [m["instance_id"] for m in build["migrations"]] == [response.json()["instance"]["id"]]
    assert [phase for _, phase in builder.calls] == ["compile", "smoke"]
    assert "app/index.tsx" in builder.staged


# --- every shipped template -------------------------------------------------------------

# A value for each :param by name, so every action can run; add to it with new templates.
SAMPLE = {
    "day": "2026-10-18",
    "date": "2026-10-18",
    "today": "2026-10-18",
    "next_due": "2026-10-18",
    "expires_on": "2026-10-25",
    "time": "08:30",
    "every_days": 7,
    "quantity": 2,
    "amount": 1,
    "pattern": "sample",
    "reps": 5,
    "weight": 60,
    "grade": 2,
    "position": 0,
    "status": "finished",
}
_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def _sample(name: str) -> object:
    if name in SAMPLE:
        return SAMPLE[name]
    if name == "id" or name.endswith("_id"):
        return 1
    return f"sample {name}"


@pytest.mark.parametrize("slug", SHIPPED)
def test_every_shipped_template_validates_and_its_actions_run(slug: str, tmp_path: Path) -> None:
    pkg = tmp_path / slug
    shutil.copytree(REPO_TEMPLATES / slug, pkg)
    fd = os.open(pkg, os.O_RDONLY | os.O_DIRECTORY)
    try:
        doc, found = manifest.validate_package(fd, slug)
    finally:
        os.close(fd)
    assert found == [], [d.as_dict() for d in found]
    assert doc["version"] == "1.0.0"

    schema_sql = (pkg / "schema.sql").read_text()
    con = appschema.scratch_from(schema_sql)
    # The data map (M22-06) shows these: every table and column but `id` says what it holds.
    docs = {d["name"]: d for d in appschema.table_docs(schema_sql)}
    for table, info in appschema.describe(con)["tables"].items():
        assert docs[table]["description"], f"{slug}: table {table} has no comment"
        own = [
            c for c in info["parsed"]["columns"] if c != "id" and c not in appschema.STAMP_COLUMNS
        ]
        assert sorted(docs[table]["columns"]) == sorted(own), f"{slug}: {table}'s column comments"
    appdb.stamp(con, "00000000-0000-0000-0000-000000000001")
    actions = sorted((pkg / "actions").glob("*.sql")) if (pkg / "actions").is_dir() else []
    # Twice, in name order, so an action that needs another's row (checkIn after addHabit) finds it.
    for _ in range(2):
        for action in actions:
            text = action.read_text()
            params = {name: _sample(name) for name in set(_PARAM.findall(text))}
            try:
                appdb.action(con, text, params)
            except appdb.SqlError as exc:
                pytest.fail(f"{slug}/actions/{action.name}: {exc}")
    con.close()
