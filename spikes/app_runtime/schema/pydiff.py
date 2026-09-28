"""Experiment 6b: compute SQLite migrations ourselves (stdlib only).

desired schema.sql -> scratch in-memory DB (under an authorizer that only
permits CREATE TABLE / CREATE INDEX) -> compare with the live DB using PRAGMAs
plus each table's parsed column/constraint text -> a classified plan:

  additive     CREATE TABLE, ADD COLUMN (when SQLite allows it), CREATE INDEX
  safe         DROP/recreate INDEX, table rebuild that only relaxes (default change, drop NOT NULL)
  destructive  DROP TABLE, DROP COLUMN, rebuild that tightens or retypes (type, NOT NULL, UNIQUE, CHECK, FK, PK)

apply() runs the whole plan in one transaction on the live DB, checks foreign
keys, re-diffs (must be empty) and only then commits.

  python schema/pydiff.py live.sqlite schema.sql [--apply] [--allow-destructive]
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_INSERT,  # sqlite_master bookkeeping
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_REINDEX,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_TRANSACTION,
}


class SchemaError(Exception):
    pass


def _authorizer(action, arg1, arg2, dbname, source):
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE) and arg1 not in ("sqlite_master", "sqlite_temp_master", "sqlite_sequence"):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def scratch_from(schema_sql: str) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.set_authorizer(_authorizer)
    try:
        con.executescript(schema_sql)
    except sqlite3.Error as e:
        raise SchemaError(f"schema.sql: {e} (only CREATE TABLE / CREATE INDEX statements are allowed)") from e
    con.set_authorizer(None)
    return con


# --- CREATE TABLE text -> column defs + table constraints ---------------------

_TOKEN = re.compile(r"""\s+|--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\]|[(),]|[^\s(),'"`\[]+""", re.S)
_CONSTRAINT_KW = ("constraint", "primary", "unique", "check", "foreign")


def _norm(tokens: list[str]) -> str:
    out = []
    for t in tokens:
        if not t.strip() or t.startswith("--") or t.startswith("/*"):
            continue
        if t[0] in "\"`[":
            out.append(t[1:-1].replace('""', '"'))
        elif t[0] == "'":
            out.append(t)
        else:
            out.append(t.lower())
    return " ".join(out).replace("( ", "(").replace(" )", ")").replace(" ,", ",")


def parse_table(sql: str) -> dict:
    toks = _TOKEN.findall(sql)
    start = toks.index("(")
    depth, parts, cur, raw, raw_parts, end = 0, [], [], [], [], len(toks)
    for i in range(start + 1, len(toks)):
        t = toks[i]
        if t == "(":
            depth += 1
        elif t == ")":
            if depth == 0:
                end = i
                break
            depth -= 1
        if t == "," and depth == 0:
            parts.append(cur)
            raw_parts.append("".join(raw).strip())
            cur, raw = [], []
            continue
        cur.append(t)
        raw.append(t)
    parts.append(cur)
    raw_parts.append("".join(raw).strip())
    columns, constraints = {}, []
    for p, r in zip(parts, raw_parts):
        words = [w for w in p if w.strip() and not w.startswith("--") and not w.startswith("/*")]
        if not words:
            continue
        if words[0].lower() in _CONSTRAINT_KW:
            constraints.append(_norm(p))
        else:
            name = _norm([words[0]])
            columns[name] = {"norm": _norm(p), "text": r}
    options = _norm(toks[end + 1 :])
    return {"columns": columns, "constraints": sorted(constraints), "options": options}


# --- introspection -------------------------------------------------------------


def describe(con: sqlite3.Connection) -> dict:
    tables, indexes = {}, {}
    for name, sql in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        info = {r[1]: {"type": (r[2] or "").lower(), "notnull": r[3], "dflt": r[4], "pk": r[5], "hidden": r[6]} for r in con.execute(f'PRAGMA table_xinfo("{name}")')}
        fks = sorted(tuple(r[2:5]) for r in con.execute(f'PRAGMA foreign_key_list("{name}")'))
        uniques = sorted(r[1] for r in con.execute(f'PRAGMA index_list("{name}")') if r[3] in ("u", "pk"))
        tables[name] = {"sql": sql, "parsed": parse_table(sql), "cols": info, "fks": fks, "auto_unique": len(uniques)}
    for name, tbl, sql in con.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
        indexes[name] = {"table": tbl, "sql": sql, "norm": _norm(_TOKEN.findall(sql))}
    return {"tables": tables, "indexes": indexes}


@dataclass
class Step:
    kind: str  # additive | safe | destructive
    op: str
    table: str
    sql: list
    reason: str


def _can_add_column(col_norm: str, info: dict) -> tuple[bool, str]:
    if info["pk"]:
        return False, "new PRIMARY KEY column"
    if re.search(r"\bunique\b", col_norm):
        return False, "new UNIQUE column"
    if info["notnull"] and info["dflt"] in (None, "NULL"):
        return False, "NOT NULL without a default"
    if info["dflt"] and re.match(r"(?i)^\(|current_(time|date|timestamp)", info["dflt"]):
        return False, "non-constant default"
    if info["hidden"] in (2, 3) and "stored" in col_norm:
        return False, "STORED generated column"
    if re.search(r"\breferences\b", col_norm) and info["dflt"] not in (None, "NULL"):
        return False, "REFERENCES with a non-NULL default"
    return True, ""


def _rebuild(name: str, cur: dict, want: dict, want_indexes: dict) -> Step:
    kept = [c for c in want["cols"] if c in cur["cols"] and not want["cols"][c]["hidden"]]
    dropped = [c for c in cur["cols"] if c not in want["cols"]]
    reasons, tighten = [], bool(dropped)
    if dropped:
        reasons.append(f"drops column(s) {dropped}")
    for c in kept:
        a, b = cur["cols"][c], want["cols"][c]
        if a["type"] != b["type"]:
            reasons.append(f"{c}: type {a['type']!r} -> {b['type']!r}")
            tighten = True
        if b["notnull"] and not a["notnull"]:
            reasons.append(f"{c}: adds NOT NULL")
            tighten = True
        if a["notnull"] and not b["notnull"]:
            reasons.append(f"{c}: drops NOT NULL")
        if a["dflt"] != b["dflt"]:
            reasons.append(f"{c}: default {a['dflt']} -> {b['dflt']}")
        if a["pk"] != b["pk"]:
            reasons.append(f"{c}: primary key change")
            tighten = True
        if cur["parsed"]["columns"].get(c, {}).get("norm") != want["parsed"]["columns"][c]["norm"] and not reasons:
            reasons.append(f"{c}: definition {cur['parsed']['columns'][c]['norm']!r} -> {want['parsed']['columns'][c]['norm']!r}")
            tighten = True
    if cur["fks"] != want["fks"]:
        reasons.append("foreign keys change")
        tighten = True
    if cur["parsed"]["constraints"] != want["parsed"]["constraints"] or cur["auto_unique"] < want["auto_unique"]:
        reasons.append("table constraints change (UNIQUE/CHECK/PK/FK)")
        tighten = True
    if cur["parsed"]["options"] != want["parsed"]["options"]:
        reasons.append(f"table options {cur['parsed']['options']!r} -> {want['parsed']['options']!r}")
        tighten = True
    new_sql = re.sub(r'(?is)^\s*create\s+table\s+(if\s+not\s+exists\s+)?("[^"]+"|`[^`]+`|\[[^\]]+\]|\S+)', f'CREATE TABLE "_homeai_new_{name}"', want["sql"], count=1)
    cols = ", ".join(f'"{c}"' for c in kept)
    sql = [new_sql, f'INSERT INTO "_homeai_new_{name}" ({cols}) SELECT {cols} FROM "{name}"', f'DROP TABLE "{name}"', f'ALTER TABLE "_homeai_new_{name}" RENAME TO "{name}"']
    sql += [ix["sql"] for ix in want_indexes.values() if ix["table"] == name]
    added_like = [c for c in want["cols"] if c not in cur["cols"]]
    for c in added_like:
        ok, why = _can_add_column(want["parsed"]["columns"][c]["norm"], want["cols"][c])
        reasons.append(f"adds column {c}" + ("" if ok else f" that can't be added in place ({why}; fails if the table has rows — add a DEFAULT)"))
        tighten = tighten or not ok
    if dropped and added_like:
        reasons.append(f"hint: if {dropped} -> {added_like} is a rename, data in {dropped} is lost; renames are drop+add")
    return Step("destructive" if tighten else "safe", "rebuild", name, sql, "; ".join(reasons) or "definition text changed")


def diff(live: sqlite3.Connection, desired: sqlite3.Connection) -> list[Step]:
    cur, want = describe(live), describe(desired)
    steps: list[Step] = []
    rebuilt = set()
    for name, w in want["tables"].items():
        if name not in cur["tables"]:
            steps.append(Step("additive", "create_table", name, [w["sql"]], "new table"))
            continue
        c = w_cur = cur["tables"][name]
        added = [col for col in w["cols"] if col not in c["cols"]]
        dropped = [col for col in c["cols"] if col not in w["cols"]]
        common_same = all(
            c["cols"][col] == w["cols"][col] and c["parsed"]["columns"][col]["norm"] == w["parsed"]["columns"][col]["norm"]
            for col in w["cols"]
            if col in c["cols"]
        )
        rest_same = c["parsed"]["constraints"] == w["parsed"]["constraints"] and c["parsed"]["options"] == w["parsed"]["options"]
        fks_same = c["fks"] == sorted(fk for fk in w["fks"] if fk[1] not in added)
        if common_same and rest_same and fks_same and not dropped and not added:
            continue
        if common_same and rest_same and fks_same and not dropped:
            checks = [(col, *_can_add_column(w["parsed"]["columns"][col]["norm"], w["cols"][col])) for col in added]
            if all(ok for _, ok, _ in checks):
                for col in added:
                    steps.append(Step("additive", "add_column", name, [f'ALTER TABLE "{name}" ADD COLUMN {w["parsed"]["columns"][col]["text"]}'], f"new column {col}"))
                continue
        if common_same and rest_same and fks_same and not added and dropped:
            indexed = {r[2] for ixname, ix in cur["indexes"].items() if ix["table"] == name for r in live.execute(f'PRAGMA index_info("{ixname}")')}
            if not (set(dropped) & indexed) and not any(c["cols"][d]["pk"] for d in dropped):
                for col in dropped:
                    steps.append(Step("destructive", "drop_column", name, [f'ALTER TABLE "{name}" DROP COLUMN "{col}"'], f"drops column {col} and its data"))
                continue
        steps.append(_rebuild(name, w_cur, w, want["indexes"]))
        rebuilt.add(name)
    for name in cur["tables"]:
        if name not in want["tables"]:
            steps.append(Step("destructive", "drop_table", name, [f'DROP TABLE "{name}"'], f"drops table {name} and all its rows"))
    for name, ix in want["indexes"].items():
        if ix["table"] in rebuilt:
            continue
        old = cur["indexes"].get(name)
        if old is None:
            steps.append(Step("additive", "create_index", ix["table"], [ix["sql"]], f"new index {name}"))
        elif old["norm"] != ix["norm"]:
            steps.append(Step("safe", "replace_index", ix["table"], [f'DROP INDEX "{name}"', ix["sql"]], f"index {name} changed"))
    for name, ix in cur["indexes"].items():
        if name not in want["indexes"] and ix["table"] in want["tables"] and ix["table"] not in rebuilt:
            steps.append(Step("safe", "drop_index", ix["table"], [f'DROP INDEX "{name}"'], f"index {name} removed (no data loss)"))
    order = {"create_table": 0, "add_column": 1, "rebuild": 2, "drop_column": 3, "replace_index": 4, "create_index": 5, "drop_index": 6, "drop_table": 7}
    return sorted(steps, key=lambda s: order[s.op])


def plan(live_path: str, schema_sql: str) -> dict:
    live = sqlite3.connect(f"file:{live_path}?mode=ro", uri=True)
    steps = diff(live, scratch_from(schema_sql))
    live.close()
    kinds = {k: sum(s.kind == k for s in steps) for k in ("additive", "safe", "destructive")}
    return {"steps": [asdict(s) for s in steps], "summary": kinds, "needs_approval": kinds["destructive"] > 0}


def apply(live_path: str, schema_sql: str, allow_destructive: bool = False) -> dict:
    p = plan(live_path, schema_sql)
    if p["needs_approval"] and not allow_destructive:
        return {**p, "applied": False, "error": "destructive changes need approval"}
    con = sqlite3.connect(live_path, isolation_level=None)
    con.execute("PRAGMA foreign_keys=OFF")  # required for table rebuilds; must be set outside the transaction
    try:
        con.execute("BEGIN IMMEDIATE")
        for s in p["steps"]:
            for stmt in s["sql"]:
                con.execute(stmt)
        bad_fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if bad_fk:
            raise sqlite3.IntegrityError(f"foreign_key_check failed: {bad_fk[:5]}")
        left = diff(con, scratch_from(schema_sql))
        if left:
            raise RuntimeError(f"post-migration diff not empty: {[asdict(s) for s in left]}")
        con.execute("COMMIT")
        return {**p, "applied": True}
    except Exception as e:  # noqa: BLE001 — report any failure as a diagnostic and roll back
        con.execute("ROLLBACK")
        return {**p, "applied": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        con.close()


if __name__ == "__main__":
    live_path, schema_path = sys.argv[1], sys.argv[2]
    sql = open(schema_path).read()
    if "--apply" in sys.argv:
        out = apply(live_path, sql, "--allow-destructive" in sys.argv)
    else:
        out = plan(live_path, sql)
    print(json.dumps(out, indent=2))
