"""A minimal valid app package on disk, for the manifest and registry tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def manifest(slug: str = "hello", **top: Any) -> dict[str, Any]:
    doc = {
        "name": "Hello",
        "slug": slug,
        "version": "1.0.0",
        "homeai": {"sdk": "1", "icon": "happy-outline", "description": "Says hello."},
    }
    doc.update(top)
    return doc


FILES = {
    "AGENT.md": "# Hello\nShows a greeting.\n",
    "schema.sql": "",
    "app/_layout.tsx": "import { Stack } from 'expo-router';\nexport default Stack;\n",
    "app/index.tsx": "export default function Index() { return null; }\n",
    "app/item/[id].tsx": "export default function Item() { return null; }\n",
    "actions/addItem.sql": "INSERT INTO items (name) VALUES (:name);\n",
}


def write_package(folder: Path, doc: Any = None, files: dict[str, str] | None = None) -> Path:
    """`folder` becomes a valid package (with `doc` as app.json, if given; a str is written raw)."""
    folder.mkdir(parents=True, exist_ok=True)
    doc = manifest(folder.name) if doc is None else doc
    (folder / "app.json").write_text(doc if isinstance(doc, str) else json.dumps(doc, indent=2))
    for rel, content in (FILES if files is None else files).items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return folder
