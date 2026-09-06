from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from tau.bridge import McpConnection, Settings
from tau.continuity import (
    NAMESPACE,
    CaptureRecord,
    MemoryLifecycle,
    Note,
    SearchHit,
    SearchPage,
    digest,
)
from tau.privacy import public_text
from tau_agent.events import MessageEndEvent
from tau_agent.messages import AssistantMessage, TextContent, UserMessage
from tau_agent.session import CustomEntry, MessageEntry
from tau_coding.events import AgentSettledEvent, CompactionEndEvent
from tau_coding.extensions import ExtensionAPI, ExtensionContext
from tau_coding.extensions.api import InputEvent
from tau_coding.tui.app import PromptInput, TauTuiApp
from test_continuity import make_session


async def test_branch_lineage_and_fresh_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = await make_session(tmp_path, monkeypatch)
    for text in ("First task.", "Second task."):
        async for _ in session.prompt(text):
            pass
    original = json.loads((tmp_path / "notes.json").read_text())
    assert len(original) == 2
    first = next(
        e
        for e in session.active_branch_entries
        if isinstance(e, MessageEntry) and isinstance(e.message, AssistantMessage)
    )
    await session.branch_to_entry(first.id)
    await session.extension_runtime.emit_event(AgentSettledEvent())
    assert (
        len(json.loads((tmp_path / "notes.json").read_text())) == 2
    )  # shared tip recovered read-only
    async for _ in session.prompt("Different branch task."):
        pass
    notes = json.loads((tmp_path / "notes.json").read_text())
    assert len(notes) == 3
    last = next(note for path, note in notes.items() if path not in original)
    ancestor = next(
        path for path, note in original.items() if note["frontmatter"]["source_tip"] == first.id
    )
    assert f"continues [[{ancestor}]]" in last["content"]
    assert not session.resource_diagnostics
    await session.aclose()
    resumed = await make_session(tmp_path, monkeypatch)
    assert any("Active branch checkpoint" in str(m) for m in resumed.messages)
    await resumed.aclose()
    assert len(json.loads((tmp_path / "notes.json").read_text())) == 3


async def test_headless_tui_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = await make_session(tmp_path, monkeypatch)
    async for _ in session.prompt("Remember blue."):
        pass
    outgoing = session.extension_runtime
    app = TauTuiApp(session)
    async with app.run_test(size=(100, 30)) as pilot:
        prompt = app.query_one("#prompt", PromptInput)
        prompt.text = "/reload"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert session.extension_runtime is not outgoing
        assert not outgoing.active
        assert not session.queued_messages.follow_up
        assert not session.resource_diagnostics
    await session.aclose()


def boundary() -> tuple[MemoryLifecycle, MagicMock, MagicMock]:
    context = MagicMock(spec=ExtensionContext)
    context.branch_entries = [MessageEntry(id="tip", message=UserMessage(content="blue"))]
    context.session_id = "s"
    context.cwd = Path("/project")
    api = MagicMock(spec=ExtensionAPI)
    connection = MagicMock(spec=McpConnection)
    connection.session = None
    lifecycle = MemoryLifecycle(api, Settings(project="notes"), connection)
    lifecycle.search = AsyncMock(return_value=SearchPage(results=[]))
    return lifecycle, context, api


@pytest.mark.parametrize(
    "failure",
    [TimeoutError(), RuntimeError(), "x" * 20000],
    ids=["timeout", "provider-error", "oversized"],
)
async def test_summary_failures_never_confirm(failure: Exception | str) -> None:
    lifecycle, context, api = boundary()
    context.summarize = (
        AsyncMock(return_value=failure)
        if isinstance(failure, str)
        else AsyncMock(side_effect=failure)
    )
    await lifecycle.checkpoint(context, "test")
    assert lifecycle.last_checkpoint is None
    assert lifecycle.last_error
    api.append_entry.assert_not_called()
    lifecycle.connection.close = AsyncMock()
    await lifecycle.shutdown(None, context)
    lifecycle.connection.close.assert_awaited_once()


async def test_cancelled_summary_propagates() -> None:
    lifecycle, context, api = boundary()
    context.summarize = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await lifecycle.checkpoint(context, "test")
    api.append_entry.assert_not_called()
    assert not lifecycle.writing.locked()


