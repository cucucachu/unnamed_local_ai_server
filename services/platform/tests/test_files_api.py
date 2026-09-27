"""`/api/platform/files*`: the file manager routes over virtual paths.

Ports agent-server's `tests/test_files_rest.py` onto `/personal`, then adds
what spaces bring: the synthetic `/` and `/spaces` listings, the role
matrix for every operation, cross-space move/copy, and who owns what gets
created (recorded by the `chowns` fixture; `test_fsops.py` checks it for
real as root).
"""

from __future__ import annotations

import os
import stat

import pytest

from tests.files_world import FILES, ROLES, World


async def _get(world: World, user: str, path: str, route: str = "", **params):
    return await world.client.get(
        f"{FILES}{route}", params={"path": path, **params}, headers=world.headers[user]
    )


async def _post(world: World, user: str, route: str, **body):
    return await world.client.post(f"{FILES}{route}", json=body, headers=world.headers[user])


async def _upload(world: World, user: str, path: str, *files: tuple[str, bytes]):
    return await world.client.post(
        f"{FILES}/upload",
        data={"path": path},
        files=[("file", (name, data, "application/octet-stream")) for name, data in files],
        headers=world.headers[user],
    )


# --- the synthetic tree ------------------------------------------------------------


async def test_root_lists_personal_and_spaces(world: World) -> None:
    response = await _get(world, "alice", "/")
    assert response.status_code == 200
    body = response.json()
    assert (body["path"], body["role"], body["writable"]) == ("/", None, False)
    entries = [(e["name"], e["path"], e["type"], e["label"]) for e in body["entries"]]
    assert entries == [
        ("personal", "/personal", "dir", "Personal"),
        ("spaces", "/spaces", "dir", "Shared spaces"),
    ]
    assert body["entries"][0]["role"] == "owner"


async def test_spaces_lists_the_callers_shared_spaces(world: World) -> None:
    for user, role in (("alice", "owner"), ("bob", "editor"), ("carol", "viewer")):
        body = (await _get(world, user, "/spaces")).json()
        assert [(e["name"], e["path"], e["label"], e["role"]) for e in body["entries"]] == [
            ("family", "/spaces/family", "Family", role)
        ]
    assert (await _get(world, "dave", "/spaces")).json()["entries"] == []


async def test_space_listing_reports_role(world: World) -> None:
    for user, writable in (("alice", True), ("bob", True), ("carol", False)):
        body = (await _get(world, user, "/spaces/family/")).json()
        assert (body["path"], body["writable"], body["space_label"]) == (
            "/spaces/family", writable, "Family",
        )  # fmt: skip


async def test_stat(world: World) -> None:
    (world.home("alice") / "a.txt").write_text("hello")
    body = (await _get(world, "alice", "/personal/a.txt", "/stat")).json()
    assert body["entry"]["path"] == "/personal/a.txt"
    assert (body["entry"]["type"], body["entry"]["size"], body["writable"]) == ("file", 5, True)
    body = (await _get(world, "carol", "/spaces/family", "/stat")).json()
    assert (body["entry"]["label"], body["role"], body["writable"]) == ("Family", "viewer", False)
    body = (await _get(world, "carol", "/", "/stat")).json()
    assert (body["entry"]["type"], body["writable"]) == ("dir", False)
    assert (await _get(world, "alice", "/personal/nope", "/stat")).status_code == 404


# --- list (ported) ----------------------------------------------------------------------


async def test_list_empty_personal(world: World) -> None:
    response = await _get(world, "alice", "/personal")
    assert response.status_code == 200
    assert response.json() == {
        "path": "/personal",
        "entries": [],
        "role": "owner",
        "writable": True,
        "space_label": "Personal",
    }


