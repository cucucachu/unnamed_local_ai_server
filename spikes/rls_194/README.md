# #194 feasibility spike: Postgres RLS cost on agent-server's hot queries

**Outcome:** the first pass was measured against a 20% per-query limit and
stopped there. The lead then shipped Design A (the one on this branch,
`services/agent-server/app/db/rls.py`) under a new budget of **≤ 15 ms
whole-turn overhead on a fake-model turn**. Re-measured on the shipped code
from the pre-deploy dump: **+3.7, +5.7 and +2.3 ms** over three runs, and
**+8.3 ms** earlier. All are within budget. Per query, Design A costs
+0.05–0.4 ms (tables below). Design B′ is kept as the documented
alternative.

## Setup

- `postgres:17` scratch container, restored from the dump
  `/home/cody/code/wt/fix-194-backup/homeai-pre-deploy-fix-194.dump`, which is
  not in the repo. Nothing in this directory contains its data.
- `inflate.sql` scales the restored data to 20 users × 50 threads. Each thread
  is a clone of the largest real thread (360 checkpoints, 114 blobs, 515
  writes). Totals: 1,003 threads, about 360k checkpoints, 114k blobs, 516k
  writes and 59k `turn_stats` rows.
- Timings are wall clock from the host to the container over loopback, using
  real `AsyncPostgresSaver` and store calls. They are interleaved across modes
  in random order, 1,000 samples per op, and the median is reported.
- `base` is the superuser, for which RLS never applies. `rls` is role `agent`
  (the table owner) with `FORCE ROW LEVEL SECURITY` and `RlsConnectionPool`.

## Design A: the ticket's design (branch code)

| op | base (ms) | rls (ms) | Δ |
|---|---:|---:|---:|
| `aget_tuple` latest | 0.528 | 0.891 | +69% |
| `aget_tuple` by id | 0.614 | 1.009 | +64% |
| `aput` + `aput_writes` | 1.377 | 1.981 | +44% |
| `threads.get` | 0.114 | 0.179 | +58% |
| `turn_stats.list_for_thread` | 0.190 | 0.311 | +64% |
| `settings.get_document` | 0.111 | 0.177 | +60% |
| GET messages (thread get + `aget_tuple` + stats) | 0.558 | 0.900 | +61% |
| Whole turn, fake model, real graph | 88.0 | 96.3 | +9.4% (+8.3 ms) |

The overhead has two sources, of similar size, that add up:

- **Checkout.** Setting `app.user_id` on each checkout costs an extra round
  trip, about +0.05–0.07 ms. For sub-0.2 ms queries alone that is +20–60%.
- **Policy.** Measured server side with `EXPLAIN ANALYZE` on generic plans,
  `SELECT_SQL` goes from about 0.10–0.14 ms to 0.29 ms. The checkpoint query
  reads three tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`).
  Each one gets its own hashed `thread_id IN (own thread ids)` subplan that
  rescans `threads`. The `(SELECT current_setting(...))` InitPlan form already
  avoids re-evaluating the setting per row. A correlated `EXISTS` with an
  expression index on `threads((id::text))` was also tried. The planner turned
  it back into the same hashed subplan, with no real gain.

## Design B′: under the threshold, but needs sign-off on two deviations

Two changes bring the hot reads under the threshold:

- **Owner column on the checkpoint tables.** Add `owner_user_id uuid DEFAULT
  nullif(current_setting('app.user_id', true), '')::uuid` to `checkpoints`,
  `checkpoint_blobs`, `checkpoint_writes` and `turn_stats`. LangGraph's
  inserts don't name the column, so the default fills it in. Existing rows are
  backfilled from `threads`. The policy then compares a column per row. Only
  the `checkpoints` `WITH CHECK` still joins to `threads`, which checks that
  the thread is owned. The SQL is in `design_b_owner_column.sql` and
  `design_b_prime_policies.sql`.
- **Cached setting on the pool (`cached_pool.py`).** `set_config` is issued
  only when the connection's last value differs, and there is no reset on
  return. The value is always corrected before a connection is handed out.
  The trade-off is that an idle connection keeps the last user's id.

| op | base | B′ + cached | Δ |
|---|---:|---:|---:|
| `aget_tuple` latest / by id | 0.237 / 0.216 | 0.224 / 0.200 | −6% / −7% |
| `aput` + `aput_writes` | 0.870 | 0.958 | +10% |
| `threads.get` / `touch` / `list_for_owner` | | | +1% / +4% / +2% |
| `turn_stats.list_for_thread` / `settings.get_document` | | | +2% / +1% |
| GET messages (composite) | 0.608 | 0.637 | +5% |
| Whole turn, fake model | 52.3 | 54.4 | +4.1% (+2.1 ms) |

`alist` over the full 360-checkpoint history is dominated by deserialization.
It varied by ±20% between runs in both directions and is not a useful signal
here.

## Reproduce

```bash
docker run -d --name rls-scratch -e POSTGRES_USER=homeai -e POSTGRES_PASSWORD=scratch \
  -e POSTGRES_DB=homeai -p 127.0.0.1::5432 postgres:17
# roles, as on the live volume: run infra/postgres/db-init.sh against it with
# AGENT_DB_PASSWORD=agentpw (see services/agent-server/tests/conftest.py's
# pg_server for the exact `docker run`); before the restore with main's copy,
# after it with this branch's
docker exec -i rls-scratch pg_restore -U homeai -d homeai \
  < /home/cody/code/wt/fix-194-backup/homeai-pre-deploy-fix-194.dump
docker exec rls-scratch psql -U homeai -d postgres -c "create database bench template homeai"
docker exec -i rls-scratch psql -U homeai -d bench < inflate.sql
# apply app.db.rls.rls_ddl() as agent, then:
PORT=$(docker port rls-scratch 5432 | cut -d: -f2)
uv run --project ../../services/agent-server python bench_queries.py $PORT 1000 bench base,pool,policy,rls,cached
uv run --project ../../services/agent-server python bench_turn.py $PORT bench 60
# Design B′: create database bench3 from bench, apply design_b_*.sql as agent,
# backfill owner_user_id as the superuser, then run the same with bench3 and `cached`.
docker rm -f rls-scratch   # it holds a copy of real chat history
```
