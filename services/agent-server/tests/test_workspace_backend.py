"""`/workspace` prefix on file-tool paths maps onto the virtual root."""

from pathlib import Path

import pytest

from app.agent.workspace_backend import (
    WorkspaceFilesystemBackend,
    normalize_file_tool_path,
)


@pytest.mark.parametrize(
    ("incoming", "expected"),
    [
        ("/exec-proof.txt", "/exec-proof.txt"),
        ("exec-proof.txt", "exec-proof.txt"),
        ("/workspace/exec-proof.txt", "/exec-proof.txt"),
        ("/workspace", "/"),
        ("/workspace/", "/"),
        ("workspace/exec-proof.txt", "/exec-proof.txt"),
        ("workspace", "/"),
        ("/workspace_backup/x", "/workspace_backup/x"),
        ("/workspaces/x", "/workspaces/x"),
        ("/notes.md", "/notes.md"),
    ],
)
def test_normalize_file_tool_path(incoming: str, expected: str) -> None:
    assert normalize_file_tool_path(incoming) == expected


def test_read_and_write_workspace_prefix_hit_root(tmp_path: Path) -> None:
    backend = WorkspaceFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    (tmp_path / "exec-proof.txt").write_text("from-disk\n")

    prefixed = backend.read("/workspace/exec-proof.txt")
    virtual = backend.read("/exec-proof.txt")
    assert prefixed.error is None
    assert virtual.error is None

    written = backend.write("/workspace/from-tool.txt", "hello")
    assert written.error is None
    assert (tmp_path / "from-tool.txt").read_text() == "hello"
    assert not (tmp_path / "workspace").exists()


def test_workspace_prefix_does_not_weaken_traversal_guard(tmp_path: Path) -> None:
    backend = WorkspaceFilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    with pytest.raises(ValueError, match="traversal"):
        backend._resolve_path("/workspace/../etc/passwd")
