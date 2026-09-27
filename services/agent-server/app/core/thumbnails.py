"""Video poster-frame thumbnail generation + on-disk caching — issue #125.

Extracts one early frame from a video file via an `ffmpeg` subprocess and
caches the resulting JPEG on disk, so `GET /api/media/thumbnail` (in
`app/api/media.py`) never re-decodes the same video twice. This module is
deliberately I/O/subprocess-only and HTTP-agnostic (no `HTTPException`
here, no `Request`) — `app/api/media.py` owns path resolution
(`resolve_files_path`) and turns `ThumbnailGenerationError` into a `500`;
that split mirrors `app/core/paths.py`'s own separation from its callers.

`is_video_file`'s extension set is copied verbatim from the frontend's own
`VIDEO_EXTENSIONS` (`services/frontend/lib/media.ts`) — same rationale as
that module's docstring: an extension-based check is a more reliable
"is this actually a video" signal than trusting `mimetypes.guess_type`
(which has no registered default MIME type for `.mkv` on many systems).
Keeping both lists in sync is a deliberate manual step (no shared
generated source between a TS and a Python package in this repo); if
`lib/media.ts`'s `VIDEO_EXTENSIONS` ever changes, this set should change
with it.
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
    """Raised when `ffmpeg` can't produce a poster frame for a source file
    — missing `ffmpeg` binary, an unreadable/corrupt video, or a
    timed-out/killed process. Callers (`app/api/media.py`) turn this into
    an HTTP `500`."""


def is_video_file(name: str) -> bool:
    """Extension-based check — see this module's docstring for why this
    mirrors the frontend's `mediaKind`'s video set rather than using
    `mimetypes.guess_type`."""
    if "." not in name:
        return False
    return name.rsplit(".", 1)[1].lower() in _VIDEO_EXTENSIONS


def thumbnail_cache_dir() -> Path:
    """Deliberately `/tmp`-backed (ephemeral — lives only for the
    container's lifetime), not a new bind-mounted volume: this is a pure
    performance cache regenerable from the source video at any time (the
    issue's own "must be cached... avoid re-decoding" acceptance
    criterion is about repeat-listing speed, not persistence), so losing
    it on a container restart is harmless — the next request for that
    thumbnail just regenerates it. That avoids a `docker-compose.yml`
    volume change and any bind-mount ownership/permission questions on
    `FILES_DIR` for something that doesn't need to survive a restart.
    `/tmp` is already the established writable location for the non-root
    runtime UID in this image (see the Dockerfile's `UV_CACHE_DIR`
    comment) — reusing it here needs no new permission setup.
    """
    cache_dir = Path("/tmp/media-thumbnails")
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def cache_key_for(source: Path) -> str:
    """Cache key derived from the source file's identity (resolved path +
    mtime + size), so replacing the file at the same path (re-upload,
    overwrite) naturally invalidates any previously-cached thumbnail —
    the new file's key simply differs, and the stale cache entry is never
    looked up again (left to rot in `/tmp`; see `thumbnail_cache_dir`'s
    docstring for why that's an acceptable trade here). No explicit
    invalidation/eviction pass is needed for that reason.
    """
    st = source.stat()
    digest_input = f"{source}:{st.st_mtime_ns}:{st.st_size}".encode()
    return hashlib.sha256(digest_input).hexdigest()


def _run_ffmpeg(source: Path, dest: Path, *, seek_s: float) -> None:
    """Run one `ffmpeg` attempt, writing a single scaled JPEG frame to
    `dest`. Raises `ThumbnailGenerationError` on any failure — missing
    binary, non-zero exit, timeout, or an exit-0-but-empty-output edge
    case (observed possible for a 0-byte/all-black-frame source in ad hoc
    testing, so checked explicitly rather than trusting the exit code
    alone)."""
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(seek_s),
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale={_THUMBNAIL_WIDTH}:-1",
        "-f",
        "image2",
        str(dest),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=_FFMPEG_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ThumbnailGenerationError("ffmpeg is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise ThumbnailGenerationError(
            f"ffmpeg timed out after {_FFMPEG_TIMEOUT_S}s"
        ) from exc

    if result.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        stderr_tail = result.stderr.decode(errors="replace")[-500:]
        raise ThumbnailGenerationError(
            f"ffmpeg exited {result.returncode} for seek={seek_s}s: {stderr_tail}"
        )


def generate_thumbnail(source: Path, dest: Path) -> None:
    """Writes a single scaled poster-frame JPEG for `source` to `dest`.

    Tries a 1s-in seek first (skips a black/blank opening frame, common on
    real-world videos) and falls back to the very first frame (`-ss 0`) if
    that fails — covers clips shorter than 1s, which aren't a rare case in
    a general "browse my files" app (e.g. a short trimmed clip or a
    GIF-to-mp4 conversion). Raises `ThumbnailGenerationError` if BOTH
    attempts fail.
    """
    try:
        _run_ffmpeg(source, dest, seek_s=1.0)
        return
    except ThumbnailGenerationError:
        pass  # fall through to the frame-0 retry below

    _run_ffmpeg(source, dest, seek_s=0.0)


def get_cached_thumbnail(source: Path) -> Path:
    """Get-or-create: returns a path to a cached JPEG thumbnail for
    `source`, generating (and caching) it first if there's no existing
    cache entry for the CURRENT `cache_key_for(source)`.

    Generation writes to a per-call unique temp path in the same cache
    directory, then atomically renames it onto the final `dest` path
    (`Path.replace`, same-filesystem rename) — this is what makes it safe
    for two concurrent requests for the same not-yet-cached thumbnail to
    both "miss" and both generate: each writes its own temp file, and
    whichever `replace()` runs last simply wins with an equivalent JPEG
    (ffmpeg's output for the same source/seek is deterministic), rather
    than either request ever reading a partially-written file mid-write.
    """
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
