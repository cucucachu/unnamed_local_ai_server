"""`app.core.manifest`: the app.json schema and the package layout check (no database)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.core import manifest
from app.core.manifest import Diagnostic, validate_manifest, validate_package
from tests.app_packages import FILES as PACKAGE_FILES
from tests.app_packages import manifest as good_manifest
from tests.app_packages import write_package


def _where(diags: list[Diagnostic]) -> list[tuple[str, str]]:
    return [(d.file, d.path) for d in diags]


def _homeai(**changes) -> dict:
    doc = good_manifest()
    doc["homeai"].update(changes)
    return doc


# --- the schema -----------------------------------------------------------------------


def test_schema_is_a_valid_2020_12_schema() -> None:
    Draft202012Validator.check_schema(manifest.SCHEMA)


@pytest.mark.parametrize(
    "doc",
    [
        good_manifest(),
        _homeai(permissions={}, exports=[], reads=[]),
        good_manifest(version="0.2.10-beta.1+build.5"),
        # Expo keys outside `homeai` are allowed.
        good_manifest(orientation="portrait", ios={"supportsTablet": True}),
    ],
)
def test_good_manifests(doc) -> None:
    assert validate_manifest(doc) == []


def test_missing_required_properties_point_at_the_missing_key() -> None:
    diags = validate_manifest({"homeai": {}})
    assert _where(diags) == [
        ("app.json", "/name"),
        ("app.json", "/slug"),
        ("app.json", "/version"),
        ("app.json", "/homeai/sdk"),
        ("app.json", "/homeai/icon"),
    ]
    assert diags[0].message == 'missing required property "name"'


@pytest.mark.parametrize(
    ("doc", "pointer", "needle"),
    [
        (good_manifest(slug="Hello World"), "/slug", "lowercase letters"),
        (good_manifest(version="1.0"), "/version", '"1.0.0"'),
        (good_manifest(name=""), "/name", "1-64 characters"),
        (good_manifest(name=7), "/name", "1-64 characters"),
        (_homeai(sdk="2"), "/homeai/sdk", 'one of: "1"'),
        (_homeai(sdk=1), "/homeai/sdk", 'one of: "1"'),
        (_homeai(icon="Cart Outline"), "/homeai/icon", "vector icon name"),
        (_homeai(description="x" * 501), "/homeai/description", "500"),
        (
            _homeai(permissions={"net": ["api.weather.gov"]}),
            "/homeai/permissions/net",
            "no permissions",
        ),
        (_homeai(permissions=[]), "/homeai/permissions", "no permissions"),
        (_homeai(exports=["items"]), "/homeai/exports", "reserved"),
        (_homeai(exports={}), "/homeai/exports", "reserved"),
        (_homeai(reads=[{"app": "x"}]), "/homeai/reads", "reserved"),
        (_homeai(colour="red"), "/homeai/colour", 'unknown property "colour"'),
        ([], "", "not of type 'object'"),
    ],
)
def test_bad_manifests(doc, pointer: str, needle: str) -> None:
    diags = validate_manifest(doc)
    assert [d.path for d in diags] == [pointer], diags
    assert needle in diags[0].message
    assert diags[0].file == "app.json"


def test_unknown_homeai_key_lists_the_allowed_ones() -> None:
    (diag,) = validate_manifest(_homeai(colour="red"))
    assert "sdk, icon, description, permissions, exports, reads" in diag.message


def test_pointers_escape_special_characters() -> None:
    (diag,) = validate_manifest(_homeai(**{"a/b~c": 1}))
    assert diag.path == "/homeai/a~1b~0c"


# --- the package -----------------------------------------------------------------------


def test_good_package(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "hello")
    doc, diags = validate_package(folder, "hello")
    assert diags == []
    assert doc["slug"] == "hello"


def test_minimal_package_needs_no_actions(tmp_path: Path) -> None:
    files = {k: v for k, v in PACKAGE_FILES.items() if "actions" not in k}
    folder = write_package(tmp_path / "hello", files=files)
    assert validate_package(folder, "hello")[1] == []


def test_missing_folder(tmp_path: Path) -> None:
    doc, diags = validate_package(tmp_path / "nope", "nope")
    assert doc is None
    assert "doesn't exist" in diags[0].message


def test_missing_files_are_each_reported(tmp_path: Path) -> None:
    folder = tmp_path / "hello"
    folder.mkdir()
    doc, diags = validate_package(folder, "hello")
    assert doc is None
    assert [d.file for d in diags] == ["app.json", "AGENT.md", "schema.sql", "app"]


def test_missing_layout_and_index(tmp_path: Path) -> None:
    folder = write_package(
        tmp_path / "hello", files={"AGENT.md": "", "schema.sql": "", "app/x.tsx": ""}
    )
    assert [d.file for d in validate_package(folder, "hello")[1]] == [
        "app/_layout.tsx",
        "app/index.tsx",
    ]


def test_invalid_json(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "hello", doc='{"name": "Hello",}')
    doc, diags = validate_package(folder, "hello")
    assert doc is None
    assert _where(diags) == [("app.json", "")]
    assert "not valid JSON" in diags[0].message and "line 1" in diags[0].message


def test_slug_must_equal_the_folder(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "hello", doc=good_manifest("other"))
    (diag,) = validate_package(folder, "hello")[1]
    assert (diag.file, diag.path) == ("app.json", "/slug")
    assert '"other"' in diag.message and '"hello"' in diag.message


def test_schema_errors_come_through(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "hello", doc=_homeai(exports=["x"]))
    assert _where(validate_package(folder, "hello")[1]) == [("app.json", "/homeai/exports")]


def test_symlinked_manifest_is_refused(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "hello")
    (tmp_path / "elsewhere.json").write_text((folder / "app.json").read_text())
    (folder / "app.json").unlink()
    (folder / "app.json").symlink_to(tmp_path / "elsewhere.json")
    (diag,) = validate_package(folder, "hello")[1]
    assert "symlink" in diag.message


@pytest.mark.parametrize(
    ("rel", "needle"),
    [
        ("app/(tabs)/index.tsx", "route groups"),
        ("app/item/_layout.tsx", "nested layouts"),
        ("app/+not-found.tsx", "special route files"),
        ("app/helper.js", "only .ts/.tsx"),
        ("app/types.d.ts", "only .ts/.tsx"),
        ("app/notes.md", "only .ts/.tsx"),
        ("app/[...rest]/index.tsx", "catch-all"),
        ("app/my page.tsx", "not a valid route segment"),
        ("app/index.ts", "same route"),
        ("app/item/[slug].tsx", "both dynamic routes"),
    ],
)
def test_bad_routes(tmp_path: Path, rel: str, needle: str) -> None:
    folder = write_package(tmp_path / "hello")
    (folder / rel).parent.mkdir(parents=True, exist_ok=True)
    (folder / rel).write_text("")
    diags = validate_package(folder, "hello")[1]
    assert len(diags) == 1, diags
    assert needle in diags[0].message
    assert diags[0].file.startswith("app/")


@pytest.mark.parametrize(
    "rel", ["app/about.tsx", "app/settings/index.tsx", "app/[...rest].tsx", "app/util.ts"]
)
def test_good_routes(tmp_path: Path, rel: str) -> None:
    folder = write_package(tmp_path / "hello")
    (folder / rel).parent.mkdir(parents=True, exist_ok=True)
    (folder / rel).write_text("")
    assert validate_package(folder, "hello")[1] == []


def test_dotfiles_are_ignored(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "hello")
    (folder / "app" / ".DS_Store").write_text("")
    (folder / "actions" / ".keep").write_text("")
    assert validate_package(folder, "hello")[1] == []


def test_symlinks_in_app_are_refused_not_followed(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "hello")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "(bad).tsx").write_text("")
    os.symlink(outside, folder / "app" / "linked")
    (diag,) = validate_package(folder, "hello")[1]
    assert (diag.file, "symlink" in diag.message) == ("app/linked", True)


@pytest.mark.parametrize(
    "name", ["AddItem.sql", "add-item.sql", "1add.sql", "addItem.SQL", "add_item.txt"]
)
def test_bad_action_names(tmp_path: Path, name: str) -> None:
    folder = write_package(tmp_path / "hello")
    (folder / "actions" / name).write_text("")
    (diag,) = validate_package(folder, "hello")[1]
    assert diag.file == f"actions/{name}"


def test_good_action_names(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "hello")
    for name in ("toggle_done.sql", "a.sql", "clearAll2.sql"):
        (folder / "actions" / name).write_text("")
    assert validate_package(folder, "hello")[1] == []


def test_actions_subfolders_and_files_are_refused(tmp_path: Path) -> None:
    folder = write_package(tmp_path / "hello")
    (folder / "actions" / "nested").mkdir()
    assert [d.file for d in validate_package(folder, "hello")[1]] == ["actions/nested"]
    other = write_package(tmp_path / "other", doc=good_manifest("other"), files={})
    for rel, content in PACKAGE_FILES.items():
        if not rel.startswith("actions"):
            (other / rel).parent.mkdir(parents=True, exist_ok=True)
            (other / rel).write_text(content)
    (other / "actions").write_text("")
    assert [d.file for d in validate_package(other, "other")[1]] == ["actions"]


def test_the_walk_is_bounded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(manifest, "MAX_PACKAGE_ENTRIES", 5)
    folder = write_package(tmp_path / "hello")
    for i in range(10):
        (folder / "app" / f"page{i}.tsx").write_text("")
    diags = validate_package(folder, "hello")[1]
    assert "more than 5 files" in diags[-1].message
