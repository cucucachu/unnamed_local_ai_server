"""M8-06: re-run the M1-03 tool-calling spike with Gemma 4 thinking re-enabled.

Reuses the exact same 5-case matrix (C1-C5) and pass/fail logic from
`spike.py` (M1-03) — imported directly, not reimplemented — under three
`model-runner` configurations, and additionally records, per real model
call: end-to-end latency, `reasoning_content` (thought) length, whether
`message.content` came back empty on a non-tool-call turn (the exact
correctness bug pattern `--reasoning-budget 0` was originally added to
prevent — see docs/TOOL_CALLING.md's M1-03/M8-06 sections), and a simple
heuristic flag for "thinking loops" (see `_is_thinking_loop` below).

Why a second script instead of editing `spike.py` in place
-------------------------------------------------------------
`spike.py` is `docs/TOOL_CALLING.md`'s literal reproduction recipe for the
*original* M1-03 GO verdict (`uv run python spike.py`, unmodified,
against whatever config is live) — mutating it to grow config/measurement
flags would make that recipe reproduce something subtly different than
what originally produced the doc. This script imports and reuses
`spike.py`'s case functions/tools instead (same underlying test logic,
zero duplication of the *behavioral* assertions), and only adds the
config-switching / measurement layer M8-06 actually needs.

Usage
-----
From this directory, with `model-runner` already reconfigured for the
config under test and healthy:

    SPIKE_CONFIG=thinking_on   uv run python spike_m8_06.py > /tmp/m8_06_thinking_on.json
    SPIKE_CONFIG=thinking_off  uv run python spike_m8_06.py > /tmp/m8_06_thinking_off.json
    SPIKE_CONFIG=budget_1024   uv run python spike_m8_06.py > /tmp/m8_06_budget_1024.json

`SPIKE_CONFIG` only controls the *client-side* request shape (whether
`chat_template_kwargs: {"enable_thinking": false}` is sent) — it does NOT
touch `model-runner` itself; the caller is responsible for having already
applied the matching server-side flags (see docs/TOOL_CALLING.md's M8-06
section for the exact `MODEL_EXTRA_ARGS` used for each config) and waited
for the healthcheck before invoking this script per config.

`REPEAT` (default 3) controls how many times the 25-run (5 case x 5 run)
matrix repeats — matching M1-03's own "three full repetitions" methodology
(see spike.py's docstring / docs/TOOL_CALLING.md's "Reproducibility"
section) so this ticket's 75-run-per-configuration results are directly
comparable to the original 75-run GO verdict.

Prints a single JSON object (all raw records + derived stats) to stdout;
progress goes to stderr. This is intentionally NOT the same
markdown-report format as `spike.py` — `docs/TOOL_CALLING.md`'s M8-06
section is written by hand from this JSON (see that doc for the actual
formatted tables), not generated automatically, because the report needs
three side-by-side configuration columns spike.py's own
single-configuration report format was never designed for.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

import openai
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import ChatResult, LLMResult
from langchain_openai import ChatOpenAI

# Reuse M1-03's exact case logic/tools — see module docstring for why this
# is an import, not a copy-paste.
from spike import (
    CASES,
    PASS_THRESHOLD,
    RUNS_PER_CASE,
    CaseResult,
    get_model_name,
    get_model_runner_base_url,
    run_case,
)

REPEAT = int(os.environ.get("REPEAT", "3"))
SPIKE_CONFIG = os.environ.get("SPIKE_CONFIG", "thinking_on")
# "baseline" isn't one of the ticket's 3 configs — it's an extra run against
# today's live (`--reasoning-budget 0`) config, added so this ticket has an
# actual measured "today's ... median latency" to compare configuration (a)
# against (the GO/NO-GO criterion explicitly requires this, and the
# original M1-03 spike never recorded latency numbers at all).
assert SPIKE_CONFIG in ("thinking_on", "thinking_off", "budget_1024", "baseline"), SPIKE_CONFIG

# Bounds worst-case wall-clock per call (a real risk once thinking is
# re-enabled with no `--reasoning-budget` cap — an unbounded runaway
# "thinking loop" could otherwise run until it exhausts the 32K context).
# Applied uniformly across all three configurations for a fair comparison;
# called out explicitly in docs/TOOL_CALLING.md's M8-06 section as a
# deliberate methodology difference from M1-03 (which set no cap at all).
MAX_TOKENS = 2048

# A single reasoning_content string repeating the same ~40-char window
# this many times is treated as a "thinking loop" (crude but effective
# heuristic — real runaway reasoning degenerates into short repeated
# phrases/loops well before 32K tokens in practice).
THINKING_LOOP_REPEAT_THRESHOLD = 6
THINKING_LOOP_WINDOW = 40


class ReasoningChatOpenAI(ChatOpenAI):
    """Same prototype as `app/agent/reasoning_model.py` in agent-server,
    duplicated here because this is a standalone `uv` project with its own
    lockfile (see spike.py's own header comment for why this project can't
    just import agent-server's package directly)."""

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):  # type: ignore[override]
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return generation_chunk
        choices = chunk.get("choices") or []
        if not choices:
            return generation_chunk
        delta = choices[0].get("delta") or {}
        reasoning_content = delta.get("reasoning_content")
        if reasoning_content:
            generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning_content
        return generation_chunk

    def _create_chat_result(  # type: ignore[override]
        self, response: dict | openai.BaseModel, generation_info: dict | None = None
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        response_dict = (
            response if isinstance(response, dict) else response.model_dump(warnings=False)
        )
        choices = response_dict.get("choices") or []
        for generation, choice in zip(result.generations, choices, strict=False):
            reasoning_content = (choice.get("message") or {}).get("reasoning_content")
            if reasoning_content:
                generation.message.additional_kwargs["reasoning_content"] = reasoning_content
        return result


@dataclass
class CallRecord:
    case_id: str
    run_index: int
    latency_s: float
    content_len: int
    reasoning_content_len: int
    reasoning_word_count: int
    finish_reason: str | None
    has_tool_calls: bool
    empty_content: bool  # content == "" on a non-tool-call, stop-finished turn
    thinking_loop: bool


def _is_thinking_loop(reasoning: str) -> bool:
    if not reasoning or len(reasoning) < THINKING_LOOP_WINDOW * THINKING_LOOP_REPEAT_THRESHOLD:
        return False
    window = reasoning[-THINKING_LOOP_WINDOW:]
    return reasoning.count(window) >= THINKING_LOOP_REPEAT_THRESHOLD


class Recorder(BaseCallbackHandler):
    """Captures per-model-call latency + reasoning_content across every
    `invoke`/`ainvoke` this callback is bound to — including calls made
    deep inside `deepagents.create_deep_agent`'s own internal loop (C5),
    not just the manual top-level calls C1-C4 make directly. Tagged with
    whatever `current_case`/`current_run` the harness sets immediately
    before invoking each case, since callbacks don't otherwise know which
    of the 5 cases/N runs they're firing within.
    """

    def __init__(self) -> None:
        self.records: list[CallRecord] = []
        self._starts: dict[str, float] = {}
        self.current_case = ""
        self.current_run = 0

    def on_chat_model_start(self, serialized: dict, messages: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._starts[str(run_id)] = time.monotonic()

    def on_llm_start(self, serialized: dict, prompts: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._starts.setdefault(str(run_id), time.monotonic())

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        start = self._starts.pop(str(run_id), None)
        latency = time.monotonic() - start if start is not None else float("nan")
        for generation_list in response.generations:
            for generation in generation_list:
                message = getattr(generation, "message", None)
                content = (getattr(message, "content", "") or "") if message else ""
                reasoning = ""
                if message is not None and hasattr(message, "additional_kwargs"):
                    reasoning = message.additional_kwargs.get("reasoning_content", "") or ""
                finish_reason = None
                if getattr(generation, "generation_info", None):
                    finish_reason = generation.generation_info.get("finish_reason")
                has_tool_calls = bool(getattr(message, "tool_calls", None)) if message else False
                empty_content = (not content) and not has_tool_calls and finish_reason != "tool_calls"
                self.records.append(
                    CallRecord(
                        case_id=self.current_case,
                        run_index=self.current_run,
                        latency_s=latency,
                        content_len=len(content),
                        reasoning_content_len=len(reasoning),
                        reasoning_word_count=len(reasoning.split()),
                        finish_reason=finish_reason,
                        has_tool_calls=has_tool_calls,
                        empty_content=empty_content,
                        thinking_loop=_is_thinking_loop(reasoning),
                    )
                )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._starts.pop(str(run_id), None)


def make_llm_factory(base_url: str, model_name: str, recorder: Recorder):
    extra_body: dict[str, Any] = {}
    if SPIKE_CONFIG == "thinking_off":
        extra_body["chat_template_kwargs"] = {"enable_thinking": False}

    def _factory() -> ChatOpenAI:
        return ReasoningChatOpenAI(
            base_url=base_url,
            api_key="none",
            model=model_name,
            timeout=180,
            max_retries=0,
            max_tokens=MAX_TOKENS,
            extra_body=extra_body or None,
            callbacks=[recorder],
        )

    return _factory


def main() -> int:
    model_name = get_model_name()
    base_url = get_model_runner_base_url()
    print(
        f"SPIKE_CONFIG={SPIKE_CONFIG} REPEAT={REPEAT} base_url={base_url} model={model_name} "
        f"max_tokens={MAX_TOKENS}",
        file=sys.stderr,
    )

    recorder = Recorder()
    llm_factory = make_llm_factory(base_url, model_name, recorder)

    all_case_results: list[CaseResult] = []
    wall_start = time.monotonic()

    for rep in range(REPEAT):
        print(f"=== repetition {rep + 1}/{REPEAT} ===", file=sys.stderr)
        for case_id, title, run_fn in CASES:

            def _wrapped_run_fn(llm, _case_id=case_id, _rep=rep):
                # Tag the recorder with case/run identity right before each
                # individual run's invoke() calls fire, so on_llm_end can
                # attribute the resulting CallRecord correctly. run_case()
                # calls run_fn(llm) exactly once per run, so incrementing
                # here (closure over a mutable counter) tracks run index
                # within this (case, repetition).
                recorder.current_case = _case_id
                recorder.current_run = _wrapped_run_fn.counter
                _wrapped_run_fn.counter += 1
                return run_fn(llm)

            _wrapped_run_fn.counter = rep * RUNS_PER_CASE
            case_result = run_case(case_id, title, _wrapped_run_fn, llm_factory)
            all_case_results.append(case_result)

    wall_elapsed = time.monotonic() - wall_start

    # --- Aggregate pass/fail per case_id across all repetitions ---
    by_case: dict[str, list] = {}
    for cr in all_case_results:
        by_case.setdefault(cr.case_id, []).extend(cr.runs)

    total_runs = sum(len(runs) for runs in by_case.values())
    total_passed = sum(1 for runs in by_case.values() for r in runs if r.passed)

    case_summaries = []
    for case_id, _title, _fn in CASES:
        runs = by_case.get(case_id, [])
        passed = sum(1 for r in runs if r.passed)
        case_summaries.append(
            {
                "case_id": case_id,
                "total_runs": len(runs),
                "passed": passed,
                "failures": [r.reason.splitlines()[0] for r in runs if not r.passed],
            }
        )

    latencies = [r.latency_s for r in recorder.records if r.latency_s == r.latency_s]  # filter NaN
    reasoning_word_counts = [r.reasoning_word_count for r in recorder.records if r.reasoning_word_count > 0]
    empty_content_records = [asdict(r) for r in recorder.records if r.empty_content]
    thinking_loop_records = [asdict(r) for r in recorder.records if r.thinking_loop]

    def _pct(data: list[float], pct: float) -> float | None:
        if not data:
            return None
        data_sorted = sorted(data)
        idx = min(len(data_sorted) - 1, int(round(pct * (len(data_sorted) - 1))))
        return data_sorted[idx]

    result = {
        "spike_config": SPIKE_CONFIG,
        "repeat": REPEAT,
        "runs_per_case": RUNS_PER_CASE,
        "pass_threshold": PASS_THRESHOLD,
        "base_url": base_url,
        "model_name": model_name,
        "max_tokens": MAX_TOKENS,
        "wall_elapsed_s": wall_elapsed,
        "total_runs": total_runs,
        "total_passed": total_passed,
        "case_summaries": case_summaries,
        "num_model_calls": len(recorder.records),
        "latency_median_s": statistics.median(latencies) if latencies else None,
        "latency_p95_s": _pct(latencies, 0.95),
        "latency_min_s": min(latencies) if latencies else None,
        "latency_max_s": max(latencies) if latencies else None,
        "reasoning_word_count_median": (
            statistics.median(reasoning_word_counts) if reasoning_word_counts else None
        ),
        "calls_with_reasoning_content": len(reasoning_word_counts),
        "empty_content_count": len(empty_content_records),
        "empty_content_records": empty_content_records,
        "thinking_loop_count": len(thinking_loop_records),
        "thinking_loop_records": thinking_loop_records,
        "all_call_records": [asdict(r) for r in recorder.records],
    }
    print(json.dumps(result, indent=2))
    print(
        f"DONE config={SPIKE_CONFIG} total={total_passed}/{total_runs} "
        f"calls={len(recorder.records)} wall_s={wall_elapsed:.1f}",
        file=sys.stderr,
    )
    return 0 if total_passed == total_runs else 0  # always exit 0; verdict is decided from the JSON


if __name__ == "__main__":
    sys.exit(main())