async def test_list_nested(world: World) -> None:
    home = world.home("alice")
    (home / "sub").mkdir()
    (home / "sub" / "nested.txt").write_text("hi")
    (home / "top.txt").write_text("top")

    names = {e["name"] for e in (await _get(world, "alice", "personal")).json()["entries"]}
    assert names == {"sub", "top.txt"}

    body = (await _get(world, "alice", "/personal/sub")).json()
    assert body["path"] == "/personal/sub"
    (entry,) = body["entries"]
    assert entry | {"mtime": None} == {
        "name": "nested.txt",
        "path": "/personal/sub/nested.txt",
        "type": "file",
        "size": 2,
        "mtime": None,
        "mime": "text/plain",
        "label": None,
        "role": None,
    }


async def test_list_sort_order_dirs_first_case_insensitive(world: World) -> None:
    home = world.home("alice")
    (home / "Zdir").mkdir()
    (home / "adir").mkdir()
    (home / "Bfile.txt").write_text("b")
    (home / "afile.txt").write_text("a")
    names = [e["name"] for e in (await _get(world, "alice", "/personal")).json()["entries"]]
    assert names == ["adir", "Zdir", "afile.txt", "Bfile.txt"]


async def test_list_missing_dir_is_404(world: World) -> None:
    response = await _get(world, "alice", "/personal/nope")
    assert response.status_code == 404
    assert response.json() == {"detail": "not_found"}


async def test_list_dir_entry_has_zero_size_and_symlink_is_a_file(world: World) -> None:
    home = world.home("alice")
    (home / "sub").mkdir()
    (home / "sub" / "x.txt").write_text("some content")
    (home / "link").symlink_to("/etc")
    entries = {e["name"]: e for e in (await _get(world, "alice", "/personal")).json()["entries"]}
    assert (entries["sub"]["type"], entries["sub"]["size"]) == ("dir", 0)
    assert entries["link"]["type"] == "file"


# --- upload (ported) ----------------------------------------------------------------------


async def test_upload_single_file(world: World) -> None:
    response = await _upload(world, "alice", "/personal", ("a.txt", b"hello"))
    assert response.status_code == 201
    assert response.json() == {"uploaded": ["/personal/a.txt"]}
    assert (world.home("alice") / "a.txt").read_bytes() == b"hello"


async def test_upload_multiple_files(world: World) -> None:
    (world.home("alice") / "sub").mkdir()
    response = await _upload(world, "alice", "/personal/sub", ("a.txt", b"aaa"), ("b.txt", b"bbb"))
    assert response.status_code == 201
    assert set(response.json()["uploaded"]) == {"/personal/sub/a.txt", "/personal/sub/b.txt"}
    assert (world.home("alice") / "sub" / "b.txt").read_bytes() == b"bbb"


async def test_upload_overwrites_existing_file(world: World) -> None:
    (world.home("alice") / "a.txt").write_bytes(b"old")
    response = await _upload(world, "alice", "/personal", ("a.txt", b"new-content"))
    assert response.status_code == 201
    assert (world.home("alice") / "a.txt").read_bytes() == b"new-content"


async def test_upload_to_missing_dir_is_404(world: World) -> None:
    response = await _upload(world, "alice", "/personal/nope", ("a.txt", b"hello"))
    assert response.status_code == 404


async def test_upload_dotdot_filename_lands_as_basename(world: World) -> None:
    home = world.home("alice")
    (home / "sub").mkdir()
    response = await _upload(world, "alice", "/personal/sub", ("../../evil.txt", b"pwned"))
    assert response.status_code == 201
    assert response.json() == {"uploaded": ["/personal/sub/evil.txt"]}
    assert (home / "sub" / "evil.txt").read_bytes() == b"pwned"
    assert not (home.parent / "evil.txt").exists()


@pytest.mark.parametrize("name", ["..", "."])
async def test_upload_bad_filename_is_422(world: World, name: str) -> None:
    response = await _upload(world, "alice", "/personal", (name, b"x"))
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_filename"}


async def test_upload_does_not_write_through_a_symlink(world: World) -> None:
    home = world.home("alice")
    (home / "victim.txt").write_text("keep")
    (home / "a.txt").symlink_to(home / "victim.txt")
    response = await _upload(world, "alice", "/personal", ("a.txt", b"new"))
    assert response.status_code == 201
    assert (home / "victim.txt").read_text() == "new"  # link resolved inside the space
    (home / "b.txt").symlink_to(world.family_root / "x.txt")
    response = await _upload(world, "alice", "/personal", ("b.txt", b"new"))
    assert response.status_code == 422
    assert not (world.family_root / "x.txt").exists()


