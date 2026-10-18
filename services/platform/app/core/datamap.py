"""The data map (M22-06, docs/APP_TEMPLATES.md §5.1): what apps the user has and what they hold.

Every live instance in the user's spaces (a routine run's `space_scope` only),
spaces in `list_user_spaces` order and apps by name within each: the app, the
space and the user's role there, `app.json`'s `homeai.description`, and each
table of the instance's database with its row count and the descriptions the
tracked version's `schema.sql` comments gave it (`app_versions.tables_doc`,
parsed on build). Tables come from the live database, in creation order; if it
can't be read, from `tables_doc` with no counts.
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app.core import spaces
from app.core.appdata import AppData
from app.core.principal import Principal

Row = dict[str, Any]

_INSTANCES = """
SELECT i.id, i.app_id, i.space_id, a.slug, a.name,
       v.manifest -> 'homeai' ->> 'description' AS description,
       COALESCE(v.tables_doc, '[]'::jsonb) AS tables_doc
FROM app_instances i
JOIN apps a ON a.id = i.app_id AND a.archived_at IS NULL
LEFT JOIN app_versions v ON v.id = i.version_id
    OR (i.version_id IS NULL AND v.app_id = i.app_id AND v.kind = 'working')
WHERE i.uninstalled_at IS NULL AND i.space_id = ANY(%s)
ORDER BY lower(a.name), a.slug, i.id
"""


def _tables(tables_doc: list[Row], rows: dict[str, int] | None) -> list[Row]:
    docs = {d["name"].lower(): d for d in tables_doc if isinstance(d, dict) and "name" in d}

    def entry(name: str, count: int | None) -> Row:
        doc = docs.get(name.lower(), {})
        return {
            "name": name,
            "rows": count,
            "description": doc.get("description"),
            "columns": doc.get("columns") or {},
        }

    if rows is None:
        return [entry(d["name"], None) for d in docs.values()]
    return [entry(name, count) for name, count in rows.items()]


async def data_map(conn: AsyncConnection, principal: Principal, appdata: AppData) -> list[Row]:
    user_spaces = await spaces.list_user_spaces(conn, principal.user_id, only=principal.space_scope)
    if not user_spaces:
        return []
    cur = await conn.execute(_INSTANCES, ([s["id"] for s in user_spaces],))
    instances = await cur.fetchall()
    out = []
    for space in user_spaces:
        for inst in (i for i in instances if i["space_id"] == space["id"]):
            rows = await appdata.table_rows(inst, space)
            out.append(
                {
                    "id": inst["id"],
                    "app": {"id": inst["app_id"], "slug": inst["slug"], "name": inst["name"]},
                    "space": {
                        "id": space["id"],
                        "slug": space["slug"],
                        "name": space["name"],
                        "kind": space["kind"],
                        "role": space["role"],
                    },
                    "description": inst["description"],
                    "tables": _tables(inst["tables_doc"], rows),
                }
            )
    return out
