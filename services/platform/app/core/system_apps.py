"""Image-shipped system apps (M14-03, docs/PLATFORM.md D17).

Packages under `system_apps/` (baked into the platform image) use the same
`app.json` / `AGENT.md` / actions format as user apps, with two differences:

- they render natively in the host (no `app/` sandbox UI);
- `homeai.permissions.privileged` may name platform-implemented action
  families. User `register_app` still validates against the public schema,
  which rejects `privileged`.

They are not rows in `apps` / `app_instances` (no source space). The agent
sees them through `GET /system-apps` and calls Files actions with
`app_action` instance `files`, which hits `POST /system-apps/files/actions/...`
and reuses the files API (`move` / `copy`).
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request

from app.api.schemas import MoveCopyBody
from app.core import fsops, manifest
from app.core.errors import InvalidInput, NotFound
from app.core.principal import Principal

_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_MAX_AGENT_MD = 64 * 1024
_MAX_ACTION_JSON = 16 * 1024


@dataclass(frozen=True)
class SystemAction:
    name: str
    description: str
    params: dict[str, Any]


@dataclass(frozen=True)
class SystemApp:
    slug: str
    name: str
    version: str
    icon: str
    description: str
    privileged: tuple[str, ...]
    agent_md: str
    actions: tuple[SystemAction, ...]
    manifest: dict[str, Any]


def default_system_apps_dir() -> Path:
    """`services/platform/system_apps` next to this package (and `/app/system_apps` in the image)."""
    return Path(__file__).resolve().parents[2] / "system_apps"


def _privileged(doc: dict[str, Any]) -> tuple[str, ...]:
    homeai = doc.get("homeai") if isinstance(doc.get("homeai"), dict) else {}
    perms = homeai.get("permissions") if isinstance(homeai.get("permissions"), dict) else {}
    raw = perms.get("privileged") if isinstance(perms, dict) else None
    if not isinstance(raw, list):
        return ()
    return tuple(x for x in raw if isinstance(x, str))


def _read_text(pkg: int, rel: str, limit: int) -> str:
    with fsops.open_regular_at(pkg, rel) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise InvalidInput("file_too_large")
    return data.decode("utf-8")


def _load_actions(pkg: int) -> tuple[SystemAction, ...]:
    try:
        fd = os.open("actions", _OPEN_DIR, dir_fd=pkg)
    except OSError:
        return ()
    out: list[SystemAction] = []
    try:
        for name in sorted(os.listdir(fd)):
            if name.startswith(".") or not manifest.ACTION_JSON_RE.fullmatch(name):
                continue
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                continue
            doc = json.loads(_read_text(fd, name, _MAX_ACTION_JSON))
            if not isinstance(doc, dict) or not isinstance(doc.get("description"), str):
                raise InvalidInput("invalid_action")
            params = doc.get("params") if isinstance(doc.get("params"), dict) else {}
            out.append(
                SystemAction(
                    name=name.removesuffix(".json"),
                    description=doc["description"],
                    params=params,
                )
            )
    finally:
        os.close(fd)
    return tuple(out)


def load_system_apps(root: Path) -> list[SystemApp]:
    """Every well-formed package under `root`, in D17 tab order then extras."""
    if not root.is_dir():
        return []
    found: dict[str, SystemApp] = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        pkg = os.open(child, _OPEN_DIR)
        try:
            doc, diags = manifest.validate_package(pkg, child.name, shipped=True)
            if diags or not isinstance(doc, dict):
                raise InvalidInput("invalid_app")
            agent_md = _read_text(pkg, "AGENT.md", _MAX_AGENT_MD)
            actions = _load_actions(pkg)
        finally:
            os.close(pkg)
        homeai = doc.get("homeai") if isinstance(doc.get("homeai"), dict) else {}
        found[child.name] = SystemApp(
            slug=doc["slug"],
            name=doc["name"],
            version=doc["version"],
            icon=str(homeai.get("icon") or "apps-outline"),
            description=str(homeai.get("description") or ""),
            privileged=_privileged(doc),
            agent_md=agent_md,
            actions=actions,
            manifest=doc,
        )
    ordered = [found[s] for s in manifest.SYSTEM_APP_SLUGS if s in found]
    extras = [found[s] for s in found if s not in manifest.SYSTEM_APP_SLUGS]
    return ordered + extras


def get_system_app(root: Path, slug: str) -> SystemApp | None:
    return next((a for a in load_system_apps(root) if a.slug == slug), None)


def as_dict(app: SystemApp, *, agent_md: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "slug": app.slug,
        "name": app.name,
        "version": app.version,
        "icon": app.icon,
        "description": app.description,
        "native": True,
        "read_only_source": True,
        "privileged": list(app.privileged),
        "actions": [
            {"name": a.name, "description": a.description, "params": a.params} for a in app.actions
        ],
    }
    if agent_md:
        out["agent_md"] = app.agent_md
    return out


def _str_param(params: dict[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise InvalidInput("invalid_params")
    return value.strip()


async def run_action(
    request: Request, principal: Principal, root: Path, slug: str, name: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Dispatch a privileged Files action onto the files API. Same auth as that API."""
    from app.api.external import files as files_api

    app = get_system_app(root, slug)
    if app is None:
        raise NotFound("not_found")
    action = next((a for a in app.actions if a.name == name), None)
    if action is None:
        raise NotFound("unknown_action")
    if slug == "files" and name == "moveToSpace":
        body = await files_api.move(
            MoveCopyBody(src=_str_param(params, "src"), dst=_str_param(params, "dst")),
            request,
            principal,
        )
        return {"src": body.src, "dst": body.dst}
    if slug == "files" and name == "copyToSpace":
        body = await files_api.copy(
            MoveCopyBody(src=_str_param(params, "src"), dst=_str_param(params, "dst")),
            request,
            principal,
        )
        return {"src": body.src, "dst": body.dst}
    raise NotFound("unknown_action")
