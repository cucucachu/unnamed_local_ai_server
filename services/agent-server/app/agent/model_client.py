"""Builds the chat-model client pointed at `model-runner`'s OpenAI-compatible API.

M8-07: returns `ReasoningChatOpenAI` (the M8-06 chosen approach) so
llama-server's `delta.reasoning_content` is visible on streamed
`AIMessageChunk`s. Per-turn `enable_thinking` is NOT bound here — see
`ReasoningChatOpenAI._default_params`, which reads
`configurable["thinking_enabled"]` (set by `chat_ws.py` each turn) and
injects `extra_body={"chat_template_kwargs": {"enable_thinking": ...}}`.
That is the "configurable -> model kwargs" path (not `model.bind(...)`):
the compiled deep agent already owns the model instance, so a per-turn
`.bind()` would be discarded.
"""

from app.agent.reasoning_model import ReasoningChatOpenAI
from app.core.config import Settings


def build_model(settings: Settings) -> ReasoningChatOpenAI:
    return ReasoningChatOpenAI(
        base_url=settings.model_base_url,
        api_key="none",
        model=settings.model_name,
        temperature=1.0,
        streaming=True,
        max_retries=1,
        timeout=600,
    )
