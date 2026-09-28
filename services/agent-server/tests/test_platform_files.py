"""Unit tests for `PlatformFilesBackend` (`app/agent/platform_files.py`) against a respx-mocked platform.

Every protocol method runs both sync and async. Where the platform's answer
means the same thing as a `FilesystemBackend` failure (missing file, a file
where a directory was expected), the expected string is computed by running
deepagents' own `FilesystemBackend` on the same path, so the two can't drift.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from deepagents.backends import FilesystemBackend
from langchain_core.runnables.config import var_child_runnable_config

from app.agent.platform_files import PlatformFilesBackend
from app.core.delegation import Delegation, Grant

BASE = "http://platform.test"
FILES = f"{BASE}/api/platform/files"
TOKEN = "dlg-secret-token"
MTIME = "2026-09-01T12:00:00Z"
READ_ONLY = "Permission denied (read-only access to this space)"


def _delegation(token: str = TOKEN, ttl: timedelta = timedelta(minutes=15)) -> Delegation:
    return Delegation(None, Grant(token, datetime.now(UTC) + ttl))  # type: ignore[arg-type]


@contextlib.contextmanager
def _run(configurable: dict | None) -> Iterator[None]:
    """Stand in for a LangGraph run: what `get_config()` returns inside a tool call."""
    reset = var_child_runnable_config.set(
        None if configurable is None else {"configurable": configurable}
    )
    try:
        yield
    finally:
        var_child_runnable_config.reset(reset)


@pytest.fixture(params=["sync", "async"])
def call(request):
    backend = PlatformFilesBackend(BASE)

    async def invoke(method: str, *args, **kwargs):
        with _run({"delegation": _delegation()}):
            if request.param == "sync":
                return getattr(backend, method)(*args, **kwargs)
            return await getattr(backend, f"a{method}")(*args, **kwargs)

    return invoke


@pytest.fixture
def platform() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


@pytest.fixture
def fs(tmp_path) -> FilesystemBackend:
    (tmp_path / "personal").mkdir()
    (tmp_path / "personal" / "a-file.md").write_text("x")
    return FilesystemBackend(root_dir=tmp_path, virtual_mode=True)


def _entry(path: str, type_: str, size: int = 0) -> dict:
    return {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "type": type_,
        "size": size,
        "mtime": MTIME,
        "mime": None,
    }


def _err(status: int, detail: str, message: str | None = None) -> httpx.Response:
    body = {"detail": detail} if message is None else {"detail": detail, "message": message}
    return httpx.Response(status, json=body)


# --- auth and fail-closed ----------------------------------------------------------


async def test_every_request_carries_the_delegation_as_bearer(call, platform) -> None:
    route = platform.post("/api/platform/files/write").respond(json={"path": "/personal/a.md"})

    result = await call("write", "/personal/a.md", "body")

    assert result.error is None and result.path == "/personal/a.md"
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == {"path": "/personal/a.md", "content": "body"}


@pytest.mark.parametrize(
    "configurable",
    [
        pytest.param(None, id="outside-a-run"),
        pytest.param({}, id="no-delegation"),
        pytest.param({"delegation": TOKEN}, id="bare-string"),
        pytest.param({"delegation": _delegation(ttl=timedelta(seconds=-1))}, id="expired"),
    ],
)
@pytest.mark.parametrize("mode", ["sync", "async"])
async def test_fails_closed_without_a_live_delegation(configurable, mode, platform) -> None:
    backend = PlatformFilesBackend(BASE)
    with _run(configurable):
        if mode == "sync":
            results = [
                backend.ls("/personal"),
                backend.read("/personal/a.md"),
                backend.write("/personal/a.md", "x"),
                backend.edit("/personal/a.md", "a", "b"),
                backend.delete("/personal/a.md"),
                backend.grep("x", "/personal"),
                backend.glob("*", "/personal"),
            ]
            uploads = backend.upload_files([("/personal/a.md", b"x")])
            downloads = backend.download_files(["/personal/a.md"])
        else:
            results = [
                await backend.als("/personal"),
                await backend.aread("/personal/a.md"),
                await backend.awrite("/personal/a.md", "x"),
                await backend.aedit("/personal/a.md", "a", "b"),
                await backend.adelete("/personal/a.md"),
                await backend.agrep("x", "/personal"),
                await backend.aglob("*", "/personal"),
            ]
            uploads = await backend.aupload_files([("/personal/a.md", b"x")])
            downloads = await backend.adownload_files(["/personal/a.md"])

    assert not platform.calls
    for result in results:
        assert result.error and "no delegation" in result.error, result
    assert not results[5].matches and not results[6].matches
    assert uploads[0].error and "no delegation" in uploads[0].error
    assert downloads[0].error and "no delegation" in downloads[0].error
    assert downloads[0].content is None


async def test_ended_session_is_reported_not_retried(call, platform) -> None:
    route = platform.post("/api/platform/files/write").mock(
        return_value=_err(401, "unauthenticated")
    )

    result = await call("write", "/personal/a.md", "x")

    assert result.error == (
        "Error writing file '/personal/a.md': not authorized (the user's session has ended)"
    )
    assert route.call_count == 1


async def test_platform_unreachable(call, platform) -> None:
    platform.post("/api/platform/files/read").mock(side_effect=httpx.ConnectError("refused"))

    result = await call("read", "/personal/a.md")

    assert result.error == (
        "Error reading file '/personal/a.md': the platform is unreachable (ConnectError)"
    )


# --- ls -----------------------------------------------------------------------------


async def test_ls_entries(call, platform) -> None:
    route = platform.get("/api/platform/files").respond(
        json={
            "path": "/personal",
            "entries": [
                _entry("/personal/b.md", "file", 3),
                _entry("/personal/a", "dir"),
            ],
            "role": "owner",
            "writable": True,
        }
    )

    result = await call("ls", "/personal")

    assert route.calls.last.request.url.params["path"] == "/personal"
    assert result.error is None
    assert result.entries == [
        {"path": "/personal/a/", "is_dir": True, "size": 0, "modified_at": MTIME},
        {"path": "/personal/b.md", "is_dir": False, "size": 3, "modified_at": MTIME},
    ]


async def test_ls_missing_matches_filesystem_backend(call, platform, fs) -> None:
    platform.get("/api/platform/files").mock(return_value=_err(404, "not_found"))
    platform.get("/api/platform/files/stat").mock(return_value=_err(404, "not_found"))

    result = await call("ls", "/personal/nope")

    assert result.error == fs.ls("/personal/nope").error == "Path '/personal/nope': path_not_found"


async def test_ls_on_a_file_matches_filesystem_backend(call, platform, fs) -> None:
    platform.get("/api/platform/files").mock(return_value=_err(404, "not_found"))
    platform.get("/api/platform/files/stat").respond(
        json={"entry": _entry("/personal/a-file.md", "file", 1), "role": "owner", "writable": True}
    )

    result = await call("ls", "/personal/a-file.md")

    assert result.error == fs.ls("/personal/a-file.md").error
    assert result.error == "Path '/personal/a-file.md': not_a_directory"


async def test_ls_outside_the_tree_hints_at_the_layout(call, platform) -> None:
    platform.get("/api/platform/files").mock(return_value=_err(404, "not_found"))
    platform.get("/api/platform/files/stat").mock(return_value=_err(404, "not_found"))

    result = await call("ls", "/notes")

    assert result.error == (
        "Path '/notes': path_not_found (paths start with /personal/ or /spaces/<slug>/)"
    )


# --- read ---------------------------------------------------------------------------


async def test_read(call, platform) -> None:
    route = platform.post("/api/platform/files/read").respond(
        json={
            "path": "/personal/a.md",
            "content": "two\nthree",
            "encoding": "utf-8",
            "total_lines": 3,
            "start_line": 2,
            "end_line": 3,
        }
    )

    result = await call("read", "/personal/a.md", offset=1, limit=2)

    assert json.loads(route.calls.last.request.content) == {
        "path": "/personal/a.md",
        "offset": 1,
        "limit": 2,
    }
    assert result.error is None
    assert result.file_data == {"content": "two\nthree", "encoding": "utf-8"}
    assert (result.total_lines, result.start_line, result.end_line) == (3, 2, 3)


async def test_read_binary_passes_base64_through(call, platform) -> None:
    platform.post("/api/platform/files/read").respond(
        json={"path": "/personal/p.png", "content": "iVBORw==", "encoding": "base64"}
    )

    result = await call("read", "/personal/p.png")

    assert result.file_data == {"content": "iVBORw==", "encoding": "base64"}


async def test_read_missing_matches_filesystem_backend(call, platform, fs) -> None:
    platform.post("/api/platform/files/read").mock(return_value=_err(404, "not_found"))

    result = await call("read", "/personal/nope.md")

    assert result.error == fs.read("/personal/nope.md").error
    assert result.error == "File '/personal/nope.md' not found"


async def test_read_platform_message_is_passed_through(call, platform) -> None:
    message = "Line offset 10 exceeds file length (3 lines)"
    platform.post("/api/platform/files/read").mock(
        return_value=_err(422, "offset_out_of_range", message)
    )

    result = await call("read", "/personal/a.md", offset=10)

    assert result.error == message


async def test_read_in_a_space_you_are_not_in(call, platform) -> None:
    platform.post("/api/platform/files/read").mock(return_value=_err(404, "not_found"))

    result = await call("read", "/spaces/theirs/a.md")

    assert result.error == "File '/spaces/theirs/a.md' not found"


# --- write --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "detail", "reason"),
    [
        (403, "insufficient_role", READ_ONLY),
        (
            403,
            "read_only",
            "Permission denied (not writable; paths start with /personal/ or /spaces/<slug>/)",
        ),
        (409, "is_a_directory", "Is a directory"),
        (422, "invalid_path", "invalid path"),
        (500, "boom", "platform error 500"),
    ],
)
async def test_write_errors(call, platform, status, detail, reason) -> None:
    platform.post("/api/platform/files/write").mock(return_value=_err(status, detail))

    result = await call("write", "/spaces/fam/a.md", "x")

    assert result.error == f"Error writing file '/spaces/fam/a.md': {reason}"
    assert result.path is None


async def test_write_outside_the_tree(call, platform) -> None:
    platform.post("/api/platform/files/write").mock(return_value=_err(404, "not_found"))

    result = await call("write", "/notes.md", "x")

    assert result.error == (
        "Error writing file '/notes.md': No such file or directory "
        "(paths start with /personal/ or /spaces/<slug>/)"
    )


# --- edit ---------------------------------------------------------------------------


async def test_edit(call, platform) -> None:
    route = platform.post("/api/platform/files/edit").respond(
        json={"path": "/personal/a.md", "occurrences": 2}
    )

    result = await call("edit", "/personal/a.md", "a", "b", replace_all=True)

    assert json.loads(route.calls.last.request.content) == {
        "path": "/personal/a.md",
        "old_string": "a",
        "new_string": "b",
        "replace_all": True,
    }
    assert (result.error, result.path, result.occurrences) == (None, "/personal/a.md", 2)


async def test_edit_missing_matches_filesystem_backend(call, platform, fs) -> None:
    platform.post("/api/platform/files/edit").mock(return_value=_err(404, "not_found"))

    result = await call("edit", "/personal/nope.md", "a", "b")

    assert result.error == fs.edit("/personal/nope.md", "a", "b").error
    assert result.error == "Error: File '/personal/nope.md' not found"


async def test_edit_platform_message_is_passed_through(call, platform) -> None:
    message = "Error: String not found in file: 'zzz'"
    platform.post("/api/platform/files/edit").mock(
        return_value=_err(422, "string_not_found", message)
    )

    result = await call("edit", "/personal/a.md", "zzz", "b")

    assert result.error == message


async def test_edit_read_only(call, platform) -> None:
    platform.post("/api/platform/files/edit").mock(return_value=_err(403, "insufficient_role"))

    result = await call("edit", "/spaces/fam/a.md", "a", "b")

    assert result.error == f"Error editing file '/spaces/fam/a.md': {READ_ONLY}"


# --- delete -------------------------------------------------------------------------


async def test_delete(call, platform) -> None:
    route = platform.delete("/api/platform/files").respond(204)

    result = await call("delete", "/personal/a.md")

    assert route.calls.last.request.url.params["path"] == "/personal/a.md"
    assert (result.error, result.path) == (None, "/personal/a.md")


async def test_delete_missing_matches_filesystem_backend(call, platform, fs) -> None:
    platform.delete("/api/platform/files").mock(return_value=_err(404, "not_found"))

    result = await call("delete", "/personal/nope.md")

    assert result.error == fs.delete("/personal/nope.md").error
    assert result.error == "Error: '/personal/nope.md' not found"


async def test_delete_read_only(call, platform) -> None:
    platform.delete("/api/platform/files").mock(return_value=_err(403, "insufficient_role"))

    result = await call("delete", "/spaces/fam/a.md")

    assert result.error == f"Error deleting '/spaces/fam/a.md': {READ_ONLY}"


# --- grep / glob --------------------------------------------------------------------


async def test_grep(call, platform) -> None:
    route = platform.post("/api/platform/files/grep").respond(
        json={
            "matches": [{"path": "/spaces/fam/a.md", "line": 4, "text": "milk"}],
            "truncated": True,
            "error": None,
        }
    )

    result = await call("grep", "milk", "/spaces/fam", "*.md")

    assert json.loads(route.calls.last.request.content) == {
        "pattern": "milk",
        "path": "/spaces/fam",
        "glob": "*.md",
    }
    assert result.error is None
    assert result.matches == [{"path": "/spaces/fam/a.md", "line": 4, "text": "milk"}]
    assert result.truncated is True


async def test_grep_max_count_is_forwarded(call, platform) -> None:
    route = platform.post("/api/platform/files/grep").respond(
        json={"matches": [], "truncated": False, "error": None}
    )

    await call("grep", "x", max_count=5)

    assert json.loads(route.calls.last.request.content) == {"pattern": "x", "max_count": 5}


@pytest.mark.parametrize("reply", [_err(404, "not_found"), _err(422, "invalid_path", "x")])
async def test_grep_nowhere_is_no_matches(call, platform, reply) -> None:
    platform.post("/api/platform/files/grep").mock(return_value=reply)

    result = await call("grep", "x", "/spaces/theirs")

    assert result.error is None and result.matches == []


async def test_grep_bad_pattern_message(call, platform) -> None:
    platform.post("/api/platform/files/grep").mock(
        return_value=_err(422, "invalid_pattern", "Invalid regex pattern: (")
    )

    result = await call("grep", "(")

    assert result.error == "Invalid regex pattern: ("


async def test_glob(call, platform) -> None:
    route = platform.post("/api/platform/files/glob").respond(
        json={
            "matches": [
                {"path": "/personal/a.md", "is_dir": False, "size": 3, "modified_at": MTIME}
            ],
            "truncated": False,
            "truncation_reason": None,
        }
    )

    result = await call("glob", "**/*.md", "/personal")

    assert json.loads(route.calls.last.request.content) == {
        "pattern": "**/*.md",
        "path": "/personal",
    }
    assert result.error is None
    assert result.matches == [
        {"path": "/personal/a.md", "is_dir": False, "size": 3, "modified_at": MTIME}
    ]


async def test_glob_nowhere_is_no_matches(call, platform) -> None:
    platform.post("/api/platform/files/glob").mock(return_value=_err(404, "not_found"))

    result = await call("glob", "*", "/spaces/theirs")

    assert result.error is None and result.matches == []


async def test_glob_budget_truncation(call, platform) -> None:
    platform.post("/api/platform/files/glob").respond(
        json={"matches": [], "truncated": True, "truncation_reason": "budget"}
    )

    result = await call("glob", "**")

    assert (result.truncated, result.truncation_reason) == (True, "budget")


# --- upload / download --------------------------------------------------------------


async def test_upload(call, platform) -> None:
    ok = platform.put("/api/platform/files/content", params={"path": "/personal/a.bin"}).respond(
        json={"path": "/personal/a.bin"}
    )
    platform.put("/api/platform/files/content", params={"path": "/spaces/fam/b.bin"}).mock(
        return_value=_err(403, "insufficient_role")
    )

    results = await call(
        "upload_files", [("/personal/a.bin", b"\x00\x01"), ("/spaces/fam/b.bin", b"x")]
    )

    assert ok.calls.last.request.content == b"\x00\x01"
    assert [(r.path, r.error) for r in results] == [
        ("/personal/a.bin", None),
        ("/spaces/fam/b.bin", "permission_denied"),
    ]


async def test_download(call, platform) -> None:
    platform.get("/api/platform/files/download", params={"path": "/personal/a.bin"}).respond(
        content=b"\x00\x01"
    )
    platform.get("/api/platform/files/download", params={"path": "/personal/dir"}).mock(
        return_value=_err(404, "not_found")
    )
    platform.get("/api/platform/files/stat", params={"path": "/personal/dir"}).respond(
        json={"entry": _entry("/personal/dir", "dir"), "role": "owner", "writable": True}
    )
    platform.get("/api/platform/files/download", params={"path": "/personal/nope"}).mock(
        return_value=_err(404, "not_found")
    )
    platform.get("/api/platform/files/stat", params={"path": "/personal/nope"}).mock(
        return_value=_err(404, "not_found")
    )

    results = await call("download_files", ["/personal/a.bin", "/personal/dir", "/personal/nope"])

    assert [(r.path, r.content, r.error) for r in results] == [
        ("/personal/a.bin", b"\x00\x01", None),
        ("/personal/dir", None, "is_directory"),
        ("/personal/nope", None, "file_not_found"),
    ]


def test_backend_holds_no_credentials() -> None:
    backend = PlatformFilesBackend(BASE)
    assert TOKEN not in repr(vars(backend))
