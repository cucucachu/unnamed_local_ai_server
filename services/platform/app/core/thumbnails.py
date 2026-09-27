"""Video poster-frame thumbnails via `ffmpeg`, cached on disk (ported from agent-server, #125).

HTTP-agnostic: `app/api/external/media.py` resolves the path and turns
`ThumbnailGenerationError` into a `500`. `is_video_file`'s extension set is
the frontend's `VIDEO_EXTENSIONS` (`services/frontend/lib/media.ts`); keep
the two in sync by hand.

The cache lives on the container's `/tmp` tmpfs: it's regenerable, so
losing it on a restart only costs a re-decode. Keys include the source's
mtime and size, so an overwritten video gets a fresh thumbnail.
"""

from __future__ import annotations

import hashlib
import subprocess
import uuid
from pathlib import Path

_VIDEO_EXTENSIONS = frozenset({"mp4", "mov", "m4v", "webm", "mkv"})

_THUMBNAIL_WIDTH = 320
_FFMPEG_TIMEOUT_S = 15.0


class ThumbnailGenerationError(Exception):
    """`ffmpeg` couldn't produce a frame: missing binary, bad video, or a timeout."""


def is_video_file(name: str) -> bool:
    if "." not in name:
        return False
    return name.rsplit(".", 1)[1].lower() in _VIDEO_EXTENSIONS


def thumbnail_cache_dir() -> Path:
    cache_dir = Path("/tmp/media-thumbnails")
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def cache_key_for(source: Path) -> str:
    st = source.stat()
    digest_input = f"{source}:{st.st_mtime_ns}:{st.st_size}".encode()
    return hashlib.sha256(digest_input).hexdigest()


def _run_ffmpeg(source: Path, dest: Path, *, seek_s: float) -> None:
    """One attempt at a single scaled JPEG frame; exit 0 with an empty file also counts as failure."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(seek_s),
        "-i", str(source),
        "-frames:v", "1",
        "-vf", f"scale={_THUMBNAIL_WIDTH}:-1",
        "-f", "image2",
        str(dest),
    ]  # fmt: skip
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT_S, check=False)
    except FileNotFoundError as exc:
        raise ThumbnailGenerationError("ffmpeg is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ThumbnailGenerationError(f"ffmpeg timed out after {_FFMPEG_TIMEOUT_S}s") from exc

    if result.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        stderr_tail = result.stderr.decode(errors="replace")[-500:]
        raise ThumbnailGenerationError(
            f"ffmpeg exited {result.returncode} for seek={seek_s}s: {stderr_tail}"
        )


def generate_thumbnail(source: Path, dest: Path) -> None:
    """A frame 1 s in (skips black openings), else frame 0 (clips shorter than 1 s)."""
    try:
        _run_ffmpeg(source, dest, seek_s=1.0)
        return
    except ThumbnailGenerationError:
        pass
    _run_ffmpeg(source, dest, seek_s=0.0)


def get_cached_thumbnail(source: Path) -> Path:
    """Get-or-create; concurrent misses each write a temp file and atomically rename it into place."""
    cache_dir = thumbnail_cache_dir()
    dest = cache_dir / f"{cache_key_for(source)}.jpg"
    if not dest.exists():
        tmp_dest = cache_dir / f".tmp-{uuid.uuid4().hex}.jpg"
        try:
            generate_thumbnail(source, tmp_dest)
            tmp_dest.replace(dest)
        finally:
            tmp_dest.unlink(missing_ok=True)
    return dest
