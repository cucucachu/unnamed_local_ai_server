"""App templates (M22-05, docs/APP_TEMPLATES.md §3): app packages shipped in the
image (`examples/apps/` in the repo, `settings.app_templates_dir`) that any editor
of a space can install as their own app.

Installing one is what the agent's `create_app` does, so both call it: copy the
package into `<space>/Apps/<slug>/`, patch `slug`, `name` and
`homeai.template` ({slug, version}: where it came from) in the copy's
`app.json`, register it and install it tracking `working`. The copy is the
user's from then on, like a fork; nothing updates it from the template.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import anyio.to_thread
from psycopg import AsyncConnection

from app.core import apps, fsops, manifest, spaces
from app.core.errors import InvalidInput, NotFound
from app.core.principal import Principal
from app.core.storage import SpaceStorage

logger = logging.getLogger(__name__)

MAX_AGENT_MD_BYTES = 64 * 1024
_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

Row = dict[str, Any]


def _open(root: Path, slug: str) -> int | None:
    if not spaces.SLUG_RE.fullmatch(slug):
        return None
    try:
        return os.open(root / slug, _OPEN_DIR)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None


def _agent_md(fd: int) -> str:
    try:
        with fsops.open_regular_at(fd, "AGENT.md") as f:
            return f.read(MAX_AGENT_MD_BYTES).decode("utf-8", "replace")
    except OSError:
        return ""


def _describe(slug: str, doc: dict[str, Any], agent_md: str) -> Row:
    homeai = doc.get("homeai") if isinstance(doc.get("homeai"), dict) else {}
    return {
        "slug": slug,
        "name": doc["name"],
        "version": doc["version"],
        "icon": homeai.get("icon"),
        "description": homeai.get("description") or "",
        "agent_md": agent_md,
    }


def _load(root: Path, slug: str) -> Row | None:
    """The template `slug` under `root`, or None if there's no valid one."""
    fd = _open(root, slug)
    if fd is None:
        return None
    try:
        doc, found = manifest.validate_package(fd, slug)
        if found or not isinstance(doc, dict):
            logger.warning("app template %s is invalid: %s", slug, [d.as_dict() for d in found])
            return None
        return _describe(slug, doc, _agent_md(fd))
    finally:
        os.close(fd)


def _list(root: Path) -> list[Row]:
    if not root.is_dir():
        return []
    out = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.is_dir() and not entry.is_symlink():
            template = _load(root, entry.name)
            if template is not None:
                out.append(template)
    return out


async def list_templates(root: Path) -> list[Row]:
    """Every valid template, by slug. An invalid one is logged and left out."""
    return await anyio.to_thread.run_sync(_list, root)


async def install_template(
    conn: AsyncConnection,
    principal: Principal,
    storage: SpaceStorage,
    root: Path,
    space_id: UUID,
    template: str,
    slug: str | None = None,
    name: str | None = None,
) -> tuple[Row, Row]:
    """Copy `template` into `space_id` as a new app (`slug`, `name` default to the
    template's), register it and install it tracking `working`. Write on the space."""
    dest = await spaces.authorize_space(conn, principal, space_id, "write")
    found = await anyio.to_thread.run_sync(_load, root, template)
    if found is None:
        raise NotFound("unknown_template")
    slug = slug or template
    if slug in manifest.SYSTEM_APP_SLUGS:
        raise InvalidInput("reserved_slug")
    name = (name if name is not None else found["name"]).strip()
    if not name or len(name) > 64:
        raise InvalidInput("invalid_name")
    dest_path, r = await apps.new_package_target(conn, principal, storage, dest.space, slug)
    owner = fsops.Owner(principal.uid, dest.space["gid"])
    provenance = {"slug": template, "version": found["version"]}

    def patch(doc: dict[str, Any]) -> None:
        doc["slug"], doc["name"] = slug, name
        doc.setdefault("homeai", {})["template"] = provenance

    src_fd = await anyio.to_thread.run_sync(_open, root, template)
    if src_fd is None:
        raise NotFound("unknown_template")
    try:
        await anyio.to_thread.run_sync(apps.copy_into_space, r, slug, src_fd, owner)
    finally:
        os.close(src_fd)
    await anyio.to_thread.run_sync(apps.patch_manifest, r, slug, owner, patch)
    app = await apps.register_app(conn, principal, storage, dest_path)
    instance = await apps.install_app(conn, principal, storage, space_id, app["id"], apps.WORKING)
    return app, instance
