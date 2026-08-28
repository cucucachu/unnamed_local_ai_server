# M2-03 — deepagents agent build (FilesystemBackend)

**Milestone**: M2 · **Size**: M · **Depends on**: M2-02 · **Blocks**: M2-04, M4-04

## Context

Assemble the deep agent: local model client + real-disk filesystem backend rooted at the
workspace. PLAN.md P3-2/P3-3/P3-5 (checkpointer arrives in M3-01; this ticket uses the
in-memory saver so the WS ticket can proceed).

**Check `docs/TOOL_CALLING.md` first.** If the verdict is NO-GO, stop and surface to the PM —
this ticket's spec assumes native tool calling. If GO-WITH-FLAGS, no code change here (flags
live in model-runner env).

## Spec

1. **`app/agent/model_client.py`**:

```python
from langchain_openai import ChatOpenAI

def build_model(settings: Settings) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.model_base_url,
        api_key="none",
        model=settings.model_name,
        temperature=1.0,
        streaming=True,
        max_retries=1,
        timeout=600,
    )
```

2. **`app/agent/prompts.py`** — `SYSTEM_PROMPT`, exactly this content (tune whitespace only):

   > You are HomeAI, a personal assistant running fully locally on your owner's home server.
   > You have direct access to a persistent workspace directory containing your owner's real
   > files. File tools (ls, read_file, write_file, edit_file, glob, grep) operate on that
   > workspace directly — changes are immediate and permanent, there is no undo. Paths are
   > relative to the workspace root.
   > Be concise. For multi-step file operations, briefly state your plan before acting. When
   > asked to organize or modify many files, list what you will change before doing it, then do
   > it, then summarize what changed. Never invent file contents — read files before claiming
   > what they contain.

3. **`app/agent/build.py`**:

```python
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

def build_agent(settings: Settings, checkpointer) -> CompiledGraph:
    return create_deep_agent(
        model=build_model(settings),
        backend=FilesystemBackend(root_dir=settings.workspace_root, virtual_mode=True),
        system_prompt=SYSTEM_PROMPT,
        tools=[],            # execute_code added in M4-04
        checkpointer=checkpointer,
    )
```

   If the pinned deepagents version's `create_deep_agent` doesn't accept `checkpointer`
   directly, attach it the documented way for that version (e.g. `.compile(checkpointer=...)` /
   post-construction assignment) — consult the installed package, and leave a comment with the
   chosen mechanism. `virtual_mode=True` is mandatory (path-traversal guard).
4. App lifespan (`main.py`): construct one agent at startup with
   `langgraph.checkpoint.memory.MemorySaver`, store on `app.state.agent`. `create_app()` gets a
   hook so tests can inject the fake-model settings before construction.
5. **Tests** (fake model, tmp workspace dir as `workspace_root`):
   - `test_agent_invoke_plain`: queue `TextTurn("hi there")`; `agent.ainvoke({"messages": [...]},
     config={"configurable": {"thread_id": "t1"}})` → last message content `"hi there"`.
   - `test_agent_file_tool_roundtrip`: queue `ToolCallTurn("write_file", {...})` +
     `TextTurn("done")`; assert the file exists in the tmp workspace with expected content and
     the fake received a second request containing a tool-result message. (Determine
     `write_file`'s exact arg schema from the installed deepagents version; encode it in the
     test, and add a comment naming the version.)
   - `test_memory_same_thread`: two turns, same thread_id → second request's messages include
     the first exchange.

## Out of scope

WS endpoint (M2-04); Postgres checkpointer (M3-01); execute_code (M4-04); subagent config,
skills, HITL middleware (not v1).

## Acceptance criteria (Tier A)

- [ ] `uv run pytest && uv run ruff check .` green (all three tests above).
- [ ] `docker compose build agent-server && docker compose up -d agent-server` healthy with the
      real model-runner running (startup must not crash constructing the agent).
- [ ] Grep check: `virtual_mode=True` present; workspace root comes from settings (no hardcoded
      `/data/workspace` outside `config.py`).

## Tier B

None (real-model behavior is proven at gate M2-07).
