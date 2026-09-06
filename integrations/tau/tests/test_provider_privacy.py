from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult
from tau.bridge import Settings
from tau.continuity import NAMESPACE, CaptureRecord, Note, SearchHit, SearchPage, digest
from tau_agent.messages import UserMessage
from tau_agent.session import CustomEntry, MessageEntry
from test_recovery import boundary

SECRET = "sk-" + "testcredential" * 3


@pytest.mark.parametrize("chunked", [False, True])
async def test_prior_note_focus_and_intermediate_summaries_are_masked_before_provider(
    chunked: bool,
) -> None:
    lifecycle, context, _api = boundary()
    lifecycle.settings = Settings(project="notes", auto_recall=False, summary_chunk_chars=1000)
    prior = CaptureRecord(
        project="notes",
        capture_id="prior",
        kind="checkpoint",
        status="confirmed",
        source_tip="old",
        content_digest=digest("original"),
        file_path="prior.md",
    )
    context.branch_entries = [
        MessageEntry(id="old", message=UserMessage(content="prior turn")),
        CustomEntry(namespace=NAMESPACE, data=prior.model_dump(mode="json")),
        MessageEntry(id="new", message=UserMessage(content="new work " * (400 if chunked else 1))),
    ]
    # Markdown is editable canonical input, even after its original receipt.
    lifecycle.read = AsyncMock(return_value=Note(file_path="prior.md", content=SECRET))
    lifecycle.recover = AsyncMock(return_value=None)
    lifecycle.persist = AsyncMock(return_value="new.md")
    context.summarize = AsyncMock(return_value=SECRET)  # also test the next chunk's input
    assert await lifecycle.checkpoint(context, "test", focus=SECRET) == "new.md"
    assert context.summarize.await_count > 1 if chunked else context.summarize.await_count == 1
    for call in context.summarize.call_args_list:
        assert SECRET not in str(call)
        assert "[credential redacted]" in str(call)
    assert SECRET not in lifecycle.persist.call_args.kwargs["content"]
    assert lifecycle.last_error is None


async def test_automatic_recall_masks_edited_note_before_context_insertion() -> None:
    lifecycle, context, api = boundary()
    lifecycle.search = AsyncMock(return_value=SearchPage(results=[SearchHit(file_path="note.md")]))
    lifecycle.read = AsyncMock(return_value=Note(file_path="note.md", content=SECRET))
    lifecycle.connection.call = AsyncMock(
        return_value=CallToolResult(content=[], structured_content={"results": []})
    )
    await lifecycle.orient(context)
    assert SECRET not in api.append_message.call_args.args[0]
    assert "[credential redacted]" in api.append_message.call_args.args[0]