async def test_upload_large_file_streams_without_full_buffering(world: World) -> None:
    payload = os.urandom(3 * 1024 * 1024 + 17)
    response = await _upload(world, "alice", "/personal", ("big.bin", payload))
    assert response.status_code == 201
    assert (world.home("alice") / "big.bin").read_bytes() == payload


# --- download (ported) ------------------------------------------------------------------


async def test_download_file_ok(world: World) -> None:
    (world.home("alice") / "a.txt").write_bytes(b"downloadable")
    response = await _get(world, "alice", "/personal/a.txt", "/download")
    assert response.status_code == 200
    assert response.content == b"downloadable"
    assert response.headers["content-disposition"] == 'attachment; filename="a.txt"'


async def test_download_dir_and_missing_are_404(world: World) -> None:
    (world.home("alice") / "sub").mkdir()
    for path in ("/personal/sub", "/personal/nope.txt", "/personal", "/"):
        assert (await _get(world, "alice", path, "/download")).status_code == 404


# --- mkdir (ported) --------------------------------------------------------------------


async def test_mkdir_nested_creates_parents(world: World) -> None:
    response = await _post(world, "alice", "/mkdir", path="/personal/a/b/c")
    assert response.status_code == 201
    assert response.json() == {"path": "/personal/a/b/c"}
    assert (world.home("alice") / "a" / "b" / "c").is_dir()


async def test_mkdir_existing_dir_is_idempotent(world: World) -> None:
    (world.home("alice") / "a").mkdir()
    assert (await _post(world, "alice", "/mkdir", path="/personal/a")).status_code == 201


async def test_mkdir_conflicts_with_existing_file(world: World) -> None:
    (world.home("alice") / "a").write_text("i am a file")
    response = await _post(world, "alice", "/mkdir", path="/personal/a")
    assert response.status_code == 409
    assert response.json() == {"detail": "already_exists"}


# --- move / rename / copy (ported) --------------------------------------------------------


async def test_move_rename_semantics(world: World) -> None:
    home = world.home("alice")
    (home / "old.txt").write_text("content")
    response = await _post(
        world, "alice", "/move", src="/personal/old.txt", dst="/personal/new.txt"
    )
    assert response.status_code == 200
    assert response.json() == {"src": "/personal/old.txt", "dst": "/personal/new.txt"}
    assert not (home / "old.txt").exists()
    assert (home / "new.txt").read_text() == "content"


async def test_move_to_existing_dst_is_409(world: World) -> None:
    home = world.home("alice")
    (home / "src.txt").write_text("a")
    (home / "dst.txt").write_text("b")
    response = await _post(
        world, "alice", "/move", src="/personal/src.txt", dst="/personal/dst.txt"
    )
    assert response.status_code == 409
    assert (home / "src.txt").exists()


async def test_move_missing_src_is_404(world: World) -> None:
    response = await _post(world, "alice", "/move", src="/personal/nope", dst="/personal/new")
    assert response.status_code == 404


async def test_move_dst_parent_missing(world: World) -> None:
    (world.home("alice") / "src.txt").write_text("a")
    response = await _post(
        world, "alice", "/move", src="/personal/src.txt", dst="/personal/nosuchdir/dst.txt"
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "parent_not_found"}


async def test_move_dir_into_itself_is_422(world: World) -> None:
    home = world.home("alice")
    (home / "a" / "sub").mkdir(parents=True)
    response = await _post(world, "alice", "/move", src="/personal/a", dst="/personal/a/sub/moved")
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_destination"}
    assert (home / "a").is_dir()


async def test_move_symlink_moves_the_link(world: World) -> None:
    home = world.home("alice")
    (home / "out").symlink_to("/etc")
    response = await _post(world, "alice", "/move", src="/personal/out", dst="/personal/out2")
    assert response.status_code == 200
    assert (home / "out2").is_symlink() and os.readlink(home / "out2") == "/etc"


