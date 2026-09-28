"""ASGI stand-in for the platform's `/internal/delegations*`, `/api/platform/files*`,
`/api/platform/{spaces,apps}*` and `/internal/hitl-approvals`.

Scripted by a `FakePlatform` (`scripting.py`); mirrors
`tests/fake_web_fetch/server.py`'s pattern.
"""

from __future__ import annotations

import fnmatch
import json
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

    # --- apps (M13-02) ---------------------------------------------------------------

    def _who(request: Request) -> Minted:
        who = fake.principal(_bearer(request))
        if who is None:
            raise _error(401, "unauthenticated")
        return who

    def _space(who: Minted, space_id: str) -> dict:
        space = next((s for s in fake.spaces_for(who.user_id) if s["id"] == space_id), None)
        if space is None:
            raise _error(404, "not_found")
        return space

    def _instance(who: Minted, instance_id: str) -> tuple[dict, dict]:
        inst = next((i for i in fake.instances if i["id"] == instance_id), None)
        if inst is None:
            raise _error(404, "not_found")
        return inst, _space(who, inst["space_id"])

    def _app_out(app: dict) -> dict:
        return {k: v for k, v in app.items() if k != "source_key"}

    def _public_space(space: dict) -> dict:
        return {k: v for k, v in space.items() if k != "key"}

    def _public_instance(inst: dict) -> dict:
        return {k: v for k, v in inst.items() if k != "space_key"}

    @app.get("/api/platform/spaces")
    async def list_spaces(request: Request) -> JSONResponse:
        who = _who(request)
        return JSONResponse({"spaces": [_public_space(s) for s in fake.spaces_for(who.user_id)]})

    @app.get("/api/platform/spaces/{space_id}/instances")
    async def list_instances(request: Request, space_id: str) -> JSONResponse:
        _space(_who(request), space_id)
        found = [_public_instance(i) for i in fake.instances if i["space_id"] == space_id]
        return JSONResponse({"instances": found})

    @app.post("/api/platform/spaces/{space_id}/instances")
    async def install(request: Request, space_id: str) -> JSONResponse:
        space = _space(_who(request), space_id)
        if space["role"] == "viewer":
            raise _error(403, "insufficient_role")
        body = await request.json()
        app_ = next((a for a in fake.apps if a["id"] == body["app_id"]), None)
        if app_ is None:
            raise _error(404, "not_found")
        return JSONResponse(_public_instance(fake.install(app_, space["key"])), status_code=201)

    @app.get("/api/platform/apps")
    async def list_apps(request: Request) -> JSONResponse:
        who = _who(request)
        keys = {s["key"] for s in fake.spaces_for(who.user_id)}
        installed = {i["app_id"] for i in fake.instances if i["space_key"] in keys}
        out = []
        for a in fake.apps:
            if a["source_key"] in keys:
                out.append(_app_out(a))
            elif a["id"] in installed:
                out.append(_app_out(a) | {"source_path": None, "working_version": None})
        return JSONResponse({"apps": out})

    @app.post("/api/platform/apps")
    async def register(request: Request) -> JSONResponse:
        body = await request.json()
        place = _place(request, body["source_path"])
        place.require_write()
        raw = place.tree.get(f"{place.rel}/app.json")
        if raw is None:
            raise _error(422, "invalid_package", "app.json is missing")
        manifest = json.loads(raw)
        app_ = fake.add_app(place.key, body["source_path"], manifest)
        return JSONResponse(
            {"app": _app_out(app_), "valid": True, "diagnostics": []}, status_code=201
        )

    @app.post("/api/platform/apps/{app_id}/build")
    async def build(request: Request, app_id: str) -> JSONResponse:
        who = _who(request)
        app_ = next((a for a in fake.apps if a["id"] == app_id), None)
        if app_ is None:
            raise _error(404, "not_found")
        place = _Place(fake, who, app_["source_path"])
        place.require_write()
        prefix = place.rel + "/"
        files = {r[len(prefix) :]: d for r, d in place.tree.items() if r.startswith(prefix)}
        fake.builds.append(app_id)
        diagnostics = fake.builder(app_, files)
        if diagnostics:
            return JSONResponse(
                {"app": _app_out(app_), "ok": False, "build": None, "diagnostics": diagnostics}
            )
        manifest = json.loads(files["app.json"])
        commit = f"{len(fake.builds):07x}" + "0" * 33
        app_["working_version"] = {
            "version": manifest.get("version"), "commit": commit, "manifest": manifest,
        }  # fmt: skip
        migrations = []
        for inst in fake.instances:
            if inst["app_id"] != app_id:
                continue
            inst["app"]["version"] = manifest.get("version")
            planned = fake.next_migration.pop(inst["id"], None)
            if planned is None:
                current = fake.migration(inst["id"], "up_to_date", steps=[]) | {"id": None}
                migrations.append({"instance_id": inst["id"], "migration": current, "error": None})
                continue
            for older in fake.migrations[inst["id"]]:
                if older["status"] == "pending":
                    older["status"] = "superseded"
            fake.migrations[inst["id"]].append(planned)
            migrations.append({"instance_id": inst["id"], "migration": planned, "error": None})
        return JSONResponse(
            {
                "app": _app_out(app_),
                "ok": True,
                "build": {"id": f"b-{len(fake.builds)}", "duration_ms": 1234,
                          "bundle_path": "bundle.js", "bundle_bytes": 10, "commit": commit},
                "diagnostics": [],
                "migrations": migrations,
            }
        )  # fmt: skip

    @app.post("/api/platform/apps/instances/{instance_id}/rpc")
    async def rpc(request: Request, instance_id: str) -> JSONResponse:
        who = _who(request)
        _, space = _instance(who, instance_id)
        body = await request.json()
        fake.rpc_calls.append((instance_id, body))
        if body["op"] == "getAll":
            if not body["sql"].lstrip().lower().startswith("select"):
                raise _error(422, "sql_not_allowed", "statement not allowed: only SELECT")
            return JSONResponse({"rows": fake.rows.get(instance_id, [])})
        if space["role"] == "viewer":
            raise _error(403, "insufficient_role")
        rows = [] if body["op"] == "run" else fake.rows.get(instance_id, [])[:1]
        return JSONResponse({"rows": rows, "changes": 1, "lastInsertRowId": 7})

    @app.get("/api/platform/apps/instances/{instance_id}/migrations")
    async def migrations(request: Request, instance_id: str) -> JSONResponse:
        _instance(_who(request), instance_id)
        return JSONResponse({"migrations": list(reversed(fake.migrations[instance_id]))})

    def _pending(instance_id: str, migration_id: str) -> dict:
        m = next((m for m in fake.migrations.get(instance_id, []) if m["id"] == migration_id), None)
        if m is None:
            raise _error(404, "not_found")
        if m["status"] != "pending":
            raise _error(409, "migration_not_pending")
        return m

    @app.post("/api/platform/apps/instances/{instance_id}/migrations/{migration_id}/approve")
    async def approve(request: Request, instance_id: str, migration_id: str) -> JSONResponse:
        who = _who(request)
        _, space = _instance(who, instance_id)
        marker = request.headers.get("x-homeai-hitl-approval")
        fake.approvals.append((instance_id, migration_id, marker))
        bound = fake.hitl_markers.pop(marker or "", None)
        if bound != (who.thread_id, instance_id, migration_id):
            raise _error(403, "hitl_approval_required")
        if space["role"] == "viewer":
            raise _error(403, "insufficient_role")
        m = _pending(instance_id, migration_id)
        m.update(status="applied", snapshot=f"snap-{migration_id}.sqlite")
        return JSONResponse(m)

    @app.post("/internal/hitl-approvals")
    async def hitl_approval(request: Request) -> JSONResponse:
        if _bearer(request) != AGENT_TOKEN:
            raise _error(401, "unauthenticated")
        body = await request.json()
        fake.hitl_mints.append(body)
        who = fake.principal(body["delegation"])
        if who is None:
            raise _error(401, "unauthenticated")
        _, space = _instance(who, body["instance_id"])
        if space["role"] == "viewer":
            raise _error(403, "insufficient_role")
        _pending(body["instance_id"], body["migration_id"])
        marker = f"hitl_fake{next(fake._counter)}"
        fake.hitl_markers[marker] = (who.thread_id, body["instance_id"], body["migration_id"])
        return JSONResponse({"token": marker, "expires_in_s": 60})

    @app.get("/api/platform/system-apps")
    async def list_system_apps(request: Request) -> JSONResponse:
        _who(request)
        return JSONResponse({"apps": fake.system_apps})

    @app.get("/api/platform/system-apps/{slug}")
    async def get_system_app(request: Request, slug: str) -> JSONResponse:
        _who(request)
        app_ = next((a for a in fake.system_apps if a["slug"] == slug), None)
        if app_ is None:
            raise _error(404, "not_found")
        return JSONResponse(app_)

    @app.post("/api/platform/system-apps/{slug}/actions/{name}")
    async def run_system_action(request: Request, slug: str, name: str) -> JSONResponse:
        who = _who(request)
        app_ = next((a for a in fake.system_apps if a["slug"] == slug), None)
        if app_ is None:
            raise _error(404, "not_found")
        known = {a["name"] for a in app_.get("actions") or []}
        if name not in known:
            raise _error(404, "unknown_action")
        body = await request.json()
        params = body.get("params") if isinstance(body, dict) else {}
        if not isinstance(params, dict):
            params = {}
        fake.system_action_calls.append((slug, name, params))
        src, dst = params.get("src"), params.get("dst")
        if not isinstance(src, str) or not isinstance(dst, str):
            raise _error(422, "invalid_params")
        src_place = _Place(fake, who, src)
        dst_place = _Place(fake, who, dst)
        src_place.require_write()
        dst_place.require_write()
        if src_place.rel not in src_place.tree:
            raise _error(404, "not_found")
        if dst_place.rel in dst_place.tree:
            raise _error(409, "already_exists")
        data = src_place.tree[src_place.rel]
        if name == "moveToSpace":
            del src_place.tree[src_place.rel]
        dst_place.tree[dst_place.rel] = data
        return JSONResponse({"ok": True, "result": {"src": src, "dst": dst}})

    return app
