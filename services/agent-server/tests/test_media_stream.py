"""Unit tests for `/api/media/stream` (`app/api/media.py`, M5-01).

`media_client`/`media_settings` mirror `test_files_rest.py`'s `files_client`/
`files_settings` fixtures exactly (same reasoning: these routes only ever
touch `app.state.settings`, set synchronously in `create_app()` well before
`lifespan` runs, so there's no need for `app.router.lifespan_context(app)`,
`checkpointer_override`, or `thread_store_override` here either) — a fresh
`tmp_path`-backed `workspace_root` per test, function-scoped.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app

_FILE_SIZE = 10 * 1024  # 10 KiB, per the ticket's spec.


@pytest.fixture
def media_settings(tmp_path: Path) -> Settings:
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
async def media_client(media_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(media_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def source_bytes(tmp_path: Path) -> bytes:
    data = os.urandom(_FILE_SIZE)
    (tmp_path / "media.bin").write_bytes(data)
    return data


# ---------------------------------------------------------------------------
# full (no Range) response
# ---------------------------------------------------------------------------


async def test_full_response_no_range(media_client: AsyncClient, source_bytes: bytes) -> None:
    response = await media_client.get("/api/media/stream", params={"path": "media.bin"})

    assert response.status_code == 200
    assert response.content == source_bytes
    assert response.headers["content-length"] == str(_FILE_SIZE)
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "no-store"


# ---------------------------------------------------------------------------
# valid single-range forms
# ---------------------------------------------------------------------------


async def test_range_explicit_start_end(media_client: AsyncClient, source_bytes: bytes) -> None:
    response = await media_client.get(
        "/api/media/stream",
        params={"path": "media.bin"},
        headers={"Range": "bytes=0-999"},
    )

    assert response.status_code == 206
    assert response.content == source_bytes[0:1000]
    assert response.headers["content-range"] == f"bytes 0-999/{_FILE_SIZE}"
    assert response.headers["content-length"] == "1000"


async def test_range_start_to_eof(media_client: AsyncClient, source_bytes: bytes) -> None:
    response = await media_client.get(
        "/api/media/stream",
        params={"path": "media.bin"},
        headers={"Range": "bytes=9000-"},
    )

    expected = source_bytes[9000:]
    expected_len = _FILE_SIZE - 9000

    assert response.status_code == 206
    assert response.content == expected
    assert len(response.content) == expected_len
    assert response.headers["content-range"] == f"bytes 9000-{_FILE_SIZE - 1}/{_FILE_SIZE}"
    assert response.headers["content-length"] == str(expected_len)


async def test_range_suffix_last_n_bytes(media_client: AsyncClient, source_bytes: bytes) -> None:
    response = await media_client.get(
        "/api/media/stream",
        params={"path": "media.bin"},
        headers={"Range": "bytes=-500"},
    )

    assert response.status_code == 206
    assert response.content == source_bytes[-500:]
    assert (
        response.headers["content-range"]
        == f"bytes {_FILE_SIZE - 500}-{_FILE_SIZE - 1}/{_FILE_SIZE}"
    )
    assert response.headers["content-length"] == "500"


# ---------------------------------------------------------------------------
# unsatisfiable / degraded-to-full cases
# ---------------------------------------------------------------------------


async def test_range_past_eof_is_416(media_client: AsyncClient, source_bytes: bytes) -> None:
    response = await media_client.get(
        "/api/media/stream",
        params={"path": "media.bin"},
        headers={"Range": "bytes=99999-"},
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{_FILE_SIZE}"


async def test_non_bytes_unit_ignored_full_response(
    media_client: AsyncClient, source_bytes: bytes
) -> None:
    response = await media_client.get(
        "/api/media/stream",
        params={"path": "media.bin"},
        headers={"Range": "seconds=1"},
    )

    assert response.status_code == 200
    assert response.content == source_bytes


async def test_multi_range_ignored_full_response(
    media_client: AsyncClient, source_bytes: bytes
) -> None:
    response = await media_client.get(
        "/api/media/stream",
        params={"path": "media.bin"},
        headers={"Range": "bytes=0-99,200-299"},
    )

    assert response.status_code == 200
    assert response.content == source_bytes


async def test_range_end_overrun_is_clamped(media_client: AsyncClient, source_bytes: bytes) -> None:
    response = await media_client.get(
        "/api/media/stream",
        params={"path": "media.bin"},
        headers={"Range": "bytes=9000-99999"},
    )

    expected = source_bytes[9000:]
    expected_len = _FILE_SIZE - 9000

    assert response.status_code == 206
    assert response.content == expected
    assert response.headers["content-range"] == f"bytes 9000-{_FILE_SIZE - 1}/{_FILE_SIZE}"
    assert response.headers["content-length"] == str(expected_len)


# ---------------------------------------------------------------------------
# HEAD
# ---------------------------------------------------------------------------


async def test_head_no_range_matches_get_headers_empty_body(
    media_client: AsyncClient, source_bytes: bytes
) -> None:
    get_response = await media_client.get("/api/media/stream", params={"path": "media.bin"})
    head_response = await media_client.head("/api/media/stream", params={"path": "media.bin"})

    assert head_response.status_code == get_response.status_code == 200
    assert head_response.content == b""
    assert head_response.headers["content-length"] == get_response.headers["content-length"]
    assert head_response.headers["accept-ranges"] == get_response.headers["accept-ranges"]
    assert head_response.headers["cache-control"] == get_response.headers["cache-control"]
    assert head_response.headers["content-type"] == get_response.headers["content-type"]


async def test_head_with_range_matches_get_headers_empty_body(
    media_client: AsyncClient, source_bytes: bytes
) -> None:
    headers = {"Range": "bytes=0-999"}
    get_response = await media_client.get(
        "/api/media/stream", params={"path": "media.bin"}, headers=headers
    )
    head_response = await media_client.head(
        "/api/media/stream", params={"path": "media.bin"}, headers=headers
    )

    assert head_response.status_code == get_response.status_code == 206
    assert head_response.content == b""
    assert head_response.headers["content-range"] == get_response.headers["content-range"]
    assert head_response.headers["content-length"] == get_response.headers["content-length"]


# ---------------------------------------------------------------------------
# 404s
# ---------------------------------------------------------------------------


async def test_missing_file_is_404(media_client: AsyncClient) -> None:
    response = await media_client.get("/api/media/stream", params={"path": "nope.bin"})

    assert response.status_code == 404


async def test_directory_path_is_404(media_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "adir").mkdir()

    response = await media_client.get("/api/media/stream", params={"path": "adir"})

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Content-Type
# ---------------------------------------------------------------------------


async def test_content_type_known_extension(media_client: AsyncClient, tmp_path: Path) -> None:
    (tmp_path / "video.mp4").write_bytes(b"fake-mp4-bytes")

    response = await media_client.get("/api/media/stream", params={"path": "video.mp4"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"


async def test_content_type_unknown_extension_falls_back(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    (tmp_path / "mystery.xyz123").write_bytes(b"who-knows")

    response = await media_client.get("/api/media/stream", params={"path": "mystery.xyz123"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"


# ---------------------------------------------------------------------------
# byte-identity property test across many random ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (0, 0),
        (1, 100),
        (500, 4999),
        (4096, 4096 + 4095),
        (7000, 7999),
        (8000, _FILE_SIZE - 1),
        (123, 4567),
        (0, _FILE_SIZE - 1),
        (9999, 9999),
        (10, 10009),
    ],
)
async def test_byte_identity_for_random_ranges(
    media_client: AsyncClient, source_bytes: bytes, start: int, end: int
) -> None:
    response = await media_client.get(
        "/api/media/stream",
        params={"path": "media.bin"},
        headers={"Range": f"bytes={start}-{end}"},
    )

    expected = source_bytes[start : end + 1]
    assert response.status_code == 206
    assert response.content == expected


# ---------------------------------------------------------------------------
# streaming in multiple 1 MiB chunks — evidence, not just a comment
# ---------------------------------------------------------------------------


async def test_large_file_streams_across_chunk_boundary(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    # >1 MiB so the 1 MiB chunk loop must iterate more than once.
    big = os.urandom(3 * 1024 * 1024 + 12345)
    (tmp_path / "big.bin").write_bytes(big)

    response = await media_client.get("/api/media/stream", params={"path": "big.bin"})

    assert response.status_code == 200
    assert response.content == big
    assert response.headers["content-length"] == str(len(big))


# ---------------------------------------------------------------------------
# traversal guard suite (Conventions & Contracts §8) — same parametrized
# cases as `test_files_rest.py`'s `GUARD_CASES`/`_bad_path`, since this
# exercises the exact same shared `resolve_workspace_path` function.
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
async def test_stream_guard(media_client: AsyncClient, tmp_path: Path, case: str) -> None:
    response = await media_client.get(
        "/api/media/stream", params={"path": _bad_path(case, tmp_path)}
    )
    assert response.status_code == 400
