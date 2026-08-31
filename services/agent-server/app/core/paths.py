"""Workspace path traversal guard — Conventions & Contracts (#34) §8.

`resolve_workspace_path` is THE traversal guard shared by every path-taking
endpoint in `app/api/files.py` (and, per the ticket, will be reused as-is by
the media API in M5-01) — implement it exactly once, here, and never
reimplement path-escape checking anywhere else.

Deviation from the ticket's illustrative §8 snippet (behavior-preserving,
only the root-injection shape differs): the snippet hardcodes
`root = Path("/data/workspace").resolve()` inside the function. This takes
`root` as a parameter instead so it's trivially unit-testable against an
arbitrary `tmp_path` without monkeypatching a module-level constant or
touching real env vars — callers (`app/api/files.py`'s route handlers) pass
`Path(request.app.state.settings.workspace_root)`, mirroring how every other
route in this codebase reaches config via `request.app.state` rather than a
process-global. The comparison logic below (`p != root and root not in
p.parents`) is copied verbatim from the reference implementation.

Introspection notes (real, not guessed — installed Python 3.12 stdlib):
- `Path.resolve()` defaults to `strict=False`, which does NOT require the
  path to exist — confirmed by resolving a multi-level nonexistent path
  (`/root/a/b/c.txt`) and getting back the fully-normalized path with no
  exception. This is required here: `mkdir`/`upload`/`move`/`copy` targets
  routinely don't exist yet at guard-check time.
- `Path.resolve()` follows symlinks for EVERY component of the path,
  including one buried in the middle (e.g. `root/link/x.txt` where `link ->
  /tmp`) — confirmed by creating such a symlink and resolving a path through
  it, which came back as `/tmp/x.txt`. This is exactly what makes the
  reference implementation's `root not in p.parents` check sufficient to
  catch the "symlink inside the workspace pointing outside it" guard-suite
  case: no separate symlink-specific check is needed, `.resolve()` already
  did the work before the containment check even runs.
- Joining an absolute path onto a `Path` with `/` discards the left operand
  entirely (matches `os.path.join` semantics for absolute components) —
  confirmed `Path("/a/b") / "/etc/passwd" == Path("/etc/passwd")`. This is
  exactly how the reference implementation's single `(root / rel).resolve()`
  line also rejects absolute-path `rel` values (e.g. `/etc/passwd`) with no
  separate `os.path.isabs` check: the join already routes them away from
  `root`, and the containment check below catches it.
- Constructing `Path("a\x00b")` does NOT raise, but calling `.resolve()` on
  it does (`ValueError: embedded null character in path`, since it costs a
  real `lstat` syscall) — confirmed directly. The ticket calls out null-byte
  rejection as its own requirement (not merely "whatever `.resolve()` raises
  happens to work"), so it's checked explicitly up front with a clean `400`
  rather than leaking a `ValueError` past this function.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException


def resolve_workspace_path(root: Path, rel: str) -> Path:
    """Resolve `rel` (a workspace-relative POSIX path) against `root`.

    `rel == ""` resolves to `root` itself (workspace root). Raises
    `HTTPException(400)` if `rel` contains a null byte, or if the resolved
    path is not `root` itself or a descendant of it (i.e. it escapes the
    workspace via `..`, an absolute path, or a symlink).
    """
    if "\x00" in rel:
        raise HTTPException(status_code=400, detail="path escapes workspace")

    root = root.resolve()
    p = (root / rel).resolve()
    if p != root and root not in p.parents:
        raise HTTPException(status_code=400, detail="path escapes workspace")
    return p
