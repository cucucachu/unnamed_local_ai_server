"""Spaces: personal-space invariants, backfill, `authorize_space`, the spaces/members API."""

import os
import stat
from uuid import UUID, uuid4

import psycopg
import pytest

from app.core import spaces
from app.core.errors import Forbidden, NotFound
from app.core.principal import Principal
from app.core.storage import DIR_MODE, SUBDIR_MODES, SUBDIRS
from app.db.migrate import MIGRATIONS_DIR, run_migrations
from app.main import create_app
from tests.conftest import make_settings, running
from tests.helpers import (
    NATIVE,
    PASSWORD,
    bootstrap_admin,
    create_user,
    delegation,
    identity,
    login,
    sql,
    step_up,
)

API = "/api/platform"


async def _headers(platform, username: str, role: str = "member") -> dict[str, str]:
    await create_user(platform, username, role=role)
    return await identity(platform, await login(platform, username))


def _personal(platform, username: str) -> dict:
    (row,) = sql(
        platform,
        "SELECT s.* FROM spaces s JOIN users u ON u.id = s.owner_user_id "
        "WHERE u.username = %s AND s.kind = 'personal'",
        (username,),
    )
    return row


def _members(platform, space_id) -> list[tuple[str, str]]:
    rows = sql(
        platform,
        "SELECT u.username, m.role FROM space_members m JOIN users u ON u.id = m.user_id "
        "WHERE m.space_id = %s ORDER BY u.username",
        (space_id,),
    )
    return [(r["username"], r["role"]) for r in rows]


