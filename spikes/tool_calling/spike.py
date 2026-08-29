"""M1-03: Tool-calling validation spike (risk gate).

Validates whether native OpenAI-style tool-calling (`bind_tools([...])` +
`tools=[...]` on the chat-completions request) works reliably against the
real `model-runner` service (llama.cpp server-vulkan, Gemma 4, `--jinja`),
and whether `deepagents.create_deep_agent` works end to end on top of it.
This is the highest-risk assumption in the project plan (see GitHub issue
#6) — if native tool-calling is flaky, M2-03 falls back to a prompted
ReAct-style loop instead of `bind_tools`.

Usage
-----
From this directory (`spikes/tool_calling/`), with the real `model-runner`
container already up and healthy (`docker compose up -d model-runner` from
the repo root):

    uv run python spike.py

Exit code: 0 iff every case (C1-C5) passes >= 4/5 runs ("GO" or
"GO-WITH-FLAGS" verdict); 1 otherwise ("NO-GO"). See ../../docs/TOOL_CALLING.md
for the actual results and verdict from the run that produced that document.

Networking
----------
`model-runner` deliberately publishes **no host port** (project rule: only
`caddy` publishes a port — see the "Conventions & Contracts" reference
issue). This script is a plain host-side `uv run` process, not a container
on `homeai-net`, so it cannot resolve the Docker-internal hostname
`model-runner` the way `agent-server` will in M2. Docker bridge networks
*are* directly routable from the host by IP even without a published port,
so instead of adding a port mapping (which would violate the "no new
published ports" rule and isn't needed here), this script shells out to
`docker inspect` to read the running container's IP on the `homeai-net`
bridge and talks to `http://<that-ip>:8080/v1` directly. Override with the
MODEL_RUNNER_BASE_URL env var if your container/network naming differs.

Gemma 4 "thinking" mode
------------------------
Gemma 4 has a reasoning/"thinking" mode that (per M1-02's notes, see
`.env`'s MODEL_EXTRA_ARGS comment) can burn the whole token budget on hidden
reasoning content and leave `message.content` empty for short completions.
This repo's docker-compose.yml already sets `--reasoning-budget 0` in
MODEL_EXTRA_ARGS to disable it server-side, so this script does not attempt
any client-side reasoning_effort override. Per the ticket spec, pass/fail
checks below look only at the final tool_calls / content and ignore any
reasoning-related fields that might still appear in additional_kwargs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_PER_CASE = 5
PASS_THRESHOLD = 4  # out of RUNS_PER_CASE


# --------------------------------------------------------------------------
# Config: base_url + model name
# --------------------------------------------------------------------------


def _read_env_var(var_name: str) -> str | None:
    """Read a KEY=VALUE line out of .env (preferred) or .env.example."""
    for candidate in (REPO_ROOT / ".env", REPO_ROOT / ".env.example"):
        if not candidate.exists():
            continue
        for line in candidate.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == var_name:
                value = value.strip()
                if value:
                    return value
    return None


def get_model_name() -> str:
    return os.environ.get("MODEL_NAME") or _read_env_var("MODEL_NAME") or "gemma-4-26b-a4b-it"


def get_model_runner_base_url() -> str:
    """Resolve model-runner's base_url via its container IP (see module docstring)."""
    override = os.environ.get("MODEL_RUNNER_BASE_URL")
    if override:
        return override

    candidate_names = [
        os.environ.get("MODEL_RUNNER_CONTAINER", ""),
        "homeai-model-runner-1",
    ]
    # Also ask compose directly in case the project/container naming differs.
    try:
        cid = subprocess.run(
            ["docker", "compose", "ps", "-q", "model-runner"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if cid:
            candidate_names.append(cid)
    except Exception:
        pass

    for name in candidate_names:
        if not name:
            continue
        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    name,
                    "--format",
                    "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            ip = result.stdout.strip()
            if result.returncode == 0 and ip:
                return f"http://{ip}:8080/v1"
        except Exception:
            continue

    raise RuntimeError(
        "Could not determine model-runner's container IP. Is it running? "
        "Try `docker compose up -d model-runner` from the repo root, or set "
        "MODEL_RUNNER_BASE_URL explicitly (e.g. http://172.18.0.2:8080/v1)."
    )


# --------------------------------------------------------------------------
# Tools (plain fakes — the point is testing tool-call *behavior*)
# --------------------------------------------------------------------------


@tool
def get_weather(city: str) -> str:
    """Get the current weather for a given city."""
    return "22C, sunny"


@tool
def list_files() -> list[str]:
    """List the files available in the workspace."""
    return ["notes.txt"]


@tool
def read_file(name: str) -> str:
    """Read the contents of a file by name."""
    if name == "notes.txt":
        return "Buy milk."
    return f"(no such file: {name})"


# --------------------------------------------------------------------------
# Result bookkeeping
# --------------------------------------------------------------------------


@dataclass
class RunResult:
    passed: bool
    reason: str = ""  # empty when passed


@dataclass
class CaseResult:
    case_id: str
    title: str
    runs: list[RunResult] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.runs if r.passed)

    @property
    def case_passed(self) -> bool:
        return self.pass_count >= PASS_THRESHOLD


def make_llm(base_url: str, model_name: str) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=base_url,
        api_key="none",
        model=model_name,
        timeout=180,
        max_retries=0,  # we want to observe real flakiness, not mask it
    )


