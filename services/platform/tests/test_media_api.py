"""`/api/platform/files/stream` (Range) and `/thumbnail`: ported from agent-server's
`tests/test_media_stream.py` and `tests/test_media_thumbnail.py`."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from app.api.external import media
from app.core import thumbnails
from tests.files_world import FILES, World

_FILE_SIZE = 10_000
STREAM = f"{FILES}/stream"


@pytest.fixture
def source_bytes(world: World) -> bytes:
    data = os.urandom(_FILE_SIZE)
    (world.family_root / "media.bin").write_bytes(data)
    return data


async def _stream(world: World, path="/spaces/family/media.bin", user="carol", method="GET", **h):
    return await world.client.request(
        method, STREAM, params={"path": path}, headers={**world.headers[user], **h}
    )


async def test_full_response_no_range(world: World, source_bytes: bytes) -> None:
    response = await _stream(world)
    assert response.status_code == 200
    assert response.content == source_bytes
    assert response.headers["content-length"] == str(_FILE_SIZE)
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("header", "start", "end"),
    [
        ("bytes=0-999", 0, 999),
        ("bytes=9000-", 9000, _FILE_SIZE - 1),
        ("bytes=-500", _FILE_SIZE - 500, _FILE_SIZE - 1),
        ("bytes=9000-99999", 9000, _FILE_SIZE - 1),
        ("bytes=0-0", 0, 0),
        ("bytes=123-4567", 123, 4567),
        ("bytes=9999-9999", 9999, 9999),
        ("bytes=-99999", 0, _FILE_SIZE - 1),
    ],
)
async def test_single_ranges(
    world: World, source_bytes: bytes, header: str, start: int, end: int
) -> None:
    response = await _stream(world, Range=header)
    assert response.status_code == 206
    assert response.content == source_bytes[start : end + 1]
    assert response.headers["content-range"] == f"bytes {start}-{end}/{_FILE_SIZE}"
    assert response.headers["content-length"] == str(end - start + 1)


@pytest.mark.parametrize("header", ["bytes=99999-", "bytes=10000-", "bytes=5-2", "bytes=-0"])
async def test_unsatisfiable_is_416(world: World, source_bytes: bytes, header: str) -> None:
    response = await _stream(world, Range=header)
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{_FILE_SIZE}"


@pytest.mark.parametrize("header", ["seconds=1", "bytes=0-99,200-299"])
async def test_ignored_ranges_give_full_response(
    world: World, source_bytes: bytes, header: str
) -> None:
    response = await _stream(world, Range=header)
    assert response.status_code == 200
    assert response.content == source_bytes


@pytest.mark.parametrize("headers", [{}, {"Range": "bytes=0-999"}])
async def test_head_matches_get_headers_with_empty_body(
    world: World, source_bytes: bytes, headers: dict
) -> None:
    get = await _stream(world, **headers)
    head = await _stream(world, method="HEAD", **headers)
    assert head.status_code == get.status_code
    assert head.content == b""
    for name in ("content-length", "content-range", "accept-ranges", "content-type"):
        assert head.headers.get(name) == get.headers.get(name)


async def test_missing_dir_and_non_member_are_404(world: World, source_bytes: bytes) -> None:
    (world.family_root / "adir").mkdir()
    for path in ("/spaces/family/nope.bin", "/spaces/family/adir", "/spaces/family"):
        assert (await _stream(world, path)).status_code == 404
    assert (await _stream(world, user="dave")).status_code == 404


async def test_fifo_is_404_not_a_hang(world: World) -> None:
    os.mkfifo(world.family_root / "pipe")
    assert (await _stream(world, "/spaces/family/pipe")).status_code == 404


async def test_content_types(world: World) -> None:
    (world.family_root / "video.mp4").write_bytes(b"fake-mp4-bytes")
    (world.family_root / "mystery.xyz123").write_bytes(b"who-knows")
    mp4 = await _stream(world, "/spaces/family/video.mp4")
    assert mp4.headers["content-type"] == "video/mp4"
    other = await _stream(world, "/spaces/family/mystery.xyz123")
    assert other.headers["content-type"] == "application/octet-stream"


async def test_large_file_streams_across_chunk_boundary(world: World) -> None:
    big = os.urandom(3 * 1024 * 1024 + 12345)
    (world.home("alice") / "big.bin").write_bytes(big)
    response = await _stream(world, "/personal/big.bin", user="alice")
    assert response.status_code == 200
    assert response.content == big


def test_parse_range_unit() -> None:
    assert media._parse_range(None, 10) is None
    assert media._parse_range("bytes=2-", 0) == "unsatisfiable"
    assert media._parse_range("bytes=abc", 10) == "unsatisfiable"
    assert media._parse_range("bytes=-", 10) == "unsatisfiable"
    assert media._parse_range(" Bytes=1-2 ", 10) == (1, 2)


# --- thumbnails ---------------------------------------------------------------------


async def _thumb(world: World, path: str, user: str = "carol"):
    return await world.client.get(
        f"{FILES}/thumbnail", params={"path": path}, headers=world.headers[user]
    )


async def test_thumbnail_non_video_is_415(world: World) -> None:
    (world.family_root / "a.txt").write_text("x")
    response = await _thumb(world, "/spaces/family/a.txt")
    assert (response.status_code, response.json()) == (415, {"detail": "unsupported_media"})


async def test_thumbnail_missing_is_404(world: World) -> None:
    assert (await _thumb(world, "/spaces/family/nope.mp4")).status_code == 404


async def test_thumbnail_failure_is_500(world: World, monkeypatch, tmp_path) -> None:
    (world.family_root / "broken.mp4").write_bytes(b"not a video")

    def fail(_path):
        raise thumbnails.ThumbnailGenerationError("ffmpeg failed")

    monkeypatch.setattr(media, "get_cached_thumbnail", fail)
    response = await _thumb(world, "/spaces/family/broken.mp4")
    assert (response.status_code, response.json()) == (500, {"detail": "thumbnail_failed"})


async def test_thumbnail_served_from_cache(world: World, monkeypatch, tmp_path) -> None:
    (world.family_root / "clip.mp4").write_bytes(b"fake")
    jpeg = tmp_path / "t.jpg"
    jpeg.write_bytes(b"\xff\xd8\xff fake jpeg")
    monkeypatch.setattr(media, "get_cached_thumbnail", lambda _path: jpeg)
    response = await _thumb(world, "/spaces/family/clip.mp4")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert response.content == jpeg.read_bytes()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg (in the platform image)")
async def test_thumbnail_real_ffmpeg(world: World, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(thumbnails, "thumbnail_cache_dir", lambda: tmp_path / "cache")
    clip = world.family_root / "clip.mp4"
    subprocess.run(  # noqa: ASYNC221 - fixture setup
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=64x48:rate=5",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )  # fmt: skip
    response = await _thumb(world, "/spaces/family/clip.mp4")
    assert response.status_code == 200
    assert response.content[:2] == b"\xff\xd8"