async def _create_space(platform, headers, slug="family", name="Family") -> dict:
    response = await platform.client.post(
        f"{API}/spaces", json={"slug": slug, "name": name}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _add(platform, headers, space_id, user_id, role="editor"):
    return await platform.client.post(
        f"{API}/spaces/{space_id}/members",
        json={"user_id": str(user_id), "role": role},
        headers=headers,
    )


def _detail(response) -> tuple[int, str]:
    return response.status_code, response.json().get("detail")


# --- personal spaces -------------------------------------------------------------


async def test_every_creation_path_makes_a_personal_space(platform):
    token = await bootstrap_admin(platform, "root")
    await create_user(platform, "alice", display_name="Alice A")
    await step_up(platform, token)
    admin = await identity(platform, token)
    invite = (await platform.client.post(f"{API}/admin/invites", json={}, headers=admin)).json()
    response = await platform.client.post(
        "/api/auth/invite/accept",
        json={
            "token": invite["token"],
            "username": "bob",
            "display_name": "Bob",
            "password": PASSWORD,
        },
        headers=NATIVE,
    )
    assert response.status_code == 200

    gids = set()
    for username, name in (("root", "Root"), ("alice", "Alice A"), ("bob", "Bob")):
        space = _personal(platform, username)
        assert (space["slug"], space["name"], space["archived_at"]) == (username, name, None)
        assert space["gid"] >= 30000
        assert _members(platform, space["id"]) == [(username, "owner")]
        gids.add(space["gid"])
    assert len(gids) == 3


async def test_taken_username_leaves_no_space_behind(platform):
    await create_user(platform, "alice")
    before = sql(platform, "SELECT count(*) AS n FROM spaces")[0]["n"]
    with pytest.raises(Exception, match="username_taken"):
        await create_user(platform, "alice")
    assert sql(platform, "SELECT count(*) AS n FROM spaces")[0]["n"] == before


async def test_personal_slugs_are_collision_safe(platform):
    owner = await _headers(platform, "carol")
    await _create_space(platform, owner, slug="dave")
    for username in ("a.b", "a_b", "personal", "spaces", "dave"):
        await create_user(platform, username)
    slugs = {u: _personal(platform, u)["slug"] for u in ("a.b", "a_b", "personal", "spaces")}
    assert slugs == {"a.b": "a-b", "a_b": "a-b-2", "personal": "personal-2", "spaces": "spaces-2"}
    assert _personal(platform, "dave")["slug"] == "dave-2"


def test_personal_slug_candidates_fit_the_slug_rule():
    long_name = "x" * 32
    for candidate in list(spaces.personal_slug_candidates(long_name))[:5] + [
        next(spaces.personal_slug_candidates("a._-b"))
    ]:
        assert spaces.SLUG_RE.fullmatch(candidate), candidate


async def test_personal_space_never_gains_members(platform):
    alice = await _headers(platform, "alice")
    bob = await create_user(platform, "bob")
    (mine,) = (await platform.client.get(f"{API}/spaces", headers=alice)).json()["spaces"]
    assert (mine["kind"], mine["role"], mine["slug"]) == ("personal", "owner", "alice")
    me = mine["owner_user_id"]

    assert _detail(await _add(platform, alice, mine["id"], bob["id"])) == (409, "personal_space")
    for method, path, body in (
        ("PATCH", f"/members/{me}", {"role": "editor"}),
        ("DELETE", f"/members/{me}", None),
        ("DELETE", "", None),
    ):
        response = await platform.client.request(
            method, f"{API}/spaces/{mine['id']}{path}", json=body, headers=alice
        )
        assert _detail(response) == (409, "personal_space")

    # Renaming is fine.
    response = await platform.client.patch(
        f"{API}/spaces/{mine['id']}", json={"name": "My stuff"}, headers=alice
    )
    assert (response.status_code, response.json()["name"]) == (200, "My stuff")

    # The database refuses it too.
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(
            platform,
            "INSERT INTO space_members (space_id, user_id, role) VALUES (%s, %s, 'viewer')",
            (mine["id"], bob["id"]),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(platform, "UPDATE space_members SET role = 'viewer' WHERE space_id = %s", (mine["id"],))


async def test_user_row_with_a_personal_space_cannot_be_deleted(platform):
    await create_user(platform, "alice")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        sql(platform, "DELETE FROM users WHERE username = 'alice'")


# --- backfill --------------------------------------------------------------------


async def test_backfill_for_users_created_before_spaces(pg_database, tmp_path, chowns):
    """Users from before 0003 get a personal space (and its dirs) on the next start."""
    pre_spaces = tmp_path / "pre"
    pre_spaces.mkdir()
    for name in ("0001_init.sql", "0002_accounts.sql"):
        (pre_spaces / name).write_text((MIGRATIONS_DIR / name).read_text())
    async with await psycopg.AsyncConnection.connect(pg_database.dsn, autocommit=True) as conn:
        await run_migrations(conn, pre_spaces)
        for username in ("old.timer", "old_timer"):
            await conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash) "
                "VALUES (%s, %s, 'member', 'x')",
                (username, username.title()),
            )

    settings = make_settings(pg_database, tmp_path)
    for _ in range(2):
        async with running(create_app(settings)):
            pass

    with psycopg.connect(pg_database.dsn) as conn:
        rows = conn.execute(
            "SELECT u.username, s.slug, s.id, s.gid, "
            "(SELECT count(*) FROM space_members m WHERE m.space_id = s.id) "
            "FROM users u JOIN spaces s ON s.owner_user_id = u.id ORDER BY u.username"
        ).fetchall()
    assert [(r[0], r[1], r[4]) for r in rows] == [
        ("old.timer", "old-timer", 1),
        ("old_timer", "old-timer-2", 1),
    ]
    for _, _, space_id, gid, _ in rows:
        root = settings.platform_spaces_dir / str(space_id)
        for path, want in ((root, DIR_MODE), *((root / s, SUBDIR_MODES[s]) for s in SUBDIRS)):
            assert stat.S_IMODE(path.stat().st_mode) == want
            assert chowns[path] == (0, gid)


# --- storage -----------------------------------------------------------------------


async def test_space_dirs_created_setgid_and_group_owned(platform, chowns):
    headers = await _headers(platform, "alice")
    space = await _create_space(platform, headers)
    root = platform.app.state.storage.space_dir(UUID(space["id"]))
    assert sorted(p.name for p in root.iterdir()) == sorted(SUBDIRS)
    for path, want in ((root, 0o2770), *((root / sub, SUBDIR_MODES[sub]) for sub in SUBDIRS)):
        mode = path.stat().st_mode
        assert stat.S_ISDIR(mode) and stat.S_IMODE(mode) == want
        assert mode & stat.S_ISGID
        assert chowns[path] == (0, space["gid"])