# --------------------------------------------------------------------------
# Case implementations. Each returns a RunResult for a single run.
# --------------------------------------------------------------------------


def _find_city_like_value(args: dict) -> str | None:
    """Return the value of whatever arg key looks like a city (spec: 'city-like key')."""
    for key, value in args.items():
        if "city" in key.lower() or "location" in key.lower():
            return str(value)
    if len(args) == 1:
        return str(next(iter(args.values())))
    return None


def run_c1(llm: ChatOpenAI) -> RunResult:
    llm_t = llm.bind_tools([get_weather])
    resp = llm_t.invoke("What's the weather in Paris?")
    if resp.invalid_tool_calls:
        return RunResult(False, f"invalid_tool_calls: {resp.invalid_tool_calls}")
    calls = resp.tool_calls
    if len(calls) != 1:
        return RunResult(False, f"expected exactly 1 tool call, got {len(calls)}: {calls}")
    call = calls[0]
    if call["name"] != "get_weather":
        return RunResult(False, f"expected tool 'get_weather', got '{call['name']}'")
    city_val = _find_city_like_value(call["args"])
    if city_val is None or "paris" not in city_val.lower():
        return RunResult(False, f"expected city-like arg == 'Paris', got args={call['args']}")
    return RunResult(True)


def run_c2(llm: ChatOpenAI) -> RunResult:
    llm_t = llm.bind_tools([get_weather])
    messages: list = [HumanMessage("What's the weather in Paris?")]
    first = llm_t.invoke(messages)
    if not first.tool_calls:
        return RunResult(False, f"step 1: expected a tool call, got none. content={first.content!r}")
    call = first.tool_calls[0]
    messages.append(first)
    messages.append(ToolMessage(content="22C, sunny", tool_call_id=call["id"]))
    final = llm_t.invoke(messages)
    if final.tool_calls:
        return RunResult(False, f"step 2: expected no tool calls, got {final.tool_calls}")
    if "22" not in (final.content or ""):
        return RunResult(False, f"step 2: final content doesn't mention 22: {final.content!r}")
    return RunResult(True)


def run_c3(llm: ChatOpenAI) -> RunResult:
    tools = [list_files, read_file]
    llm_t = llm.bind_tools(tools)
    messages: list = [HumanMessage("Read the file that list_files returns.")]

    step1 = llm_t.invoke(messages)
    if not step1.tool_calls:
        return RunResult(False, f"step 1: expected a tool call, got none. content={step1.content!r}")
    if len(step1.tool_calls) != 1 or step1.tool_calls[0]["name"] != "list_files":
        return RunResult(False, f"step 1: expected exactly 1 call to 'list_files', got {step1.tool_calls}")
    call1 = step1.tool_calls[0]
    files = list_files.invoke({})
    messages.append(step1)
    messages.append(ToolMessage(content=json.dumps(files), tool_call_id=call1["id"]))

    step2 = llm_t.invoke(messages)
    if not step2.tool_calls:
        return RunResult(False, f"step 2: expected a tool call, got none. content={step2.content!r}")
    if len(step2.tool_calls) != 1 or step2.tool_calls[0]["name"] != "read_file":
        return RunResult(False, f"step 2: expected exactly 1 call to 'read_file', got {step2.tool_calls}")
    call2 = step2.tool_calls[0]
    name_arg = call2["args"].get("name") or next(iter(call2["args"].values()), None)
    if name_arg != files[0]:
        return RunResult(
            False, f"step 2: read_file called with name={name_arg!r}, expected {files[0]!r} (from list_files)"
        )
    return RunResult(True)


