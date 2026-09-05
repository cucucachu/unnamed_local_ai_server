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

---

# M8-06 — SPIKE (risk gate): re-validate tool calling with Gemma 4 thinking enabled

**Verdict: GO.** Re-enabling Gemma 4's thinking mode (`--reasoning-format
deepseek`, no `--reasoning-budget` cap) is safe to ship for tool-calling:
**75/75** real-model runs passed with thinking fully on (config a), **zero**
empty-`content` completions, **zero** thinking loops, and median end-to-end
latency of 2.57s is **1.77x** today's 1.45s baseline — under the 2x
threshold. Per-request `enable_thinking=false` (config b) reproduces
today's exact **75/75** with latency indistinguishable from baseline. Both
GO criteria from the ticket spec are met. See "What this means for M8-07"
below for what changes (or doesn't) as a result.

## How this was produced

Same real hardware/model as M1-03 (AMD Radeon 890M iGPU, Vulkan, real
`model-runner` container, no mocking) and the **same 5-case matrix, same
3-repetition methodology** (5 cases x 5 runs x 3 repetitions = 75 runs per
configuration) as the original M1-03 spike, via a new harness,
`spikes/tool_calling/spike_m8_06.py`, that **imports and reuses**
`spike.py`'s case functions/tools directly (zero duplication of the
pass/fail assertions) and adds a measurement layer on top:

- A small `ChatOpenAI` subclass (`ReasoningChatOpenAI`, duplicated from the
  real prototype in `app/agent/reasoning_model.py` — see "Client-side
  prototype" below) that surfaces llama-server's `reasoning_content` field
  onto `additional_kwargs`, so the harness can actually see and measure it.
- A `BaseCallbackHandler` (`Recorder`) bound to every model client
  constructed by the harness, so **every** real LLM call is measured —
  including calls made deep inside `deepagents.create_deep_agent`'s own
  loop for case C5, not just the manual top-level calls C1-C4 make.
- Four configurations were actually run (three from the ticket spec, plus
  a **baseline** re-measurement of today's live config — the original
  M1-03 spike never recorded latency numbers at all, so "today's ...
  median latency" for the GO criterion had to be measured fresh, not
  pulled from the existing doc above).
- **Methodology deviation from M1-03, applied uniformly to all four
  configs for a fair comparison**: `max_tokens=2048` was set on the
  client (M1-03 set no cap). This bounds worst-case wall-clock per call
  once thinking has no `--reasoning-budget` ceiling — a real risk this
  ticket exists to check for (a genuine "thinking loop" could otherwise
  run until it exhausts the 32K context window). No case actually hit
  this cap in any configuration (see "thinking-loop count" below).
- Raw per-configuration JSON output (every one of the 480 recorded model
  calls, full latency/reasoning/pass-fail detail) is committed at
  `spikes/tool_calling/m8_06_results/m8_06_{baseline,thinking_on,thinking_off,budget_1024}.json`.

### Step 1: curl-confirmed llama-server wire behavior

Reconfigured `model-runner` (`MODEL_EXTRA_ARGS=--verbose --reasoning-format
deepseek`, `--jinja` unchanged, `--reasoning-budget` dropped entirely) and
confirmed via a streamed `/v1/chat/completions` request
(`"messages":[{"role":"user","content":"What is 2+2? Answer directly."}]`):

```
data: {"choices":[{"delta":{"role":"assistant","content":null}, ...}]}
data: {"choices":[{"delta":{"reasoning_content":"The"}, ...}]}
data: {"choices":[{"delta":{"reasoning_content":" user"}, ...}]}
... (69 more reasoning_content delta chunks) ...
data: {"choices":[{"delta":{"reasoning_content":".*"}, ...}]}
data: {"choices":[{"delta":{"content":"4"}, ...}]}
data: {"choices":[{"finish_reason":"stop","delta":{}, "timings":{...,"predicted_n":71,...}}]}
data: [DONE]
```

`delta.reasoning_content` streams first, then `delta.content` — exactly as
the ticket spec predicted. Adding `"chat_template_kwargs":
{"enable_thinking": false}` to the same request body against the **same**
server config suppresses reasoning entirely, reproducing today's behavior:

```
data: {"choices":[{"delta":{"role":"assistant","content":null}, ...}]}
data: {"choices":[{"delta":{"content":"4"}, ...}]}
data: {"choices":[{"finish_reason":"stop","delta":{}, ...}]}
data: [DONE]
```

No `reasoning_content` at all — direct `content`, parity confirmed. (The
non-streamed `stream:false` response was also checked: llama-server puts
`reasoning_content` as a sibling field on `message`, alongside `content`,
not nested inside it — this shape is what `ReasoningChatOpenAI` below
targets.)

### Step 2: three-configuration re-run (75 runs each)

| Config | Server `MODEL_EXTRA_ARGS` | Client override | Pass | Median latency | p95 latency | Median thought words | Calls w/ reasoning | Empty-content | Thinking loops |
|---|---|---|---|---|---|---|---|---|---|
| **baseline** (today, for comparison only) | `--verbose --reasoning-budget 0` | none | 75/75 | 1.45s | 2.33s | n/a (3 calls leaked ~21 words each — see note) | 3/120 | 0 | 0 |
| **(a) thinking ON** | `--verbose --reasoning-format deepseek` | none | **75/75** | **2.57s** | 7.95s | 32 | 57/120 | **0** | **0** |
| **(b) thinking OFF (per-request)** | `--verbose --reasoning-format deepseek` (same as a) | `chat_template_kwargs.enable_thinking=false` | **75/75** | 1.55s | 2.05s | n/a | 0/120 | 0 | 0 |
| **(c) `--reasoning-budget 1024`** | `--verbose --reasoning-format deepseek --reasoning-budget 1024` | none | 75/75 | 2.19s | 7.38s | 32 | 56/120 | 0 | 0 |

Per-case breakdown was identical across all four configurations — every
case passed 15/15 (5 runs x 3 repetitions):

| Case | Description | baseline | (a) thinking ON | (b) thinking OFF | (c) budget 1024 |
|------|-------------|----------|------------------|-------------------|------------------|
| C1 | single call (get_weather Paris) | 15/15 | 15/15 | 15/15 | 15/15 |
| C2 | tool loop (weather -> final answer) | 15/15 | 15/15 | 15/15 | 15/15 |
| C3 | multi-step (list_files -> read_file) | 15/15 | 15/15 | 15/15 | 15/15 |
| C4 | tool restraint (2+2, no tool call) | 15/15 | 15/15 | 15/15 | 15/15 |
| C5 | deepagents smoke (create hello.txt) | 15/15 | 15/15 | 15/15 | 15/15 |

**Note on the baseline's 3 leaked `reasoning_content` calls**: even with
`--reasoning-budget 0` and no `--reasoning-format` flag at all, 3 of 120
calls (all in C3's second step, `read_file`) came back with a short
(~150-character) `reasoning_content` field. This did **not** cause an
empty-`content` completion or any failure — `content`/tool-call output was
present and correct in every case — but it is a small, previously-unknown
crack in "budget 0 == provably zero reasoning" worth flagging: llama-server
appears to occasionally emit a short thinking fragment even at budget 0.
Not a blocker for this verdict (it doesn't change any pass/fail outcome
and was already latent in the shipped config before this ticket), but
noted for anyone debugging an unexplained latency outlier on the current
`--reasoning-budget 0` config in production.

### Verdict determination (per the ticket's exact criteria)

- **(a) thinking ON**: 75/75 >= 72/75 required ✅. Zero empty-`content`
  completions ✅. Median latency 2.57s <= 2x baseline's 1.45s (2.90s) ✅.
- **(b) thinking OFF via per-request override**: 75/75, matching today's
  75/75 exactly ✅.

Both conditions hold → **GO**.

(Config (c), `--reasoning-budget 1024`, was not part of the GO/NO-GO
formula per the ticket spec — it's extra data for a future tuning
decision. It also passed 75/75 with zero empty-content/thinking-loop
counts and a latency/reasoning profile close to full thinking-on, for
whatever that's worth if a future ticket wants "some bounded thinking"
instead of "no cap" or "no thinking".)

## What this means for M8-07

Per the ticket spec, a GO verdict means M8-07 (surfacing the reasoning
stream in the UI/WS protocol) **stays open** for a future ticket — it is
**not** closed as not-planned. `--reasoning-budget 0` is **not** being
removed from the live `MODEL_EXTRA_ARGS` by this ticket (this is a SPIKE:
investigation + doc/fixture/prototype changes only, no production config
change) — the live stack was returned to its exact original config
(`--verbose --reasoning-budget 0`, `--jinja`, no `--reasoning-format`)
before this ticket finished; see `.env.example`'s updated comment for the
recommended flags a future M8-07 (or follow-up) should actually flip.

## Client-side prototype: surfacing `reasoning_content` in LangChain

`langchain-openai`'s `ChatOpenAI` targets the official OpenAI API surface
only — its own module docstring says non-standard fields like
`reasoning_content` "are not extracted or preserved." Confirmed empirically
above: a plain `ChatOpenAI().stream(...)` against `model-runner` with
`--reasoning-format deepseek` silently drops every `reasoning_content`
delta; every streamed chunk's `additional_kwargs` comes back `{}`.

Three candidates were evaluated (per the ticket spec):

1. **A small `ChatOpenAI` subclass overriding chunk conversion** — chosen.
   `services/agent-server/app/agent/reasoning_model.py`'s
   `ReasoningChatOpenAI` overrides the one private hook responsible for
   turning a raw SSE chunk into a `ChatGenerationChunk`
   (`_convert_chunk_to_generation_chunk`), delegates to the real
   implementation for everything else, and additionally copies
   `delta.reasoning_content` onto `message.additional_kwargs`. Because
   `AIMessageChunk.__add__` merges `additional_kwargs` string values by
   concatenation (`langchain_core.utils._merge.merge_dicts`), the full
   reasoning text accumulates automatically across a stream exactly like
   `content` already does — no extra buffering logic needed. Also
   symmetrically overrides `_create_chat_result` for the non-streaming
   path (llama-server puts `reasoning_content` as a sibling field on
   `message` there too), even though the ticket's specific ask was about
   `on_chat_model_stream` chunks.
2. **`output_version="v1"` content blocks** — ruled out without a
   throwaway prototype: `langchain_core.messages.block_translators.openai`
   translates v1 blocks based on `type` values from OpenAI's *Responses*
   API shape (`text`, `refusal`, `reasoning`, ...), not Chat Completions
   `delta.reasoning_content` — it doesn't apply to this server's wire
   format at all.
3. **`langchain-deepseek`'s `ChatDeepSeek`** — would work (llama-server's
   `reasoning_content` shape is exactly what `ChatDeepSeek` targets), but
   is a heavier swap: a new pinned dependency, a different class at every
   `bind_tools`/`tools=`/`create_deep_agent(model=...)` call site, for a
   one-field problem. Subclassing `ChatOpenAI` (already depended on,
   already constructed everywhere in this codebase — see
   `model_client.py`) is strictly less invasive.

**Chosen: (1), `ReasoningChatOpenAI`.** This is a **prototype only** — per
the ticket spec, `build_model()` in `model_client.py` is unchanged and
still returns a plain `ChatOpenAI`; wiring `ReasoningChatOpenAI` into the
real WS handler / turn loop is M8-07's job (now unblocked, since M8-07
stays open per the GO verdict above). Proven against the fake-model
fixture (now `reasoning_content`-capable, see below) in
`tests/test_reasoning_prototype.py`: a scripted streamed response with
`reasoning_content` deltas produces LangChain chunks whose
`additional_kwargs["reasoning_content"]` is accessible per-chunk and
correctly accumulates on the merged final message; a plain `ChatOpenAI`
against the identical scripted response is proven to drop it (the negative
case this prototype exists to fix).

## Fake model fixture: `reasoning_content` support

`tests/fake_model/scripting.py`'s `TextTurn` gained two new optional
fields: `reasoning_content: str | None = None` and
`reasoning_chunk_size: int = 8`. When set, `tests/fake_model/server.py`
emits `delta.reasoning_content` chunks *before* `delta.content` chunks in
the streamed case (matching the real wire order confirmed in Step 1 above)
and a sibling `message.reasoning_content` field in the non-streamed case
(matching llama-server's real non-streamed shape). `None` (the default)
preserves the exact old behavior for every pre-M8-06 test — no existing
test needed to change. This gives M8-07 deterministic
`reasoning_content`-bearing fixtures to test against without depending on
the real model's actual "did it decide to think this time" nondeterminism.

## Reproducing this spike

```bash
# from the repo root, model-runner already built:
cd spikes/tool_calling

# Step 1 (curl confirmation) — reconfigure model-runner first:
#   MODEL_EXTRA_ARGS=--verbose --reasoning-format deepseek   (in .env)
#   docker compose up -d model-runner  # wait for healthy

# Step 2 (three-config re-run, ~3-7 minutes each on this hardware):
SPIKE_CONFIG=baseline     REPEAT=3 uv run python spike_m8_06.py > /tmp/baseline.json      # after restoring --reasoning-budget 0
SPIKE_CONFIG=thinking_on  REPEAT=3 uv run python spike_m8_06.py > /tmp/thinking_on.json   # server: --reasoning-format deepseek, no budget
SPIKE_CONFIG=thinking_off REPEAT=3 uv run python spike_m8_06.py > /tmp/thinking_off.json  # server: same as thinking_on; client sends enable_thinking=false
SPIKE_CONFIG=budget_1024  REPEAT=3 uv run python spike_m8_06.py > /tmp/budget_1024.json   # server: --reasoning-format deepseek --reasoning-budget 1024

# Don't forget to restore .env to --reasoning-budget 0 (no --reasoning-format)
# and `docker compose up -d model-runner` afterward — this ticket is a spike,
# not a production config change.
```
