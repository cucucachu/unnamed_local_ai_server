"""Experiment 7 writer (stands in for the platform, the only writer — D12).

Every transaction appends a ~2 KB row to `events` and bumps `counter`, so a
consistent reader always sees counter.n == count(events). Frequent WAL growth
+ periodic checkpoints (PASSIVE and TRUNCATE) stress readers that ignore the
WAL. Every SNAP_EVERY seconds it publishes snapshots with VACUUM INTO and the
backup API (write to a temp name, fsync, atomic rename).

  python writer.py /data SECONDS
"""

import os
import sqlite3
import sys
import time

root, seconds = sys.argv[1], float(sys.argv[2])
SNAP_EVERY = 1.0
db_path = os.path.join(root, "app.sqlite")
snap_dir = os.path.join(root, "snapshots")
os.makedirs(snap_dir, exist_ok=True)

con = sqlite3.connect(db_path, isolation_level=None)
con.execute("PRAGMA journal_mode=WAL")
con.execute("PRAGMA synchronous=NORMAL")
con.execute("PRAGMA wal_autocheckpoint=200")
con.executescript(
    """
    CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at REAL NOT NULL, payload BLOB NOT NULL);
    CREATE TABLE IF NOT EXISTS counter (id INTEGER PRIMARY KEY CHECK (id = 1), n INTEGER NOT NULL, at REAL NOT NULL);
    INSERT OR IGNORE INTO counter VALUES (1, 0, 0);
    """
)


def publish(kind):
    tmp = os.path.join(snap_dir, f".{kind}.tmp")
    final = os.path.join(snap_dir, f"{kind}.sqlite")
    if os.path.exists(tmp):
        os.remove(tmp)
    t0 = time.perf_counter()
    if kind == "vacuum_into":
        con.execute("VACUUM INTO ?", (tmp,))
    else:
        dst = sqlite3.connect(tmp)
        con.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")
        dst.close()
    fd = os.open(tmp, os.O_RDONLY)
    os.fsync(fd)
    os.close(fd)
    os.chmod(tmp, 0o444)
    os.replace(tmp, final)
    return (time.perf_counter() - t0) * 1000


stats = {"tx": 0, "checkpoints": 0, "checkpoint_busy": 0, "wal_max_bytes": 0, "snapshots": 0, "snap_ms": []}
end = time.time() + seconds
next_snap = time.time()
payload = os.urandom(2048)
while time.time() < end:
    con.execute("BEGIN IMMEDIATE")
    now = time.time()
    con.execute("INSERT INTO events (at, payload) VALUES (?, ?)", (now, payload))
    con.execute("UPDATE counter SET n = n + 1, at = ? WHERE id = 1", (now,))
    con.execute("COMMIT")
    stats["tx"] += 1
    if stats["tx"] % 500 == 0:
        busy, _, _ = con.execute("PRAGMA wal_checkpoint(TRUNCATE)" if stats["tx"] % 1000 == 0 else "PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        stats["checkpoints"] += 1
        stats["checkpoint_busy"] += busy
        stats["wal_max_bytes"] = max(stats["wal_max_bytes"], os.path.getsize(db_path + "-wal"))
    if now >= next_snap:
        stats["snap_ms"].append(round(publish("vacuum_into"), 1))
        stats["snap_ms"].append(round(publish("backup_api"), 1))
        stats["snapshots"] += 2
        next_snap = now + SNAP_EVERY
    time.sleep(0.001)

rows = con.execute("SELECT n FROM counter").fetchone()[0]
mode = os.environ.get("WRITER_EXIT", "clean")
print({"writer": {**stats, "rows": rows, "db_bytes": os.path.getsize(db_path), "sqlite": sqlite3.sqlite_version, "exit": mode}}, flush=True)
if mode == "crash":
    os._exit(9)  # leave -wal/-shm behind, like a killed platform container
con.close()
