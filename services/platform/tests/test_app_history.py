"""App source history: a commit per build, `GET .../history`, `POST .../revert` (`app.core.apphistory`).

Builds use the fake builder of `tests/test_app_build.py`. The repos are real
(the host's `git`); `scripts/e2e/app_history_smoke.sh` runs the platform
image's.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from app.core import apphistory
from app.core.apphistory import AppHistory
from tests.app_packages import FILES, manifest, write_package
from tests.files_world import API, World
from tests.helpers import sql
from tests.test_app_build import ITEMS, ITEMS_NOTE, FakeBuilder, fail_with, succeed

APPS = f"{API}/apps"
INDEX_V1 = FILES["app/index.tsx"]
INDEX_V2 = "export default function Index() { return 'two'; }\n"


@pytest.fixture
def builder(world: World) -> FakeBuilder:
    fake = FakeBuilder(world.platform.app.state.builds.root)
    world.platform.app.state.builder = fake
    return fake


def _src(world: World, slug: str = "hello") -> Path:
    return world.family_root / "Apps" / slug


async def _registered(world: World, slug: str = "hello") -> dict:
    write_package(_src(world, slug))
    response = await world.client.post(
        APPS, json={"source_path": f"/spaces/family/Apps/{slug}"}, headers=world.headers["alice"]
    )
    assert response.status_code == 201, response.text
    return response.json()["app"]


async def _build(world: World, app_id: str, headers: dict | None = None) -> dict:
    response = await world.client.post(
        f"{APPS}/{app_id}/build", headers=headers or world.headers["alice"]
    )
    assert response.status_code == 200, response.text
    return response.json()


def _history(world: World, app_id: str, user: str = "alice", **params) -> httpx.Response:
    return world.client.get(f"{APPS}/{app_id}/history", params=params, headers=world.headers[user])


async def _commits(world: World, app_id: str) -> list[dict]:
    response = await _history(world, app_id)
    assert response.status_code == 200, response.text
    return response.json()["commits"]


def _revert(world: World, app_id: str, commit: str, headers: dict | None = None):
    return world.client.post(
        f"{APPS}/{app_id}/revert", json={"commit": commit},
        headers=headers or world.headers["alice"],
    )  # fmt: skip


def _history_store(world: World) -> AppHistory:
    return world.platform.app.state.history


def _tree(world: World, app_id: str, commit: str) -> dict[str, bytes]:
    return _history_store(world).read_tree(UUID(app_id), commit, 1000, 1 << 24)


async def _two_builds(world: World) -> tuple[dict, str, str]:
    """An app built as v1, then with a changed index and an extra file as v2."""
    app = await _registered(world)
    first = (await _build(world, app["id"]))["build"]["commit"]
    (_src(world) / "app" / "index.tsx").write_text(INDEX_V2)
    (_src(world) / "lib").mkdir()
    (_src(world) / "lib" / "extra.ts").write_text("export const extra = 1;\n")
    second = (await _build(world, app["id"]))["build"]["commit"]
    return app, first, second


# --- a commit per build ------------------------------------------------------------------


async def test_each_successful_build_commits_what_it_built(world, builder) -> None:
    app = await _registered(world)

    body = await _build(world, app["id"])

    first = body["build"]["commit"]
    assert apphistory.COMMIT_RE.fullmatch(first)
    assert body["app"]["working_version"]["commit"] == first
    [commit] = await _commits(world, app["id"])
    assert commit | {"created_at": None} == {
        "id": first, "parent": None, "kind": "build", "subject": "Build 1.0.0",
        "version": "1.0.0", "user": "alice", "thread_id": None, "reverts": None,
        "created_at": None, "current": True,
    }  # fmt: skip
    assert {path: data.decode() for path, data in _tree(world, app["id"], first).items()} == {
        path: data.decode() for path, data in builder.staged.items()
    }

    (_src(world) / "app" / "index.tsx").write_text(INDEX_V2)
    (_src(world) / "app.json").write_text(json.dumps(manifest("hello", version="1.1.0")))
    second = (await _build(world, app["id"]))["build"]["commit"]

    commits = await _commits(world, app["id"])
    assert [(c["id"], c["parent"], c["version"], c["current"]) for c in commits] == [
        (second, first, "1.1.0", True),
        (first, None, "1.0.0", False),
    ]
    assert _tree(world, app["id"], second)["app/index.tsx"].decode() == INDEX_V2
    assert not (_src(world) / ".git").exists()
    (row,) = sql(world.platform, "SELECT commit FROM app_versions WHERE app_id = %s", (app["id"],))
    assert row["commit"] == second


async def test_rebuilding_unchanged_source_adds_no_commit(world, builder) -> None:
    app = await _registered(world)
    first = (await _build(world, app["id"]))["build"]["commit"]

    again = await _build(world, app["id"])

    assert again["build"]["commit"] == first
    assert [c["id"] for c in await _commits(world, app["id"])] == [first]


async def test_a_failed_build_commits_nothing(world, builder) -> None:
    app = await _registered(world)
    builder.script = fail_with("compile", {"step": "type", "message": "nope"})

    body = await _build(world, app["id"])

    assert body["ok"] is False and body["build"]["commit"] is None
    assert await _commits(world, app["id"]) == []

    builder.script = succeed
    good = (await _build(world, app["id"]))["build"]["commit"]
    (_src(world) / "app" / "index.tsx").write_text(INDEX_V2)
    builder.script = fail_with("smoke", {"step": "render", "message": "boom"})
    assert (await _build(world, app["id"]))["ok"] is False
    (_src(world) / "app.json").write_text('{"name": 1}')
    assert (await _build(world, app["id"]))["build"] is None

    assert [c["id"] for c in await _commits(world, app["id"])] == [good]
    (row,) = sql(world.platform, "SELECT commit FROM app_versions WHERE app_id = %s", (app["id"],))
    assert row["commit"] == good


async def test_an_agent_build_records_its_thread(world, builder) -> None:
    app = await _registered(world)

    await _build(world, app["id"], await world.agent("bob"))

    [commit] = await _commits(world, app["id"])
    assert (commit["user"], commit["thread_id"]) == ("bob", "t-1")
    assert commit["subject"] == "Build 1.0.0 by the agent"


async def test_builds_still_work_without_a_usable_history(world, builder) -> None:
    app = await _registered(world)
    world.platform.app.state.history = None

    body = await _build(world, app["id"])

    assert body["ok"] is True and body["build"]["commit"] is None
    assert (await _history(world, app["id"])).status_code == 503
    response = await _revert(world, app["id"], "a" * 40)
    assert (response.status_code, response.json()["detail"]) == (503, "history_unavailable")


async def test_history_is_paginated_newest_first(world, builder) -> None:
    app = await _registered(world)
    ids = []
    for n in range(3):
        (_src(world) / "app" / "index.tsx").write_text(f"export default () => {n};\n")
        ids.append((await _build(world, app["id"]))["build"]["commit"])

    page = (await _history(world, app["id"], limit=2)).json()
    assert [c["id"] for c in page["commits"]] == ids[:0:-1] and page["next_offset"] == 2
    page = (await _history(world, app["id"], offset=2, limit=2)).json()
    assert [c["id"] for c in page["commits"]] == ids[:1] and page["next_offset"] is None
    assert (await _history(world, app["id"], limit=0)).status_code == 422
    assert (await _history(world, app["id"], limit=101)).status_code == 422


# --- the patch bump after a publish ----------------------------------------------------------


async def _publish(world: World, app_id: str) -> httpx.Response:
    return await world.client.post(
        f"{APPS}/{app_id}/publish",
        json={"space_ids": [world.family["id"]]},
        headers=world.headers["alice"],
    )


def _src_version(world: World) -> str:
    return json.loads((_src(world) / "app.json").read_text())["version"]


async def test_the_first_build_after_a_publish_bumps_the_patch_once(world, builder) -> None:
    app = await _registered(world)
    await _build(world, app["id"])
    assert (await _publish(world, app["id"])).status_code == 200

    unchanged = await _build(world, app["id"])
    assert unchanged["app"]["working_version"]["version"] == "1.0.0"
    assert _src_version(world) == "1.0.0"

    (_src(world) / "app" / "index.tsx").write_text(INDEX_V2)
    body = await _build(world, app["id"])
    assert body["app"]["working_version"]["version"] == "1.0.1"
    assert _src_version(world) == "1.0.1"
    head = body["build"]["commit"]
    assert json.loads(_tree(world, app["id"], head)["app.json"])["version"] == "1.0.1"
    assert (await _commits(world, app["id"]))[0]["subject"] == "Build 1.0.1"

    (_src(world) / "app" / "index.tsx").write_text(INDEX_V1)
    again = await _build(world, app["id"])
    assert again["app"]["working_version"]["version"] == "1.0.1"
    published = await _publish(world, app["id"])
    assert (published.status_code, published.json()["version"]["version"]) == (200, "1.0.1")


async def test_an_authors_bump_above_the_published_versions_is_kept(world, builder) -> None:
    app = await _registered(world)
    await _build(world, app["id"])
    await _publish(world, app["id"])

    (_src(world) / "app" / "index.tsx").write_text(INDEX_V2)
    (_src(world) / "app.json").write_text(json.dumps(manifest("hello", version="1.1.0")))
    body = await _build(world, app["id"])

    assert body["app"]["working_version"]["version"] == "1.1.0"
    assert _src_version(world) == "1.1.0"


async def test_a_revert_past_a_publish_builds_as_a_new_patch(world, builder) -> None:
    app, first, _second = await _two_builds(world)
    await _publish(world, app["id"])

    response = await _revert(world, app["id"], first)

    assert response.status_code == 200, response.text
    assert response.json()["app"]["working_version"]["version"] == "1.0.1"
    assert (_src(world) / "app" / "index.tsx").read_text() == INDEX_V1
    assert _src_version(world) == "1.0.1"


async def test_an_app_never_built_has_no_history(world, builder) -> None:
    app = await _registered(world)
    assert (await _history(world, app["id"])).json() == {"commits": [], "next_offset": None}
    response = await _revert(world, app["id"], "a" * 40)
    assert (response.status_code, response.json()["detail"]) == (422, "unknown_commit")


# --- revert --------------------------------------------------------------------------------


async def test_revert_commits_the_old_tree_writes_it_back_and_rebuilds(world, builder) -> None:
    app, first, second = await _two_builds(world)
    (_src(world) / ".notes").write_text("mine")
    builder.calls.clear()

    response = await _revert(world, app["id"], first[:12])

    assert response.status_code == 200, response.text
    body = response.json()
    head = body["commit"]
    assert body["ok"] is True and body["build"]["commit"] == head
    assert body["app"]["working_version"]["commit"] == head
    assert [phase for _, phase in builder.calls] == ["compile", "smoke"]
    commits = await _commits(world, app["id"])
    assert [(c["id"], c["kind"], c["reverts"], c["current"]) for c in commits] == [
        (head, "revert", first, True),
        (second, "build", None, False),
        (first, "build", None, False),
    ]
    assert commits[0]["subject"] == f"Revert to {first[:12]} (1.0.0)"
    assert commits[0]["parent"] == second
    assert _tree(world, app["id"], head) == _tree(world, app["id"], first)
    assert (_src(world) / "app" / "index.tsx").read_text() == INDEX_V1
    assert not (_src(world) / "lib").exists()
    assert (_src(world) / ".notes").read_text() == "mine"
    assert builder.staged == _tree(world, app["id"], first)


async def test_revert_replaces_planted_links_without_following_them(
    world, builder, tmp_path
) -> None:
    app, first, _second = await _two_builds(world)
    outside = tmp_path / "outside"
    (outside / "app").mkdir(parents=True)
    (outside / "app" / "index.tsx").write_text("outside")
    (outside / "AGENT.md").write_text("outside")
    src = _src(world)
    for name in ("app", "AGENT.md"):
        target = src / name
        if target.is_dir():
            for child in sorted(target.rglob("*"), reverse=True):
                child.rmdir() if child.is_dir() else child.unlink()
            target.rmdir()
        else:
            target.unlink()
        target.symlink_to(outside / name)

    response = await _revert(world, app["id"], first)

    assert response.status_code == 200 and response.json()["ok"] is True, response.text
    assert not (src / "app").is_symlink() and (src / "app" / "index.tsx").read_text() == INDEX_V1
    assert not (src / "AGENT.md").is_symlink() and (src / "AGENT.md").is_file()
    assert (outside / "app" / "index.tsx").read_text() == "outside"
    assert (outside / "AGENT.md").read_text() == "outside"
    assert sorted(os.listdir(outside)) == ["AGENT.md", "app"]
    assert not [n for n in os.listdir(src) if n.startswith(".homeai-")]


async def test_revert_rebuild_migrates_instances_and_leaves_destructive_steps_pending(
    world, builder
) -> None:
    app = await _registered(world)
    response = await world.client.post(
        f"{API}/spaces/{world.family['id']}/instances", json={"app_id": app["id"]},
        headers=world.headers["alice"],
    )  # fmt: skip
    assert response.status_code == 201
    (_src(world) / "schema.sql").write_text(ITEMS)
    first = (await _build(world, app["id"]))["build"]["commit"]
    (_src(world) / "schema.sql").write_text(ITEMS_NOTE)
    assert (await _build(world, app["id"]))["migrations"][0]["migration"]["status"] == "applied"

    body = (await _revert(world, app["id"], first)).json()

    migration = body["migrations"][0]["migration"]
    assert (migration["status"], migration["needs_approval"]) == ("pending", True)
    assert [s["kind"] for s in migration["steps"]] == ["destructive"]
    assert (_src(world) / "schema.sql").read_text() == ITEMS


async def test_reverting_to_the_head_restores_edits_without_a_new_commit(world, builder) -> None:
    app, _first, second = await _two_builds(world)
    (_src(world) / "app" / "index.tsx").write_text("scribbled")
    (_src(world) / "stray.txt").write_text("stray")

    body = (await _revert(world, app["id"], second)).json()

    assert body["commit"] == second and body["build"]["commit"] == second
    assert (_src(world) / "app" / "index.tsx").read_text() == INDEX_V2
    assert not (_src(world) / "stray.txt").exists()
    assert len(await _commits(world, app["id"])) == 2


@pytest.mark.parametrize(
    ("commit", "status", "detail"),
    [
        ("0" * 40, 422, "unknown_commit"),
        ("other", 422, "unknown_commit"),
        ("HEAD", 422, "invalid_request"),
        ("--output=/tmp/x", 422, "invalid_request"),
        ("abc", 422, "invalid_request"),
    ],
)
async def test_revert_needs_a_commit_of_this_app(world, builder, commit, status, detail) -> None:
    app, _first, second = await _two_builds(world)
    if commit == "other":
        other = await _registered(world, "other")
        commit = (await _build(world, other["id"]))["build"]["commit"]
    builder.calls.clear()

    response = await _revert(world, app["id"], commit)

    assert (response.status_code, response.json()["detail"]) == (status, detail)
    assert builder.calls == []
    assert (await _commits(world, app["id"]))[0]["id"] == second


# --- who may see and revert --------------------------------------------------------------


@pytest.mark.parametrize(
    ("user", "agent", "status"),
    [("alice", False, 200), ("bob", False, 200), ("carol", False, 200), ("carol", True, 200),
     ("dave", False, 404)],
)  # fmt: skip
async def test_history_needs_read_on_the_source_space(world, builder, user, agent, status) -> None:
    app = await _registered(world)
    await _build(world, app["id"])
    headers = await world.agent(user) if agent else world.headers[user]

    response = await world.client.get(f"{APPS}/{app['id']}/history", headers=headers)

    assert response.status_code == status, response.text
    if status == 200:
        assert len(response.json()["commits"]) == 1


@pytest.mark.parametrize(
    ("user", "agent", "status"),
    [("bob", False, 200), ("bob", True, 200), ("carol", False, 403), ("carol", True, 403),
     ("dave", False, 404)],
)  # fmt: skip
async def test_revert_needs_write_on_the_source_space(world, builder, user, agent, status) -> None:
    app, first, second = await _two_builds(world)
    headers = await world.agent(user) if agent else world.headers[user]
    builder.calls.clear()

    response = await _revert(world, app["id"], first, headers)

    assert response.status_code == status, response.text
    head = (await _commits(world, app["id"]))[0]
    if status == 200:
        assert head["reverts"] == first and head["user"] == user
        assert head["thread_id"] == ("t-1" if agent else None)
    else:
        assert head["id"] == second and builder.calls == []
        assert (_src(world) / "app" / "index.tsx").read_text() == INDEX_V2


async def test_history_and_revert_need_a_session(world, builder) -> None:
    app = await _registered(world)
    assert (await world.client.get(f"{APPS}/{app['id']}/history")).status_code == 401
    response = await world.client.post(f"{APPS}/{app['id']}/revert", json={"commit": "a" * 40})
    assert response.status_code == 401


# --- a planted .git is inert ------------------------------------------------------------


def _plant_git(src: Path, marker: Path) -> None:
    """A real repo in the source folder whose config, hooks and attributes would each touch `marker`."""
    hook = f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\ncat\n"
    subprocess.run(["git", "init", "-q", str(src)], check=True, capture_output=True)
    hooks = src / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-commit", "post-commit", "pre-auto-gc", "reference-transaction",
                 "post-index-change", "fsmonitor-watchman"):  # fmt: skip
        (hooks / name).write_text(hook)
        (hooks / name).chmod(0o755)
    evil = src / ".git" / "evil.sh"
    evil.write_text(hook)
    evil.chmod(0o755)
    with (src / ".git" / "config").open("a") as f:
        f.write(
            f"[core]\n\thooksPath = {hooks}\n\tfsmonitor = {evil}\n\tsshCommand = {evil}\n"
            f'[filter "evil"]\n\tclean = {evil}\n\tsmudge = {evil}\n\trequired = true\n'
            f"[include]\n\tpath = {src / 'included'}\n"
        )
    (src / ".gitattributes").write_text("* filter=evil\n")
    (src / "lib").mkdir(exist_ok=True)
    (src / "lib" / ".gitattributes").write_text("* filter=evil\n")
    # The plant is live: git run in the folder the ordinary way fires it.
    subprocess.run(["git", "-C", str(src), "add", "-A"], check=True, capture_output=True,
                   stdin=subprocess.DEVNULL, timeout=30)  # fmt: skip
    assert marker.exists()
    marker.unlink()
    (src / ".git" / "index").unlink()


def _digest(folder: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        h.update(str(path.relative_to(folder)).encode())
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()


async def test_a_planted_git_repo_in_the_source_is_never_used(
    world, builder, tmp_path, monkeypatch
) -> None:
    app = await _registered(world)
    src = _src(world)
    marker = tmp_path / "marker"
    _plant_git(src, marker)
    planted = _digest(src / ".git")
    # And nothing git would pick up from the platform's own environment gets through.
    monkeypatch.setenv("GIT_DIR", str(src / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(src))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", f"'core.fsmonitor={src / '.git' / 'evil.sh'}'")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(src / ".git" / "hooks"))

    first = (await _build(world, app["id"]))["build"]["commit"]
    (src / "app" / "index.tsx").write_text(INDEX_V2)
    await _build(world, app["id"])
    reverted = (await _revert(world, app["id"], first)).json()
    commits = await _commits(world, app["id"])

    assert not marker.exists()
    assert reverted["ok"] is True and len(commits) == 3
    assert _digest(src / ".git") == planted
    assert (src / ".gitattributes").is_file() and (src / "lib" / ".gitattributes").is_file()
    for commit in commits:
        assert not [p for p in _tree(world, app["id"], commit["id"]) if "/." in f"/{p}"]
    repo = _history_store(world).repo(UUID(app["id"]))
    assert repo.is_relative_to(world.platform.data_dir / "app-git")
    assert sorted(os.listdir(repo / "hooks") if (repo / "hooks").exists() else []) == []


# --- the repo layer -----------------------------------------------------------------------


def test_messages_carry_trailers_that_survive_hostile_values(tmp_path) -> None:
    history = AppHistory(tmp_path / "app-git")
    history.prepare()
    app_id = UUID("00000000-0000-4000-8000-000000000001")
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("a")
    text = apphistory.message(
        "Build 1.0\nReverts: " + "f" * 40, version="1.0\n\x00x", user="al\nice",
        thread_id="t\x1e1",
    )  # fmt: skip

    commit, new = history.commit(app_id, history.write_tree(app_id, work), text)

    assert new
    [parsed], more = history.log(app_id, 0, 10)
    assert (parsed.id, more, parsed.reverts) == (commit, False, None)
    assert (parsed.subject, parsed.version, parsed.user, parsed.thread_id) == (
        "Build 1.0Reverts: " + "f" * 40, "1.0x", "alice", "t1",
    )  # fmt: skip
    assert history.resolve(app_id, commit[:7]) == commit
    assert history.resolve(app_id, "--all") is None
    assert os.stat(history.root).st_mode & 0o777 == 0o700


def test_prepare_refuses_without_git(tmp_path) -> None:
    with pytest.raises(apphistory.HistoryError):
        AppHistory(tmp_path / "app-git", git="no-such-git-binary").prepare()