async def test_startup_reconciles_missing_dirs_and_modes(platform, chowns):
    headers = await _headers(platform, "alice")
    space = await _create_space(platform, headers)
    storage = platform.app.state.storage
    root = storage.space_dir(UUID(space["id"]))
    (root / "files").rmdir()
    os.chmod(root / "apps", 0o755)
    os.chmod(root, 0o700)
    chowns.clear()

    async with running(create_app(platform.app.state.settings)):
        pass
    for path, want in ((root, DIR_MODE), (root / "files", DIR_MODE), (root / "apps", 0o2750)):
        assert stat.S_IMODE(path.stat().st_mode) == want
        assert chowns[path] == (0, space["gid"])


async def test_reconcile_refuses_symlinks(platform, tmp_path, chowns, caplog):
    headers = await _headers(platform, "alice")
    space = await _create_space(platform, headers)
    root = platform.app.state.storage.space_dir(UUID(space["id"]))
    target = tmp_path / "elsewhere"
    target.mkdir()
    (root / "files").rmdir()
    (root / "files").symlink_to(target)
    chowns.clear()

    ok, failed = platform.app.state.storage.reconcile([(UUID(space["id"]), space["gid"])])
    assert (ok, failed) == (0, 1)
    assert target not in chowns
    assert stat.S_IMODE(target.stat().st_mode) != DIR_MODE
    assert "storage reconcile failed" in caplog.text


# --- authorize_space ------------------------------------------------------------------


MATRIX = {
    # role: (read, write, manage) for act=user
    "owner": (True, True, True),
    "editor": (True, True, False),
    "viewer": (True, False, False),
}


@pytest.mark.parametrize("act", ["user", "agent"])
@pytest.mark.parametrize("role", ["owner", "editor", "viewer", None])
async def test_authorize_space_role_matrix(platform, role, act):
    owner = await create_user(platform, "owner")
    caller = await create_user(platform, "caller", role="admin")
    async with platform.app.state.db_pool.connection() as conn:
        space = await spaces.create_shared_space(
            conn, slug="s", name="S", owner_id=owner["id"], storage=platform.app.state.storage
        )
        if role is not None:
            await spaces.add_member(conn, space, caller["id"], role)

    # A stepped-up admin: the admin override must not leak into authorize_space.
    principal = Principal(
        user_id=caller["id"], session_id=uuid4(), username="caller", display_name="Caller",
        role="admin", act=act, stepped_up=True,
    )  # fmt: skip
    for need, allowed in zip(("read", "write", "manage"), MATRIX.get(role, (False,) * 3)):
        async with platform.app.state.db_pool.connection() as conn:
            if role is None:
                with pytest.raises(NotFound):
                    await spaces.authorize_space(conn, principal, space["id"], need)
            elif need == "manage" and act == "agent":
                with pytest.raises(Forbidden, match="agent_not_allowed"):
                    await spaces.authorize_space(conn, principal, space["id"], need)
            elif allowed:
                access = await spaces.authorize_space(conn, principal, space["id"], need)
                assert (access.space["id"], access.role) == (space["id"], role)
            else:
                with pytest.raises(Forbidden, match="insufficient_role"):
                    await spaces.authorize_space(conn, principal, space["id"], need)


async def test_authorize_space_hides_archived_and_unknown(platform):
    alice = await create_user(platform, "alice")
    principal = Principal(alice["id"], uuid4(), "alice", "Alice", "member", "user", False)
    async with platform.app.state.db_pool.connection() as conn:
        space = await spaces.create_shared_space(
            conn, slug="s", name="S", owner_id=alice["id"], storage=platform.app.state.storage
        )
        await spaces.archive_space(conn, space)
        for space_id in (space["id"], uuid4()):
            with pytest.raises(NotFound):
                await spaces.authorize_space(conn, principal, space_id, "read")


# --- spaces API ------------------------------------------------------------------------