async def test_space_roots_cannot_be_moved_or_replaced(world: World) -> None:
    (world.home("alice") / "a.txt").write_text("x")
    for src, dst in (
        ("/personal", "/personal/x"),
        ("/spaces/family", "/personal/family"),
        ("/personal/a.txt", "/spaces/family"),
    ):
        response = await _post(world, "alice", "/move", src=src, dst=dst)
        assert response.status_code in (403, 409), (src, dst, response.text)
    response = await _post(world, "alice", "/move", src="/personal", dst="/personal/x")
    assert response.json() == {"detail": "read_only"}


async def test_rename(world: World) -> None:
    home = world.home("alice")
    (home / "sub").mkdir()
    (home / "sub" / "a.txt").write_text("x")
    response = await _post(world, "alice", "/rename", path="/personal/sub/a.txt", name="b.txt")
    assert response.status_code == 200
    assert response.json()["dst"] == "/personal/sub/b.txt"
    assert (home / "sub" / "b.txt").exists()
    for bad in ("", "..", "a/b", "."):
        response = await _post(world, "alice", "/rename", path="/personal/sub/b.txt", name=bad)
        assert response.status_code == 422, bad


async def test_copy_dir_recursive(world: World) -> None:
    home = world.home("alice")
    (home / "src" / "nested").mkdir(parents=True)
    (home / "src" / "nested" / "f.txt").write_text("deep")
    (home / "src" / "link").symlink_to("/etc/passwd")
    response = await _post(world, "alice", "/copy", src="/personal/src", dst="/personal/dst")
    assert response.status_code == 200
    assert (home / "src" / "nested" / "f.txt").exists()
    assert (home / "dst" / "nested" / "f.txt").read_text() == "deep"
    assert os.readlink(home / "dst" / "link") == "/etc/passwd"  # recreated, not followed


async def test_copy_file_and_errors(world: World) -> None:
    home = world.home("alice")
    (home / "src.txt").write_text("copy me")
    (home / "dst.txt").write_text("b")
    ok = await _post(world, "alice", "/copy", src="/personal/src.txt", dst="/personal/copy.txt")
    assert ok.status_code == 200
    assert (home / "copy.txt").read_text() == "copy me"
    exists = await _post(world, "alice", "/copy", src="/personal/src.txt", dst="/personal/dst.txt")
    assert exists.status_code == 409
    missing = await _post(world, "alice", "/copy", src="/personal/nope", dst="/personal/new")
    assert missing.status_code == 404


async def test_copy_a_whole_space(world: World) -> None:
    (world.family_root / "a.txt").write_text("fam")
    response = await _post(world, "alice", "/copy", src="/spaces/family", dst="/personal/backup")
    assert response.status_code == 200
    assert (world.home("alice") / "backup" / "a.txt").read_text() == "fam"


# --- delete (ported) --------------------------------------------------------------------


async def test_delete_file_and_dir(world: World) -> None:
    home = world.home("alice")
    (home / "a.txt").write_text("bye")
    (home / "sub" / "nested").mkdir(parents=True)
    (home / "sub" / "nested" / "f.txt").write_text("x")
    for path in ("/personal/a.txt", "/personal/sub"):
        response = await world.client.delete(
            FILES, params={"path": path}, headers=world.headers["alice"]
        )
        assert response.status_code == 204
    assert list(home.iterdir()) == []


async def test_delete_space_root_and_synthetic_are_403(world: World) -> None:
    (world.home("alice") / "keep.txt").write_text("still here")
    for path in ("/personal", "", "/spaces", "/spaces/family"):
        response = await world.client.delete(
            FILES, params={"path": path}, headers=world.headers["alice"]
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "read_only"}
    assert (world.home("alice") / "keep.txt").exists()


async def test_delete_missing_is_404_and_symlink_removes_link(world: World) -> None:
    home = world.home("alice")
    (home / "target.txt").write_text("stays")
    (home / "link").symlink_to(home / "target.txt")
    headers = world.headers["alice"]
    missing = await world.client.delete(FILES, params={"path": "/personal/nope"}, headers=headers)
    assert missing.status_code == 404
    gone = await world.client.delete(FILES, params={"path": "/personal/link"}, headers=headers)
    assert gone.status_code == 204
    assert (home / "target.txt").read_text() == "stays"


