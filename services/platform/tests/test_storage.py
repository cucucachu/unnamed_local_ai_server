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
import json, os, stat, uuid
from pathlib import Path
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

# A file created by a member (primary gid elsewhere) inherits the space gid.
os.setgroups([30001])
os.setgid(20000)
os.setuid(20000)
f = root / str(a) / "files" / "note.txt"
f.write_text("hi")
out["file"] = describe(f)
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


def test_created_tree_is_root_space_gid_2770(result):
    for node in result["created"].values():
        assert node == {"uid": 0, "gid": 30001, "mode": "0o2770"}


def test_reconcile_fixes_drift_and_refuses_symlinks(result):
    assert result["reconcile"] == [1, 1]
    for node in result["reconciled"].values():
        assert node == {"uid": 0, "gid": 30001, "mode": "0o2770"}
    assert result["bait"]["gid"] == 0
    assert result["bait"]["mode"] != "0o2770"


def test_member_files_inherit_the_space_gid(result):
    assert result["file"]["uid"] == 20000
    assert result["file"]["gid"] == 30001


def test_caps_match_compose():
    compose = COMPOSE_FILE.read_text()
    platform = compose[compose.index("\n  platform:\n") :]
    assert f"cap_add: [{', '.join(PLATFORM_CAPS)}]" in platform.split("\n  code-exec-manager:")[0]
