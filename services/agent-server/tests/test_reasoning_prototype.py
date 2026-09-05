"""M8-06 SPIKE: proves `ReasoningChatOpenAI` surfaces `reasoning_content`
deltas from a scripted fake model into LangChain streamed chunks'
`additional_kwargs`, and that the merged final message carries the full
reasoning text too — the exact claim the M8-06 ticket asks to be prototyped
and tested (see `app/agent/reasoning_model.py`'s module docstring for why
this specific approach was chosen over `output_version="v1"` /
`langchain-deepseek`).

Also proves the *negative* case: a plain `ChatOpenAI` against the same
scripted response drops `reasoning_content` entirely (this is the exact
behavior confirmed against the real model-runner in docs/TOOL_CALLING.md's
M8-06 section, and the reason this prototype exists at all).
"""

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.agent.reasoning_model import ReasoningChatOpenAI
from tests.fake_model.scripting import FakeModel, TextTurn


def _reasoning_chat(fake_model: FakeModel, **kwargs) -> ReasoningChatOpenAI:
    return ReasoningChatOpenAI(
        base_url=fake_model.base_url,
        api_key="fake-key-not-checked",
        model="fake-model",
        **kwargs,
    )


def _plain_chat(fake_model: FakeModel, **kwargs) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=fake_model.base_url,
        api_key="fake-key-not-checked",
        model="fake-model",
        **kwargs,
    )


async def test_reasoning_content_surfaced_on_streamed_chunks(fake_model: FakeModel) -> None:
    fake_model.queue(
        TextTurn(
            text="4",
            reasoning_content="The user asked 2+2. That's 4.",
            reasoning_chunk_size=6,
        )
    )

    chat = _reasoning_chat(fake_model)

    reasoning_pieces = []
    content_pieces = []
    async for chunk in chat.astream([HumanMessage("What is 2+2?")]):
        rc = chunk.additional_kwargs.get("reasoning_content")
        if rc:
            reasoning_pieces.append(rc)
        if chunk.content:
            content_pieces.append(chunk.content)

    # Reasoning arrived as more than one chunk (real streaming, not one blob)
    # and in full, exactly like `content` chunking already works.
    assert len(reasoning_pieces) >= 2
    assert "".join(reasoning_pieces) == "The user asked 2+2. That's 4."
    assert "".join(content_pieces) == "4"


async def test_reasoning_content_accumulates_on_merged_message(fake_model: FakeModel) -> None:
    fake_model.queue(
        TextTurn(
            text="Paris",
            reasoning_content="France's capital is Paris.",
        )
    )

    chat = _reasoning_chat(fake_model)

    merged = None
    async for chunk in chat.astream([HumanMessage("Capital of France?")]):
        merged = chunk if merged is None else merged + chunk

    assert merged is not None
    assert merged.content == "Paris"
    # `AIMessageChunk.__add__` concatenates string `additional_kwargs`
    # values (langchain_core.utils._merge.merge_dicts), so the fully
    # merged final chunk carries the complete reasoning text, not just
    # the last delta piece.
    assert merged.additional_kwargs["reasoning_content"] == "France's capital is Paris."


async def test_reasoning_content_absent_when_turn_has_none(fake_model: FakeModel) -> None:
    """No regression: a turn with no `reasoning_content` never adds the key."""
    fake_model.queue(TextTurn(text="hello"))

    chat = _reasoning_chat(fake_model)

    merged = None
    async for chunk in chat.astream([HumanMessage("hi")]):
        assert "reasoning_content" not in chunk.additional_kwargs
        merged = chunk if merged is None else merged + chunk

    assert merged.content == "hello"
    assert "reasoning_content" not in merged.additional_kwargs


async def test_plain_chat_openai_drops_reasoning_content(fake_model: FakeModel) -> None:
    """The negative case this prototype exists to fix.

    Confirms `langchain_openai.ChatOpenAI`'s documented behavior (its own
    module docstring: non-standard fields like `reasoning_content` "are not
    extracted or preserved") holds against our scripted fake exactly like it
    did against the real model-runner (docs/TOOL_CALLING.md, M8-06).
    """
    fake_model.queue(
        TextTurn(text="4", reasoning_content="2+2 is 4.")
    )

    chat = _plain_chat(fake_model)

    merged = None
    async for chunk in chat.astream([HumanMessage("What is 2+2?")]):
        assert "reasoning_content" not in chunk.additional_kwargs
        merged = chunk if merged is None else merged + chunk

    assert merged.content == "4"
    assert "reasoning_content" not in merged.additional_kwargs


async def test_reasoning_content_surfaced_on_non_streamed_invoke(fake_model: FakeModel) -> None:
    """`ReasoningChatOpenAI` also overrides the non-streaming path
    (`_create_chat_result`), so `ainvoke()` (not just `astream()`) surfaces
    `reasoning_content` too — see that override's docstring for why this
    was added symmetrically even though the ticket's specific ask was about
    `on_chat_model_stream` chunks.
    """
    fake_model.queue(TextTurn(text="4", reasoning_content="2+2 is 4."))

    chat = _reasoning_chat(fake_model, streaming=False)
    result = await chat.ainvoke([HumanMessage("What is 2+2?")])

    assert result.content == "4"
    assert result.additional_kwargs["reasoning_content"] == "2+2 is 4."


async def test_plain_chat_openai_drops_reasoning_content_non_streamed(
    fake_model: FakeModel,
) -> None:
    """Negative case for the non-streaming path: plain `ChatOpenAI` still drops it."""
    fake_model.queue(TextTurn(text="4", reasoning_content="2+2 is 4."))

    chat = _plain_chat(fake_model, streaming=False)
    result = await chat.ainvoke([HumanMessage("What is 2+2?")])

    assert result.content == "4"
    assert "reasoning_content" not in result.additional_kwargs