async def test_create_list_get_rename_archive(platform):
    alice = await _headers(platform, "alice")
    bob = await _headers(platform, "bob")
    space = await _create_space(platform, alice, slug="  Family-Home ", name=" The Family ")
    assert (space["slug"], space["name"], space["kind"], space["role"]) == (
        "family-home",
        "The Family",
        "shared",
        "owner",
    )
    assert space["owner_user_id"] is None and space["archived_at"] is None
    listed = (await platform.client.get(f"{API}/spaces", headers=alice)).json()["spaces"]
    assert [(s["slug"], s["role"]) for s in listed] == [
        ("alice", "owner"),
        ("family-home", "owner"),
    ]

    url = f"{API}/spaces/{space['id']}"
    assert (await platform.client.get(url, headers=alice)).json()["role"] == "owner"
    assert _detail(await platform.client.get(url, headers=bob)) == (404, "not_found")
    assert _detail(await platform.client.patch(url, json={"name": "x"}, headers=bob)) == (
        404,
        "not_found",
    )
    assert _detail(await platform.client.delete(url, headers=bob)) == (404, "not_found")

    response = await platform.client.patch(url, json={"name": "Home"}, headers=alice)
    assert (response.status_code, response.json()["name"]) == (200, "Home")
    response = await platform.client.patch(url, json={"name": "  "}, headers=alice)
    assert _detail(response) == (422, "invalid_name")
    response = await platform.client.patch(url, json={"name": "Home", "slug": "new"}, headers=alice)
    assert (response.status_code, response.json()["slug"]) == (200, "family-home")

    assert (await platform.client.delete(url, headers=alice)).status_code == 204
    assert _detail(await platform.client.get(url, headers=alice)) == (404, "not_found")
    listed = (await platform.client.get(f"{API}/spaces", headers=alice)).json()["spaces"]
    assert [s["slug"] for s in listed] == ["alice"]
    # An archived space keeps its slug.
    response = await platform.client.post(
        f"{API}/spaces", json={"slug": "family-home", "name": "Again"}, headers=alice
    )
    assert _detail(response) == (409, "slug_taken")


@pytest.mark.parametrize(
    ("slug", "code"),
    [
        ("", "invalid_slug"),
        ("-leading", "invalid_slug"),
        ("under_score", "invalid_slug"),
        ("a" * 41, "invalid_slug"),
        ("personal", "reserved_slug"),
        ("Spaces", "reserved_slug"),
    ],
)
async def test_slug_rules(platform, slug, code):
    headers = await _headers(platform, "alice")
    response = await platform.client.post(
        f"{API}/spaces", json={"slug": slug, "name": "X"}, headers=headers
    )
    assert _detail(response) == (422, code)


async def test_slug_taken_includes_personal_slugs(platform):
    headers = await _headers(platform, "alice")
    await create_user(platform, "bob")
    response = await platform.client.post(
        f"{API}/spaces", json={"slug": "bob", "name": "X"}, headers=headers
    )
    assert _detail(response) == (409, "slug_taken")


async def test_agent_reads_but_never_manages(platform):
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    human = await identity(platform, token)
    agent = await delegation(platform, token)
    space = await _create_space(platform, human)
    url = f"{API}/spaces/{space['id']}"

    listed = (await platform.client.get(f"{API}/spaces", headers=agent)).json()["spaces"]
    assert {s["slug"] for s in listed} == {"alice", "family"}
    assert (await platform.client.get(url, headers=agent)).status_code == 200
    assert (await platform.client.get(f"{url}/members", headers=agent)).status_code == 200
    bob = await create_user(platform, "bob")
    for method, path, body in (
        ("PATCH", "", {"name": "Agent"}),
        ("DELETE", "", None),
        ("POST", "/members", {"user_id": str(bob["id"]), "role": "viewer"}),
        ("PATCH", f"/members/{bob['id']}", {"role": "viewer"}),
        ("DELETE", f"/members/{bob['id']}", None),
    ):
        response = await platform.client.request(method, url + path, json=body, headers=agent)
        assert _detail(response) == (403, "agent_not_allowed"), (method, path)


# --- members ---------------------------------------------------------------------------