@pytest.mark.parametrize("pending", [True, False])
@pytest.mark.parametrize("mode", ["duplicate", "changed"])
async def test_reconciliation_rejects_ambiguity_and_changed_bytes(pending: bool, mode: str) -> None:
    lifecycle, context, api = boundary()
    if pending:
        record = CaptureRecord(
            project="notes",
            capture_id="id",
            kind="transcript",
            status="pending",
            source_tip="tip",
            content_digest=digest("original"),
        )
        context.branch_entries.append(
            CustomEntry(namespace=NAMESPACE, data=record.model_dump(mode="json"))
        )
    lifecycle.search = AsyncMock(
        return_value=SearchPage(
            results=[SearchHit(file_path="note.md")] * (2 if mode == "duplicate" else 1)
        )
    )
    lifecycle.read = AsyncMock(return_value=Note(file_path="note.md", content="changed"))
    with pytest.raises(RuntimeError):
        await lifecycle.persist(
            context,
            kind="transcript",
            capture_id="id",
            source_tip="tip",
            content="original",
            reason="replay",
        )
    call = lifecycle.connection.call
    assert isinstance(call, AsyncMock)
    call.assert_not_called()
    api.append_entry.assert_not_called()


async def test_cross_branch_transcript_recovery() -> None:
    lifecycle, context, api = boundary()
    lifecycle.search = AsyncMock(return_value=SearchPage(results=[SearchHit(file_path="note.md")]))
    lifecycle.read = AsyncMock(
        return_value=Note(
            file_path="note.md",
            content="original",
            frontmatter={
                "capture_id": "id",
                "source_tip": "tip",
                "content_digest": digest("original"),
            },
        )
    )
    assert (
        await lifecycle.persist(
            context,
            kind="transcript",
            capture_id="id",
            source_tip="tip",
            content="original",
            reason="replay",
        )
        == "note.md"
    )
    call = lifecycle.connection.call
    assert isinstance(call, AsyncMock)
    call.assert_not_called()
    assert api.append_entry.call_args.args[1]["status"] == "confirmed"


async def test_controls_and_explicit_orientation() -> None:
    lifecycle, context, api = boundary()
    lifecycle.settings = Settings()
    await lifecycle.checkpoint(context, "test")
    await lifecycle.orient(context)
    assert lifecycle.last_error
    with pytest.raises(ValueError):
        await lifecycle.persist(
            context, kind="transcript", capture_id="id", source_tip="tip", content="", reason="test"
        )
    handled = await lifecycle.input(
        InputEvent(text="/basic-memory-internal-checkpoint ", source="extension"), context
    )
    assert handled and handled.action == "handled" and handled.message
    assert await lifecycle.input(object(), context) is None
    assert await lifecycle.input(InputEvent(text="ordinary", source="extension"), context) is None
    await lifecycle.compacted(CompactionEndEvent(reason="manual", aborted=True), context)
    lifecycle.settings = Settings(project="notes", capture_transcript=True)
    await lifecycle.capture(
        MessageEndEvent(
            message=AssistantMessage(content=[TextContent(text="failed")], stop_reason="error")
        ),
        context,
    )
    api.append_entry.assert_not_called()


async def test_orientation_reads_topic_and_respects_budget() -> None:
    from mcp.types import CallToolResult

    lifecycle, context, api = boundary()
    lifecycle.settings = Settings(project="notes", recall_chars=100)
    lifecycle.search = AsyncMock(return_value=SearchPage(results=[SearchHit(file_path="cwd.md")]))
    lifecycle.read = AsyncMock(
        side_effect=lambda path, *, project=None: Note(file_path=path, content="x" * 200)
    )
    lifecycle.connection.call = AsyncMock(
        return_value=CallToolResult(
            content=[], structured_content={"results": [{"file_path": "topic.md"}]}
        )
    )
    await lifecycle.orient(context, "topic")
    assert lifecycle.read.await_count == 1
    lifecycle.connection.call.assert_not_called()
    assert "Recall truncated" in api.append_message.call_args.args[0]


def test_common_credentials_are_masked() -> None:
    value = "sk-" + "a" * 30
    assert value not in public_text(f"Use {value}")
    assert "password-value" not in public_text("PASSWORD=password-value")
    assert "private" not in public_text(
        "-----BEGIN RSA PRIVATE KEY-----private-----END RSA PRIVATE KEY-----"
    )
    assert public_text("Keep the astrolabe blue") == "Keep the astrolabe blue"
