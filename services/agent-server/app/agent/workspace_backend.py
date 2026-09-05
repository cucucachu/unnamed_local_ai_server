"""Filesystem backend that treats `/workspace` as the virtual root.

`FilesystemBackend(virtual_mode=True)` joins the path *after* the leading
`/` onto `root_dir`. That makes `/notes.txt` correct (`<root>/notes.txt`)
but `/workspace/notes.txt` one directory too deep
(`<root>/workspace/notes.txt`).

The exec sandbox bind-mounts the same host directory at `/workspace`, and
`execute_code`'s tool description says the file-tool root *is* `/workspace`.
The model therefore (reasonably) calls `read_file`/`write_file` with
`/workspace/exec-proof.txt` after a shell write to that path. This wrapper
strips a whole `/workspace` prefix before the parent resolver runs, so both
namespaces name the same files. Traversal (`..`, `~`) is still rejected by
`FilesystemBackend._resolve_path`.
"""

from __future__ import annotations

from pathlib import Path

from deepagents.backends import FilesystemBackend


def normalize_file_tool_path(key: str) -> str:
    """Map `/workspace/...` onto the virtual root; leave other paths alone.

    `/workspace` and `/workspace/` become `/`. `/workspace/foo` becomes
    `/foo`. A relative `workspace/foo` is treated the same as
    `/workspace/foo`. Names that merely start with the letters
    `workspace` (`/workspace_backup`, `/workspaces/x`) are unchanged.
    """
    raw = key.strip()
    if not raw:
        return raw
    if raw == "workspace" or raw.startswith("workspace/"):
        raw = "/" + raw
    if raw == "/workspace":
        return "/"
    if raw.startswith("/workspace/"):
        rest = raw[len("/workspace") :]
        return rest if rest != "/" else "/"
    return raw


class WorkspaceFilesystemBackend(FilesystemBackend):
    """`FilesystemBackend` whose virtual root is also addressable as `/workspace`."""

    def _resolve_path(self, key: str) -> Path:
        return super()._resolve_path(normalize_file_tool_path(key))
