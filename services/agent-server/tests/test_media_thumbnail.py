"""Unit tests for `GET /api/media/thumbnail` (`app/api/media.py`, issue #125).

`thumbnail_settings`/`thumbnail_client` mirror `test_media_stream.py`'s
`media_settings`/`media_client` fixtures exactly (same reasoning given
there: this route only ever touches `app.state.settings`, set
synchronously in `create_app()`, so no `lifespan`/`checkpointer_override`/
`thread_store_override` plumbing is needed here either).

Tests that generate a real video fixture and expect a real JPEG back are
guarded by `_requires_ffmpeg` — SKIPPED (not failed) when no `ffmpeg`
binary is on `PATH`, same skip-marker pattern as `test_thumbnails.py` (see
that module's docstring) and `test_checkpointer_pg.py`'s `TEST_PG_DSN`
convention. Tests that only need the traversal guard, the 404s, or the
415 (all of which return before ever invoking `ffmpeg`) are NOT
skip-guarded — they exercise real request/response code with no external
binary dependency.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import thumbnails as thumbnails_module
from app.core.config import Settings
from app.main import create_app

_HAS_FFMPEG = shutil.which("ffmpeg") is not None
_requires_ffmpeg = pytest.mark.skipif(
    not _HAS_FFMPEG, reason="ffmpeg not on PATH - no real binary to test against"
)

_JPEG_MAGIC = b"\xff\xd8"


@pytest.fixture
def thumbnail_settings(tmp_path: Path) -> Settings:
    return Settings(
        model_base_url="http://model-runner:8080/v1",
        model_name="test-model",
        exec_manager_url="http://code-exec-manager:8090",
        exec_default_timeout_s=1,
        files_root=str(tmp_path),
        postgres_password="test",
        _env_file=None,
    )


@pytest.fixture
async def thumbnail_client(thumbnail_settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(thumbnail_settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Same rationale as `test_thumbnails.py`'s identically-named fixture:
    a fresh, real (created) per-test cache directory instead of the real
    `/tmp/media-thumbnails`."""
    cache_dir = tmp_path / "thumb-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(thumbnails_module, "thumbnail_cache_dir", lambda: cache_dir)
    return cache_dir


def _make_test_video(dest: Path, *, duration_s: float = 2.0) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration_s}:size=64x64:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


@_requires_ffmpeg
async def test_thumbnail_for_valid_video_returns_jpeg(
    thumbnail_client: AsyncClient, tmp_path: Path
) -> None:
    _make_test_video(tmp_path / "clip.mp4")

    response = await thumbnail_client.get("/api/media/thumbnail", params={"path": "clip.mp4"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == _JPEG_MAGIC
    assert int(response.headers["content-length"]) == len(response.content)


@_requires_ffmpeg
async def test_thumbnail_cache_control_header_present(
    thumbnail_client: AsyncClient, tmp_path: Path
) -> None:
    _make_test_video(tmp_path / "clip.mp4")

    response = await thumbnail_client.get("/api/media/thumbnail", params={"path": "clip.mp4"})

    assert response.headers["cache-control"] == "public, max-age=86400"


@_requires_ffmpeg
@pytest.mark.parametrize("extension", ["mp4", "mov", "m4v", "webm", "mkv"])
async def test_thumbnail_recognizes_every_supported_extension(
    thumbnail_client: AsyncClient, tmp_path: Path, extension: str
) -> None:
    name = f"clip.{extension}"
    _make_test_video(tmp_path / name)

    response = await thumbnail_client.get("/api/media/thumbnail", params={"path": name})

    assert response.status_code == 200
    assert response.content[:2] == _JPEG_MAGIC


# ---------------------------------------------------------------------------
# caching — second request must not regenerate
# ---------------------------------------------------------------------------


@_requires_ffmpeg
async def test_thumbnail_second_request_is_cache_hit_not_regenerated(
    thumbnail_client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_test_video(tmp_path / "clip.mp4")

    call_count = 0
    real_generate = thumbnails_module.generate_thumbnail

    def _counting_generate(src: Path, dst: Path) -> None:
        nonlocal call_count
        call_count += 1
        real_generate(src, dst)

    monkeypatch.setattr(thumbnails_module, "generate_thumbnail", _counting_generate)

    first = await thumbnail_client.get("/api/media/thumbnail", params={"path": "clip.mp4"})
    second = await thumbnail_client.get("/api/media/thumbnail", params={"path": "clip.mp4"})

    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert call_count == 1


# ---------------------------------------------------------------------------
# 404s
# ---------------------------------------------------------------------------


async def test_thumbnail_missing_file_is_404(thumbnail_client: AsyncClient) -> None:
    response = await thumbnail_client.get("/api/media/thumbnail", params={"path": "nope.mp4"})

    assert response.status_code == 404


async def test_thumbnail_directory_path_is_404(
    thumbnail_client: AsyncClient, tmp_path: Path
) -> None:
    (tmp_path / "adir").mkdir()

    response = await thumbnail_client.get("/api/media/thumbnail", params={"path": "adir"})

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 415 — recognized file, but not a video extension
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["photo.png", "song.mp3", "doc.pdf", "noextension"])
async def test_thumbnail_non_video_extension_is_415(
    thumbnail_client: AsyncClient, tmp_path: Path, name: str
) -> None:
    (tmp_path / name).write_bytes(b"not a video")

    response = await thumbnail_client.get("/api/media/thumbnail", params={"path": name})

    assert response.status_code == 415


# ---------------------------------------------------------------------------
# 500 — recognized video extension, but ffmpeg can't actually decode it
# ---------------------------------------------------------------------------


@_requires_ffmpeg
async def test_thumbnail_unreadable_video_is_500(
    thumbnail_client: AsyncClient, tmp_path: Path
) -> None:
    (tmp_path / "bad.mp4").write_bytes(b"not actually a video, just text bytes here")

    response = await thumbnail_client.get("/api/media/thumbnail", params={"path": "bad.mp4"})

    assert response.status_code == 500


# ---------------------------------------------------------------------------
# traversal guard suite (Conventions & Contracts §8) — same parametrized
# cases as `test_media_stream.py`'s `GUARD_CASES`/`_bad_path`, since this
# exercises the exact same shared `resolve_files_path` function.
# ---------------------------------------------------------------------------

GUARD_CASES = ["dotdot", "absolute", "nested_dotdot", "symlink"]


def _bad_path(case: str, tmp_path: Path) -> str:
    if case == "dotdot":
        return "../x.mp4"
    if case == "absolute":
        return "/etc/passwd"
    if case == "nested_dotdot":
        return "a/../../x.mp4"
    if case == "symlink":
        link = tmp_path / "escape_link"
        if not link.exists():
            link.symlink_to("/tmp")
        return "escape_link/x.mp4"
    raise ValueError(case)  # pragma: no cover - guarded by GUARD_CASES itself


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_thumbnail_guard(
    thumbnail_client: AsyncClient, tmp_path: Path, case: str
) -> None:
    response = await thumbnail_client.get(
        "/api/media/thumbnail", params={"path": _bad_path(case, tmp_path)}
    )
    assert response.status_code == 400
