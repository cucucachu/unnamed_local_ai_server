"""Experiment 6a: sqlite3def against a live DB (with rows) for every case.

For each case: dry run without and with --enable-drop, then apply with
--enable-drop on a copy and record whether it succeeded and whether rows
survived. Run offline:

  docker run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD":/spike:ro -v "$PWD/results":/out \
    python:3.12-slim python /spike/schema/sqldef_matrix.py /spike/.tools/sqlite3def /out
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from cases import BASE, CASES, SEED  # noqa: E402

BIN = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else None


def live_db(path):
    con = sqlite3.connect(path)
    con.executescript(BASE + SEED)
    con.execute("PRAGMA journal_mode=WAL")
    con.close()


def run(args, stdin):
    p = subprocess.run([BIN, *args], input=stdin, capture_output=True, text=True)
    return {"exit": p.returncode, "out": (p.stdout + p.stderr).strip()}


def ddl(out):
    return [l for l in out.splitlines() if l and not l.startswith("--") and l not in ("BEGIN;", "COMMIT;")]


def counts(path):
    con = sqlite3.connect(path)
    try:
        r = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cols = [c[1] for c in con.execute("PRAGMA table_info(items)")]
        return {"rows": r, "items_columns": cols}
    finally:
        con.close()


version = subprocess.run([BIN, "--version"], capture_output=True, text=True).stdout.strip()
results = {"sqlite3def": version, "sqlite": sqlite3.sqlite_version, "cases": []}
tmp = tempfile.mkdtemp()
for name, expected, desired in CASES:
    db = os.path.join(tmp, f"{name}.sqlite")
    live_db(db)
    safe = run([db, "--dry-run"], desired)
    drop = run([db, "--dry-run", "--enable-drop"], desired)
    check = run([db, "--check"], desired)
    applied = os.path.join(tmp, f"{name}.applied.sqlite")
    shutil.copy(db, applied)
    for ext in ("-wal", "-shm"):
        if os.path.exists(db + ext):
            shutil.copy(db + ext, applied + ext)
    ap = run([applied, "--apply", "--enable-drop"], desired)
    rerun = run([applied, "--dry-run", "--enable-drop"], desired)
    case = {
        "case": name,
        "expected": expected,
        "dry_run_safe": ddl(safe["out"]),
        "dry_run_safe_skipped": [l for l in safe["out"].splitlines() if "Skipped" in l or "skipped" in l.lower()],
        "dry_run_enable_drop": ddl(drop["out"]),
        "check_exit": check["exit"],
        "apply_enable_drop_exit": ap["exit"],
        "apply_error": ap["out"] if ap["exit"] else None,
        "after_apply": counts(applied),
        "idempotent_after_apply": ddl(rerun["out"]) == [],
    }
    results["cases"].append(case)
    print(f"== {name} ({expected})")
    print("  safe       :", case["dry_run_safe"] or "-", case["dry_run_safe_skipped"] or "")
    print("  enable-drop:", case["dry_run_enable_drop"] or "-")
    print("  apply exit :", ap["exit"], (ap["out"][-200:] if ap["exit"] else ""), "| after:", case["after_apply"], "| idempotent:", case["idempotent_after_apply"])

if OUT:
    with open(os.path.join(OUT, "sqldef.json"), "w") as f:
        json.dump(results, f, indent=2)
