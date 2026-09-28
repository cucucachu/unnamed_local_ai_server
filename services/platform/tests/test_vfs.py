"""`app.core.vfs`: the virtual-path guard, and `app.core.beneath` under it.

The first half ports agent-server's `tests/test_paths.py` (the
`resolve_files_path` suite) onto `beneath.locate`, the containment step;
the rest exercises `resolve_virtual_path` end to end - spaces, membership,
roles, and symlinks that point into another space.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from app.core import beneath, vfs
from app.core.errors import Forbidden, InvalidInput, NotFound
from app.core.principal import Principal
from tests.files_world import World

# --- parse / locate (ported from agent-server's resolve_files_path suite) ---------


def _where(root: Path, path: str) -> Path:
    """The real location `path` names below `root`, through `beneath.locate`."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        dir_fd, name = beneath.locate(beneath.Root(fd, str(root)), path.split("/"))
    finally:
        os.close(fd)
    try:
        where = Path(os.readlink(f"/proc/self/fd/{dir_fd}"))
    finally:
        os.close(dir_fd)
    return where if name is None else where / name


def _refused(root: Path, path: str, code: int = errno.EXDEV) -> None:
    with pytest.raises(OSError) as exc:
        _where(root, path)
    assert exc.value.errno == code


def test_empty_string_resolves_to_root(tmp_path: Path) -> None:
    assert _where(tmp_path.resolve(), "") == tmp_path.resolve()


def test_plain_relative_path_resolves_under_root(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "a").mkdir()
    assert _where(root, "a/b.txt") == root / "a" / "b.txt"


def test_missing_nested_path_is_not_an_escape(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _where(tmp_path.resolve(), "does/not/exist/yet.txt")


@pytest.mark.parametrize("path", ["../x", "a/../../x", "/personal/../x", "a/.."])
def test_dotdot_is_rejected(path: str) -> None:
    with pytest.raises(InvalidInput, match="invalid_path"):
        vfs.parse(path)


def test_absolute_path_stays_relative_to_the_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _where(tmp_path.resolve(), "/etc/passwd")


def test_nested_dotdot_escape_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    _refused(tmp_path.resolve(), "a/../../x")


def test_symlink_escaping_root_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "escape_link").symlink_to("/tmp")
    _refused(tmp_path.resolve(), "escape_link/x")


def test_symlink_to_the_link_itself_is_also_rejected(tmp_path: Path) -> None:
    (tmp_path / "escape_link").symlink_to("/tmp")
    _refused(tmp_path.resolve(), "escape_link")


