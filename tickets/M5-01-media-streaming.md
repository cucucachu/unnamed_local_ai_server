# M5-01 — Range-request media streaming endpoint

**Milestone**: M5 · **Size**: M · **Depends on**: M3-03 · **Blocks**: M5-02

## Context

Direct HTTP byte streaming from the workspace for `<video>`/`<audio>`/expo players — no media
server, no transcoding (PLAN.md P3-9, "Media streaming is direct HTTP Range"). Seek/scrub
depends on correct `Range` semantics; browsers are unforgiving about them.

## Spec

1. **`app/api/media.py`** — `GET /api/media/stream?path=<file>` (+ `HEAD` same route):
   - Path via `resolve_workspace_path` (M3-03); 404 if missing/dir.
   - Headers always: `Accept-Ranges: bytes`, `Content-Type` from `mimetypes.guess_type`
     (fallback `application/octet-stream`), `Cache-Control: no-store`.
   - No `Range` header → `200`, full file, `Content-Length` set.
   - `Range: bytes=start-end` (single range only; the three forms `a-b`, `a-`, `-suffix`):
     - Valid → `206`, `Content-Range: bytes {start}-{end}/{size}`, `Content-Length: end-start+1`,
       body = exactly that slice.
     - `start >= size` or unparseable-but-present → `416` with `Content-Range: bytes */{size}`.
     - `end >= size` → clamp to `size-1` (per RFC 9110).
     - Multi-range (`a-b,c-d`) → treat as unsatisfiable-complexity: respond `200` full file
       (legal per RFC — servers MAY ignore Range; browsers never send multi-range for media).
   - Body streamed in 1 MiB chunks from an offset (`await anyio.to_thread` around
     open/seek/read loop or `starlette` streaming response with a generator); never read the
     whole file into memory.
   - `HEAD` → same headers, no body.
2. **Tests** (tmp workspace, a 10 KiB random-bytes file, httpx):
   - 200 full: body identical, length header right.
   - `bytes=0-999` → 206, correct slice + `Content-Range`.
   - `bytes=9000-` → 206, last 1240 bytes... (compute exact); `bytes=-500` → last 500.
   - `bytes=99999-` → 416 with `*/10240`. Garbage `Range: seconds=1` → 200 full (ignore
     non-bytes units per RFC).
   - `end` overrun clamps. HEAD: headers equal GET's, empty body. Traversal guard (reuse the
     parametrized guard suite). mp4 name → `video/mp4` content type.
   - Byte-identity property: for a set of random (start, end) pairs, concatenating slice
     responses reconstructs the file exactly (loop in one test).

## Out of scope

Transcoding, thumbnails, playlists, DLNA; multi-range bodies; ETag/conditional requests
(no-store keeps it simple for v1).

## Acceptance criteria (Tier A)

- [ ] Test suite above green; ruff green.
- [ ] Live check through Caddy with a real mp4 (generate one on host:
      `docker run --rm -v /srv/homeai/workspace:/w homeai-exec-toolbox:latest ffmpeg -f lavfi -i testsrc=duration=10:size=640x360:rate=30 -pix_fmt yuv420p /w/test-video.mp4`):
      `curl -H 'Range: bytes=0-1023' -s -D- -o /dev/null http://localhost/api/media/stream?path=test-video.mp4`
      → `206`, `Content-Range: bytes 0-1023/<size>`, `Content-Type: video/mp4`.
- [ ] `curl` full download of the mp4 via the endpoint is byte-identical to the file (`cmp`).

## Tier B

None (playback UX is M5-02's gate).
