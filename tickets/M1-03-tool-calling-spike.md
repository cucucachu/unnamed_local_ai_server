# M1-03 — Tool-calling validation spike (risk gate)

**Milestone**: M1 · **Size**: M · **Depends on**: M1-02 · **Blocks**: M2-07 (gate), informs M2-03

## Context

**This is the highest-risk assumption in the whole plan.** deepagents requires reliable native
tool-calling through llama.cpp's OpenAI-compatible endpoint with `--jinja` and Gemma 4's chat
template. If it's flaky, we fall back to a prompted ReAct loop before building on top of it
(PLAN.md "Tool-calling risk"). This spike produces a **written verdict** that M2-07 (gate) reads.

## Spec

1. Create `spikes/tool_calling/` as a standalone uv project (Python 3.12; deps:
   `langchain-openai` v1.x, `deepagents>=0.7.10,<0.8`, `pytest`).
2. `spike.py` runs the following case matrix against the real model-runner
   (`base_url` = model-runner `/v1`, `api_key="none"`, `model=MODEL_NAME`), **5 runs per case**,
   temperature per model defaults:

   | Case | Setup | Pass condition (per run) |
   |------|-------|--------------------------|
   | C1 single call | `ChatOpenAI.bind_tools([get_weather])`, prompt "What's the weather in Paris?" | response contains exactly 1 tool call named `get_weather`, JSON args with a `city`-like key = "Paris" |
   | C2 tool loop | C1 + append ToolMessage("22C, sunny"), re-invoke | final AIMessage has no tool calls and mentions 22 |
   | C3 multi-step | tools `list_files`, `read_file`; prompt "Read the file that list_files returns" driven by a 2-iteration manual loop | model calls `list_files` first, then `read_file` with the returned name |
   | C4 tool restraint | same tools bound, prompt "What is 2+2? Answer directly." | no tool calls |
   | C5 deepagents smoke | `create_deep_agent(model=..., backend=FilesystemBackend(root_dir=tmpdir, virtual_mode=True))`, prompt "Create a file called hello.txt containing exactly HELLO" | `tmpdir/hello.txt` exists with content `HELLO` (strip whitespace) |

   Tools are plain `@tool` functions with docstrings and typed args. The script prints a
   markdown results table (case × 5 runs, pass/fail + failure reason) and exits 0 iff every
   case passes ≥ 4/5 runs.
3. Write **`docs/TOOL_CALLING.md`**: results table, verdict (one of):
   - **GO** — native tool calling reliable; M2-03 proceeds as specced.
   - **GO-WITH-FLAGS** — reliable only with specific llama-server flags (e.g. a grammar/
     `--chat-template` tweak); document the flags and add them to `MODEL_EXTRA_ARGS` default
     in `.env.example`.
   - **NO-GO** — unreliable; document failure modes. Fallback decision for M2-03: use
     LangChain's prompted tool-calling (ReAct-style with structured output parsing) instead of
     native `tools=[...]` binding, and note that deepagents built-in tools must then be
     re-exposed through the fallback loop. **Stop and flag to the PM before M2-03 starts.**
4. Keep the spike runnable: `uv run python spike.py` documented in the file header.

## Out of scope

Fixing model-runner flags beyond documenting them; building the fallback loop itself.

## Acceptance criteria (Tier A)

- [ ] `uv run python spike.py` runs the full matrix against the real model and writes results.
- [ ] `docs/TOOL_CALLING.md` exists with the table, the verdict, and (if not GO) explicit
      instructions for M2-03.
- [ ] Spike exit code reflects the verdict (0 = GO/GO-WITH-FLAGS, 1 = NO-GO).

## Tier B

- [ ] PM reads `docs/TOOL_CALLING.md` and signs off on the verdict before M2-07 gate runs.