def test_relative_symlink_climbing_out_is_rejected(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "root"
    (root / "a").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (root / "a" / "up").symlink_to("../../outside")
    _refused(root, "a/up/x")


def test_symlink_staying_inside_root_is_allowed(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "real").mkdir()
    (root / "link").symlink_to(root / "real")
    (root / "rel").symlink_to("real/../real")
    assert _where(root, "link/x.txt") == root / "real" / "x.txt"
    assert _where(root, "rel/x.txt") == root / "real" / "x.txt"


def test_absolute_symlink_must_name_the_root_exactly(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "root"
    root.mkdir()
    (tmp_path / "root-other").mkdir()
    (root / "sibling").symlink_to(tmp_path.resolve() / "root-other")
    (root / "spelled").symlink_to(f"{tmp_path.resolve()}/root/../root-other")
    _refused(root, "sibling/x")
    _refused(root, "spelled/x")


def test_symlink_loop_is_rejected(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "a").symlink_to(root / "b")
    (root / "b").symlink_to(root / "a")
    _refused(root, "a/x", errno.ELOOP)


def test_follow_false_names_a_final_link_but_checks_the_rest(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / "out").symlink_to("/etc")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        r = beneath.Root(fd, str(root))
        with beneath.parent(r, ["out"]) as (_, name):
            assert name == "out"
        with pytest.raises(OSError) as exc, beneath.parent(r, ["out", "passwd"]):
            pass
        assert exc.value.errno == errno.EXDEV
    finally:
        os.close(fd)


@pytest.mark.parametrize("path", ["a\x00b", "/personal/a\x00b"])
def test_null_byte_is_rejected(path: str) -> None:
    with pytest.raises(InvalidInput):
        vfs.parse(path)


def test_parse_normalizes() -> None:
    assert vfs.parse("") == ()
    assert vfs.parse("/") == ()
    assert vfs.parse("personal//./a/") == ("personal", "a")
    assert vfs.join("personal", "a") == "/personal/a"
    assert vfs.join() == "/"


# --- resolve_virtual_path --------------------------------------------------------


async def _principal(world: World, username: str, act: str = "user") -> Principal:
    user = world.users[username]
    return Principal(
        user_id=user["id"],
        session_id=user["id"],
        username=username,
        display_name=username,
        role="member",
        act=act,
        stepped_up=False,
        uid=user["uid"],
    )


async def _resolve(world: World, username: str, vpath: str, need="read", **kw):
    principal = await _principal(world, username, kw.pop("act", "user"))
    async with world.platform.app.state.db_pool.connection() as conn:
        return await vfs.resolve_virtual_path(
            conn, principal, world.platform.app.state.storage, vpath, need, **kw
        )


async def test_synthetic_roots_are_read_only(world: World) -> None:
    root = await _resolve(world, "dave", "/")
    assert (root.kind, root.vpath, root.writable) == ("root", "/", False)
    spaces = await _resolve(world, "dave", "spaces/")
    assert (spaces.kind, spaces.vpath) == ("spaces", "/spaces")
    for path in ("/", "/spaces"):
        with pytest.raises(Forbidden, match="read_only"):
            await _resolve(world, "alice", path, "write")


async def test_personal_is_the_callers_own(world: World) -> None:
    for username in ("alice", "dave"):
        r = await _resolve(world, username, "/personal/notes/a.txt")
        assert r.rel == ("notes", "a.txt")
        assert (r.vpath, r.role, r.writable) == ("/personal/notes/a.txt", "owner", True)
        assert r.space["id"] == world.personal(username)["id"]


async def test_shared_space_by_slug_and_role(world: World) -> None:
    for username, role in (("alice", "owner"), ("bob", "editor"), ("carol", "viewer")):
        r = await _resolve(world, username, "/spaces/Family/x")
        assert r.vpath == "/spaces/family/x"
        assert (r.rel, str(r.space["id"])) == (("x",), world.family["id"])
        assert (r.role, r.writable) == (role, role != "viewer")
    with pytest.raises(Forbidden, match="insufficient_role"):
        await _resolve(world, "carol", "/spaces/family/x", "write")


async def test_non_member_is_indistinguishable_from_missing(world: World) -> None:
    errors = []
    for path in ("/spaces/family", "/spaces/family/x", "/spaces/nosuch/x"):
        with pytest.raises(NotFound) as exc:
            await _resolve(world, "dave", path)
        errors.append(str(exc.value))
    assert len(set(errors)) == 1


async def test_personal_space_slug_is_not_addressable(world: World) -> None:
    slug = world.personal("alice")["slug"]
    with pytest.raises(NotFound):
        await _resolve(world, "alice", f"/spaces/{slug}")


@pytest.mark.parametrize("path", ["/etc/passwd", "/files/x", "/data"])
async def test_unknown_top_level_is_404(world: World, path: str) -> None:
    with pytest.raises(NotFound):
        await _resolve(world, "alice", path)


async def test_symlink_into_another_space_is_refused(world: World) -> None:
    """Even when the caller can read the target space: every path stays in its own space."""
    (world.family_root / "doc.txt").write_text("family")
    home = world.home("alice")
    (home / "family").symlink_to(world.family_root)
    (home / "doc").symlink_to(world.family_root / "doc.txt")
    for path in ("/personal/family/doc.txt", "/personal/family", "/personal/doc"):
        with pytest.raises(InvalidInput, match="invalid_path"):
            await _resolve(world, "alice", path)


async def test_symlink_into_a_space_the_caller_cannot_see(world: World) -> None:
    (world.family_root / "secret.txt").write_text("family only")
    (world.home("dave") / "peek").symlink_to(world.family_root / "secret.txt")
    with pytest.raises(InvalidInput):
        await _resolve(world, "dave", "/personal/peek")


async def test_symlink_to_other_users_personal_space(world: World) -> None:
    (world.home("bob") / "alice").symlink_to(world.home("alice"))
    with pytest.raises(InvalidInput):
        await _resolve(world, "bob", "/personal/alice/x")


async def test_no_follow_names_the_link_itself(world: World) -> None:
    home = world.home("alice")
    (home / "out").symlink_to("/etc")
    r = await _resolve(world, "alice", "/personal/out", "write", follow=False)
    assert r.rel == ("out",)
    with pytest.raises(InvalidInput):
        await _resolve(world, "alice", "/personal/out/passwd", "write", follow=False)


async def test_swapped_files_dir_is_refused(world: World) -> None:
    """A group member able to write the space dir can't swap `files/` for a link."""
    space_dir = world.platform.app.state.storage.space_dir(world.personal("dave")["id"])
    (space_dir / "files").rename(space_dir / "files.orig")
    (space_dir / "files").symlink_to(world.family_root)
    with pytest.raises(NotFound):
        await _resolve(world, "dave", "/personal/x")


async def test_agent_gets_the_users_roles(world: World) -> None:
    r = await _resolve(world, "bob", "/spaces/family/x", "write", act="agent")
    assert r.role == "editor"
    with pytest.raises(Forbidden):
        await _resolve(world, "carol", "/spaces/family/x", "write", act="agent")