# --- traversal guard suite: every path-taking route --------------------------------------

GUARD_CASES = ["dotdot", "nested_dotdot", "null_byte", "symlink", "cross_space_symlink"]


def _bad_path(case: str, world: World) -> str:
    home = world.home("alice")
    if case == "dotdot":
        return "/personal/../x"
    if case == "nested_dotdot":
        return "/personal/a/../../x"
    if case == "null_byte":
        return "/personal/a\x00b"
    if case == "symlink":
        if not os.path.lexists(home / "escape_link"):
            (home / "escape_link").symlink_to("/tmp")
        return "/personal/escape_link/x"
    if case == "cross_space_symlink":
        if not os.path.lexists(home / "fam"):
            (home / "fam").symlink_to(world.family_root)
        return "/personal/fam/x"
    raise ValueError(case)  # pragma: no cover


def _guard_requests(world: World, bad: str):
    (world.home("alice") / "ok.txt").write_text("x")
    ok = "/personal/ok.txt"
    upload = [("file", ("a.txt", b"x", "text/plain"))]
    return [
        ("GET", "", {"params": {"path": bad}}),
        ("GET", "/stat", {"params": {"path": bad}}),
        ("GET", "/download", {"params": {"path": bad}}),
        ("GET", "/stream", {"params": {"path": bad}}),
        ("GET", "/thumbnail", {"params": {"path": bad}}),
        ("POST", "/upload", {"data": {"path": bad}, "files": upload}),
        ("POST", "/mkdir", {"json": {"path": bad}}),
        ("POST", "/move", {"json": {"src": bad, "dst": "/personal/new"}}),
        ("POST", "/move", {"json": {"src": ok, "dst": bad}}),
        ("POST", "/copy", {"json": {"src": bad, "dst": "/personal/new"}}),
        ("POST", "/copy", {"json": {"src": ok, "dst": bad}}),
        ("POST", "/rename", {"json": {"path": bad, "name": "y"}}),
        ("DELETE", "", {"params": {"path": bad}}),
        ("POST", "/read", {"json": {"path": bad}}),
        ("POST", "/write", {"json": {"path": bad, "content": "x"}}),
        ("PUT", "/content", {"params": {"path": bad}, "content": b"x"}),
        ("POST", "/edit", {"json": {"path": bad, "old_string": "a", "new_string": "b"}}),
        ("POST", "/grep", {"json": {"pattern": "x", "path": bad}}),
        ("POST", "/glob", {"json": {"pattern": "*", "path": bad}}),
    ]


@pytest.mark.parametrize("case", GUARD_CASES)
async def test_guard_on_every_route(world: World, case: str) -> None:
    bad = _bad_path(case, world)
    for method, route, kw in _guard_requests(world, bad):
        response = await world.client.request(
            method, f"{FILES}{route}", headers=world.headers["alice"], **kw
        )
        assert response.status_code == 422, (case, method, route, response.text)
        assert response.json()["detail"] == "invalid_path"
    assert not (world.family_root / "x").exists()


@pytest.mark.parametrize("path", ["/etc/passwd", "/files/a.txt", "/spaces/nope/a.txt"])
async def test_unknown_roots_are_404(world: World, path: str) -> None:
    assert (await _get(world, "alice", path)).status_code == 404
    assert (await _post(world, "alice", "/mkdir", path=path)).status_code == 404


# --- role matrix: every operation × owner/editor/viewer/non-member -----------------------

