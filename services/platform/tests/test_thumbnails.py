"""`app/core/thumbnails.py`: ported from agent-server's `tests/test_thumbnails.py`.

Tests that shell out to a real `ffmpeg` are skipped when it isn't on `PATH`
(it is in the platform image); the rest exercise pure-Python logic.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.core import thumbnails as thumbnails_module
from app.core.thumbnails import (
    ThumbnailGenerationError,
    cache_key_for,
    generate_thumbnail,
    get_cached_thumbnail,
    is_video_file,
)

_HAS_FFMPEG = shutil.which("ffmpeg") is not None
_requires_ffmpeg = pytest.mark.skipif(
    not _HAS_FFMPEG, reason="ffmpeg not on PATH - no real binary to test against"
)

_JPEG_MAGIC = b"\xff\xd8"


def _make_test_video(dest: Path, *, duration_s: float = 2.0) -> None:
    """Generates a real, tiny (`testsrc` pattern) video file at `dest` via
    a direct `ffmpeg` CLI call — same tool under test, but this is fixture
    setup (mirrors `test_media_stream.py`'s own `source_bytes` fixture
    writing real bytes to disk), not itself something being tested."""
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


@pytest.fixture(autouse=True)
def _isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirects `thumbnail_cache_dir()` to a fresh per-test directory
    instead of the real `/tmp/media-thumbnails` — avoids cross-test/
    cross-run pollution and guarantees a clean cache-miss at the start of
    every test. Creates the directory itself (the real function's
    `mkdir(parents=True, exist_ok=True)` call is part of what's being
    replaced here, so this stand-in must do the same or `ffmpeg` fails
    with a confusing muxer I/O error writing into a nonexistent dir)."""
    cache_dir = tmp_path / "thumb-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(thumbnails_module, "thumbnail_cache_dir", lambda: cache_dir)
    return cache_dir


@contextmanager
def _opened(path: Path) -> Iterator[int]:
    fd = os.open(path, os.O_RDONLY)
    try:
        yield fd
    finally:
        os.close(fd)


def _cache_key(path: Path) -> str:
    with _opened(path) as fd:
        return cache_key_for(fd)


def _generate(source: Path, dest: Path) -> None:
    with _opened(source) as fd:
        generate_thumbnail(fd, dest)


def _thumbnail(source: Path) -> Path:
    with _opened(source) as fd:
        return get_cached_thumbnail(fd)


def _placeholder(tmp_path: Path) -> Path:
    source = tmp_path / "src.mp4"
    source.write_bytes(b"x")
    return source


# ---------------------------------------------------------------------------
# is_video_file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["clip.mp4", "clip.MOV", "clip.m4v", "clip.webm", "clip.mkv", "a.b.c.mp4"],
)
def test_is_video_file_recognized_extensions(name: str) -> None:
    assert is_video_file(name) is True


@pytest.mark.parametrize(
    "name",
    ["photo.png", "song.mp3", "doc.pdf", "noextension", "trailing.", ""],
)
def test_is_video_file_rejects_non_video(name: str) -> None:
    assert is_video_file(name) is False


# ---------------------------------------------------------------------------
# cache_key_for
# ---------------------------------------------------------------------------


def test_cache_key_stable_for_unchanged_file(tmp_path: Path) -> None:
    f = tmp_path / "a.mp4"
    f.write_bytes(b"hello")

    assert _cache_key(f) == _cache_key(f)


def test_cache_key_changes_with_size(tmp_path: Path) -> None:
    f = tmp_path / "a.mp4"
    f.write_bytes(b"hello")
    key1 = _cache_key(f)

    f.write_bytes(b"hello world, this is longer now")
    key2 = _cache_key(f)

    assert key1 != key2


def test_cache_key_changes_with_mtime(tmp_path: Path) -> None:
    f = tmp_path / "a.mp4"
    f.write_bytes(b"hello")
    key1 = _cache_key(f)

    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    key2 = _cache_key(f)

    assert key1 != key2


def test_cache_key_differs_by_path(tmp_path: Path) -> None:
    f1 = tmp_path / "a.mp4"
    f2 = tmp_path / "b.mp4"
    f1.write_bytes(b"hello")
    f2.write_bytes(b"hello")

    assert _cache_key(f1) != _cache_key(f2)


# ---------------------------------------------------------------------------
# generate_thumbnail — real ffmpeg
# ---------------------------------------------------------------------------


@_requires_ffmpeg
def test_generate_thumbnail_produces_valid_jpeg(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    _make_test_video(source, duration_s=2.0)
    dest = tmp_path / "out.jpg"

    _generate(source, dest)

    assert dest.exists()
    assert dest.stat().st_size > 0
    assert dest.read_bytes()[:2] == _JPEG_MAGIC


@_requires_ffmpeg
def test_generate_thumbnail_falls_back_for_sub_second_clip(tmp_path: Path) -> None:
    # Shorter than the primary 1s seek target - the -ss 1 attempt should
    # fail/produce nothing, exercising the -ss 0 fallback path for real.
    source = tmp_path / "short.mp4"
    _make_test_video(source, duration_s=0.5)
    dest = tmp_path / "out.jpg"

    _generate(source, dest)

    assert dest.exists()
    assert dest.read_bytes()[:2] == _JPEG_MAGIC


@_requires_ffmpeg
def test_generate_thumbnail_raises_for_non_video_source(tmp_path: Path) -> None:
    source = tmp_path / "not-a-video.mp4"
    source.write_bytes(b"this is definitely not a video file, just text bytes")
    dest = tmp_path / "out.jpg"

    with pytest.raises(ThumbnailGenerationError):
        _generate(source, dest)


def test_run_ffmpeg_missing_binary_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_not_found(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(thumbnails_module.subprocess, "run", _raise_not_found)

    with pytest.raises(ThumbnailGenerationError, match="not installed"):
        _generate(_placeholder(tmp_path), tmp_path / "out.jpg")


def test_run_ffmpeg_timeout_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=15.0)

    monkeypatch.setattr(thumbnails_module.subprocess, "run", _raise_timeout)

    with pytest.raises(ThumbnailGenerationError, match="timed out"):
        _generate(_placeholder(tmp_path), tmp_path / "out.jpg")


# ---------------------------------------------------------------------------
# get_cached_thumbnail — get-or-create caching behavior
# ---------------------------------------------------------------------------


@_requires_ffmpeg
def test_get_cached_thumbnail_creates_and_returns_path(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    _make_test_video(source)

    result = _thumbnail(source)

    assert result.exists()
    assert result.read_bytes()[:2] == _JPEG_MAGIC


@_requires_ffmpeg
def test_get_cached_thumbnail_reuses_cache_without_regenerating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "clip.mp4"
    _make_test_video(source)

    call_count = 0
    real_generate = thumbnails_module.generate_thumbnail

    def _counting_generate(src: Path, dst: Path) -> None:
        nonlocal call_count
        call_count += 1
        real_generate(src, dst)

    monkeypatch.setattr(thumbnails_module, "generate_thumbnail", _counting_generate)

    first = _thumbnail(source)
    second = _thumbnail(source)

    assert first == second
    assert call_count == 1  # second call was a pure cache hit, no regeneration


@_requires_ffmpeg
def test_get_cached_thumbnail_regenerates_after_source_overwritten(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clip.mp4"
    _make_test_video(source, duration_s=2.0)
    first = _thumbnail(source)

    # Overwrite with a different duration -> different size -> different
    # `cache_key_for` hash, regardless of how close the two mtimes land.
    _make_test_video(source, duration_s=3.0)
    second = _thumbnail(source)

    assert second != first
    assert second.exists()
