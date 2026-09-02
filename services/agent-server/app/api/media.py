"""Range-request media streaming — `GET`/`HEAD /api/media/stream`.

Direct HTTP byte streaming from the workspace for `<video>`/`<audio>`/Expo
players (README.md: "Media streaming is direct HTTP Range") — no media
server, no transcoding. Correct `Range` semantics (RFC 9110 §14) matter
because browser seek/scrub depends on them.

The traversal guard is the SAME `app.core.paths.resolve_workspace_path`
already used by `app/api/files.py` (see that module's docstring: "will be
reused as-is by the media API in M5-01") — imported here, not reimplemented.

## Introspection notes (real, not guessed — installed `fastapi==0.141.1`,
`starlette==1.6.0`; see this ticket's final report for the full transcript):

- A route decorated ONLY with `@router.get(...)` does NOT implicitly answer
  `HEAD` in this FastAPI/Starlette version — confirmed with a scratch app: a
  `HEAD` request against a GET-only route returned `405 {"detail": "Method
  Not Allowed"}` with `Allow: GET`, not a body-stripped 200. A route with
  BOTH `@router.get(...)` and `@router.head(...)` stacked on the same
  handler DOES work for both methods, so this module registers both
  decorators on `_stream` (via two thin wrappers below) rather than relying
  on any implicit HEAD support.
- More surprising: for a route that IS explicitly registered for `HEAD`, the
  ASGI stack (confirmed against `httpx.ASGITransport`, which drives the app
  the same way a real ASGI server does) still fully DRAINS a
  `StreamingResponse`'s async generator for a `HEAD` request before
  discarding the body bytes — the generator was confirmed to run to
  completion (all chunks yielded) even though the client received an empty
  body. That's correct-but-wasteful for a multi-GB media file: it would
  seek/read the entire byte range from disk on every `HEAD` call just to
  throw the bytes away. So the `head=True` path here swaps in `_empty_body`
  (an async generator that yields nothing) instead of the real
  file-reading generator, while computing status code and every header
  (`Content-Length`/`Content-Range`/etc.) through the exact same range-math
  as the `GET` path — headers are identical, no wasted disk I/O.
- `starlette.responses.Response.init_headers` (reading its source directly)
  only auto-populates `Content-Length` from `len(self.body)`, and
  `StreamingResponse` has no `body` attribute at all (`getattr(self,
  "body", None)` is `None`) — so passing `Content-Length` explicitly via
  `headers=` is both necessary (nothing else will set it for a streamed
  200/206) and safe (nothing overwrites an explicitly-provided value).
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

import anyio.to_thread
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.paths import resolve_workspace_path

router = APIRouter()

_CHUNK_SIZE = 1024 * 1024  # 1 MiB, per the ticket's exact spec.

# Single `bytes=start-end` / `bytes=start-` / `bytes=-suffix_len` range, per
# RFC 9110 §14.1.2. A comma anywhere (multi-range) is rejected by the caller
# before this ever runs; each of `start`/`end` is either all-digits or empty.
_RANGE_RE = re.compile(r"(\d*)-(\d*)")


def _workspace_root(request: Request) -> Path:
    return Path(request.app.state.settings.workspace_root)


def _parse_range(range_header: str | None, size: int) -> tuple[int, int] | str | None:
    """Parse a `Range` header against a file of `size` bytes.

    Returns:
    - `None` — no `Range` header, a non-`bytes` unit, or a multi-range
      request (comma-separated) — all three mean "respond with the full
      file", per RFC 9110 (servers MAY ignore `Range` entirely; real
      browsers never send multi-range requests for media playback).
    - `"unsatisfiable"` — syntactically invalid `bytes=` range, or a range
      whose start is at/past EOF — caller responds `416`.
    - `(start, end)` — a valid, satisfiable, INCLUSIVE byte range, with
      `end` already clamped to `size - 1` per RFC 9110 §14.1.2.
    """
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
        return "unsatisfiable"  # bare "bytes=-", no digits at all
    if size == 0:
        return "unsatisfiable"  # no byte in an empty file can ever be in-range

    if not start_s:
        # Suffix form: `bytes=-N` — last N bytes.
        suffix_len = int(end_s)
        if suffix_len == 0:
            return "unsatisfiable"
        start = max(0, size - suffix_len)
        return (start, size - 1)

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
    """No-op async generator — used for `HEAD` so no file I/O ever happens."""
    for chunk in ():  # pragma: no cover - never iterates, just satisfies typing
        yield chunk


async def _iter_range(path: Path, start: int, length: int) -> AsyncIterator[bytes]:
    """Stream `length` bytes of `path` starting at `start`, in 1 MiB chunks.

    Every blocking call (`open`/`seek`/`read`/`close`) runs via
    `anyio.to_thread.run_sync` — matching `app/api/files.py`'s own judgement
    for a big/slow filesystem operation that must not block the event loop
    (as opposed to upload's per-chunk loop, which is a stream-in/stream-out
    concern already bounded to <=1MiB per await; this generator is exactly
    the "stream out" analogue of that same reasoning).
    """

    def _open_and_seek() -> BinaryIO:
        f = path.open("rb")
        f.seek(start)
        return f

    file_obj = await anyio.to_thread.run_sync(_open_and_seek)
    try:
        remaining = length
        while remaining > 0:
            to_read = min(_CHUNK_SIZE, remaining)
            chunk = await anyio.to_thread.run_sync(file_obj.read, to_read)
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        await anyio.to_thread.run_sync(file_obj.close)


async def _stream(request: Request, path: str, *, head: bool) -> StreamingResponse:
    root = _workspace_root(request)
    target = resolve_workspace_path(root, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"file '{path}' not found")

    size = target.stat().st_size
    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    base_headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}

    parsed = _parse_range(request.headers.get("range"), size)

    if parsed == "unsatisfiable":
        headers = {**base_headers, "Content-Range": f"bytes */{size}"}
        return StreamingResponse(
            _empty_body(),
            status_code=416,
            media_type=content_type,
            headers=headers,
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
    body = _empty_body() if head else _iter_range(target, start, length)
    return StreamingResponse(
        body, status_code=status_code, media_type=content_type, headers=headers
    )


@router.get("/media/stream")
async def stream_media_get(request: Request, path: str) -> StreamingResponse:
    return await _stream(request, path, head=False)


@router.head("/media/stream")
async def stream_media_head(request: Request, path: str) -> StreamingResponse:
    return await _stream(request, path, head=True)