FAMILY = "/spaces/family"
READ_OPS = {
    "list": ("GET", "", {"params": {"path": FAMILY}}, 200),
    "stat": ("GET", "/stat", {"params": {"path": f"{FAMILY}/a.txt"}}, 200),
    "download": ("GET", "/download", {"params": {"path": f"{FAMILY}/a.txt"}}, 200),
    "stream": ("GET", "/stream", {"params": {"path": f"{FAMILY}/a.txt"}}, 200),
    "thumbnail": ("GET", "/thumbnail", {"params": {"path": f"{FAMILY}/a.txt"}}, 415),
    "read": ("POST", "/read", {"json": {"path": f"{FAMILY}/a.txt"}}, 200),
    "grep": ("POST", "/grep", {"json": {"pattern": "hello", "path": FAMILY}}, 200),
    "glob": ("POST", "/glob", {"json": {"pattern": "*.txt", "path": FAMILY}}, 200),
}
WRITE_OPS = {
    "upload": (
        "POST", "/upload", {"data": {"path": FAMILY}, "files": [("file", ("u.txt", b"u"))]}, 201,
    ),
    "mkdir": ("POST", "/mkdir", {"json": {"path": f"{FAMILY}/d"}}, 201),
    "write": ("POST", "/write", {"json": {"path": f"{FAMILY}/w.txt", "content": "w"}}, 200),
    "content": ("PUT", "/content", {"params": {"path": f"{FAMILY}/c.bin"}, "content": b"c"}, 200),
    "edit": (
        "POST", "/edit",
        {"json": {"path": f"{FAMILY}/a.txt", "old_string": "hello", "new_string": "bye"}}, 200,
    ),
    "move": ("POST", "/move", {"json": {"src": f"{FAMILY}/a.txt", "dst": f"{FAMILY}/m.txt"}}, 200),
    "rename": ("POST", "/rename", {"json": {"path": f"{FAMILY}/a.txt", "name": "r.txt"}}, 200),
    "copy": ("POST", "/copy", {"json": {"src": f"{FAMILY}/a.txt", "dst": f"{FAMILY}/k.txt"}}, 200),
    "delete": ("DELETE", "", {"params": {"path": f"{FAMILY}/a.txt"}}, 204),
}  # fmt: skip
EXPECTED = {
    "owner": (True, True),
    "editor": (True, True),
    "viewer": (True, False),
    None: (False, False),
}


@pytest.mark.parametrize("agent", [False, True], ids=["user", "agent"])
@pytest.mark.parametrize("op", [*READ_OPS, *WRITE_OPS])
async def test_role_matrix(world: World, op: str, agent: bool) -> None:
    method, route, kw, ok_status = READ_OPS.get(op) or WRITE_OPS[op]
    is_write = op in WRITE_OPS
    for user in ("dave", "carol", "bob", "alice"):
        (world.family_root / "a.txt").write_text("hello")
        headers = await world.agent(user) if agent else world.headers[user]
        response = await world.client.request(method, f"{FILES}{route}", headers=headers, **kw)
        can_read, can_write = EXPECTED[ROLES[user]]
        if not can_read:
            expected, detail = 404, "not_found"
        elif is_write and not can_write:
            expected, detail = 403, "insufficient_role"
        else:
            expected, detail = ok_status, None
        assert response.status_code == expected, (user, op, response.text)
        if detail:
            assert response.json() == {"detail": detail}
        for leftover in ("m.txt", "r.txt", "k.txt"):
            if (world.family_root / leftover).exists():
                (world.family_root / leftover).unlink()


# --- cross-space move / copy ------------------------------------------------------------


async def test_cross_space_move_needs_write_on_both(world: World) -> None:
    (world.family_root / "fam.txt").write_text("fam")
    (world.home("carol") / "mine.txt").write_text("mine")
    # viewer: can't move out of, or into, the shared space
    out = await _post(world, "carol", "/move", src=f"{FAMILY}/fam.txt", dst="/personal/fam.txt")
    assert (out.status_code, out.json()) == (403, {"detail": "insufficient_role"})
    into = await _post(world, "carol", "/move", src="/personal/mine.txt", dst=f"{FAMILY}/mine.txt")
    assert (into.status_code, into.json()) == (403, {"detail": "insufficient_role"})
    copy_out = await _post(world, "carol", "/copy", src=f"{FAMILY}/fam.txt", dst="/personal/f")
    assert copy_out.status_code == 403
    # non-member: the space doesn't exist as far as dave can tell
    probe = await _post(world, "dave", "/move", src="/personal/x", dst=f"{FAMILY}/x")
    assert probe.json() == {"detail": "not_found"}
    assert (world.family_root / "fam.txt").exists() and (world.home("carol") / "mine.txt").exists()


