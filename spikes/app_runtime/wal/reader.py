"""Experiment 7 reader (stands in for an exec container: :ro bind mount,
--read-only rootfs, no network, no caps, arbitrary UID).

Tries each access mode repeatedly for SECONDS and records: successful reads,
invariant violations (counter.n != count(events) inside one read transaction),
errors by message, and staleness (now - counter.at).

  python reader.py /data SECONDS
"""

import json
import os
import sqlite3
import sys
import time
from collections import Counter

root, seconds = sys.argv[1], float(sys.argv[2])
db = os.path.join(root, "app.sqlite")
MODES = {
    "mode=ro": f"file:{db}?mode=ro",
    "mode=ro (long-lived conn)": f"file:{db}?mode=ro",
    "immutable=1": f"file:{db}?mode=ro&immutable=1",
    "snapshot VACUUM INTO + immutable=1": f"file:{root}/snapshots/vacuum_into.sqlite?mode=ro&immutable=1",
    "snapshot backup API + immutable=1": f"file:{root}/snapshots/backup_api.sqlite?mode=ro&immutable=1",
}


def read_once(con):
    con.execute("BEGIN")
    try:
        n, at = con.execute("SELECT n, at FROM counter").fetchone()
        count = con.execute("SELECT count(*) FROM events").fetchone()[0]
        # Touch the newest pages too, not just the counter row.
        con.execute("SELECT max(id), sum(length(payload)) FROM events").fetchone()
    finally:
        con.execute("COMMIT")
    return n, count, at


res = {m: {"ok": 0, "violations": 0, "errors": Counter(), "lag_ms": []} for m in MODES}
long_lived = None
end = time.time() + seconds
while time.time() < end:
    for mode, uri in MODES.items():
        r = res[mode]
        try:
            if mode.endswith("(long-lived conn)"):
                long_lived = long_lived or sqlite3.connect(uri, uri=True, isolation_level=None)
                con = long_lived
            else:
                con = sqlite3.connect(uri, uri=True, isolation_level=None)
            n, count, at = read_once(con)
            if not mode.endswith("(long-lived conn)"):
                con.close()
            r["ok"] += 1
            if n != count:
                r["violations"] += 1
            r["lag_ms"].append((time.time() - at) * 1000)
        except sqlite3.Error as e:
            r["errors"][f"{type(e).__name__}: {e}"] += 1
            if mode.endswith("(long-lived conn)"):
                long_lived = None
    time.sleep(0.005)


def summary(r):
    lag = sorted(r["lag_ms"])
    q = lambda p: round(lag[min(len(lag) - 1, int(p * len(lag)))], 1) if lag else None  # noqa: E731
    return {"ok": r["ok"], "invariant_violations": r["violations"], "errors": dict(r["errors"]), "lag_ms_p50": q(0.5), "lag_ms_p95": q(0.95), "lag_ms_max": q(1.0)}


phase = os.environ.get("PHASE", "concurrent")
print(json.dumps({"phase": phase, "sqlite": sqlite3.sqlite_version, "files": sorted(os.listdir(root)), "modes": {m: summary(r) for m, r in res.items()}}), flush=True)
