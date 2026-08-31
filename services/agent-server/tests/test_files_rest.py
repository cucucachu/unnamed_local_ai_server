"""Unit tests for `/api/files*` REST routes (`app/api/files.py`, M3-03).

Unlike `test_chat.py`'s `rest_app`/`rest_client` fixtures, these routes never
touch `app.state.agent`/`app.state.checkpointer`/`app.state.thread_store`
(only `app.state.settings`, set synchronously in `create_app()` itself, well
before `lifespan` ever runs) — so there's no need for `app.router.
lifespan_context(app)` here, and no need for `checkpointer_override`/
`thread_store_override` either. `files_client` below builds `create_app()`
directly and wraps it in a plain `ASGITransport`, exactly like `tests/
conftest.py`'s own `client` fixture, just with `workspace_root` pointed at
a fresh `tmp_path` (function-scoped, so a brand-new empty directory per
test — no shared state across tests) instead of the module's hardcoded
`/data/workspace`.

Confirmed directly (see `test_upload_dotdot_filename_lands_as_basename`'s
own module-level introspection, run once against a scratch FastAPI app
before writing this suite): `UploadFile.filename` is the raw, unsanitized
client-supplied string — Starlette/`python-multipart` do NOT strip `../`
components themselves. `os.path.basename` sanitization in `app/api/
files.py::upload_files` is therefore load-bearing, not defensive-only.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def files_settings(tmp_path: Path) -> Settings:
    return Settings(
        model_base_url="http://model-runner:8080/v1",
        model_name="test-model",
        exec_manager_url="http://code-exec-manager:8090",
        exec_default_timeout_s=1,
        workspace_root=str(tmp_path),
        postgres_password="test",
        _env_file=None,
    )


@pytest.fixture
async def files_client(files_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(files_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


async def test_list_empty_root(files_client: AsyncClient) -> None:
    response = await files_client.get("/api/files")

    assert response.status_code == 200
    assert response.json() == {"path": "", "entries": []}


async def test_list_nested(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.txt").write_text("hi")
    (tmp_path / "top.txt").write_text("top")

    root_response = await files_client.get("/api/files")
    assert root_response.status_code == 200
    root_names = {e["name"] for e in root_response.json()["entries"]}
    assert root_names == {"sub", "top.txt"}

    sub_response = await files_client.get("/api/files", params={"path": "sub"})
    assert sub_response.status_code == 200
    body = sub_response.json()
    assert body["path"] == "sub"
    assert body["entries"] == [
        {
            "name": "nested.txt",
            "path": "sub/nested.txt",
            "type": "file",
            "size": 2,
            "mtime": body["entries"][0]["mtime"],
            "mime": "text/plain",
        }
    ]


async def test_list_sort_order_dirs_first_case_insensitive(
    files_client: AsyncClient, tmp_path: Path
) -> None:
    (tmp_path / "Zdir").mkdir()
    (tmp_path / "adir").mkdir()
    (tmp_path / "Bfile.txt").write_text("b")
    (tmp_path / "afile.txt").write_text("a")

    response = await files_client.get("/api/files")

    assert response.status_code == 200
    names = [e["name"] for e in response.json()["entries"]]
    assert names == ["adir", "Zdir", "afile.txt", "Bfile.txt"]


async def test_list_missing_dir_is_404(files_client: AsyncClient) -> None:
    response = await files_client.get("/api/files", params={"path": "nope"})

    assert response.status_code == 404
    assert "detail" in response.json()


async def test_list_dir_entry_has_zero_size_even_if_nonempty(
    files_client: AsyncClient, tmp_path: Path
) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.txt").write_text("some content")

    response = await files_client.get("/api/files")

    entry = response.json()["entries"][0]
    assert entry["type"] == "dir"
    assert entry["size"] == 0


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


async def test_upload_single_file(files_client: AsyncClient, tmp_path: Path) -> None:
    response = await files_client.post(
        "/api/files/upload",
        data={"path": ""},
        files=[("file", ("a.txt", b"hello", "text/plain"))],
    )

    assert response.status_code == 201
    assert response.json() == {"uploaded": ["a.txt"]}
    assert (tmp_path / "a.txt").read_bytes() == b"hello"


async def test_upload_multiple_files(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()

    response = await files_client.post(
        "/api/files/upload",
        data={"path": "sub"},
        files=[
            ("file", ("a.txt", b"aaa", "text/plain")),
            ("file", ("b.txt", b"bbb", "text/plain")),
        ],
    )

    assert response.status_code == 201
    assert set(response.json()["uploaded"]) == {"sub/a.txt", "sub/b.txt"}
    assert (tmp_path / "sub" / "a.txt").read_bytes() == b"aaa"
    assert (tmp_path / "sub" / "b.txt").read_bytes() == b"bbb"


async def test_upload_overwrites_existing_file(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"old")

    response = await files_client.post(
        "/api/files/upload",
        data={"path": ""},
        files=[("file", ("a.txt", b"new-content", "text/plain"))],
    )

    assert response.status_code == 201
    assert (tmp_path / "a.txt").read_bytes() == b"new-content"


async def test_upload_to_missing_dir_is_404(files_client: AsyncClient) -> None:
    response = await files_client.post(
        "/api/files/upload",
        data={"path": "nope"},
        files=[("file", ("a.txt", b"hello", "text/plain"))],
    )

    assert response.status_code == 404


async def test_upload_dotdot_filename_lands_as_basename(
    files_client: AsyncClient, tmp_path: Path
) -> None:
    (tmp_path / "sub").mkdir()

    response = await files_client.post(
        "/api/files/upload",
        data={"path": "sub"},
        files=[("file", ("../../evil.txt", b"pwned", "text/plain"))],
    )

    assert response.status_code == 201
    assert response.json() == {"uploaded": ["sub/evil.txt"]}
    assert (tmp_path / "sub" / "evil.txt").read_bytes() == b"pwned"
    # And, crucially, nothing escaped to tmp_path's parent.
    assert not (tmp_path.parent / "evil.txt").exists()


async def test_upload_large_file_streams_without_full_buffering(
    files_client: AsyncClient, tmp_path: Path
) -> None:
    # 3 MiB (a few chunk boundaries at the 1 MiB chunk size) to exercise the
    # chunked read/write loop for real, not just a single-chunk file.
    payload = os.urandom(3 * 1024 * 1024 + 17)

    response = await files_client.post(
        "/api/files/upload",
        data={"path": ""},
        files=[("file", ("big.bin", payload, "application/octet-stream"))],
    )

    assert response.status_code == 201
    assert (tmp_path / "big.bin").read_bytes() == payload


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


async def test_download_file_ok(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"downloadable")

    response = await files_client.get("/api/files/download", params={"path": "a.txt"})

    assert response.status_code == 200
    assert response.content == b"downloadable"
    assert response.headers["content-disposition"] == 'attachment; filename="a.txt"'


async def test_download_dir_is_404(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()

    response = await files_client.get("/api/files/download", params={"path": "sub"})

    assert response.status_code == 404


async def test_download_missing_file_is_404(files_client: AsyncClient) -> None:
    response = await files_client.get("/api/files/download", params={"path": "nope.txt"})

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# mkdir
# ---------------------------------------------------------------------------


async def test_mkdir_nested_creates_parents(files_client: AsyncClient, tmp_path: Path) -> None:
    response = await files_client.post("/api/files/mkdir", json={"path": "a/b/c"})

    assert response.status_code == 201
    assert (tmp_path / "a" / "b" / "c").is_dir()


async def test_mkdir_existing_dir_is_idempotent(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()

    response = await files_client.post("/api/files/mkdir", json={"path": "a"})

    assert response.status_code == 201


async def test_mkdir_conflicts_with_existing_file(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "a").write_text("i am a file")

    response = await files_client.post("/api/files/mkdir", json={"path": "a"})

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# move
# ---------------------------------------------------------------------------


async def test_move_rename_semantics(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("content")

    response = await files_client.post(
        "/api/files/move", json={"src": "old.txt", "dst": "new.txt"}
    )

    assert response.status_code == 200
    assert not (tmp_path / "old.txt").exists()
    assert (tmp_path / "new.txt").read_text() == "content"


async def test_move_to_existing_dst_is_409(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "src.txt").write_text("a")
    (tmp_path / "dst.txt").write_text("b")

    response = await files_client.post(
        "/api/files/move", json={"src": "src.txt", "dst": "dst.txt"}
    )

    assert response.status_code == 409
    assert (tmp_path / "src.txt").exists()  # untouched


async def test_move_missing_src_is_404(files_client: AsyncClient) -> None:
    response = await files_client.post(
        "/api/files/move", json={"src": "nope.txt", "dst": "new.txt"}
    )

    assert response.status_code == 404


async def test_move_dst_parent_missing_is_400(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "src.txt").write_text("a")

    response = await files_client.post(
        "/api/files/move", json={"src": "src.txt", "dst": "nosuchdir/dst.txt"}
    )

    assert response.status_code == 400


async def test_move_dir_into_itself_is_400(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "a" / "sub").mkdir(parents=True)

    response = await files_client.post(
        "/api/files/move", json={"src": "a", "dst": "a/sub/moved"}
    )

    assert response.status_code == 400
    assert (tmp_path / "a").is_dir()  # untouched


# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------


async def test_copy_dir_recursive(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "src" / "nested" / "f.txt").write_text("deep")

    response = await files_client.post("/api/files/copy", json={"src": "src", "dst": "dst"})

    assert response.status_code == 200
    assert (tmp_path / "src" / "nested" / "f.txt").exists()  # src untouched
    assert (tmp_path / "dst" / "nested" / "f.txt").read_text() == "deep"


async def test_copy_file(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "src.txt").write_text("copy me")

    response = await files_client.post(
        "/api/files/copy", json={"src": "src.txt", "dst": "copy.txt"}
    )

    assert response.status_code == 200
    assert (tmp_path / "src.txt").read_text() == "copy me"
    assert (tmp_path / "copy.txt").read_text() == "copy me"


async def test_copy_to_existing_dst_is_409(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "src.txt").write_text("a")
    (tmp_path / "dst.txt").write_text("b")

    response = await files_client.post(
        "/api/files/copy", json={"src": "src.txt", "dst": "dst.txt"}
    )

    assert response.status_code == 409


async def test_copy_missing_src_is_404(files_client: AsyncClient) -> None:
    response = await files_client.post(
        "/api/files/copy", json={"src": "nope.txt", "dst": "new.txt"}
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_file(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("bye")

    response = await files_client.delete("/api/files", params={"path": "a.txt"})

    assert response.status_code == 204
    assert not (tmp_path / "a.txt").exists()


async def test_delete_dir_recursive(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "sub" / "nested").mkdir(parents=True)
    (tmp_path / "sub" / "nested" / "f.txt").write_text("x")

    response = await files_client.delete("/api/files", params={"path": "sub"})

    assert response.status_code == 204
    assert not (tmp_path / "sub").exists()


async def test_delete_root_is_400(files_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("still here")

    response = await files_client.delete("/api/files", params={"path": ""})

    assert response.status_code == 400
    assert (tmp_path / "keep.txt").exists()


async def test_delete_missing_path_is_404(files_client: AsyncClient) -> None:
    response = await files_client.delete("/api/files", params={"path": "nope.txt"})

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# traversal guard suite (Conventions & Contracts §8) — every path-taking
# endpoint, parametrized across every guard case.
# ---------------------------------------------------------------------------

GUARD_CASES = ["dotdot", "absolute", "nested_dotdot", "symlink"]


def _bad_path(case: str, tmp_path: Path) -> str:
    if case == "dotdot":
        return "../x"
    if case == "absolute":
        return "/etc/passwd"
    if case == "nested_dotdot":
        return "a/../../x"
    if case == "symlink":
        link = tmp_path / "escape_link"
        if not link.exists():
            link.symlink_to("/tmp")
        return "escape_link/x"
    raise ValueError(case)  # pragma: no cover - guarded by GUARD_CASES itself


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_list_guard(files_client: AsyncClient, tmp_path: Path, case: str) -> None:
    response = await files_client.get("/api/files", params={"path": _bad_path(case, tmp_path)})
    assert response.status_code == 400


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_upload_guard(files_client: AsyncClient, tmp_path: Path, case: str) -> None:
    response = await files_client.post(
        "/api/files/upload",
        data={"path": _bad_path(case, tmp_path)},
        files=[("file", ("a.txt", b"x", "text/plain"))],
    )
    assert response.status_code == 400


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_download_guard(files_client: AsyncClient, tmp_path: Path, case: str) -> None:
    response = await files_client.get(
        "/api/files/download", params={"path": _bad_path(case, tmp_path)}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_mkdir_guard(files_client: AsyncClient, tmp_path: Path, case: str) -> None:
    response = await files_client.post(
        "/api/files/mkdir", json={"path": _bad_path(case, tmp_path)}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_move_guard_src(files_client: AsyncClient, tmp_path: Path, case: str) -> None:
    response = await files_client.post(
        "/api/files/move", json={"src": _bad_path(case, tmp_path), "dst": "ok.txt"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_move_guard_dst(files_client: AsyncClient, tmp_path: Path, case: str) -> None:
    (tmp_path / "ok.txt").write_text("x")
    response = await files_client.post(
        "/api/files/move", json={"src": "ok.txt", "dst": _bad_path(case, tmp_path)}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_copy_guard_src(files_client: AsyncClient, tmp_path: Path, case: str) -> None:
    response = await files_client.post(
        "/api/files/copy", json={"src": _bad_path(case, tmp_path), "dst": "ok.txt"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_copy_guard_dst(files_client: AsyncClient, tmp_path: Path, case: str) -> None:
    (tmp_path / "ok.txt").write_text("x")
    response = await files_client.post(
        "/api/files/copy", json={"src": "ok.txt", "dst": _bad_path(case, tmp_path)}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_delete_guard(files_client: AsyncClient, tmp_path: Path, case: str) -> None:
    response = await files_client.delete(
        "/api/files", params={"path": _bad_path(case, tmp_path)}
    )
    assert response.status_code == 400
