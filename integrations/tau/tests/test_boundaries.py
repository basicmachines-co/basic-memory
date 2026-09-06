from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp import ClientSession
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from pydantic import JsonValue, ValidationError
from tau import bridge
from tau.bridge import McpConnection, Settings, list_tools
from tau.extension import MemoryLifecycle, confirm_write, execute_tool, tool_result
from tau_agent.events import MessageEndEvent
from tau_agent.messages import AssistantMessage, CustomMessage, UserMessage
from tau_coding.extensions import ExtensionAPI, ExtensionCommandContext, ExtensionContext


async def test_paginated_duplicates_and_cursor_cycle() -> None:
    session = MagicMock(spec=ClientSession)
    tool = Tool(name="example", input_schema={"type": "object"})
    session.list_tools = AsyncMock(
        side_effect=[
            ListToolsResult(tools=[tool], next_cursor="a"),
            ListToolsResult(tools=[tool]),
        ]
    )
    with pytest.raises(ValueError, match="duplicate tool"):
        await list_tools(session)
    session.list_tools.side_effect = [
        ListToolsResult(tools=[], next_cursor="a"),
        ListToolsResult(tools=[], next_cursor="a"),
    ]
    with pytest.raises(ValueError, match="repeated a pagination cursor"):
        await list_tools(session)


async def test_recall_controls_truncation_and_failure() -> None:
    api = MagicMock(spec=ExtensionAPI)
    connection = MagicMock(spec=McpConnection)
    connection.start = AsyncMock()
    context = MagicMock(spec=ExtensionContext)
    context.branch_entries = ()
    unconfigured = MemoryLifecycle(api, Settings(), connection)
    await unconfigured.start(None, context)
    api.notify.assert_called_once()
    disabled = MemoryLifecycle(api, Settings(project="notes", auto_recall=False), connection)
    await disabled.start(None, context)
    connection.call.assert_not_called()

    async def call(name: str, arguments: dict[str, JsonValue]) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text="x" * 1000)],
            structured_content={"results": []} if name == "search_notes" else None,
        )

    connection.call = AsyncMock(side_effect=call)
    lifecycle = MemoryLifecycle(api, Settings(project="notes", recall_chars=100), connection)
    await lifecycle.start(None, context)
    assert "Recall truncated" in api.append_message.call_args.args[0]
    connection.call.side_effect = RuntimeError("sensitive server detail")
    await lifecycle.start(None, context)
    assert lifecycle.last_error is not None
    assert "sensitive" not in str(api.notify.call_args)


def test_workflows_and_status_report_requests_not_saves() -> None:
    api = MagicMock(spec=ExtensionAPI)
    context = MagicMock(spec=ExtensionCommandContext)
    connection = MagicMock(spec=McpConnection)
    connection.session = None
    lifecycle = MemoryLifecycle(api, Settings(), connection)
    assert "not set" in lifecycle.status("", context)
    assert "none confirmed" in lifecycle.status("", context)
    for name in ("orient", "checkpoint", "remember"):
        assert "no write confirmed" in lifecycle.workflow(name, "user input", context)
        assert "user input" in api.send_user_message.call_args.args[0]
    configured = MemoryLifecycle(api, Settings(project="notes"), connection)
    configured.workflow("remember", "decision", context)
    assert '"notes"' in api.send_user_message.call_args.args[0]


async def test_capture_ignores_nonpublic_messages_and_requires_receipt() -> None:
    api = MagicMock(spec=ExtensionAPI)
    context = MagicMock(spec=ExtensionContext)
    context.session_id = "s"
    context.branch_entries = ()
    connection = MagicMock(spec=McpConnection)
    lifecycle = MemoryLifecycle(api, Settings(project="notes", capture_transcript=True), connection)
    for event in (
        object(),
        MessageEndEvent(message=CustomMessage(custom_type="recall", content="private context")),
        MessageEndEvent(message=AssistantMessage(content=[], stop_reason="error")),
        MessageEndEvent(message=UserMessage(content=" ")),
    ):
        await lifecycle.capture(event, context)
    connection.call.assert_not_called()
    connection.call = AsyncMock(
        return_value=CallToolResult(
            content=[],
            structured_content={"result": {"action": "conflict", "error": "NOTE_ALREADY_EXISTS"}},
        )
    )
    await lifecycle.capture(MessageEndEvent(message=UserMessage(content="a message")), context)
    assert lifecycle.last_checkpoint is None
    assert lifecycle.last_error is not None
    with pytest.raises(ValidationError):
        confirm_write(CallToolResult(content=[]))
    receipt = confirm_write(
        CallToolResult(
            content=[],
            structured_content={
                "file_path": "notes/note.md",
                "action": "created",
                "checksum": None,
            },
        )
    )
    assert receipt.file_path == "notes/note.md"


def test_non_native_content_is_explicit_and_preserved() -> None:
    result = CallToolResult.model_validate(
        {
            "content": [
                {"type": "audio", "data": "YWJj", "mimeType": "audio/wav"},
            ]
        }
    )
    converted = tool_result(result)
    assert "MCP audio:" in converted.text
    assert "YWJj" in converted.text


async def test_cancelled_tool_never_calls_server() -> None:
    connection = MagicMock(spec=McpConnection)
    signal = MagicMock()
    signal.is_cancelled.return_value = True
    with pytest.raises(asyncio.CancelledError):
        await execute_tool(connection, "write_note", "id", {}, signal=signal)
    connection.call.assert_not_called()


async def test_owner_start_cancel_and_context_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()

    @asynccontextmanager
    async def slow_connect(settings: Settings) -> AsyncIterator[ClientSession]:
        entered.set()
        await asyncio.sleep(10)
        session = MagicMock(spec=ClientSession)
        assert isinstance(session, ClientSession)
        yield session

    monkeypatch.setattr(bridge, "connect", slow_connect)
    connection = McpConnection(Settings())
    starting = asyncio.create_task(connection.start())
    await entered.wait()
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert connection.owner is None
    assert connection.session is None

    @asynccontextmanager
    async def failing_close(settings: Settings) -> AsyncIterator[ClientSession]:
        session = MagicMock(spec=ClientSession)
        assert isinstance(session, ClientSession)
        yield session
        raise RuntimeError("cleanup failure")

    monkeypatch.setattr(bridge, "connect", failing_close)
    connection = McpConnection(Settings())
    await connection.start()
    with pytest.raises(RuntimeError, match="already started"):
        await connection.start()
    with pytest.raises(RuntimeError, match="cleanup failure"):
        await connection.close()
    assert connection.owner is None
