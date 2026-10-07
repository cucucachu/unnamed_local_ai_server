from langchain_core.messages import ToolMessage

from app.agent.tool_notes import ToolNotesMiddleware, add_note


def _handler(status: str = "success"):
    def handler(_request):
        add_note("Matched ignoring whitespace at lines 2-4.")
        return ToolMessage(
            content="Successfully replaced 1 instance(s)", tool_call_id="t", status=status
        )

    return handler


def test_notes_are_appended_to_a_successful_result() -> None:
    result = ToolNotesMiddleware().wrap_tool_call(None, _handler())
    assert result.content == (
        "Successfully replaced 1 instance(s)\nMatched ignoring whitespace at lines 2-4."
    )


async def test_notes_are_appended_on_the_async_path() -> None:
    async def handler(request):
        return _handler()(request)

    result = await ToolNotesMiddleware().awrap_tool_call(None, handler)
    assert result.content.endswith("\nMatched ignoring whitespace at lines 2-4.")


def test_an_error_result_is_left_alone() -> None:
    result = ToolNotesMiddleware().wrap_tool_call(None, _handler("error"))
    assert result.content == "Successfully replaced 1 instance(s)"


def test_add_note_outside_a_tool_call_is_a_no_op() -> None:
    add_note("nobody is listening")
