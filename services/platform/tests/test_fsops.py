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
import json, os, shutil, stat
from pathlib import Path
from app.core import fsops

def describe(path):
    st = os.lstat(path)
    return {{"uid": st.st_uid, "gid": st.st_gid, "mode": oct(stat.S_IMODE(st.st_mode))}}

root = Path("/spaces")
os.chmod(root, 0o755)
a, b = root / "a", root / "b"
for d, gid in ((a, {SPACE_A}), (b, {SPACE_B})):
    d.mkdir()
    os.chown(d, 0, gid)
    os.chmod(d, 0o2770)
alice = fsops.Owner({ALICE}, {SPACE_A})
out = {{}}

fsops.make_dirs(a, a / "x" / "y", alice)
with fsops.open_for_write(a / "x" / "y" / "f.txt", alice) as f:
    f.write(b"hi")
with fsops.open_for_write(a / "run.sh", alice, mode=fsops.EXEC_FILE_MODE) as f:
    f.write(b"#!/bin/sh\n")
os.symlink("x/y/f.txt", a / "link")
out["created"] = {{p: describe(a / p) for p in ("x", "x/y", "x/y/f.txt", "run.sh")}}

# Overwrite keeps the existing owner.
with fsops.open_for_write(a / "x" / "y" / "f.txt", fsops.Owner({BOB}, {SPACE_B})) as f:
    f.write(b"again")
out["overwritten"] = describe(a / "x" / "y" / "f.txt")

# Copy into space b as bob.
fsops.copy(a, b / "copy", fsops.Owner({BOB}, {SPACE_B}))
out["copied"] = {{p: describe(b / "copy" / p) for p in ("", "x", "x/y/f.txt", "run.sh", "link")}}
out["copied_link"] = os.readlink(b / "copy" / "link")

# Cross-space move: rename, then regroup (owner kept).
os.rename(a / "x", b / "moved")
fsops.adopt_tree(b / "moved", {SPACE_B})
out["moved"] = {{p: describe(b / "moved" / p) for p in ("", "y", "y/f.txt")}}

# Legacy-style adoption: a foreign tree handed to a user and a space.
legacy = root / "legacy"
(legacy / "d").mkdir(parents=True)
(legacy / "d" / "n.txt").write_text("old")
os.chmod(legacy / "d" / "n.txt", 0o644)
shutil.move(legacy / "d", a / "d")
fsops.adopt_tree(a / "d", {SPACE_A}, {ALICE})
out["adopted"] = {{p: describe(a / p) for p in ("d", "d/n.txt")}}

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
    }


def test_other_members_can_use_it(result):
    assert result["member_new"]["gid"] == SPACE_B


def test_module_is_stdlib_only():
    """The container above has no project dependencies installed."""
    source = (APP_DIR / "core" / "fsops.py").read_text()
    imports = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and not line.startswith("from __future__")
    }
    assert imports <= {"os", "shutil", "stat", "collections", "contextlib", "dataclasses",
                       "pathlib", "typing"}, imports  # fmt: skip
