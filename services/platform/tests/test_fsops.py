"""`app/core/fsops.py` as root, with the platform container's capabilities.

Like `test_storage.py`: the rest of the suite records chowns instead of
doing them; this runs the module for real in a `python` container and
checks what the kernel reports - owner, group, mode (setgid surviving the
chown), and that another member of the space can then use what was made.
"""

import json
import shutil
import subprocess

import pytest

from tests.test_storage import APP_DIR, IMAGE, PLATFORM_CAPS

ALICE, BOB, SPACE_A, SPACE_B = 20001, 20002, 30001, 30002

SCRIPT = rf"""
import errno, json, os, stat
from pathlib import Path
from app.core import fsops
from app.core.beneath import Root

def describe(path):
    st = os.lstat(path)
    return {{"uid": st.st_uid, "gid": st.st_gid, "mode": oct(stat.S_IMODE(st.st_mode))}}

def open_root(d):
    return Root(os.open(d, os.O_RDONLY | os.O_DIRECTORY), str(d))

root = Path("/spaces")
os.chmod(root, 0o755)
a, b = root / "a", root / "b"
for d, gid in ((a, {SPACE_A}), (b, {SPACE_B})):
    d.mkdir()
    os.chown(d, 0, gid)
    os.chmod(d, 0o2770)
A, B = open_root(a), open_root(b)
alice, bob = fsops.Owner({ALICE}, {SPACE_A}), fsops.Owner({BOB}, {SPACE_B})
out = {{}}

fsops.make_dirs(A, ("x", "y"), alice)
with fsops.open_for_write(A, ("x", "y", "f.txt"), alice) as f:
    f.write(b"hi")
with fsops.open_for_write(A, ("run.sh",), alice, mode=fsops.EXEC_FILE_MODE) as f:
    f.write(b"#!/bin/sh\n")
os.symlink("x/y/f.txt", a / "link")
out["created"] = {{p: describe(a / p) for p in ("x", "x/y", "x/y/f.txt", "run.sh")}}

# Overwrite keeps the existing owner.
with fsops.open_for_write(A, ("x", "y", "f.txt"), bob) as f:
    f.write(b"again")
out["overwritten"] = describe(a / "x" / "y" / "f.txt")

# Copy space a into space b as bob.
fsops.copy(A, (), B, ("copy",), bob)
out["copied"] = {{p: describe(b / "copy" / p) for p in ("", "x", "x/y/f.txt", "run.sh", "link")}}
out["copied_link"] = os.readlink(b / "copy" / "link")

# Cross-space move: rename, then regroup (owner kept).
fsops.move(A, ("x",), B, ("moved",), gid={SPACE_B})
out["moved"] = {{p: describe(b / "moved" / p) for p in ("", "y", "y/f.txt")}}

# Legacy-style adoption: a foreign tree renamed in, or copied across a mount, then adopted.
legacy = root / "legacy"
(legacy / "d").mkdir(parents=True)
(legacy / "d" / "n.txt").write_text("old")
os.chmod(legacy / "d" / "n.txt", 0o644)
(legacy / "e").mkdir()
(legacy / "e" / "m.txt").write_text("old")
L = open_root(legacy)
os.rename("d", "d", src_dir_fd=L.fd, dst_dir_fd=A.fd)
fsops.adopt_at(A.fd, "d", {SPACE_A}, {ALICE})
fsops.copy_at(L.fd, "e", A.fd, "e", alice)
fsops.remove_at(L.fd, "e")
fsops.adopt_at(A.fd, "e", {SPACE_A}, {ALICE})
out["adopted"] = {{p: describe(a / p) for p in ("d", "d/n.txt", "e", "e/m.txt")}}
out["legacy_left"] = sorted(os.listdir(legacy))

# A move never replaces a file or an (empty) directory at the destination.
(a / "keep.txt").write_text("keep")
(a / "keepdir").mkdir()
(a / "new.txt").write_text("new")
out["noreplace"] = []
for dst in ("keep.txt", "keepdir"):
    try:
        fsops.move(A, ("new.txt",), A, (dst,))
        out["noreplace"].append("replaced")
    except OSError as exc:
        out["noreplace"].append(errno.errorcode[exc.errno])
out["noreplace_kept"] = [(a / "keep.txt").read_text(), os.listdir(a / "keepdir"),
                         (a / "new.txt").read_text()]

# A link into space b planted in space a redirects neither a write nor a chown.
os.symlink(str(b), a / "evil")
before = describe(b)
try:
    with fsops.open_for_write(A, ("evil", "pwn.txt"), alice) as f:
        f.write(b"x")
    out["planted_write"] = "written"
except OSError as exc:
    out["planted_write"] = errno.errorcode[exc.errno]
fsops.adopt_at(A.fd, "evil", {SPACE_A}, {ALICE})
out["planted_chown"] = {{"b": describe(b), "b_before": before, "link": describe(a / "evil")}}
out["planted_listing"] = sorted(os.listdir(b))

# Writing a tree back (app revert): links planted where a file or folder goes
# are replaced, never followed; a folder where a file goes is removed.
os.symlink(str(b / "moved"), a / "as_dir")
os.symlink(str(b / "moved" / "y" / "f.txt"), a / "as_file")
(a / "was_dir" / "sub").mkdir(parents=True)
fd = fsops.open_dir_at(A.fd, "as_dir", alice)
fsops.replace_file_at(fd, "inner.txt", b"in", alice)
os.close(fd)
fsops.replace_file_at(A.fd, "as_file", b"file", alice)
fsops.replace_file_at(A.fd, "was_dir", b"dir", alice)
out["replaced"] = {{p: describe(a / p) for p in ("as_dir", "as_dir/inner.txt", "as_file", "was_dir")}}
out["replaced_kept"] = [sorted(os.listdir(b / "moved")), (b / "moved" / "y" / "f.txt").read_text(),
                        sorted(n for n in os.listdir(a) if n.startswith(".homeai-"))]

# Another member of space b (not the owner) can use what was created there.
os.setgroups([{SPACE_B}])
os.setgid(40000)
os.setuid(40000)
(b / "moved" / "y" / "f.txt").write_text("member edit")
(b / "moved" / "y" / "new.txt").write_text("member new")
out["member_new"] = describe(b / "moved" / "y" / "new.txt")
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory) -> dict:
    if shutil.which("docker") is None:
        pytest.fail("this test needs the `docker` CLI")
    spaces = tmp_path_factory.mktemp("fsops-root")
    try:
        proc = subprocess.run(
            [
                "docker", "run", "--rm",
                "--cap-drop", "ALL",
                *(f"--cap-add={cap}" for cap in PLATFORM_CAPS),
                "--cap-add", "SETUID", "--cap-add", "SETGID",
                "--security-opt", "no-new-privileges:true",
                "-v", f"{APP_DIR}:/src/app:ro",
                "-v", f"{spaces}:/spaces",
                "-e", "PYTHONPATH=/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
                IMAGE, "python", "-c", SCRIPT,
            ],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])
    finally:
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{spaces}:/spaces", IMAGE,
             "sh", "-c", "rm -rf /spaces/* /spaces/.[!.]*"],
            capture_output=True, check=False,
        )  # fmt: skip


def _node(uid: int, gid: int, mode: str) -> dict:
    return {"uid": uid, "gid": gid, "mode": mode}


def test_created_belongs_to_user_and_space(result):
    assert result["created"] == {
        "x": _node(ALICE, SPACE_A, "0o2770"),
        "x/y": _node(ALICE, SPACE_A, "0o2770"),
        "x/y/f.txt": _node(ALICE, SPACE_A, "0o660"),
        "run.sh": _node(ALICE, SPACE_A, "0o770"),
    }
    assert result["overwritten"] == _node(ALICE, SPACE_A, "0o660")


def test_copy_belongs_to_copier_and_destination(result):
    copied = result["copied"]
    assert copied[""] == copied["x"] == _node(BOB, SPACE_B, "0o2770")
    assert copied["x/y/f.txt"] == _node(BOB, SPACE_B, "0o660")
    assert copied["run.sh"] == _node(BOB, SPACE_B, "0o770")
    assert (copied["link"]["uid"], copied["link"]["gid"]) == (BOB, SPACE_B)
    assert result["copied_link"] == "x/y/f.txt"


def test_cross_space_move_regroups_but_keeps_owner(result):
    assert result["moved"] == {
        "": _node(ALICE, SPACE_B, "0o2770"),
        "y": _node(ALICE, SPACE_B, "0o2770"),
        "y/f.txt": _node(ALICE, SPACE_B, "0o660"),
    }


def test_adopted_tree(result):
    assert result["adopted"] == {
        "d": _node(ALICE, SPACE_A, "0o2770"),
        "d/n.txt": _node(ALICE, SPACE_A, "0o660"),
        "e": _node(ALICE, SPACE_A, "0o2770"),
        "e/m.txt": _node(ALICE, SPACE_A, "0o660"),
    }
    assert result["legacy_left"] == []


def test_move_never_replaces(result):
    assert result["noreplace"] == ["EEXIST", "EEXIST"]
    assert result["noreplace_kept"] == ["keep", [], "new"]


def test_planted_link_to_another_space_redirects_nothing(result):
    assert result["planted_write"] == "EXDEV"
    chown = result["planted_chown"]
    assert chown["b"] == chown["b_before"] == _node(0, SPACE_B, "0o2770")
    assert (chown["link"]["uid"], chown["link"]["gid"]) == (ALICE, SPACE_A)
    assert "pwn.txt" not in result["planted_listing"]


def test_replacing_a_planted_link_writes_in_place_and_follows_nothing(result):
    assert result["replaced"] == {
        "as_dir": _node(ALICE, SPACE_A, "0o2770"),
        "as_dir/inner.txt": _node(ALICE, SPACE_A, "0o660"),
        "as_file": _node(ALICE, SPACE_A, "0o660"),
        "was_dir": _node(ALICE, SPACE_A, "0o660"),
    }
    assert result["replaced_kept"] == [["y"], "again", []]


def test_other_members_can_use_it(result):
    assert result["member_new"]["gid"] == SPACE_B


@pytest.mark.parametrize("module", ["fsops", "beneath"])
def test_module_is_stdlib_only(module):
    """The container above has no project dependencies installed."""
    source = (APP_DIR / "core" / f"{module}.py").read_text()
    imports = {
        line.split()[1]
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and not line.startswith("from __future__")
    }
    stdlib = {"ctypes", "errno", "os", "shutil", "stat", "collections.abc", "contextlib",
              "dataclasses", "typing"}  # fmt: skip
    assert imports <= stdlib | {"app.core", "app.core.beneath"}, imports