def run_c4(llm: ChatOpenAI) -> RunResult:
    llm_t = llm.bind_tools([get_weather, list_files, read_file])
    resp = llm_t.invoke("What is 2+2? Answer directly.")
    if resp.tool_calls:
        return RunResult(False, f"expected no tool calls, got {resp.tool_calls}")
    return RunResult(True)


def run_c5(llm: ChatOpenAI) -> RunResult:
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend

    with tempfile.TemporaryDirectory() as tmpdir:
        backend = FilesystemBackend(root_dir=tmpdir, virtual_mode=True)
        agent = create_deep_agent(model=llm, backend=backend)
        agent.invoke(
            {"messages": [{"role": "user", "content": "Create a file called hello.txt containing exactly HELLO"}]},
            config={"recursion_limit": 50},
        )
        target = Path(tmpdir) / "hello.txt"
        if not target.exists():
            return RunResult(False, f"{target} was not created")
        content = target.read_text().strip()
        if content != "HELLO":
            return RunResult(False, f"hello.txt content = {content!r}, expected 'HELLO'")
    return RunResult(True)


CASES = [
    ("C1", "single call (get_weather Paris)", run_c1),
    ("C2", "tool loop (weather -> final answer)", run_c2),
    ("C3", "multi-step (list_files -> read_file)", run_c3),
    ("C4", "tool restraint (2+2, no tool call)", run_c4),
    ("C5", "deepagents smoke (create hello.txt)", run_c5),
]


def run_case(case_id: str, title: str, run_fn, llm_factory) -> CaseResult:
    result = CaseResult(case_id=case_id, title=title)
    for i in range(RUNS_PER_CASE):
        try:
            llm = llm_factory()
            run_result = run_fn(llm)
        except Exception as exc:  # noqa: BLE001 - must never crash the whole script
            tb = traceback.format_exc(limit=2)
            run_result = RunResult(False, f"{type(exc).__name__}: {exc}\n{tb}")
        result.runs.append(run_result)
        status = "PASS" if run_result.passed else "FAIL"
        print(f"  [{case_id}] run {i + 1}/{RUNS_PER_CASE}: {status}" + (f" ({run_result.reason.splitlines()[0]})" if run_result.reason else ""), file=sys.stderr)
    return result


def print_markdown_report(case_results: list[CaseResult], base_url: str, model_name: str) -> None:
    print()
    print("## Tool-calling spike results")
    print()
    print(f"- `base_url`: `{base_url}`")
    print(f"- `model`: `{model_name}`")
    print(f"- runs per case: {RUNS_PER_CASE}, pass threshold: >= {PASS_THRESHOLD}/{RUNS_PER_CASE}")
    print()
    print("### Detail (every run)")
    print()
    print("| Case | Description | Run | Result | Reason (if failed) |")
    print("|------|-------------|-----|--------|---------------------|")
    for case in case_results:
        for i, run in enumerate(case.runs, start=1):
            reason = run.reason.splitlines()[0] if run.reason else ""
            reason = reason.replace("|", "\\|")
            result_str = "PASS" if run.passed else "FAIL"
            print(f"| {case.case_id} | {case.title} | {i} | {result_str} | {reason} |")
    print()
    print("### Summary")
    print()
    print("| Case | Description | Pass rate | Case status (>= 4/5) |")
    print("|------|-------------|-----------|------------------------|")
    for case in case_results:
        status = "PASS" if case.case_passed else "FAIL"
        print(f"| {case.case_id} | {case.title} | {case.pass_count}/{RUNS_PER_CASE} | {status} |")
    print()


def main() -> int:
    model_name = get_model_name()
    base_url = get_model_runner_base_url()
    print(f"Using base_url={base_url} model={model_name}", file=sys.stderr)

    llm_factory = lambda: make_llm(base_url, model_name)  # noqa: E731

    case_results = []
    for case_id, title, run_fn in CASES:
        print(f"Running {case_id}: {title}", file=sys.stderr)
        case_results.append(run_case(case_id, title, run_fn, llm_factory))

    print_markdown_report(case_results, base_url, model_name)

    all_passed = all(c.case_passed for c in case_results)
    if all_passed:
        print("VERDICT INPUT: all cases passed >= 4/5 runs -> GO or GO-WITH-FLAGS territory (see docs/TOOL_CALLING.md)", file=sys.stderr)
    else:
        failing = [c.case_id for c in case_results if not c.case_passed]
        print(f"VERDICT INPUT: cases failed threshold: {failing} -> NO-GO territory (see docs/TOOL_CALLING.md)", file=sys.stderr)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
