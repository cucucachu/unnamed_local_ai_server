"""A session's exec grants: who its container runs as and what it mounts (docs/PLATFORM.md §6).

Fetched from the platform (`POST /internal/exec-grants`) on every
`ensure`/`execute`/`delete`, never cached, so a membership change or a
revoked session takes effect on the next call. The platform is trusted to
decide *what* a user may mount; this module still refuses a reply that
could never be a valid grant (a root or system id, a relative or `..` host
path, a mount outside `/files/personal` or `/files/spaces/<slug>`) before
any of it reaches Docker.

`digest` is what the container's `homeai.grants` label records; a session
whose container carries a different one is recreated.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

import httpx

MIN_ID = 1000
CONTAINER_PATH_RE = re.compile(r"^/files/(personal|spaces/[a-z0-9][a-z0-9-]{0,39})$")


class GrantsDenied(Exception):
    """The platform refused the delegation (session ended, user disabled, forged)."""


class GrantsUnavailable(Exception):
    """The platform couldn't be asked, or answered something that isn't a valid grant."""


@dataclass(frozen=True)
class Mount:
    host_path: str
    container_path: str
    read_only: bool


@dataclass(frozen=True)
class Grants:
    user_id: str
    uid: int
    gid: int
    gids: tuple[int, ...]
    mounts: tuple[Mount, ...]

    @property
    def user(self) -> str:
        return f"{self.uid}:{self.gid}"

    @property
    def group_add(self) -> list[str]:
        return [str(g) for g in self.gids if g != self.gid]

    @property
    def digest(self) -> str:
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def _id(value: Any) -> int:
    if type(value) is not int or value < MIN_ID:
        raise GrantsUnavailable(f"invalid id in grants: {value!r}")
    return value


def _mount(value: Any) -> Mount:
    if not isinstance(value, dict):
        raise GrantsUnavailable("invalid mount in grants")
    host, target, read_only = (value.get(k) for k in ("host_path", "container_path", "read_only"))
    if not isinstance(host, str) or not isinstance(target, str) or type(read_only) is not bool:
        raise GrantsUnavailable("invalid mount in grants")
    host_path = PurePosixPath(host)
    if not host_path.is_absolute() or ".." in host_path.parts or str(host_path) != host:
        raise GrantsUnavailable(f"invalid host path in grants: {host!r}")
    if not CONTAINER_PATH_RE.fullmatch(target):
        raise GrantsUnavailable(f"invalid container path in grants: {target!r}")
    return Mount(host_path=host, container_path=target, read_only=read_only)


def parse_grants(user_id: str, payload: Any) -> Grants:
    if not isinstance(payload, dict):
        raise GrantsUnavailable("grants is not an object")
    gids = payload.get("gids")
    mounts = payload.get("mounts")
    if not isinstance(gids, list) or not isinstance(mounts, list):
        raise GrantsUnavailable("grants has no gids/mounts")
    grants = Grants(
        user_id=user_id,
        uid=_id(payload.get("uid")),
        gid=_id(payload.get("gid")),
        gids=tuple(_id(g) for g in gids),
        mounts=tuple(_mount(m) for m in mounts),
    )
    targets = [m.container_path for m in grants.mounts]
    sources = [m.host_path for m in grants.mounts]
    if len(set(targets)) != len(targets) or len(set(sources)) != len(sources):
        raise GrantsUnavailable("duplicate mount in grants")
    if grants.gid not in grants.gids:
        raise GrantsUnavailable("primary gid not among the grant's gids")
    return grants


class GrantsClient(Protocol):
    async def fetch(self, delegation: str, user_id: str) -> Grants: ...


class HttpGrantsClient:
    def __init__(self, platform_url: str, exec_token: str, timeout_s: float = 5.0) -> None:
        self._url = f"{platform_url.rstrip('/')}/internal/exec-grants"
        self._exec_token = exec_token
        self._timeout_s = timeout_s

    async def fetch(self, delegation: str, user_id: str) -> Grants:
        if not self._exec_token:
            raise GrantsUnavailable("PLATFORM_EXEC_TOKEN is not set")
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.post(
                    self._url,
                    json={"delegation": delegation},
                    headers={"Authorization": f"Bearer {self._exec_token}"},
                )
        except httpx.HTTPError as exc:
            raise GrantsUnavailable(repr(exc)) from exc
        if response.status_code == 401:
            raise GrantsDenied("delegation refused")
        if response.status_code != 200:
            raise GrantsUnavailable(f"exec-grants: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise GrantsUnavailable("exec-grants: not JSON") from exc
        return parse_grants(user_id, payload)
