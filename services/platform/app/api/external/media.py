"""`/api/platform/files/stream` and `/thumbnail`: media over virtual paths (ported from agent-server).

Range semantics follow RFC 9110 §14 (browser seek/scrub depends on them):
one `bytes=` range per request, `206` with `Content-Range`, `416` past EOF,
and a full `200` for no range, another unit, or a multi-range request.
`HEAD` is registered explicitly (FastAPI doesn't derive it from `GET`) and
sends an empty generator, since Starlette would otherwise read and discard
the whole range. `Content-Length` is set by hand: `StreamingResponse`
doesn't compute one.
"""

from __future__ import annotations

import mimetypes
import os
import re
from collections.abc import AsyncIterator
from typing import BinaryIO

import anyio.to_thread
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.api.external.files import open_file
from app.core.errors import ServerError, UnsupportedMedia
from app.core.principal import CurrentUser
from app.core.thumbnails import ThumbnailGenerationError, get_cached_thumbnail, is_video_file

router = APIRouter(prefix="/files")

_CHUNK_SIZE = 1024 * 1024

_RANGE_RE = re.compile(r"(\d*)-(\d*)")


def _parse_range(range_header: str | None, size: int) -> tuple[int, int] | str | None:
    """`None` = serve the whole file, `"unsatisfiable"` = 416, else an inclusive (start, end)."""
    if range_header is None:
        return None
    value = range_header.strip()
    if not value.lower().startswith("bytes="):
        return None
    spec = value[len("bytes=") :]
    if "," in spec:
        return None
    match = _RANGE_RE.fullmatch(spec)
    if not match:
        return "unsatisfiable"

    start_s, end_s = match.group(1), match.group(2)
    if not start_s and not end_s:
        return "unsatisfiable"
    if size == 0:
        return "unsatisfiable"

    if not start_s:
        suffix_len = int(end_s)
        if suffix_len == 0:
            return "unsatisfiable"
        return (max(0, size - suffix_len), size - 1)

    start = int(start_s)
    if start >= size:
        return "unsatisfiable"
    if not end_s:
        return (start, size - 1)
    end = int(end_s)
    if end < start:
        return "unsatisfiable"
    return (start, min(end, size - 1))


async def _empty_body() -> AsyncIterator[bytes]:
    for chunk in ():  # pragma: no cover - never iterates
        yield chunk


async def _iter_range(file_obj: BinaryIO, start: int, length: int) -> AsyncIterator[bytes]:
    try:
        await anyio.to_thread.run_sync(file_obj.seek, start)
        remaining = length
        while remaining > 0:
            chunk = await anyio.to_thread.run_sync(file_obj.read, min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        await anyio.to_thread.run_sync(file_obj.close)


async def _stream(request: Request, principal, path: str, *, head: bool) -> StreamingResponse:
    r, f = await open_file(request, principal, path)
    size = os.fstat(f.fileno()).st_size
    content_type = mimetypes.guess_type(r.rel[-1])[0] or "application/octet-stream"
    base_headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}

    parsed = _parse_range(request.headers.get("range"), size)
    if parsed == "unsatisfiable" or head:
        f.close()
    if parsed == "unsatisfiable":
        headers = {**base_headers, "Content-Range": f"bytes */{size}"}
        return StreamingResponse(
            _empty_body(), status_code=416, media_type=content_type, headers=headers
        )

    if parsed is None:
        start, end, status_code = 0, size - 1, 200
        headers = {**base_headers, "Content-Length": str(size)}
    else:
        start, end = parsed
        status_code = 206
        headers = {
            **base_headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        }

    length = end - start + 1 if size > 0 else 0
    body = _empty_body() if head else _iter_range(f, start, length)
    return StreamingResponse(
        body, status_code=status_code, media_type=content_type, headers=headers
    )


@router.get("/stream")
async def stream_get(request: Request, principal: CurrentUser, path: str) -> StreamingResponse:
    return await _stream(request, principal, path, head=False)


@router.head("/stream")
async def stream_head(request: Request, principal: CurrentUser, path: str) -> StreamingResponse:
    return await _stream(request, principal, path, head=True)


@router.get("/thumbnail")
async def thumbnail(request: Request, principal: CurrentUser, path: str) -> FileResponse:
    """A cached JPEG poster frame for a video file (`415 unsupported_media` for anything else)."""
    r, f = await open_file(request, principal, path)
    with f:
        if not is_video_file(r.rel[-1]):
            raise UnsupportedMedia("unsupported_media")
        try:
            thumbnail_path = await anyio.to_thread.run_sync(get_cached_thumbnail, f.fileno())
        except ThumbnailGenerationError as exc:
            raise ServerError("thumbnail_failed") from exc
    return FileResponse(
        thumbnail_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )
