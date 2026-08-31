"""Unit tests for `app.core.paths.resolve_workspace_path` (M3-03, Conventions
& Contracts §8) — the traversal guard shared by every path-taking `/api/
files*` endpoint (`tests/test_files_rest.py` re-exercises the same guard
end-to-end through the REST routes; these tests isolate the function
itself).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.core.paths import resolve_workspace_path


def test_empty_string_resolves_to_root(tmp_path: Path) -> None:
    assert resolve_workspace_path(tmp_path, "") == tmp_path.resolve()


def test_plain_relative_path_resolves_under_root(tmp_path: Path) -> None:
    result = resolve_workspace_path(tmp_path, "a/b.txt")
    assert result == tmp_path.resolve() / "a" / "b.txt"


def test_nonexistent_nested_path_still_resolves(tmp_path: Path) -> None:
    # `.resolve()` defaults to `strict=False` - a target that doesn't exist
    # yet (e.g. an upload/mkdir/move destination) must still resolve.
    result = resolve_workspace_path(tmp_path, "does/not/exist/yet.txt")
    assert result == tmp_path.resolve() / "does" / "not" / "exist" / "yet.txt"


def test_parent_dir_traversal_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_workspace_path(tmp_path, "../x")
    assert exc_info.value.status_code == 400


def test_absolute_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_workspace_path(tmp_path, "/etc/passwd")
    assert exc_info.value.status_code == 400


def test_nested_dotdot_escape_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_workspace_path(tmp_path, "a/../../x")
    assert exc_info.value.status_code == 400


def test_symlink_escaping_workspace_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "escape_link").symlink_to("/tmp")

    with pytest.raises(HTTPException) as exc_info:
        resolve_workspace_path(tmp_path, "escape_link/x")
    assert exc_info.value.status_code == 400


def test_symlink_to_the_link_itself_is_also_rejected(tmp_path: Path) -> None:
    # Requesting the symlink path itself (not something nested under it)
    # must also be rejected: `.resolve()` follows the final component too.
    (tmp_path / "escape_link").symlink_to("/tmp")

    with pytest.raises(HTTPException) as exc_info:
        resolve_workspace_path(tmp_path, "escape_link")
    assert exc_info.value.status_code == 400


def test_symlink_staying_inside_workspace_is_allowed(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")

    result = resolve_workspace_path(tmp_path, "link/x.txt")
    assert result == (tmp_path / "real" / "x.txt").resolve()


def test_null_byte_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_workspace_path(tmp_path, "a\x00b")
    assert exc_info.value.status_code == 400
