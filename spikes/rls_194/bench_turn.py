"""Whole-turn DB cost (#194): the real agent graph + AsyncPostgresSaver, fake model.

For each mode, run T turns (one user message, one text reply) on a fresh
owned thread, interleaved across modes; report the median `ainvoke` wall
time. The fake model answers in-process over loopback, so the difference is
the checkpointer/pool cost.
"""

import asyncio
import random
import statistics
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "services" / "agent-server"))
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg_pool import AsyncConnectionPool  # noqa: E402
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: E402

from app.agent.build import build_agent  # noqa: E402
from app.db import rls  # noqa: E402
from tests.conftest import _UvicornThreadServer  # noqa: E402
from tests.fake_model.scripting import FakeModel, TextTurn  # noqa: E402
from tests.fake_model.server import create_fake_model_app  # noqa: E402

PORT, DB = sys.argv[1], sys.argv[2]
T = int(sys.argv[3]) if len(sys.argv) > 3 else 60
SUPER = f"postgresql://homeai:scratch@127.0.0.1:{PORT}/{DB}"
AGENT = f"postgresql://agent:agentpw@127.0.0.1:{PORT}/{DB}"
KW = {"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}


async def main():
    fake = FakeModel()
    srv = _UvicornThreadServer(create_fake_model_app(fake))
    srv.start()
    fake.base_url = f"http://127.0.0.1:{srv.port}/v1"
    settings = fake.settings()
    with psycopg.connect(SUPER) as c:
        tid, owner = c.execute(
            "select t.id::text, t.owner_user_id::text from threads t where title='bench' order by id offset 17 limit 1"
        ).fetchone()
    sys.argv.append("")
    rls_cls = rls.RlsConnectionPool
    if sys.argv[4] == "cached":
        import importlib.util
        spec = importlib.util.spec_from_file_location("b2", str(__import__("pathlib").Path(__file__).with_name("cached_pool.py")))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rls_cls = mod.CachedRlsPool
    modes = {"base": (SUPER, rls_cls if sys.argv[4] == "cached" else AsyncConnectionPool), "rls": (AGENT, rls_cls)}
    agents, pools = {}, []
    for m, (dsn, cls) in modes.items():
        pool = cls(dsn, open=False, kwargs=KW, min_size=4)
        await pool.open()
        await pool.wait()
        pools.append(pool)
        agents[m] = build_agent(settings, AsyncPostgresSaver(pool))
    rls.bind_user(owner)
    cfg = {"configurable": {"thread_id": tid}}
    samples = {m: [] for m in modes}
    for i in range(T + 5):
        order = list(modes)
        random.shuffle(order)
        for m in order:
            fake.queue(TextTurn("ok"))
            t0 = time.perf_counter()
            await agents[m].ainvoke({"messages": [{"role": "user", "content": "hi"}]}, config=cfg)
            if i >= 5:
                samples[m].append((time.perf_counter() - t0) * 1000)
            st = await agents[m].aget_state(cfg)
    b = statistics.median(samples["base"])
    r = statistics.median(samples["rls"])
    print(f"whole turn (fake model), thread w/ growing history: base {b:.2f} ms, rls {r:.2f} ms, {100*(r-b)/b:+.1f}% ({r-b:+.2f} ms)")
    print(f"messages at end: {len(st.values['messages'])}")
    for p in pools:
        await p.close()
    srv.stop()


asyncio.run(main())