async def test_member_lifecycle_and_roles(platform):
    alice = await _headers(platform, "alice")
    bob_user = await create_user(platform, "bob")
    bob = await identity(platform, await login(platform, "bob"))
    carol = await create_user(platform, "carol")
    space = await _create_space(platform, alice)
    members_url = f"{API}/spaces/{space['id']}/members"

    response = await _add(platform, alice, space["id"], bob_user["id"], "viewer")
    assert response.status_code == 201
    assert {k: response.json()[k] for k in ("username", "display_name", "role")} == {
        "username": "bob",
        "display_name": "Bob",
        "role": "viewer",
    }
    assert _detail(await _add(platform, alice, space["id"], bob_user["id"])) == (
        409,
        "already_member",
    )
    assert _detail(await _add(platform, alice, space["id"], uuid4())) == (422, "unknown_user")
    response = await platform.client.post(
        members_url, json={"user_id": str(carol["id"]), "role": "admin"}, headers=alice
    )
    assert response.status_code == 422

    # Bob (viewer) sees the space and its members but can't change anything.
    listed = (await platform.client.get(f"{API}/spaces", headers=bob)).json()["spaces"]
    assert [(s["slug"], s["role"]) for s in listed] == [("bob", "owner"), ("family", "viewer")]
    members = (await platform.client.get(members_url, headers=bob)).json()["members"]
    assert [(m["username"], m["role"]) for m in members] == [("alice", "owner"), ("bob", "viewer")]
    assert _detail(await _add(platform, bob, space["id"], carol["id"])) == (
        403,
        "insufficient_role",
    )
    response = await platform.client.patch(
        f"{API}/spaces/{space['id']}", json={"name": "Mine"}, headers=bob
    )
    assert _detail(response) == (403, "insufficient_role")

    # Promote to editor; still not a manager.
    response = await platform.client.patch(
        f"{members_url}/{bob_user['id']}", json={"role": "editor"}, headers=alice
    )
    assert (response.status_code, response.json()["role"]) == (200, "editor")
    assert (await platform.client.get(f"{API}/spaces/{space['id']}", headers=bob)).json()[
        "role"
    ] == "editor"
    assert _detail(await platform.client.delete(f"{members_url}/{carol['id']}", headers=bob)) == (
        403,
        "insufficient_role",
    )

    # Removal revokes access.
    response = await platform.client.delete(f"{members_url}/{bob_user['id']}", headers=alice)
    assert response.status_code == 204
    assert _detail(await platform.client.get(f"{API}/spaces/{space['id']}", headers=bob)) == (
        404,
        "not_found",
    )
    assert _detail(
        await platform.client.delete(f"{members_url}/{bob_user['id']}", headers=alice)
    ) == (
        404,
        "not_found",
    )


async def test_disabled_users_cannot_be_added(platform):
    alice = await _headers(platform, "alice")
    bob = await create_user(platform, "bob")
    sql(platform, "UPDATE users SET disabled_at = now() WHERE id = %s", (bob["id"],))
    space = await _create_space(platform, alice)
    assert _detail(await _add(platform, alice, space["id"], bob["id"])) == (409, "user_disabled")


async def test_never_remove_or_demote_the_last_owner(platform):
    alice_user = await create_user(platform, "alice")
    alice = await identity(platform, await login(platform, "alice"))
    bob_user = await create_user(platform, "bob")
    bob = await identity(platform, await login(platform, "bob"))
    space = await _create_space(platform, alice)
    members_url = f"{API}/spaces/{space['id']}/members"
    alice_url = f"{members_url}/{alice_user['id']}"

    assert _detail(await platform.client.delete(alice_url, headers=alice)) == (409, "last_owner")
    response = await platform.client.patch(alice_url, json={"role": "editor"}, headers=alice)
    assert _detail(response) == (409, "last_owner")

    assert (await _add(platform, alice, space["id"], bob_user["id"], "owner")).status_code == 201
    # With a second owner, alice can step down, and then bob is the last one.
    response = await platform.client.patch(alice_url, json={"role": "viewer"}, headers=alice)
    assert (response.status_code, response.json()["role"]) == (200, "viewer")
    bob_url = f"{members_url}/{bob_user['id']}"
    assert _detail(await platform.client.delete(bob_url, headers=bob)) == (409, "last_owner")
    assert (await platform.client.delete(alice_url, headers=bob)).status_code == 204
    assert _members(platform, space["id"]) == [("bob", "owner")]


# --- admin override -------------------------------------------------------------------


