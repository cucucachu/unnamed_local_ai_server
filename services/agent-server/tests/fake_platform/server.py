"""ASGI stand-in for the platform's `/internal/delegations*` and `/api/platform/files*`.

Scripted by a `FakePlatform` (`scripting.py`); mirrors
`tests/fake_web_fetch/server.py`'s pattern.
"""

from __future__ import annotations

import fnmatch
from datetime import UTC, datetime
from pathlib import PurePosixPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.core.delegation import DelegationDenied, DelegationUnavailable, Grant

from .scripting import AGENT_TOKEN, FakePlatform, Minted

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC).isoformat()


def _error(status: int, detail: str, message: str | None = None) -> HTTPException:
    return HTTPException(status, {"detail": detail, "message": message})


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header.removeprefix("Bearer ") if header.startswith("Bearer ") else None


class _Place:
    def __init__(self, fake: FakePlatform, who: Minted, vpath: str) -> None:
        parts = PurePosixPath(vpath or "/").parts
        if not parts or parts[0] != "/" or ".." in parts:
            raise _error(422, "invalid_path", f"Invalid path: {vpath}")
        parts = parts[1:]
        if parts[:1] == ("personal",):
            self.key, self.prefix, rest = f"personal:{who.user_id}", "/personal", parts[1:]
            self.role = "owner"
        elif parts[:1] == ("spaces",) and len(parts) >= 2:
            role = fake.members.get(parts[1], {}).get(who.user_id)
            if role is None:
                raise _error(404, "not_found")
            self.key, self.prefix, rest = parts[1], f"/spaces/{parts[1]}", parts[2:]
            self.role = role
        else:
            raise _error(404, "not_found")
        self.rel = "/".join(rest)
        self.tree = fake.tree(self.key)

    def vpath(self, rel: str) -> str:
        return f"{self.prefix}/{rel}" if rel else self.prefix

    def is_file(self) -> bool:
        return self.rel in self.tree

    def is_dir(self) -> bool:
        return not self.rel or any(p.startswith(self.rel + "/") for p in self.tree)

    def require_write(self) -> None:
        if self.role == "viewer":
            raise _error(403, "insufficient_role")


