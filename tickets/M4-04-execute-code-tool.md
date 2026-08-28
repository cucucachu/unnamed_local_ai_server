# M4-04 — `execute_code` tool in the agent

**Milestone**: M4 · **Size**: M · **Depends on**: M4-03, M2-03 · **Blocks**: M4-06

## Context

The agent's hands get a toolbox: one tool that sends a command string to the exec manager,
session-scoped to the chat thread (PLAN.md P3-4). The tool must give the model enough context
in its description to use the sandbox well (no network, workspace at /workspace, what's
installed).

## Spec

1. **`app/agent/execute_code_tool.py`**:

```python
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

@tool
async def execute_code(command: str, config: RunnableConfig, timeout_seconds: int = 120) -> str:
    """Run a shell command in a sandboxed Linux container.

    The container has NO network access. The user's workspace is mounted read-write at
    /workspace (the same files your file tools see; your file-tool root == /workspace).
    Installed: Python 3 with pandas/numpy/pillow/matplotlib/openpyxl/pypdf, Node.js, git,
    ffmpeg, imagemagick, pandoc, ripgrep, jq. You cannot install packages. State in /tmp
    and $HOME is ephemeral; only /workspace persists. Long jobs: raise timeout_seconds
    (max 600).
    """
```

   - Thread id from `config["configurable"]["thread_id"]`; session id = thread id (sanitized
     to the §7 regex; fallback `"default"` if missing).
   - Clamp `timeout_seconds` to [1, 600].
   - httpx (async, base_url from `Settings.exec_manager_url`): `ensure` (404-proof), then
     `execute`. Manager unreachable / 5xx → return
     `"execute_code failed: <reason>"` as the tool result (string, not raised — the model
     should see and report it).
   - Result formatting (exactly):

```
exit_code: {exit_code}{" (TIMED OUT)" if timed_out}
--- stdout ---
{stdout or "(empty)"}
--- stderr ---
{stderr or "(empty)"}
```

     If `truncated`, append `[output truncated]`.
2. Register in `build_agent`: `tools=[execute_code]`.
3. Extend `SYSTEM_PROMPT` (prompts.py) with one paragraph:
   > For anything beyond reading/writing/searching files — running scripts, converting or
   > batch-processing media, installing nothing — use execute_code. Write scripts into the
   > workspace with your file tools first when they are worth keeping; use one-liners
   > otherwise. File tool paths and /workspace in execute_code refer to the same directory.
4. **Tests**:
   - Unit (fake exec-manager via `respx` or a stub ASGI app): ensure+execute called with
     session id = thread id; formatting exact-match; clamp; manager-down → error string
     returned, no exception.
   - Agent-level (fake model): `ToolCallTurn("execute_code", {"command": "echo hi"})` +
     `TextTurn("done")` → fake manager receives the command; WS test asserts the frame
     `category == "exec"` (extends M2-04's mapping test).
   - Integration (`-m integration`, full stack): via WS, prompt the real agent:
     "Use execute_code to run: python3 -c 'print(21*2)' and tell me the output." → a
     `tool_end` frame for execute_code contains `42` (retry once).

## Out of scope

UI rendering (M4-06); per-command container specs; sudo/root execution; network egress.

## Acceptance criteria (Tier A)

- [ ] Unit + agent-level tests green; ruff green.
- [ ] Integration test green on the host with the full stack up.
- [ ] Cross-visibility proof scripted: WS prompt asks the agent to run
      `bash -lc 'date > /workspace/exec-proof.txt'` via execute_code, then `read_file` the same
      file with its file tool and report content — both tool frames succeed and the host file
      exists (add to `scripts/e2e/` as `exec_crossview_smoke.sh`).

## Tier B

None.
