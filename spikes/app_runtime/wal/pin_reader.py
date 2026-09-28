"""Experiment 7 (abuse case): an untrusted read-only reader that opens mode=ro
and never finishes its read transaction. Shows whether it can pin the WAL.

  python pin_reader.py /data SECONDS
"""

import sqlite3
import sys
import time

con = sqlite3.connect(f"file:{sys.argv[1]}/app.sqlite?mode=ro", uri=True, isolation_level=None)
con.execute("BEGIN")
n = con.execute("SELECT n FROM counter").fetchone()[0]
time.sleep(float(sys.argv[2]))
print({"pin_reader": {"held_read_tx_seconds": float(sys.argv[2]), "saw_n": n}}, flush=True)
