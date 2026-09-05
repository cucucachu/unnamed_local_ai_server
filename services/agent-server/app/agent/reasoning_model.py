"""M8-06 SPIKE prototype: surface llama-server's `reasoning_content` deltas
(emitted when `--reasoning-format deepseek` is set, see docs/TOOL_CALLING.md's
M8-06 section) through LangChain's `on_chat_model_stream` chunks.

Why this exists
----------------
`langchain-openai`'s `ChatOpenAI` targets the *official* OpenAI API surface
only — its own module docstring says so explicitly: "Non-standard response
fields added by third-party providers (e.g. `reasoning_content`,
`reasoning_details`) are **not** extracted or preserved." Confirmed
empirically against the real `model-runner` for this ticket: with
`--reasoning-format deepseek` set server-side, `reasoning_content` deltas
arrive over the wire before `content` deltas, but a plain `ChatOpenAI().stream(...)`
silently drops them — every chunk's `additional_kwargs` comes back `{}`.

Candidates considered (see the M8-06 ticket spec / docs/TOOL_CALLING.md):
1. **A small `ChatOpenAI` subclass overriding chunk conversion** (chosen,
   see `ReasoningChatOpenAI` below).
2. `output_version="v1"` content blocks — inspected
   `langchain_core.messages.block_translators.openai`; v1 content-block
   translation is keyed off `_dict.get("type")` values OpenAI itself
   defines (`text`, `refusal`, `reasoning`, ...) inside the Responses API
   shape, not Chat Completions `delta.reasoning_content` — it doesn't
   apply to this server's wire format at all, so this candidate was ruled
   out without needing a throwaway prototype.
3. `langchain-deepseek`'s `ChatDeepSeek` — a *heavier* dependency swap
   (new package, new pinned version, its own quirks/compat surface) for a
   one-field problem, when llama-server already speaks Chat Completions +
   `reasoning_content` (the exact shape `ChatDeepSeek` itself targets) —
   pointing it at `model-runner` would work, but subclassing `ChatOpenAI`
   (which this codebase already depends on and constructs everywhere,
   see `model_client.py`) is strictly less invasive: no new dependency, no
   change to `bind_tools`/`tools=` call sites, no change to how
   `deepagents.create_deep_agent` is invoked.

This module is a **prototype only** (per the ticket spec) — `build_model`
in `model_client.py` is unchanged and still returns a plain `ChatOpenAI`.
Wiring `ReasoningChatOpenAI` into the real WS handler / turn loop is
M8-07's job, contingent on the GO/NO-GO verdict in docs/TOOL_CALLING.md.
"""

from __future__ import annotations

from typing import Any

import openai
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI


class ReasoningChatOpenAI(ChatOpenAI):
    """`ChatOpenAI` that also surfaces `delta.reasoning_content` chunks.

    Overrides the one private hook (`_convert_chunk_to_generation_chunk`)
    responsible for turning a raw SSE chunk dict into a `ChatGenerationChunk`
    during streaming, and — after delegating to the real implementation for
    everything else (tool-call chunks, finish_reason, usage metadata, ...) —
    additionally copies `choices[0]["delta"]["reasoning_content"]` (when
    present) onto `message.additional_kwargs["reasoning_content"]`.

    Because `AIMessageChunk.__add__` merges `additional_kwargs` string
    values by concatenation (`langchain_core.utils._merge.merge_dicts`),
    accumulating the full reasoning text across a stream is automatic: each
    chunk only needs to carry its own delta piece, exactly like `content`
    already does. This means a LangGraph/deepagents node's
    `on_chat_model_stream` callback receives `additional_kwargs["reasoning_content"]`
    on the individual streamed chunks it sees, and the fully-merged final
    message also has the complete `reasoning_content` string.

    No behavior changes for a server that never sends `reasoning_content`
    (e.g. today's `--reasoning-budget 0` config, or the plain fake-model
    fixture with `reasoning_content=None`) — the extra key is only ever
    added when the field is actually present in the delta, and every other
    field/branch of the base class's conversion is untouched.
    """

    def _convert_chunk_to_generation_chunk(  # type: ignore[override]
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> Any:
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
        self,
        response: dict | openai.BaseModel,
        generation_info: dict[str, Any] | None = None,
    ) -> ChatResult:
        """Same idea as above, for the non-streaming (`invoke`/`ainvoke`) path.

        llama-server's *non-streamed* JSON also carries `reasoning_content`
        as a sibling field on `message` (confirmed via curl for M8-06 —
        see docs/TOOL_CALLING.md), and the base `ChatOpenAI` drops it here
        too (`_convert_dict_to_message` only looks at documented OpenAI
        fields). Not required by the ticket's specific "on_chat_model_stream"
        ask, but trivial to add symmetrically so this class behaves the same
        regardless of `streaming=True/False`.
        """
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
