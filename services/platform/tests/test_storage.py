"""`app/core/storage.py` as root, with the platform container's capabilities.

The rest of the suite runs unprivileged and records chowns instead of doing
them; this runs the module for real in a `python` container (root,
`cap_drop: ALL` + the same `cap_add` as compose) against a
throwaway directory, and checks ownership, mode, reconciliation, and
symlink refusal as the kernel reports them.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

IMAGE = os.environ.get("TEST_PYTHON_IMAGE", "python:3.12-slim")
APP_DIR = Path(__file__).resolve().parents[1] / "app"
COMPOSE_FILE = Path(__file__).resolve().parents[3] / "docker-compose.yml"
PLATFORM_CAPS = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID")

SCRIPT = r"""
import json, os, sqlite3, stat, uuid
from pathlib import Path
from app.core import appdb
from app.core.storage import SpaceStorage

def describe(path):
    st = os.lstat(path)
    return {"uid": st.st_uid, "gid": st.st_gid, "mode": oct(stat.S_IMODE(st.st_mode))}

root = Path("/spaces")
os.chmod(root, 0o755)
storage = SpaceStorage(root)
a, b = uuid.uuid4(), uuid.uuid4()
out = {}

storage.ensure(a, 30001)
tree = lambda sid: {p: describe(root / str(sid) / p) for p in ("", "files", "apps")}
out["created"] = tree(a)

# Drift: wrong group, lost setgid, a missing subdir; then a startup reconcile.
os.chown(root / str(a) / "files", 0, 0)
os.chmod(root / str(a) / "files", 0o755)
os.rmdir(root / str(a) / "apps")
os.chmod(root / str(a), 0o700)
(root / str(b)).mkdir()
bait = Path("/spaces-bait")
bait.mkdir()
os.symlink(bait, root / str(b) / "files")
out["reconcile"] = storage.reconcile([(a, 30001), (b, 30002)])
out["reconciled"] = tree(a)
out["bait"] = describe(bait)

# App instance dirs: created, then trashed (kept, moved under apps/.trash/).
i = uuid.uuid4()
out["instance"] = describe(storage.ensure_instance(a, 30001, i))
out["instance_subdirs"] = [describe(root / str(a) / "apps" / str(i) / d) for d in ("ro", "snapshots")]
(root / str(a) / "apps" / str(i) / "data.sqlite").write_text("rows")
kept = storage.trash_instance(a, 30001, i, "20260101T000000Z")
out["trash"] = describe(kept.parent)
out["trashed"] = {"name": kept.name, "data": (kept / "data.sqlite").read_text(),
                  "gone": not (root / str(a) / "apps" / str(i)).exists()}
out["trash_missing"] = storage.trash_instance(a, 30001, uuid.uuid4(), "x")

# App data: the live database and its published read-only copy.
j = uuid.uuid4()
fd = storage.open_instance(a, 30001, j)
with appdb.connect(fd) as con:
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (1)")
    appdb.publish(con, fd)
    appdb.snapshot(con, fd, "m1")
    live = root / str(a) / "apps" / str(j)
    out["appdata"] = {n: describe(live / n) for n in ("data.sqlite", "data.sqlite-wal", "ro/data.sqlite")}
os.close(fd)

# A file created by a member (primary gid elsewhere) inherits the space gid.
os.setgroups([30001])
os.setgid(20000)
os.setuid(20000)
f = root / str(a) / "files" / "note.txt"
f.write_text("hi")
out["file"] = describe(f)

def attempt(fn):
    try:
        fn()
        return "ok"
    except PermissionError:
        return "denied"