def create_fake_platform_app(fake: FakePlatform) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        body = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
        return JSONResponse(status_code=exc.status_code, content=body)

    # --- delegations --------------------------------------------------------------

    def _grant(request: Request, mint) -> JSONResponse:
        if _bearer(request) != AGENT_TOKEN:
            raise _error(401, "unauthenticated")
        try:
            grant: Grant = mint()
        except DelegationDenied as exc:
            raise _error(401, "unauthenticated") from exc
        except DelegationUnavailable as exc:
            raise _error(503, "unavailable") from exc
        return JSONResponse({"token": grant.token, "expires_at": grant.expires_at.isoformat()})

    @app.post("/internal/delegations")
    async def exchange(request: Request) -> JSONResponse:
        body = await request.json()
        return _grant(request, lambda: fake.exchange(body["identity_token"], body["thread_id"]))

    @app.post("/internal/delegations/refresh")
    async def refresh(request: Request) -> JSONResponse:
        body = await request.json()
        return _grant(request, lambda: fake.refresh(body["token"]))

    # --- files --------------------------------------------------------------------

    def _place(request: Request, path: str) -> _Place:
        bearer = _bearer(request)
        fake.file_requests.append((request.method, request.url.path, bearer))
        who = fake.principal(bearer)
        if who is None:
            raise _error(401, "unauthenticated")
        return _Place(fake, who, path)

    def _entry(place: _Place, rel: str, is_dir: bool) -> dict:
        return {
            "name": PurePosixPath(rel).name,
            "path": place.vpath(rel),
            "type": "dir" if is_dir else "file",
            "size": 0 if is_dir else len(place.tree[rel]),
            "mtime": _EPOCH,
            "mime": None,
        }

    @app.get("/api/platform/files")
    async def list_dir(request: Request, path: str) -> JSONResponse:
        place = _place(request, path)
        if not place.is_dir():
            raise _error(404, "not_found")
        base = place.rel + "/" if place.rel else ""
        children: dict[str, bool] = {}
        for rel in place.tree:
            if rel.startswith(base):
                head, sep, _ = rel[len(base) :].partition("/")
                children[base + head] = children.get(base + head, False) or bool(sep)
        entries = [_entry(place, rel, is_dir) for rel, is_dir in sorted(children.items())]
        return JSONResponse({"path": place.vpath(place.rel), "entries": entries})

    @app.get("/api/platform/files/stat")
    async def stat(request: Request, path: str) -> JSONResponse:
        place = _place(request, path)
        if place.is_file():
            return JSONResponse({"entry": _entry(place, place.rel, False)})
        if place.is_dir():
            return JSONResponse({"entry": _entry(place, place.rel, True)})
        raise _error(404, "not_found")

    @app.post("/api/platform/files/read")
    async def read(request: Request) -> JSONResponse:
        body = await request.json()
        place = _place(request, body["path"])
        if not place.is_file():
            raise _error(404, "not_found")
        lines = place.tree[place.rel].decode().splitlines()
        offset, limit = body.get("offset", 0), body.get("limit", 2000)
        chunk = lines[offset : offset + limit]
        return JSONResponse(
            {
                "path": place.vpath(place.rel),
                "content": "\n".join(chunk),
                "encoding": "utf-8",
                "total_lines": len(lines),
                "start_line": offset + 1,
                "end_line": offset + len(chunk),
            }
        )

    @app.post("/api/platform/files/write")
    async def write(request: Request) -> JSONResponse:
        body = await request.json()
        place = _place(request, body["path"])
        place.require_write()
        place.tree[place.rel] = body["content"].encode()
        return JSONResponse({"path": place.vpath(place.rel)})

    @app.post("/api/platform/files/edit")
    async def edit(request: Request) -> JSONResponse:
        body = await request.json()
        place = _place(request, body["path"])
        place.require_write()
        if not place.is_file():
            raise _error(404, "not_found")
        text = place.tree[place.rel].decode()
        count = text.count(body["old_string"])
        if count == 0:
            raise _error(
                422, "string_not_found", f"Error: String not found in file: '{body['old_string']}'"
            )
        if count > 1 and not body.get("replace_all"):
            raise _error(422, "multiple_occurrences", "Error: String appears more than once")
        new = text.replace(body["old_string"], body["new_string"], -1 if count > 1 else 1)
        place.tree[place.rel] = new.encode()
        return JSONResponse({"path": place.vpath(place.rel), "occurrences": count})

    @app.delete("/api/platform/files")
    async def delete(request: Request, path: str) -> Response:
        place = _place(request, path)
        place.require_write()
        if not place.is_file():
            raise _error(404, "not_found")
        del place.tree[place.rel]
        return Response(status_code=204)

    @app.put("/api/platform/files/content")
    async def put_content(request: Request, path: str) -> JSONResponse:
        place = _place(request, path)
        place.require_write()
        place.tree[place.rel] = await request.body()
        return JSONResponse({"path": place.vpath(place.rel)})

    @app.get("/api/platform/files/download")
    async def download(request: Request, path: str) -> Response:
        place = _place(request, path)
        if not place.is_file():
            raise _error(404, "not_found")
        return Response(place.tree[place.rel], media_type="application/octet-stream")

    @app.post("/api/platform/files/grep")
    async def grep(request: Request) -> JSONResponse:
        body = await request.json()
        place = _place(request, body.get("path") or "/personal")
        matches = []
        for rel, data in sorted(place.tree.items()):
            if place.rel and not (rel == place.rel or rel.startswith(place.rel + "/")):
                continue
            for n, line in enumerate(data.decode(errors="replace").splitlines(), 1):
                if body["pattern"] in line:
                    matches.append({"path": place.vpath(rel), "line": n, "text": line})
        return JSONResponse({"matches": matches, "truncated": False, "error": None})

    @app.post("/api/platform/files/glob")
    async def glob(request: Request) -> JSONResponse:
        body = await request.json()
        place = _place(request, body.get("path") or "/personal")
        matches = [
            {"path": place.vpath(rel), "is_dir": False, "size": len(data)}
            for rel, data in sorted(place.tree.items())
            if fnmatch.fnmatch(rel, body["pattern"])
        ]
        return JSONResponse({"matches": matches, "truncated": False, "truncation_reason": None})

    return app