async def test_admin_manages_membership_but_not_data(platform):
    token = await bootstrap_admin(platform, "root")
    root = await identity(platform, token)
    alice_user = await create_user(platform, "alice")
    alice = await identity(platform, await login(platform, "alice"))
    bob = await create_user(platform, "bob")
    space = await _create_space(platform, alice)
    space_url = f"{API}/spaces/{space['id']}"
    members_url = f"{space_url}/members"

    # Not stepped up: nothing.
    assert _detail(await platform.client.get(members_url, headers=root)) == (404, "not_found")
    assert _detail(await platform.client.get(f"{API}/admin/spaces", headers=root)) == (
        403,
        "step_up_required",
    )

    await step_up(platform, token)
    root = await identity(platform, token)
    listed = (await platform.client.get(f"{API}/admin/spaces", headers=root)).json()["spaces"]
    assert [(s["slug"], s["kind"], s["role"]) for s in listed] == [
        ("root", "personal", "owner"),
        ("alice", "personal", None),
        ("bob", "personal", None),
        ("family", "shared", None),
    ]

    assert (await platform.client.get(members_url, headers=root)).status_code == 200
    assert (await _add(platform, root, space["id"], bob["id"], "viewer")).status_code == 201
    response = await platform.client.patch(
        f"{members_url}/{bob['id']}", json={"role": "owner"}, headers=root
    )
    assert response.status_code == 200
    assert (
        await platform.client.delete(f"{members_url}/{alice_user['id']}", headers=root)
    ).status_code == 204
    assert _members(platform, space["id"]) == [("bob", "owner")]
    response = await platform.client.delete(f"{members_url}/{bob['id']}", headers=root)
    assert _detail(response) == (409, "last_owner")

    # Membership management only: no reading, renaming, or archiving the space itself.
    assert _detail(await platform.client.get(space_url, headers=root)) == (404, "not_found")
    for method, body in (("PATCH", {"name": "Admin's"}), ("DELETE", None)):
        response = await platform.client.request(method, space_url, json=body, headers=root)
        assert _detail(response) == (404, "not_found")
    async with platform.app.state.db_pool.connection() as conn:
        principal = Principal(
            UUID(listed[0]["owner_user_id"]), uuid4(), "root", "Root", "admin", "user", True
        )
        with pytest.raises(NotFound):
            await spaces.authorize_space(conn, principal, UUID(space["id"]), "read")

    # Personal spaces stay single-member even for an admin.
    alice_personal = next(s for s in listed if s["slug"] == "alice")
    response = await _add(platform, root, alice_personal["id"], bob["id"])
    assert _detail(response) == (409, "personal_space")

    # The admin's agent gets no override.
    agent = await delegation(platform, token)
    assert _detail(await platform.client.get(members_url, headers=agent)) == (404, "not_found")


async def test_admin_list_includes_archived(platform):
    token = await bootstrap_admin(platform, "root")
    await step_up(platform, token)
    root = await identity(platform, token)
    space = await _create_space(platform, root)
    await platform.client.delete(f"{API}/spaces/{space['id']}", headers=root)
    listed = (await platform.client.get(f"{API}/admin/spaces", headers=root)).json()["spaces"]
    archived = next(s for s in listed if s["id"] == space["id"])
    assert archived["archived_at"] is not None
    response = await platform.client.get(f"{API}/spaces/{space['id']}/members", headers=root)
    assert _detail(response) == (404, "not_found")


# --- directory ------------------------------------------------------------------------


async def test_directory_lists_enabled_users_identity_only(platform):
    await create_user(platform, "zed", display_name="Zed")
    carl = await create_user(platform, "carl")
    sql(platform, "UPDATE users SET disabled_at = now() WHERE id = %s", (carl["id"],))
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    response = await platform.client.get(
        f"{API}/users/directory", headers=await identity(platform, token)
    )
    assert response.status_code == 200
    users = response.json()["users"]
    assert [u["username"] for u in users] == ["alice", "zed"]
    assert set(users[0]) == {"id", "username", "display_name"}
    agent = await delegation(platform, token)
    response = await platform.client.get(f"{API}/users/directory", headers=agent)
    assert _detail(response) == (403, "agent_not_allowed")
    assert _detail(await platform.client.get(f"{API}/users/directory")) == (401, "unauthenticated")
