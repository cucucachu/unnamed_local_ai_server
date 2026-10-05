"""State for the fake platform (`server.py`): delegations, an in-memory files tree and apps.

Just enough of the real one (services/platform) for agent-level tests:
`/personal/...` per user, `/spaces/<slug>/...` with a role per member, and
delegation tokens that stop working once their session is revoked. The
exact error wording is the real platform's job (tested there); this fake
only returns the same statuses and `detail` codes.

Apps (M13-02): apps registered from a files-tree folder, instances in a
space, a scripted builder (`builder`) and per-instance migrations, rpc and
HITL markers - the shapes of the real `/api/platform/apps*` replies.

`client()` is an in-process `DelegationClient` over the same state, for
tests that don't need the HTTP exchange itself.
"""

from __future__ import annotations

import itertools
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.core.delegation import DelegationDenied, DelegationUnavailable, Grant
from tests.fake_identity import TEST_USER_ID

AGENT_TOKEN = "fake-agent-service-token"
TEST_SESSION_ID = "11111111-1111-4111-8111-111111111111"


def _default_system_apps() -> list[dict]:
    """The image-shipped set the real platform lists (M14-03)."""
    files_actions = [
        {
            "name": "moveToSpace",
            "description": "Move or rename a file or folder, including across spaces.",
            "params": {"src": {"type": "string"}, "dst": {"type": "string"}},
        },
        {
            "name": "copyToSpace",
            "description": "Copy a file or folder, including across spaces.",
            "params": {"src": {"type": "string"}, "dst": {"type": "string"}},
        },
    ]
    return [
        {
            "slug": "home",
            "name": "Home",
            "version": "1.0.0",
            "icon": "home-outline",
            "description": "The host launcher.",
            "native": True,
            "read_only_source": True,
            "privileged": [],
            "actions": [],
            "agent_md": "# Home\nNative launcher.\n",
        },
        {
            "slug": "chat",
            "name": "Chat",
            "version": "1.0.0",
            "icon": "chatbubbles-outline",
            "description": "The host Chat tab.",
            "native": True,
            "read_only_source": True,
            "privileged": [],
            "actions": [],
            "agent_md": "# Chat\nYou are the chat.\n",
        },
        {
            "slug": "files",
            "name": "Files",
            "version": "1.0.0",
            "icon": "folder-outline",
            "description": "The host Files tab.",
            "native": True,
            "read_only_source": True,
            "privileged": ["files"],
            "actions": files_actions,
            "agent_md": "# Files\nUse moveToSpace to move across spaces.\n",
        },
        {
            "slug": "settings",
            "name": "Settings",
            "version": "1.0.0",
            "icon": "settings-outline",
            "description": "The host Settings tab.",
            "native": True,
            "read_only_source": True,
            "privileged": [],
            "actions": [],
            "agent_md": "# Settings\nNative settings.\n",
        },
    ]


@dataclass
class Minted:
    user_id: str
    session_id: str
    thread_id: str
    expires_at: datetime


@dataclass
class RoutineGrant:
    user_id: str
    routine_id: str
    space: str
    label: str
    session_id: str