async def test_cross_space_move_regroups_the_tree(world: World, chowns) -> None:
    home, fam_gid = world.home("bob"), world.family["gid"]
    (home / "trip" / "day1").mkdir(parents=True)
    (home / "trip" / "day1" / "p.jpg").write_bytes(b"jpg")
    (home / "trip" / "link").symlink_to("day1")
    chowns.clear()
    response = await _post(world, "bob", "/move", src="/personal/trip", dst=f"{FAMILY}/trip")
    assert response.status_code == 200
    moved = world.family_root / "trip"
    assert (moved / "day1" / "p.jpg").read_bytes() == b"jpg" and not (home / "trip").exists()
    # uid kept (-1), group handed to the destination space, modes normalized
    assert chowns == {
        moved: (-1, fam_gid),
        moved / "day1": (-1, fam_gid),
        moved / "day1" / "p.jpg": (-1, fam_gid),
        moved / "link": (-1, fam_gid),
    }
    assert stat.S_IMODE((moved / "day1").stat().st_mode) == 0o2770
    assert stat.S_IMODE((moved / "day1" / "p.jpg").stat().st_mode) == 0o660


async def test_move_within_a_space_keeps_ownership(world: World, chowns) -> None:
    (world.family_root / "a.txt").write_text("x")
    chowns.clear()
    response = await _post(world, "bob", "/move", src=f"{FAMILY}/a.txt", dst=f"{FAMILY}/b.txt")
    assert response.status_code == 200
    assert chowns == {}


async def test_cross_space_copy_belongs_to_caller_and_destination(world: World, chowns) -> None:
    (world.family_root / "album").mkdir()
    (world.family_root / "album" / "run.sh").write_text("#!/bin/sh\n")
    os.chmod(world.family_root / "album" / "run.sh", 0o750)
    chowns.clear()
    response = await _post(world, "bob", "/copy", src=f"{FAMILY}/album", dst="/personal/album")
    assert response.status_code == 200
    copied = world.home("bob") / "album"
    bob = (world.users["bob"]["uid"], world.personal("bob")["gid"])
    assert chowns == {copied: bob, copied / "run.sh": bob}
    assert stat.S_IMODE((copied / "run.sh").stat().st_mode) == 0o770


# --- ownership of everything the API creates ---------------------------------------------


async def test_created_files_and_dirs_belong_to_caller_and_space(world: World, chowns) -> None:
    bob = world.users["bob"]["uid"]
    gid = world.family["gid"]
    root = world.family_root
    chowns.clear()
    assert (await _post(world, "bob", "/mkdir", path=f"{FAMILY}/a/b")).status_code == 201
    assert (await _upload(world, "bob", f"{FAMILY}/a", ("u.txt", b"u"))).status_code == 201
    assert (await _post(world, "bob", "/write", path=f"{FAMILY}/w/x.md", content="x")).is_success
    put = await world.client.put(
        f"{FILES}/content", params={"path": f"{FAMILY}/c/d.bin"}, content=b"c",
        headers=world.headers["bob"],
    )  # fmt: skip
    assert put.status_code == 200
    expected = ["a", "a/b", "a/u.txt", "w", "w/x.md", "c", "c/d.bin"]
    assert chowns == {root / p: (bob, gid) for p in expected}
    for p in expected:
        mode = stat.S_IMODE((root / p).stat().st_mode)
        assert mode == (0o2770 if (root / p).is_dir() else 0o660), p


async def test_overwrite_keeps_the_original_owner(world: World, chowns) -> None:
    (world.family_root / "a.txt").write_text("alice's")
    chowns.clear()
    assert (await _upload(world, "bob", FAMILY, ("a.txt", b"bob's"))).status_code == 201
    assert (await _post(world, "bob", "/write", path=f"{FAMILY}/a.txt", content="x")).is_success
    assert chowns == {}