ro = sqlite3.connect(f"file:{live}/ro/data.sqlite?mode=ro&immutable=1", uri=True)
out["member"] = {
    "ro_rows": ro.execute("SELECT x FROM t").fetchall(),
    "open_live": attempt(lambda: open(live / "data.sqlite", "rb").close()),
    "create_in_instance": attempt(lambda: (live / "x").write_text("x")),
    "create_in_ro": attempt(lambda: (live / "ro" / "x").write_text("x")),
    "replace_ro": attempt(lambda: os.symlink("/etc/passwd", live / "ro" / "evil")),
    "create_in_apps": attempt(lambda: os.mkdir(root / str(a) / "apps" / "x")),
    "list_snapshots": attempt(lambda: os.listdir(live / "snapshots")),
    "read_snapshot": attempt(lambda: [open(live / "snapshots" / n, "rb").close()
                                      for n in os.listdir(live / "snapshots")]),
}
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory) -> dict:
    if shutil.which("docker") is None:
        pytest.fail("this test needs the `docker` CLI")
    spaces = tmp_path_factory.mktemp("spaces-root")
    try:
        proc = subprocess.run(
            [
                "docker", "run", "--rm",
                "--cap-drop", "ALL",
                *(f"--cap-add={cap}" for cap in PLATFORM_CAPS),
                # Only for the script's final step (acting as a member).
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
        # The tree is root-owned; remove it from inside a container too.
        subprocess.run(
            ["docker", "run", "--rm", "-v", f"{spaces}:/spaces", IMAGE,
             "sh", "-c", "rm -rf /spaces/* /spaces/.[!.]*"],
            capture_output=True, check=False,
        )  # fmt: skip


def _tree_modes(tree: dict) -> dict:
    return {p: node["mode"] for p, node in tree.items()}


TREE = {"": "0o2770", "files": "0o2770", "apps": "0o2750"}


def test_created_tree_is_root_space_gid_2770_apps_2750(result):
    for node in result["created"].values():
        assert (node["uid"], node["gid"]) == (0, 30001)
    assert _tree_modes(result["created"]) == TREE


def test_reconcile_fixes_drift_and_refuses_symlinks(result):
    assert result["reconcile"] == [1, 1]
    for node in result["reconciled"].values():
        assert (node["uid"], node["gid"]) == (0, 30001)
    assert _tree_modes(result["reconciled"]) == TREE
    assert result["bait"]["gid"] == 0
    assert result["bait"]["mode"] != "0o2770"


def test_instance_dirs_are_root_space_gid_2750_and_trash_keeps_them(result):
    assert result["instance"] == {"uid": 0, "gid": 30001, "mode": "0o2750"}
    assert result["instance_subdirs"] == [{"uid": 0, "gid": 30001, "mode": "0o2750"}] * 2
    assert result["trash"] == {"uid": 0, "gid": 30001, "mode": "0o2750"}
    assert result["trashed"]["name"].endswith("-20260101T000000Z")
    assert (result["trashed"]["data"], result["trashed"]["gone"]) == ("rows", True)
    assert result["trash_missing"] is None


def test_app_data_is_platform_only_except_the_read_only_copy(result):
    node = {"uid": 0, "gid": 30001}
    assert result["appdata"] == {
        "data.sqlite": {**node, "mode": "0o600"},
        "data.sqlite-wal": {**node, "mode": "0o600"},
        "ro/data.sqlite": {**node, "mode": "0o444"},
    }
    member = result["member"]
    assert member.pop("ro_rows") == [[1]]
    assert member == {
        "open_live": "denied",
        "create_in_instance": "denied",
        "create_in_ro": "denied",
        "replace_ro": "denied",
        "create_in_apps": "denied",
        "list_snapshots": "ok",
        "read_snapshot": "denied",
    }


def test_member_files_inherit_the_space_gid(result):
    assert result["file"]["uid"] == 20000
    assert result["file"]["gid"] == 30001


def test_caps_match_compose():
    compose = COMPOSE_FILE.read_text()
    platform = compose[compose.index("\n  platform:\n") :]
    assert f"cap_add: [{', '.join(PLATFORM_CAPS)}]" in platform.split("\n  code-exec-manager:")[0]
