"""Experiment 6b runner: the same case matrix through pydiff.py.

  docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD":/spike:ro -v "$PWD/results":/out \
    python:3.12-slim python /spike/schema/pydiff_matrix.py /out
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from cases import BASE, CASES, SEED  # noqa: E402
from pydiff import SchemaError, apply, plan  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else None
tmp = tempfile.mkdtemp()
results = {"sqlite": sqlite3.sqlite_version, "cases": []}


def snapshot(path):
    con = sqlite3.connect(path)
    try:
        rows = {t: con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {"rows": rows, "items": con.execute("SELECT * FROM items ORDER BY id").fetchall() if "items" in rows else None}
    finally:
        con.close()


for name, expected, desired in CASES:
    db = os.path.join(tmp, f"{name}.sqlite")
    con = sqlite3.connect(db)
    con.executescript(BASE + SEED)
    con.execute("PRAGMA journal_mode=WAL")
    con.close()
    p = plan(db, desired)
    refused = apply(db, desired)  # without approval
    shutil.copy(db, db + ".copy")
    a = apply(db + ".copy", desired, allow_destructive=True)
    again = plan(db + ".copy", desired)
    case = {
        "case": name,
        "expected": expected,
        "summary": p["summary"],
        "steps": [(s["kind"], s["op"], s["reason"]) for s in p["steps"]],
        "sql": [q for s in p["steps"] for q in s["sql"]],
        "without_approval": "applied" if refused.get("applied") else refused.get("error", "nothing to do"),
        "with_approval_applied": a.get("applied"),
        "error": a.get("error"),
        "after": snapshot(db + ".copy"),
        "idempotent": again["steps"] == [],
    }
    results["cases"].append(case)
    print(f"== {name} (expected {expected}) -> {p['summary']}")
    for s in p["steps"]:
        print(f"   [{s['kind']}] {s['op']} {s['table']}: {s['reason']}")
    print(f"   without approval: {case['without_approval']} | approved apply: {a.get('applied')} {a.get('error') or ''} | rows after: {case['after']['rows']} | idempotent: {case['idempotent']}")

# Hardening: schema.sql may only create tables/indexes.
for bad in ["ATTACH '/tmp/x.db' AS x;", "CREATE TABLE t (a); INSERT INTO t VALUES (1);", "PRAGMA writable_schema=1;", "CREATE TABLE t (a); CREATE TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; END;", "CREATE TABLE t (a); CREATE VIEW v AS SELECT * FROM t;"]:
    try:
        plan(os.path.join(tmp, "noop.sqlite"), bad)
        verdict = "ACCEPTED"
    except SchemaError as e:
        verdict = f"rejected: {e}"
    results.setdefault("rejected_schema", []).append({"schema": bad, "verdict": verdict})
    print(f"schema {bad!r}: {verdict}")

if OUT:
    with open(os.path.join(OUT, "pydiff.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)
