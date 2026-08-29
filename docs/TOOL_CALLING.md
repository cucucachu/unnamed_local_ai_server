# M1-03: Tool-calling validation spike (risk gate)

**Verdict: GO** — native tool-calling via `bind_tools([...])` / OpenAI-style
`tools=[...]` against the real `model-runner` (llama.cpp server-vulkan +
Gemma 4, `--jinja`) is reliable, including through `deepagents.create_deep_agent`.
M2-03 can proceed as specced, using native tool binding rather than a
prompted ReAct fallback.

This was flagged as the highest-risk assumption in the project plan (see
GitHub issue #6). The spike was run **three full times** (75 model-backed
runs total across the 5 cases) against the real, GPU-loaded model — not
mocked — specifically to guard against writing a false-positive GO off a
single lucky run. All three runs were clean.

## How this was produced

- Real hardware: AMD Radeon 890M iGPU, Vulkan backend, `--n-gpu-layers 999`.
- Real model: `gemma-4-26B-A4B-it-Q4_0.gguf` (~14.6GB, Q4_0 quant), loaded by
  the `model-runner` compose service exactly as built in M1-01/M1-02 —
  no compose/Dockerfile changes were made for this ticket.
- Client: `spikes/tool_calling/spike.py`, a standalone `uv` project
  (`langchain-openai` v1.x + `deepagents` 0.7.x), run with
  `uv run python spike.py` from `spikes/tool_calling/`.
