"""The app tools (`app/agent/app_tools.py`, M13-02) through a real chat turn.

A fake model calls the tools over `/ws/chat`; the fake platform
(`tests/fake_platform`) plays the apps API, rpc, migrations and HITL
markers. The test user edits the shared space "family" and owns their
personal space.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.agent.app_tools import APP_TOOL_NAMES, make_app_tools
from app.core.config import Settings
from app.db.settings import InMemorySettingsStore
from app.main import create_app
from tests.fake_identity import TEST_USER_ID, AutoCreateThreadStore, FixedIdentityVerifier
from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallsTurn, ToolCallTurn
from tests.fake_platform.scripting import AGENT_TOKEN, FakePlatform
from tests.test_chat_ws import _assert_turn_end, _drain_turn

TEMPLATES = Path(__file__).resolve().parents[3] / "examples" / "apps"
PERSONAL = f"personal:{TEST_USER_ID}"


async def _settings_store(hitl: bool) -> InMemorySettingsStore:
    store = InMemorySettingsStore()
    await store.update_document(TEST_USER_ID, {"hitl_enabled": hitl})
    return store


def _client(fake_model: FakeModel, fake_platform: FakePlatform, store) -> TestClient:
    settings = fake_model.settings(
        platform_url=fake_platform.base_url,
        platform_agent_token=AGENT_TOKEN,
        app_templates_dir=str(TEMPLATES),
    )
    return TestClient(
        create_app(
            settings,
            checkpointer_override=MemorySaver(),
            thread_store_override=AutoCreateThreadStore(),
            settings_store_override=store,
            identity_verifier_override=FixedIdentityVerifier(),
            delegation_client_override=fake_platform.client(),
        )
    )


def _tool_results(fake_model: FakeModel) -> list[str]:
    """What the model was shown for each tool call, in order, as of its last request."""
    return [m["content"] for m in fake_model.requests[-1]["messages"] if m.get("role") == "tool"]


def _approve(ws, approval: dict, decision: str = "approve") -> list[dict]:
    ws.send_json(
        {
            "type": "approval_response",
            "interrupt_id": approval["interrupt_id"],
            "decisions": [
                {"tool_call_id": a["tool_call_id"], "decision": decision}
                for a in approval["actions"]
            ],
        }
    )
    return _drain_turn(ws)


def _family(fake_platform: FakePlatform, role: str = "editor") -> None:
    fake_platform.add_space("family", {TEST_USER_ID: role})


# --- list_apps / create_app / build_app ------------------------------------------------------


async def test_list_apps_shows_instances_sources_and_data_paths(fake_model, fake_platform):
    _family(fake_platform, "viewer")
    _, groceries = fake_platform.seed_app("family", "groceries")
    _, notes = fake_platform.seed_app(PERSONAL, "notes", built=False)
    fake_model.queue(ToolCallTurn("list_apps", {}), TextTurn("ok"))
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/list") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "what apps do I have?"})
        frames = _drain_turn(ws)
    _assert_turn_end(frames[-1], "completed")
    start = next(f for f in frames if f["type"] == "tool_start")
    assert (start["name"], start["category"]) == ("list_apps", "app")
    [out] = _tool_results(fake_model)
    assert f"instance id: {groceries['id']}" in out
    assert 'in /spaces/family (shared space "Family"); the user\'s role: viewer' in out
    assert "execute_code read-only data: /app-data/spaces/family/groceries/data.sqlite" in out
    assert "The groceries app." in out
    assert f"instance id: {notes['id']}" in out
    assert "source: /personal/Apps/notes (app id" in out and "not built yet" in out
    assert "/app-data/personal/notes/data.sqlite" in out
    assert "image-shipped system app" in out
    assert "instance id: files" in out
    assert "moveToSpace(src, dst)" in out


async def test_app_action_files_move_to_space(fake_model, fake_platform):
    _family(fake_platform)
    fake_platform.personal()["notes.txt"] = b"hello"
    fake_model.queue(
        ToolCallTurn(
            "app_action",
            {
                "instance": "files",
                "name": "moveToSpace",
                "params": {"src": "/personal/notes.txt", "dst": "/spaces/family/notes.txt"},
            },
        ),
        TextTurn("moved"),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/files-move") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "move notes into family"})
        frames = _drain_turn(ws)
    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    assert _tool_results(fake_model) == [
        "OK: moved /personal/notes.txt to /spaces/family/notes.txt."
    ]
    assert "notes.txt" not in fake_platform.personal()
    assert fake_platform.tree("family")["notes.txt"] == b"hello"
    assert fake_platform.system_action_calls == [
        ("files", "moveToSpace", {"src": "/personal/notes.txt", "dst": "/spaces/family/notes.txt"})
    ]


async def test_create_app_copies_the_template_registers_and_installs(fake_model, fake_platform):
    fake_model.queue(
        ToolCallTurn("create_app", {"space": "personal", "slug": "chores", "name": "Chore Chart"}),
        TextTurn("created"),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/create") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "make me a chore chart"})
        frames = _drain_turn(ws)
    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    tree = fake_platform.personal()
    template = {
        p.relative_to(TEMPLATES / "grocery-list").as_posix()
        for p in (TEMPLATES / "grocery-list").rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.parts)
    }
    assert {k.removeprefix("Apps/chores/") for k in tree} == template
    manifest = json.loads(tree["Apps/chores/app.json"])
    assert (manifest["slug"], manifest["name"]) == ("chores", "Chore Chart")
    [app] = fake_platform.apps
    assert (app["slug"], app["source_path"]) == ("chores", "/personal/Apps/chores")
    [inst] = fake_platform.instances
    assert inst["space_key"] == PERSONAL
    [out] = _tool_results(fake_model)
    assert f"instance id: {inst['id']}" in out and 'build_app(app="/personal/Apps/chores")' in out


async def test_create_app_copies_a_named_template(fake_model, fake_platform):
    fake_model.queue(
        ToolCallTurn(
            "create_app",
            {"space": "personal", "slug": "journal", "name": "Journal", "template": "notes"},
        ),
        TextTurn("created"),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/create-notes") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "make me a journal"})
        _assert_turn_end(_drain_turn(ws)[-1], "completed")
    tree = fake_platform.personal()
    assert "Apps/journal/schema.sql" in tree
    assert b"CREATE TABLE notes" in tree["Apps/journal/schema.sql"]
    assert "Apps/journal/actions/addNote.sql" in tree
    assert "Apps/journal/app/note/[id].tsx" in tree
    assert "Apps/journal/actions/addItem.sql" not in tree
    [out] = _tool_results(fake_model)
    assert "from the notes template" in out


async def test_create_app_refuses_bad_input_without_writing(fake_model, fake_platform):
    _family(fake_platform, "viewer")
    fake_platform.personal()["Apps/taken/app.json"] = b"{}"
    fake_model.queue(
        ToolCallsTurn(
            [
                ("create_app", {"space": "family", "slug": "x", "name": "X"}),
                ("create_app", {"space": "personal", "slug": "taken", "name": "T"}),
                ("create_app", {"space": "personal", "slug": "Bad Slug", "name": "B"}),
                ("create_app", {"space": "personal", "slug": "ok", "name": "O", "template": "no"}),
                ("create_app", {"space": "work", "slug": "ok", "name": "O"}),
            ]
        ),
        TextTurn("hm"),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/create-bad") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "make apps"})
        _assert_turn_end(_drain_turn(ws)[-1], "completed")
    viewer, taken, slug, template, space = _tool_results(fake_model)
    assert "can only view /spaces/family" in viewer
    assert "/personal/Apps/taken already exists" in taken
    assert "slug must be" in slug
    assert "no template 'no'" in template
    for name in ("grocery-list", "list", "notes", "tracker"):
        assert name in template
    assert "no space 'work'" in space
    assert fake_platform.apps == [] and list(fake_platform.personal()) == ["Apps/taken/app.json"]


async def test_build_app_reports_diagnostics_then_success_with_a_pending_migration(
    fake_model, fake_platform
):
    _family(fake_platform)
    _, inst = fake_platform.seed_app("family", "groceries")
    fake_platform.builder = lambda _app, _files: [
        {"step": "type", "file": "app/index.tsx", "path": "/spaces/family/Apps/groceries/app/index.tsx",
         "line": 3, "column": 7, "message": "Type 'number' is not assignable to type 'string'.",
         "source": "const x: string = 1;"}
    ]  # fmt: skip
    fake_model.queue(ToolCallTurn("build_app", {"app": "family/groceries"}), TextTurn("broken"))
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/build") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "build it"})
        _assert_turn_end(_drain_turn(ws)[-1], "completed")
        [failed] = _tool_results(fake_model)
        assert "Build FAILED for /spaces/family/Apps/groceries: 1 problem(s)" in failed
        assert "- [type] app/index.tsx:3:7: Type 'number' is not assignable" in failed
        assert "const x: string = 1;" in failed

        fake_platform.builder = lambda _app, _files: []
        pending = fake_platform.migration(inst["id"])
        fake_platform.next_migration[inst["id"]] = pending
        fake_model.queue(ToolCallTurn("build_app", {"app": "groceries"}), TextTurn("built"))
        ws.send_json({"type": "user_message", "content": "again"})
        frames = _drain_turn(ws)
    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    ok = _tool_results(fake_model)[-1]
    assert ok.startswith("Build succeeded: Groceries 1.0.0 (build b-2, commit 0000002")
    assert f"a DESTRUCTIVE migration is pending (id {pending['id']}); it was NOT applied" in ok
    assert "ALTER TABLE items DROP COLUMN done" in ok
    assert f'approve_migration(instance="{inst["id"]}", migration_id="{pending["id"]}")' in ok
    assert fake_platform.approvals == [] and fake_platform.hitl_mints == []


# --- app_sql / app_action --------------------------------------------------------------------


async def test_reads_never_ask_even_in_a_shared_space(fake_model, fake_platform):
    _family(fake_platform)
    _, inst = fake_platform.seed_app("family", "groceries")
    fake_platform.rows[inst["id"]] = [{"id": 1, "name": "Milk"}]
    sql = "SELECT id, name FROM items WHERE name = ?"
    fake_model.queue(
        ToolCallTurn("app_sql", {"instance": inst["id"], "sql": sql, "params": ["Milk"]}),
        TextTurn("Milk"),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/read") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "what's on the list?"})
        frames = _drain_turn(ws)
    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    assert _tool_results(fake_model) == ['1 row(s):\n{"id": 1, "name": "Milk"}']
    assert fake_platform.rpc_calls == [
        (inst["id"], {"op": "getAll", "sql": sql, "params": ["Milk"]})
    ]


async def test_a_shared_write_asks_first_and_runs_once_approved(fake_model, fake_platform):
    _family(fake_platform)
    _, inst = fake_platform.seed_app("family", "groceries")
    sql = "INSERT INTO items (name) VALUES (:name)"
    fake_model.queue(
        ToolCallTurn(
            "app_sql", {"instance": "family/groceries", "sql": sql, "params": {"name": "Eggs"}}
        )
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/write") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "add eggs"})
        frames = _drain_turn(ws)
        _assert_turn_end(frames[-1], "awaiting_approval")
        [approval] = [f for f in frames if f["type"] == "approval_request"]
        [action] = approval["actions"]
        assert (action["name"], action["category"]) == ("app_sql", "app")
        assert action["args"] == {
            "instance": inst["id"], "space": "/spaces/family", "sql": sql, "params": {"name": "Eggs"},
        }  # fmt: skip
        assert action["description"] == (
            'Change data in Groceries in /spaces/family (shared space "Family"):\n'
            f'{sql}\nparams: {{"name": "Eggs"}}'
        )
        assert [op for _, op in fake_platform.rpc_calls] == [
            {"op": "getAll", "sql": sql, "params": {"name": "Eggs"}}
        ]
        # The paused call's tool_start has no tool_end; the client drops it on approval_request.
        assert [f["type"] for f in frames if f["type"].startswith("tool_")] == ["tool_start"]

        fake_model.queue(TextTurn("added"))
        resumed = _approve(ws, approval)
    _assert_turn_end(resumed[-1], "completed")
    ops = [body["op"] for _, body in fake_platform.rpc_calls]
    assert ops == ["getAll", "getAll", "run"]
    assert fake_platform.rpc_calls[-1] == (
        inst["id"], {"op": "run", "sql": sql, "params": {"name": "Eggs"}}
    )  # fmt: skip
    end = next(f for f in resumed if f["type"] == "tool_end")
    assert (end["name"], end["status"]) == ("app_sql", "success")
    assert _tool_results(fake_model) == ["OK: 1 row(s) changed; lastInsertRowId 7."]


async def test_a_rejected_write_is_not_run(fake_model, fake_platform):
    _family(fake_platform)
    _, inst = fake_platform.seed_app("family", "groceries")
    fake_model.queue(ToolCallTurn("app_sql", {"instance": inst["id"], "sql": "DELETE FROM items"}))
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/reject") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "clear the list"})
        approval = next(f for f in _drain_turn(ws) if f["type"] == "approval_request")
        fake_model.queue(TextTurn("left it"))
        _assert_turn_end(_approve(ws, approval, "reject")[-1], "completed")
    assert [body["op"] for _, body in fake_platform.rpc_calls] == ["getAll", "getAll"]
    [out] = _tool_results(fake_model)
    assert out.startswith("The user rejected this statement; it was not run.")


async def test_writes_run_directly_with_hitl_off_or_in_the_personal_space(
    fake_model, fake_platform
):
    _family(fake_platform)
    _, shared = fake_platform.seed_app("family", "groceries")
    _, mine = fake_platform.seed_app(PERSONAL, "notes")
    for hitl, inst in ((False, shared), (True, mine)):
        fake_platform.rpc_calls.clear()
        fake_model.queue(
            ToolCallTurn("app_sql", {"instance": inst["id"], "sql": "UPDATE items SET done = 1"}),
            TextTurn("done"),
        )
        with (
            _client(fake_model, fake_platform, await _settings_store(hitl)) as client,
            client.websocket_connect(f"/ws/chat/direct-{hitl}") as ws,
        ):
            ws.send_json({"type": "user_message", "content": "mark all done"})
            frames = _drain_turn(ws)
        _assert_turn_end(frames[-1], "completed")
        assert not any(f["type"] == "approval_request" for f in frames)
        assert [body["op"] for _, body in fake_platform.rpc_calls] == ["getAll", "run"]


async def test_viewers_cant_write_and_ddl_is_refused(fake_model, fake_platform):
    _family(fake_platform, "viewer")
    _, inst = fake_platform.seed_app("family", "groceries")
    fake_model.queue(
        ToolCallsTurn(
            [
                ("app_sql", {"instance": inst["id"], "sql": "DELETE FROM items"}),
                ("app_action", {"instance": inst["id"], "name": "addItem"}),
                ("app_sql", {"instance": inst["id"], "sql": "-- hi\n drop table items"}),
            ]
        ),
        TextTurn("can't"),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/viewer") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "clear it"})
        frames = _drain_turn(ws)
    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    sql_out, action_out, ddl_out = _tool_results(fake_model)
    assert "can only view /spaces/family" in sql_out and "this statement writes" in sql_out
    assert "can only view /spaces/family" in action_out
    assert ddl_out.startswith("Error: DROP isn't allowed here.")
    assert [body["op"] for _, body in fake_platform.rpc_calls] == ["getAll"]


async def test_a_shared_action_shows_its_sql_and_runs_once_approved(fake_model, fake_platform):
    _family(fake_platform)
    add = "INSERT INTO items (name) VALUES (:name);\n"
    _, inst = fake_platform.seed_app("family", "groceries", {"actions/addItem.sql": add})
    call = {"instance": inst["id"], "name": "addItem", "params": {"name": "Tea"}}
    fake_model.queue(ToolCallTurn("app_action", call))
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/action") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "add tea"})
        approval = next(f for f in _drain_turn(ws) if f["type"] == "approval_request")
        [action] = approval["actions"]
        assert action["name"] == "app_action" and action["category"] == "app"
        assert action["args"] == call | {"space": "/spaces/family"}
        assert action["description"] == (
            'Run action addItem of Groceries in /spaces/family (shared space "Family") '
            f'with {{"name": "Tea"}}:\n{add}'
        )
        assert fake_platform.rpc_calls == []
        fake_model.queue(TextTurn("added"))
        _assert_turn_end(_approve(ws, approval)[-1], "completed")
    assert fake_platform.rpc_calls == [
        (inst["id"], {"op": "action", "name": "addItem", "params": {"name": "Tea"}})
    ]


async def test_parallel_writes_are_one_card_and_resume_together(fake_model, fake_platform):
    _family(fake_platform)
    _, inst = fake_platform.seed_app("family", "groceries")
    first, second = "DELETE FROM items WHERE id = 1", "DELETE FROM items WHERE id = 2"
    fake_model.queue(
        ToolCallsTurn(
            [
                ("app_sql", {"instance": inst["id"], "sql": first}),
                ("list_apps", {}),
                ("app_sql", {"instance": inst["id"], "sql": second}),
            ]
        )
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/parallel") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "remove both"})
        frames = _drain_turn(ws)
        _assert_turn_end(frames[-1], "awaiting_approval")
        [approval] = [f for f in frames if f["type"] == "approval_request"]
        assert sorted(a["args"]["sql"] for a in approval["actions"]) == [first, second]
        assert len({a["tool_call_id"] for a in approval["actions"]}) == 2

        state = client.get("/api/threads/parallel/state").json()["pending_approval"]
        assert state == {"interrupt_id": approval["interrupt_id"], "actions": approval["actions"]}

        fake_model.queue(TextTurn("removed"))
        ws.send_json(
            {
                "type": "approval_response",
                "interrupt_id": approval["interrupt_id"],
                "decisions": [
                    {
                        "tool_call_id": a["tool_call_id"],
                        "decision": "approve" if a["args"]["sql"] == first else "reject",
                    }
                    for a in approval["actions"]
                ],
            }
        )
        _assert_turn_end(_drain_turn(ws)[-1], "completed")
    runs = [body["sql"] for _, body in fake_platform.rpc_calls if body["op"] == "run"]
    assert runs == [first]
    results = _tool_results(fake_model)
    assert results[0] == "OK: 1 row(s) changed; lastInsertRowId 7."
    assert results[1].startswith("Apps (system apps are native host screens")
    assert results[2].startswith("The user rejected this statement")


# --- approve_migration -----------------------------------------------------------------------


def _pending(fake_platform: FakePlatform, space_key: str = PERSONAL) -> tuple[dict, dict]:
    _, inst = fake_platform.seed_app(space_key, "groceries")
    migration = fake_platform.migration(inst["id"])
    fake_platform.migrations[inst["id"]].append(migration)
    return inst, migration


async def test_a_migration_always_asks_and_applies_with_a_marker(fake_model, fake_platform):
    inst, migration = _pending(fake_platform)
    call = {"instance": inst["id"], "migration_id": migration["id"]}
    fake_model.queue(ToolCallTurn("approve_migration", call))
    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/migrate") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "drop the done column"})
        frames = _drain_turn(ws)
        _assert_turn_end(frames[-1], "awaiting_approval")
        [approval] = [f for f in frames if f["type"] == "approval_request"]
        [action] = approval["actions"]
        assert action["name"] == "approve_migration" and action["category"] == "app"
        assert action["args"] == call | {
            "space": "/personal", "steps": ["ALTER TABLE items DROP COLUMN done"],
        }  # fmt: skip
        assert "DESTRUCTIVE" in action["description"]
        assert "/personal (the user's personal space)" in action["description"]
        assert "ALTER TABLE items DROP COLUMN done" in action["description"]
        assert fake_platform.hitl_mints == [] and fake_platform.approvals == []

        fake_model.queue(TextTurn("applied"))
        resumed = _approve(ws, approval)
    _assert_turn_end(resumed[-1], "completed")
    [mint] = fake_platform.hitl_mints
    assert (mint["instance_id"], mint["migration_id"]) == (inst["id"], migration["id"])
    [(iid, mid, marker)] = fake_platform.approvals
    assert (iid, mid) == (inst["id"], migration["id"]) and marker.startswith("hitl_fake")
    assert migration["status"] == "applied"
    [out] = _tool_results(fake_model)
    assert out == f"Migration {migration['id']} applied (snapshot snap-{migration['id']}.sqlite)."
    seen = json.dumps(fake_model.requests) + json.dumps(frames + resumed)
    assert marker not in seen and mint["delegation"] not in seen


async def test_a_rejected_migration_mints_nothing(fake_model, fake_platform):
    inst, migration = _pending(fake_platform)
    fake_model.queue(
        ToolCallTurn("approve_migration", {"instance": inst["id"], "migration_id": migration["id"]})
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/migrate-no") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "apply it"})
        approval = next(f for f in _drain_turn(ws) if f["type"] == "approval_request")
        fake_model.queue(TextTurn("kept"))
        _assert_turn_end(_approve(ws, approval, "reject")[-1], "completed")
    assert fake_platform.hitl_mints == [] and fake_platform.approvals == []
    assert migration["status"] == "pending"
    assert _tool_results(fake_model)[0].startswith("The user rejected the migration")


async def test_a_migration_that_isnt_pending_asks_nothing(fake_model, fake_platform):
    inst, migration = _pending(fake_platform)
    migration["status"] = "superseded"
    fake_model.queue(
        ToolCallTurn(
            "approve_migration", {"instance": inst["id"], "migration_id": migration["id"]}
        ),
        TextTurn("nothing to do"),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/migrate-old") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "apply it"})
        frames = _drain_turn(ws)
    _assert_turn_end(frames[-1], "completed")
    assert not any(f["type"] == "approval_request" for f in frames)
    assert _tool_results(fake_model) == [
        f"Migration {migration['id']} is superseded, not pending; nothing to approve."
    ]


async def test_without_a_delegation_nothing_is_called(fake_platform):
    settings = Settings(platform_url=fake_platform.base_url, _env_file=None)
    tools = {t.name: t for t in make_app_tools(settings)}
    assert set(tools) == set(APP_TOOL_NAMES)
    out = await tools["list_apps"].ainvoke({}, config={"configurable": {}})
    assert out == "Error: apps are unavailable for this run (no delegation)."
    assert fake_platform.file_requests == []


# --- the build loop --------------------------------------------------------------------------


async def test_create_build_fail_fix_build_ok(fake_model, fake_platform):
    """create_app, a build that fails, an edit that fixes it, a build that succeeds."""

    def builder(_app: dict, files: dict[str, bytes]) -> list[dict]:
        text = files["app/index.tsx"].decode()
        if "BROKEN" not in text:
            return []
        line = text[: text.index("BROKEN")].count("\n") + 1
        return [{"step": "type", "file": "app/index.tsx", "path": "", "line": line,
                 "column": 1, "message": "Cannot find name 'BROKEN'."}]  # fmt: skip

    fake_platform.builder = builder
    fake_model.queue(
        ToolCallTurn("create_app", {"space": "personal", "slug": "todo", "name": "To-do"}),
        ToolCallTurn(
            "edit_file",
            {
                "file_path": "/personal/Apps/todo/app/index.tsx",
                "old_string": "export default",
                "new_string": "BROKEN;\nexport default",
            },
        ),
        ToolCallTurn("build_app", {"app": "/personal/Apps/todo"}),
        ToolCallTurn(
            "edit_file",
            {
                "file_path": "/personal/Apps/todo/app/index.tsx",
                "old_string": "BROKEN;\n",
                "new_string": "",
            },
        ),
        ToolCallTurn("build_app", {"app": "todo"}),
        TextTurn("Your to-do app is ready."),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/loop") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "make me a to-do app"})
        frames = _drain_turn(ws)
    _assert_turn_end(frames[-1], "completed")
    ends = [(f["name"], f["status"]) for f in frames if f["type"] == "tool_end"]
    assert [name for name, _ in ends] == [
        "create_app", "edit_file", "build_app", "edit_file", "build_app",
    ]  # fmt: skip
    created, _, failed, _, built = _tool_results(fake_model)
    assert created.startswith("Created 'To-do' from the grocery-list template in /personal")
    assert "Build FAILED" in failed and "Cannot find name 'BROKEN'." in failed
    assert built.startswith("Build succeeded: To-do")
    [inst] = fake_platform.instances
    assert f"instance {inst['id']} in /personal" in built
    assert "database schema already up to date" in built
    assert len(fake_platform.builds) == 2
    assert fake_platform.apps[0]["working_version"]["commit"].startswith("0000002")


async def test_an_edit_that_breaks_syntax_is_reported_on_its_result(fake_model, fake_platform):
    path = "/personal/Apps/todo/app/index.tsx"
    fake_model.queue(
        ToolCallTurn("create_app", {"space": "personal", "slug": "todo", "name": "To-do"}),
        ToolCallTurn(
            "edit_file",
            {"file_path": path, "old_string": "export default", "new_string": "}\nexport default"},
        ),
        ToolCallTurn("write_file", {"file_path": "/personal/Apps/todo/notes.md", "content": "}"}),
        TextTurn("done"),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/syntax") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "make me a to-do app"})
        _assert_turn_end(_drain_turn(ws)[-1], "completed")
    _, edited, noted = _tool_results(fake_model)
    assert edited.startswith(f"Successfully replaced 1 instance(s) of the string in '{path}'\n")
    assert "\nSyntax error after this edit:\n  line " in edited
    assert 'unexpected "}"' in edited
    assert noted == "Updated file /personal/Apps/todo/notes.md"


async def test_outline_and_replace_symbol_edit_a_screen(fake_model, fake_platform):
    path = "/personal/Apps/todo/app/index.tsx"
    new_code = "function GroceryListScreen() {\n  return null;\n}"
    fake_model.queue(
        ToolCallTurn("create_app", {"space": "personal", "slug": "todo", "name": "To-do"}),
        ToolCallTurn("outline", {"file_path": path}),
        ToolCallTurn(
            "replace_symbol",
            {"file_path": path, "name": "GroceryListScreen", "new_code": new_code},
        ),
        TextTurn("done"),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(False)) as client,
        client.websocket_connect("/ws/chat/symbols") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "simplify the screen"})
        frames = _drain_turn(ws)
    _assert_turn_end(frames[-1], "completed")
    starts = {f["name"]: f["category"] for f in frames if f["type"] == "tool_start"}
    assert (starts["outline"], starts["replace_symbol"]) == ("file", "file")
    _, outlined, replaced = _tool_results(fake_model)
    assert "export default component GroceryListScreen" in outlined
    assert replaced.startswith(
        f"Replaced component GroceryListScreen (kept `export default`) in {path}: lines 8-"
    )
    screen = fake_platform.personal()["Apps/todo/app/index.tsx"].decode()
    assert "export default function GroceryListScreen() {\n  return null;\n}" in screen
    assert screen.startswith("import")


async def test_replace_symbol_asks_for_approval(fake_model, fake_platform):
    path = "/personal/Apps/todo/app/index.tsx"
    fake_model.queue(
        ToolCallTurn("create_app", {"space": "personal", "slug": "todo", "name": "To-do"}),
        ToolCallTurn(
            "replace_symbol",
            {"file_path": path, "name": "styles", "new_code": "const styles = {};"},
        ),
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/symbols-hitl") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "drop the styles"})
        request = next(f for f in _drain_turn(ws) if f["type"] == "approval_request")
        [action] = request["actions"]
        assert (action["name"], action["description"]) == (
            "replace_symbol",
            f"Replace `styles` in `{path}`",
        )


async def test_a_file_write_and_an_app_write_ask_one_after_the_other(fake_model, fake_platform):
    _family(fake_platform)
    _, inst = fake_platform.seed_app("family", "groceries")
    fake_model.queue(
        ToolCallsTurn(
            [
                ("write_file", {"file_path": "/personal/note.txt", "content": "hi"}),
                ("app_sql", {"instance": inst["id"], "sql": "DELETE FROM items"}),
            ]
        )
    )
    with (
        _client(fake_model, fake_platform, await _settings_store(True)) as client,
        client.websocket_connect("/ws/chat/mixed") as ws,
    ):
        ws.send_json({"type": "user_message", "content": "note it and clear"})
        first = next(f for f in _drain_turn(ws) if f["type"] == "approval_request")
        assert [a["name"] for a in first["actions"]] == ["write_file"]
        frames = _approve(ws, first)
        _assert_turn_end(frames[-1], "awaiting_approval")
        second = next(f for f in frames if f["type"] == "approval_request")
        assert [a["name"] for a in second["actions"]] == ["app_sql"]
        assert fake_platform.personal() == {"note.txt": b"hi"}
        fake_model.queue(TextTurn("done"))
        _assert_turn_end(_approve(ws, second)[-1], "completed")
    assert [body["op"] for _, body in fake_platform.rpc_calls][-1] == "run"
