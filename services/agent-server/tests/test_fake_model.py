"""Self-tests proving the fake model speaks the real OpenAI streaming dialect.

These tests deliberately go through `langchain_openai.ChatOpenAI` (which in
turn uses the real `openai` client, whose response objects are validated
against OpenAI's own pydantic models) rather than a hand-rolled HTTP parser —
that's what actually proves the wire format is correct, not just internally
self-consistent.
"""

import httpx
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from tests.fake_model.scripting import FakeModel, TextTurn, ToolCallsTurn, ToolCallTurn


def _chat(fake_model: FakeModel, **kwargs) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=fake_model.base_url,
        api_key="fake-key-not-checked",
        model="fake-model",
        **kwargs,
    )


@tool
def get_weather(location: str, unit: str = "celsius") -> str:
    """Get the current weather for a location."""
    return "sunny"


async def test_text_turn_streamed_content_matches_exactly(fake_model: FakeModel) -> None:
    text = "The quick brown fox jumps over the lazy dog, several times over."
    fake_model.queue(TextTurn(text=text, chunk_size=7))

    chat = _chat(fake_model)
    pieces = []
    async for chunk in chat.astream([HumanMessage("hi")]):
        pieces.append(chunk.content)

    assert "".join(pieces) == text
    # More than one non-empty content chunk actually arrived (i.e. this test
    # is exercising real streaming, not one big blob).
    assert sum(1 for p in pieces if p) >= 2

    assert len(fake_model.requests) == 1
    assert fake_model.requests[0]["stream"] is True


async def test_text_turn_non_streamed_content_matches_exactly(fake_model: FakeModel) -> None:
    text = "A short non-streamed reply."
    fake_model.queue(TextTurn(text=text))

    chat = _chat(fake_model)
    result = await chat.ainvoke([HumanMessage("hi")])

    assert result.content == text
    assert fake_model.requests[0]["stream"] is False


async def test_tool_call_turn_via_bind_tools_ainvoke(fake_model: FakeModel) -> None:
    args = {"location": "Boston", "unit": "celsius"}
    fake_model.queue(ToolCallTurn(name="get_weather", args=args))

    chat = _chat(fake_model).bind_tools([get_weather])
    result = await chat.ainvoke([HumanMessage("What's the weather in Boston?")])

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "get_weather"
    assert result.tool_calls[0]["args"] == args

    # The request actually sent a tools schema, proving bind_tools wired
    # through correctly.
    sent = fake_model.requests[0]
    assert sent["tools"][0]["function"]["name"] == "get_weather"


async def test_tool_call_turn_via_bind_tools_astream(fake_model: FakeModel) -> None:
    args = {"city": "Paris", "days": 3}
    fake_model.queue(ToolCallTurn(name="get_weather", args=args))

    chat = _chat(fake_model).bind_tools([get_weather])

    chunks = []
    async for chunk in chat.astream([HumanMessage("Forecast for Paris?")]):
        chunks.append(chunk)

    merged = chunks[0]
    for chunk in chunks[1:]:
        merged = merged + chunk

    assert len(merged.tool_calls) == 1
    assert merged.tool_calls[0]["name"] == "get_weather"
    assert merged.tool_calls[0]["args"] == args


async def test_tool_calls_turn_multiple_calls_in_one_turn(fake_model: FakeModel) -> None:
    fake_model.queue(
        ToolCallsTurn(
            calls=[
                ("get_weather", {"location": "Tokyo"}),
                ("get_weather", {"location": "Berlin"}),
            ]
        )
    )

    chat = _chat(fake_model).bind_tools([get_weather])
    result = await chat.ainvoke([HumanMessage("Weather in Tokyo and Berlin?")])

    assert len(result.tool_calls) == 2
    calls_by_arg = {tc["args"]["location"]: tc for tc in result.tool_calls}
    assert set(calls_by_arg) == {"Tokyo", "Berlin"}
    assert all(tc["name"] == "get_weather" for tc in result.tool_calls)
    # Distinct tool call ids.
    assert len({tc["id"] for tc in result.tool_calls}) == 2


async def test_turns_consumed_in_order_across_separate_requests(fake_model: FakeModel) -> None:
    fake_model.queue(
        TextTurn(text="first turn reply"),
        TextTurn(text="second turn reply"),
    )
    chat = _chat(fake_model)

    first = await chat.ainvoke([HumanMessage("one")])
    second = await chat.ainvoke([HumanMessage("two")])

    assert first.content == "first turn reply"
    assert second.content == "second turn reply"
    assert len(fake_model.requests) == 2


async def test_distinct_call_ids_across_turns(fake_model: FakeModel) -> None:
    fake_model.queue(
        ToolCallTurn(name="get_weather", args={"location": "Rome"}),
        ToolCallTurn(name="get_weather", args={"location": "Cairo"}),
    )
    chat = _chat(fake_model).bind_tools([get_weather])

    first = await chat.ainvoke([HumanMessage("one")])
    second = await chat.ainvoke([HumanMessage("two")])

    assert first.tool_calls[0]["id"] != second.tool_calls[0]["id"]


async def test_empty_turn_queue_returns_http_500(fake_model: FakeModel) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{fake_model.base_url}/chat/completions",
            json={"model": "fake-model", "messages": [], "stream": False},
        )

    assert response.status_code == 500


async def test_settings_helper_points_at_fake_base_url(fake_model: FakeModel) -> None:
    settings = fake_model.settings()
    assert settings.model_base_url == fake_model.base_url