- `base_url` was the container's IP on the `homeai-net` bridge network
  (`http://172.18.0.2:8080/v1` in this run — see spike.py's header comment
  for why a hostname doesn't work here and isn't needed), `api_key="none"`,
  `model=gemma-4-26b-a4b-it` (from `.env`'s `MODEL_NAME`).
- Sampling params were **left at server defaults** (`--temp 1.0 --top-p 0.95
  --top-k 64`, from `docker-compose.yml`) — not overridden client-side —
  per the ticket spec, so these results reflect real production-like
  sampling variance, not a temperature=0 best case.
- 5 runs per case, fresh `ChatOpenAI` client and fresh conversation state
  per run, `max_retries=0` on the client so real flakiness isn't silently
  retried away.

## Results (representative run — all 3 runs were 25/25 or effectively so; see "Reproducibility" below)

### Detail (every run)

| Case | Description | Run | Result | Reason (if failed) |
|------|-------------|-----|--------|---------------------|
| C1 | single call (get_weather Paris) | 1 | PASS | |
| C1 | single call (get_weather Paris) | 2 | PASS | |
| C1 | single call (get_weather Paris) | 3 | PASS | |
| C1 | single call (get_weather Paris) | 4 | PASS | |
| C1 | single call (get_weather Paris) | 5 | PASS | |
| C2 | tool loop (weather -> final answer) | 1 | PASS | |
| C2 | tool loop (weather -> final answer) | 2 | PASS | |
| C2 | tool loop (weather -> final answer) | 3 | PASS | |
| C2 | tool loop (weather -> final answer) | 4 | PASS | |
| C2 | tool loop (weather -> final answer) | 5 | PASS | |
| C3 | multi-step (list_files -> read_file) | 1 | PASS | |
| C3 | multi-step (list_files -> read_file) | 2 | PASS | |
| C3 | multi-step (list_files -> read_file) | 3 | PASS | |
| C3 | multi-step (list_files -> read_file) | 4 | PASS | |
| C3 | multi-step (list_files -> read_file) | 5 | PASS | |
| C4 | tool restraint (2+2, no tool call) | 1 | PASS | |
| C4 | tool restraint (2+2, no tool call) | 2 | PASS | |
| C4 | tool restraint (2+2, no tool call) | 3 | PASS | |
| C4 | tool restraint (2+2, no tool call) | 4 | PASS | |
| C4 | tool restraint (2+2, no tool call) | 5 | PASS | |
| C5 | deepagents smoke (create hello.txt) | 1 | PASS | |
| C5 | deepagents smoke (create hello.txt) | 2 | PASS | |
| C5 | deepagents smoke (create hello.txt) | 3 | PASS | |
| C5 | deepagents smoke (create hello.txt) | 4 | PASS | |
| C5 | deepagents smoke (create hello.txt) | 5 | PASS | |

### Summary

| Case | Description | Pass rate | Case status (>= 4/5 required) |
|------|-------------|-----------|--------------------------------|
| C1 | single call (get_weather Paris) | 5/5 | PASS |
| C2 | tool loop (weather -> final answer) | 5/5 | PASS |
| C3 | multi-step (list_files -> read_file) | 5/5 | PASS |
| C4 | tool restraint (2+2, no tool call) | 5/5 | PASS |
| C5 | deepagents smoke (create hello.txt) | 5/5 | PASS |

**Overall: 25/25 runs passed. Exit code: 0.**

### Reproducibility

The exact matrix above (`uv run python spike.py`) was executed **three
separate times** against the same running container, all with the server's
default (non-zero) sampling temperature, to rule out a single lucky pass.
Result: **25/25, 25/25, 25/25** — 75/75 total, zero failures observed across
every case, every run, every repetition. No malformed tool-call JSON,
no missing tool calls, no spurious tool calls, no timeouts.

## Why GO, not GO-WITH-FLAGS

`docker-compose.yml`'s `model-runner` command already includes `--jinja`
(from M1-01) and `.env`'s `MODEL_EXTRA_ARGS` already includes
`--reasoning-budget 0` (added in M1-02, to stop Gemma 4's default "auto"
thinking mode from consuming the whole token budget on hidden reasoning
before short completions). **No new flags were added or needed for this
ticket** — the spike ran against the exact `model-runner` configuration
that already exists on `main`. Because the fix that makes this reliable was
already in place before M1-03 started (not introduced by this spike), the
correct verdict is a clean **GO**, not GO-WITH-FLAGS. This is called out
explicitly so a future reader doesn't have to reverse-engineer why it
"just worked": it works *because* `--reasoning-budget 0` is set. If that
flag is ever removed from `MODEL_EXTRA_ARGS`, re-run this spike before
trusting native tool-calling again — thinking-mode output was observed to
interfere with short completions in M1-02's testing, and this spike did not
re-test with thinking mode re-enabled (out of scope — see "What we did not
test" below).

## Case notes / how each case was actually implemented

- **C1 (single call)**: `ChatOpenAI.bind_tools([get_weather])`, prompt
  `"What's the weather in Paris?"`. Checked `resp.tool_calls` has exactly 1
  entry named `get_weather` with an arg key containing `city`/`location`
  whose value contains "Paris" (case-insensitive). Also checked
  `resp.invalid_tool_calls` was empty (langchain-openai's signal for
  malformed tool-call JSON from the server) — never observed non-empty.
- **C2 (tool loop)**: C1's flow, then the resulting `AIMessage` + a
  `ToolMessage("22C, sunny", tool_call_id=...)` were appended and the model
  re-invoked with the full history. Checked the final `AIMessage` has no
  tool calls and its content mentions "22".
- **C3 (multi-step)**: tools `list_files() -> list[str]` (always returns
  `["notes.txt"]`) and `read_file(name: str) -> str` bound together; prompt
  `"Read the file that list_files returns."` driven by a manual 2-iteration
  loop (script calls `list_files`, feeds the result back as a
  `ToolMessage`, re-invokes, and checks the model then calls `read_file`
  with the exact filename `list_files` returned).
- **C4 (tool restraint)**: all three tools (`get_weather`, `list_files`,
  `read_file`) bound at once — this is a stricter test than binding only
  one, since it gives the model more surface area to spuriously reach for a
  tool. Prompt `"What is 2+2? Answer directly."`. Checked zero tool calls.
- **C5 (deepagents smoke)**: `create_deep_agent(model=llm,
  backend=FilesystemBackend(root_dir=tmpdir, virtual_mode=True))`, prompt
  `"Create a file called hello.txt containing exactly HELLO"`. Checked
  `tmpdir/hello.txt` exists with stripped content `HELLO`. This exercises
  deepagents' own built-in filesystem tools (not the spike's fake tools),
  so it's the most representative case for what M2-03 will actually run —
  and the highest apparent risk (longest prompt, most tool-call turns,
  deepagents' own system prompt/tool schemas) — yet it was also 100%
  reliable across all 15 runs (3 repetitions x 5 runs).

## What we did not test (explicitly out of scope per the ticket)

- Sampling temperature 0 / greedy decoding (spec required using server
  defaults, which are `temp=1.0`).
- Gemma 4's thinking/reasoning mode *enabled* (`--reasoning-budget` unset or
  nonzero) — M1-02 already found this problematic for short completions and
  this ticket's job was to validate tool-calling under the config the
  project actually ships with, not to re-litigate that flag.
- Load/concurrency (multiple simultaneous requests) — out of scope for this
  ticket; M1-03 is single-client, sequential-request reliability only.
- Longer/more complex multi-tool agent tasks than the C5 smoke test — a
  single-file creation is a minimal deepagents workload, not a stress test.
- Adversarial or ambiguous prompts designed to induce tool-call confusion.

If any of the above become relevant to M2-03's actual workload, consider
re-running or extending this spike rather than assuming these results
generalize indefinitely.

## For M2-03 (and PM sign-off)

- **Proceed with native tool-calling** (`bind_tools` / `tools=[...]`) as
  specced — no ReAct-style prompted fallback is needed based on this data.
- **Do not remove `--reasoning-budget 0` from `MODEL_EXTRA_ARGS`** without
  re-running this spike first; it is load-bearing for this GO verdict, even
  though it was set for an unrelated reason in M1-02.
- No changes to `docker-compose.yml`, `.env.example`, or `MODEL_EXTRA_ARGS`
  were made by this ticket, since no new flags were needed.
- **PM sign-off (Tier B, human-only)**: please read this document and the
  literal `spike.py` output above, and confirm you're comfortable with
  M2-03 proceeding on native tool-calling before that ticket starts. See
  `docs/HOST-CHECKS.md` for the tracked checklist item.

## Reproducing this spike

```bash
# from the repo root, with model-runner already built (M1-01/M1-02):
docker compose up -d model-runner
docker compose ps model-runner   # wait for "healthy"

cd spikes/tool_calling
uv run python spike.py
echo $?   # 0 = GO/GO-WITH-FLAGS, 1 = NO-GO
```

If your container/network naming differs from `homeai-model-runner-1` /
`homeai-net`, either let `spike.py` auto-detect via `docker compose ps -q
model-runner`, or set `MODEL_RUNNER_BASE_URL` explicitly, e.g.:

```bash
MODEL_RUNNER_BASE_URL=http://172.18.0.2:8080/v1 uv run python spike.py
```