@dataclass
class FakePlatform:
    base_url: str = ""
    ttl: timedelta = timedelta(minutes=15)
    # identity token -> (user_id, session_id); unknown tokens are TEST_USER's.
    identities: dict[str, tuple[str, str]] = field(default_factory=dict)
    revoked_sessions: set[str] = field(default_factory=set)
    # Set to make the delegation endpoints answer 503 (platform trouble).
    unavailable: bool = False
    # space key ("personal:<user_id>" or a shared slug) -> {relative path: bytes}
    trees: dict[str, dict[str, bytes]] = field(default_factory=dict)
    # shared slug -> {user_id: role}
    members: dict[str, dict[str, str]] = field(default_factory=dict)
    grants: dict[str, Minted] = field(default_factory=dict)
    exchanges: list[tuple[str, str]] = field(default_factory=list)
    # Live routine grants (M17-03); revoking one revokes its session.
    routine_grants: dict[str, RoutineGrant] = field(default_factory=dict)
    grant_exchanges: list[tuple[str, str]] = field(default_factory=list)
    refreshes: list[str] = field(default_factory=list)
    # (method, path, bearer) for every files API request
    file_requests: list[tuple[str, str, str | None]] = field(default_factory=list)
    # --- apps ---
    apps: list[dict] = field(default_factory=list)
    instances: list[dict] = field(default_factory=list)
    # instance id -> its migrations, newest last
    migrations: dict[str, list[dict]] = field(default_factory=dict)
    # instance id -> the migration its next successful build plans (else up to date)
    next_migration: dict[str, dict] = field(default_factory=dict)
    # (app, {relative path: bytes}) -> diagnostics; none means the build succeeds
    builder: Callable[[dict, dict[str, bytes]], list[dict]] = lambda _app, _files: []
    builds: list[str] = field(default_factory=list)
    # instance id -> rows any SELECT returns
    rows: dict[str, list[dict]] = field(default_factory=dict)
    rpc_calls: list[tuple[str, dict]] = field(default_factory=list)
    system_action_calls: list[tuple[str, str, dict]] = field(default_factory=list)
    system_apps: list[dict] = field(default_factory=_default_system_apps)
    # marker -> (thread_id, instance_id, migration_id)
    hitl_markers: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    hitl_mints: list[dict] = field(default_factory=list)
    approvals: list[tuple[str, str, str | None]] = field(default_factory=list)
    _counter: itertools.count = field(default_factory=itertools.count)

    # --- delegations ------------------------------------------------------------

    def _mint(self, user_id: str, session_id: str, thread_id: str) -> Grant:
        if session_id in self.revoked_sessions:
            raise DelegationDenied("unauthenticated")
        token = f"dlg-{next(self._counter)}-{user_id[:8]}"
        expires_at = datetime.now(UTC) + self.ttl
        self.grants[token] = Minted(user_id, session_id, thread_id, expires_at)
        return Grant(token, expires_at)

    def exchange(self, identity_token: str, thread_id: str) -> Grant:
        if self.unavailable:
            raise DelegationUnavailable("503")
        self.exchanges.append((identity_token, thread_id))
        user_id, session_id = self.identities.get(identity_token, (TEST_USER_ID, TEST_SESSION_ID))
        return self._mint(user_id, session_id, thread_id)

    def refresh(self, token: str) -> Grant:
        if self.unavailable:
            raise DelegationUnavailable("503")
        self.refreshes.append(token)
        minted = self.grants.get(token)
        if minted is None:
            raise DelegationDenied("unauthenticated")
        return self._mint(minted.user_id, minted.session_id, minted.thread_id)

    def space_role(self, identity_token: str, space: str) -> tuple[str, str] | None:
        if self.unavailable:
            raise DelegationUnavailable("503")
        user_id, session_id = self.identities.get(identity_token, (TEST_USER_ID, TEST_SESSION_ID))
        if session_id in self.revoked_sessions:
            raise DelegationDenied("unauthenticated")
        parts = [p for p in space.split("/") if p]
        if parts == ["personal"]:
            return "/personal", "owner"
        if len(parts) == 2 and parts[0] == "spaces":
            slug = parts[1].lower()
            role = self.members.get(slug, {}).get(user_id)
            return (f"/spaces/{slug}", role) if role else None
        return None

    def issue_routine_grant(
        self, identity_token: str, routine_id: str, space: str, label: str
    ) -> str | None:
        access = self.space_role(identity_token, space)
        if access is None or access[1] not in ("owner", "editor"):
            return None
        user_id, _ = self.identities.get(identity_token, (TEST_USER_ID, TEST_SESSION_ID))
        for grant, held in list(self.routine_grants.items()):
            if (held.user_id, held.routine_id) == (user_id, routine_id):
                self.revoke_routine_grant(grant)
        n = next(self._counter)
        grant = f"hr_{n}"
        self.routine_grants[grant] = RoutineGrant(
            user_id, routine_id, access[0], label, f"routine-session-{n}"
        )
        return grant

    def exchange_routine_grant(self, grant: str, routine_id: str, thread_id: str) -> Grant:
        if self.unavailable:
            raise DelegationUnavailable("503")
        held = self.routine_grants.get(grant)
        if held is None or held.routine_id != routine_id:
            raise DelegationDenied("unauthenticated")
        self.grant_exchanges.append((grant, thread_id))
        return self._mint(held.user_id, held.session_id, thread_id)

    def revoke_routine_grant(self, grant: str) -> None:
        if self.unavailable:
            raise DelegationUnavailable("503")
        held = self.routine_grants.pop(grant, None)
        if held is not None:
            self.revoked_sessions.add(held.session_id)

    def principal(self, bearer: str | None) -> Minted | None:
        minted = self.grants.get(bearer or "")
        if minted is None or minted.session_id in self.revoked_sessions:
            return None
        if datetime.now(UTC) >= minted.expires_at:
            return None
        return minted

    def client(self) -> FakeDelegationClient:
        return FakeDelegationClient(self)

    # --- files ------------------------------------------------------------------

    def tree(self, key: str) -> dict[str, bytes]:
        return self.trees.setdefault(key, {})

    def personal(self, user_id: str = TEST_USER_ID) -> dict[str, bytes]:
        return self.tree(f"personal:{user_id}")

    def add_space(self, slug: str, roles: dict[str, str]) -> dict[str, bytes]:
        self.members[slug] = dict(roles)
        return self.tree(slug)

    # --- apps -------------------------------------------------------------------

    @staticmethod
    def space_id(key: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"fake-space:{key}"))

    def spaces_for(self, user_id: str) -> list[dict]:
        key = f"personal:{user_id}"
        out = [
            {"id": self.space_id(key), "key": key, "slug": f"u-{user_id[:8]}",
             "name": "Personal", "kind": "personal", "role": "owner"}
        ]  # fmt: skip
        for slug, roles in sorted(self.members.items()):
            if user_id in roles:
                out.append(
                    {"id": self.space_id(slug), "key": slug, "slug": slug,
                     "name": slug.title(), "kind": "shared", "role": roles[user_id]}
                )  # fmt: skip
        return out

    def add_app(self, space_key: str, source_path: str, manifest: dict) -> dict:
        """Register an app (as `POST /apps` does) from a folder already in the tree."""
        app = {
            "id": str(uuid.uuid4()),
            "slug": manifest["slug"],
            "name": manifest["name"],
            "source_space_id": self.space_id(space_key),
            "source_key": space_key,
            "source_path": source_path,
            "working_version": None,
        }
        self.apps.append(app)
        return app

    def install(self, app: dict, space_key: str) -> dict:
        version = (app.get("working_version") or {}).get("version")
        inst = {
            "id": str(uuid.uuid4()),
            "app_id": app["id"],
            "space_id": self.space_id(space_key),
            "space_key": space_key,
            "tracks": "working",
            "app": {"id": app["id"], "slug": app["slug"], "name": app["name"],
                    "version": version, "icon": None},
        }  # fmt: skip
        self.instances.append(inst)
        self.migrations.setdefault(inst["id"], [])
        return inst

    def seed_app(
        self, space_key: str, slug: str, files: dict[str, str] | None = None, *, built: bool = True
    ) -> tuple[dict, dict]:
        """An app with source `<space>/Apps/<slug>` installed in the same space."""
        prefix = "/personal" if space_key.startswith("personal:") else f"/spaces/{space_key}"
        manifest = {"name": slug.replace("-", " ").title(), "slug": slug, "version": "1.0.0",
                    "homeai": {"sdk": "1", "description": f"The {slug} app."}}  # fmt: skip
        tree = self.tree(space_key)
        tree[f"Apps/{slug}/app.json"] = json.dumps(manifest).encode()
        for rel, text in (files or {}).items():
            tree[f"Apps/{slug}/{rel}"] = text.encode()
        app = self.add_app(space_key, f"{prefix}/Apps/{slug}", manifest)
        if built:
            app["working_version"] = {"version": "1.0.0", "commit": "c0ffee0" * 5 + "c0",
                                      "manifest": manifest}  # fmt: skip
        return app, self.install(app, space_key)

    def migration(self, instance_id: str, status: str = "pending", steps=None) -> dict:
        if steps is None:
            steps = [
                {"kind": "destructive", "op": "drop_column", "table": "items",
                 "sql": ["ALTER TABLE items DROP COLUMN done"],
                 "reason": "column done is no longer in schema.sql"}
            ]  # fmt: skip
        return {
            "id": str(uuid.uuid4()), "instance_id": instance_id, "status": status,
            "steps": steps, "summary": {"additive": 0, "safe": 0, "destructive": len(steps)},
            "needs_approval": status == "pending", "snapshot": None, "error": None,
        }  # fmt: skip


class FakeDelegationClient:
    def __init__(self, platform: FakePlatform) -> None:
        self.platform = platform

    async def exchange(self, identity_token: str, thread_id: str) -> Grant:
        return self.platform.exchange(identity_token, thread_id)

    async def refresh(self, token: str) -> Grant:
        return self.platform.refresh(token)

    async def space_role(self, identity_token: str, space: str) -> tuple[str, str] | None:
        return self.platform.space_role(identity_token, space)

    async def issue_routine_grant(
        self, identity_token: str, routine_id: str, space: str, label: str
    ) -> str | None:
        return self.platform.issue_routine_grant(identity_token, routine_id, space, label)

    async def exchange_routine_grant(self, grant: str, routine_id: str, thread_id: str) -> Grant:
        return self.platform.exchange_routine_grant(grant, routine_id, thread_id)

    async def revoke_routine_grant(self, grant: str) -> None:
        self.platform.revoke_routine_grant(grant)
