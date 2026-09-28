"""RLS overhead benchmark (#194), interleaved.

Four pools open at once, each op timed round-robin across them so host noise
hits every mode equally:
  base   - superuser (RLS never applies), plain AsyncConnectionPool
  pool   - superuser, RlsConnectionPool (cost of set_config on checkout + reset)
  policy - agent (owner, FORCE RLS), plain pool, app.user_id fixed per connection
  rls    - agent, RlsConnectionPool: what ships
"""

import asyncio
import random
import statistics
import sys
import time
import uuid

import psycopg
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "services" / "agent-server"))
from app.db import rls  # noqa: E402
from app.db.settings import PgSettingsStore  # noqa: E402
from app.db.threads import PgThreadStore  # noqa: E402
from app.db.turn_stats import PgTurnStatsStore  # noqa: E402

PORT = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
DB = sys.argv[3] if len(sys.argv) > 3 else "bench"
SUPER = f"postgresql://homeai:scratch@127.0.0.1:{PORT}/{DB}"
AGENT = f"postgresql://agent:agentpw@127.0.0.1:{PORT}/{DB}"
KW = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}
MODES = (sys.argv[4] if len(sys.argv) > 4 else "base,pool,policy,rls").split(",")
_UNKNOWN = object()


class CachedRlsPool(AsyncConnectionPool):
    """Sets app.user_id on checkout only when the connection's last value differs."""

    async def getconn(self, timeout=None):
        conn = await super().getconn(timeout)
        want = rls._user_id.get() or ""
        if getattr(conn, "_rls_user", _UNKNOWN) != want:
            conn._rls_user = _UNKNOWN
            await conn.execute("SELECT set_config('app.user_id', %s, false)", (want,))
            conn._rls_user = want
        return conn


async def main():
    with psycopg.connect(SUPER) as c:
        tid, owner = c.execute(
            "select c.thread_id, t.owner_user_id::text from checkpoints c join threads t on t.id::text = c.thread_id"
            " where t.title = 'bench' group by 1, 2 having count(*) = 360 order by 1 offset 11 limit 1"
        ).fetchone()
        wtid = c.execute(
            "select id::text from threads where owner_user_id=%s and id::text<>%s limit 1", (owner, tid)
        ).fetchone()[0]
        cid, nck = c.execute(
            "select (array_agg(checkpoint_id order by checkpoint_id))[count(*)/2], count(*) from checkpoints where thread_id=%s",
            (tid,),
        ).fetchone()
        for t in ["threads", "user_settings", "checkpoints", "checkpoint_blobs",
                  "checkpoint_writes", "turn_stats", "settings"]:
            c.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        c.commit()
    print(f"thread {tid[:8]}… has {nck} checkpoints; N={N}")

    ctx = {}
    for mode in MODES:
        dsn = SUPER if mode in ("base", "pool") else AGENT
        kw = dict(KW)
        if mode == "policy":
            kw["options"] = f"-c app.user_id={owner}"
        cls = {"pool": rls.RlsConnectionPool, "rls": rls.RlsConnectionPool,
               "cached": CachedRlsPool}.get(mode, AsyncConnectionPool)
        pool = cls(dsn, open=False, kwargs=kw, min_size=4)
        await pool.open()
        await pool.wait()
        ctx[mode] = pool

    rls.bind_user(owner)  # ignored by plain pools

    def ops(pool):
        saver = AsyncPostgresSaver(pool)
        threads, settings, stats = PgThreadStore(pool), PgSettingsStore(pool), PgTurnStatsStore(pool)
        cfg = {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
        wcfg = {"configurable": {"thread_id": wtid, "checkpoint_ns": ""}}
        cfg_id = {"configurable": {"thread_id": tid, "checkpoint_ns": "", "checkpoint_id": cid}}

        async def put():
            ck = empty_checkpoint()
            ck["id"] = str(uuid.uuid4())
            c = await saver.aput(wcfg, ck, {"source": "bench"}, {})
            await saver.aput_writes(c, [("messages", "x"), ("messages", "y")], str(uuid.uuid4()))

        async def history():
            return [x async for x in saver.alist(cfg)]

        async def get_messages():
            await threads.get(tid, owner)
            await saver.aget_tuple(cfg)
            await stats.list_for_thread(tid)

        return {
            "aget_tuple latest": lambda: saver.aget_tuple(cfg),
            "aget_tuple by id": lambda: saver.aget_tuple(cfg_id),
            f"alist full history ({nck})": history,
            "aput + aput_writes": put,
            "threads.get": lambda: threads.get(tid, owner),
            "threads.touch": lambda: threads.touch(tid),
            "threads.list_for_owner": lambda: threads.list_for_owner(owner),
            "turn_stats.list_for_thread": lambda: stats.list_for_thread(tid),
            "settings.get_document": lambda: settings.get_document(owner),
            "GET messages (composite)": get_messages,
        }

    per_mode = {m: ops(p) for m, p in ctx.items()}
    names = list(per_mode["base"])
    print(f"{'op (median ms)':30} {'base':>8} " + " ".join(f"{m:>16}" for m in MODES[1:]))
    for name in names:
        n = max(N // 20, 30) if "history" in name else N
        samples = {m: [] for m in MODES}
        for i in range(n + 20):
            order = MODES[:]
            random.shuffle(order)
            for m in order:
                t0 = time.perf_counter()
                await per_mode[m][name]()
                if i >= 20:
                    samples[m].append((time.perf_counter() - t0) * 1000)
        med = {m: statistics.median(v) for m, v in samples.items()}
        b = med["base"]
        print(f"{name:30} {b:8.3f} " + " ".join(
            f"{med[m]:8.3f} {100 * (med[m] - b) / b:+6.1f}%" for m in MODES[1:]))
    for p in ctx.values():
        await p.close()


asyncio.run(main())
