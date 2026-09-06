from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import ValidationError
from tau.bridge import McpConnection, Settings, discover_sync, load_settings
from tau.extension import MemoryLifecycle, make_tool, tool_result
from tau_agent.events import MessageEndEvent
from tau_agent.messages import AssistantMessage, ThinkingContent, UserMessage
from tau_agent.messages import TextContent as TauText
from tau_coding.events import CompactionEndEvent
from tau_coding.extensions import ExtensionAPI, ExtensionContext, ExtensionRuntime
from tau_coding.extensions.runtime import BoundSession
from tau_coding.resources import TauResourcePaths

ROOT = Path(__file__).resolve().parents[1]


def settings(**overrides: object) -> Settings:
    return Settings.model_validate(
        {
            "command": sys.executable,
            "args": [str(ROOT / "tests/fake_server.py")],
            **overrides,
        }
    )


def test_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setenv("TAU_BASIC_MEMORY_CONFIG", str(path))
    assert load_settings().capture_transcript is False
    path.write_text('{"capture_transcript": "false"}')
    with pytest.raises(ValidationError):
        load_settings()
    path.write_text('{"project": "personal/notes", "capture_transcript": true}')
    assert load_settings().project == "personal/notes"


async def test_sync_discovery_inside_running_loop_and_persistent_calls() -> None:
    cfg = settings()
    tools = discover_sync(cfg)
    assert [t.name for t in tools] == ["write_note", "recent_activity", "extra_tool"]
    connection = McpConnection(cfg)
    await connection.start()
    try:
        first = await connection.call("extra_tool", {"project": "team/notes"})
        second = await connection.call("extra_tool", {})
        assert first.structured_content is not None
        assert second.structured_content is not None
        assert first.structured_content["pid"] == second.structured_content["pid"]
        assert first.structured_content["arguments"] == {"project": "team/notes"}
        wrapped = make_tool(connection, tools[2])
        assert wrapped.parameters == tools[2].input_schema
        assert wrapped.name == "bm_extra_tool"
        result = await wrapped.execute("id", {"project": "another/project"})
        assert "another/project" in result.text
        with pytest.raises(RuntimeError, match="tool failed"):
            await wrapped.execute("id", {"fail": True})
    finally:
        await connection.close()
    assert connection.owner is None
    assert connection.session is None
    with pytest.raises(RuntimeError, match="disconnected"):
        await connection.call("extra_tool", {})


async def test_shutdown_cancels_active_call() -> None:
    connection = McpConnection(settings())
    await connection.start()
    pending = asyncio.create_task(connection.call("extra_tool", {"sleep": True}))
    await asyncio.sleep(0.05)
    await asyncio.wait_for(connection.close(), 5)
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert not connection.active_calls


async def test_start_failure_closes_owner() -> None:
    connection = McpConnection(settings(command="/nonexistent/bm"))
    with pytest.raises(RuntimeError, match="startup failed"):
        await connection.start()
    assert connection.owner is None


def test_result_preserves_image_structured_and_errors() -> None:
    result = tool_result(
        CallToolResult(
            content=[
                TextContent(type="text", text="note"),
                ImageContent(type="image", data="aGVsbG8=", mime_type="image/png"),
            ],
            structured_content={"permalink": "notes/example"},
        )
    )
    assert result.content[1].type == "image"
    assert "notes/example" in result.text
    assert result.details is not None
    with pytest.raises(RuntimeError, match="not saved"):
        tool_result(
            CallToolResult(is_error=True, content=[TextContent(type="text", text="not saved")])
        )


async def test_real_tau_loader_recall_compaction_and_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(settings(project="personal/notes").model_dump_json())
    monkeypatch.setenv("TAU_BASIC_MEMORY_CONFIG", str(path))
    # Two actual runtime generations: reload must rediscover and close each connection.
    for reason in ("startup", "reload"):
        runtime = ExtensionRuntime(built_in_extensions=())
        runtime.load(
            TauResourcePaths(root=tmp_path), extra_paths=[ROOT], include_resource_dirs=False
        )
        assert not runtime.diagnostics
        assert len(runtime.compose_tools([])) == 3
        session = MagicMock(spec=BoundSession)
        session.is_running = False
        session.session_id = "session-1"
        runtime.bind(session)
        await runtime.emit_session_start(reason)
        assert not runtime.diagnostics
        session.queue_follow_up_message.assert_called_once()
        recalled = session.queue_follow_up_message.call_args.args[0]
        assert "personal/notes" in recalled
        assert "untrusted reference" in recalled
        await runtime.emit_event(CompactionEndEvent(reason="overflow"))
        assert session.queue_follow_up_message.call_count == 2
        assert "Write a durable" in session.queue_follow_up_message.call_args.args[0]
        await runtime.emit_session_shutdown("reload")
        with pytest.raises(RuntimeError, match="disconnected"):
            await runtime.compose_tools([])[0].execute("id", {})
        assert not runtime.diagnostics


async def test_capture_controls_replay_and_failure() -> None:
    api = MagicMock(spec=ExtensionAPI)
    connection = MagicMock(spec=McpConnection)
    connection.call = AsyncMock(
        return_value=CallToolResult(
            content=[],
            structured_content={"result": {"file_path": "tau/message.md", "action": "created"}},
        )
    )
    context = MagicMock(spec=ExtensionContext)
    context.session_id = "session-1"
    lifecycle = MemoryLifecycle(
        api, settings(project="personal/notes", capture_transcript=True), connection
    )
    event = MessageEndEvent(message=UserMessage(content="Remember the decision", timestamp=123))
    await lifecycle.capture(event, context)
    await lifecycle.capture(event, context)
    assert connection.call.await_count == 1
    arguments = connection.call.call_args.args[1]
    assert arguments["overwrite"] is False
    assert arguments["project"] == "personal/notes"
    assert arguments["note_type"] == "tau_transcript"
    assistant = MessageEndEvent(
        message=AssistantMessage(
            content=[ThinkingContent(thinking="PRIVATE"), TauText(text="Public response")],
            stop_reason="stop",
        )
    )
    await lifecycle.capture(assistant, context)
    assert "PRIVATE" not in json.dumps(connection.call.call_args.args)
    assert "Public response" in json.dumps(connection.call.call_args.args)
    connection.call.side_effect = RuntimeError("secret server details")
    failed = MessageEndEvent(message=UserMessage(content="new message"))
    await lifecycle.capture(failed, context)
    assert lifecycle.last_error is not None
    assert "secret server details" not in str(api.notify.call_args)
    connection.call.reset_mock()
    disabled = MemoryLifecycle(api, settings(project="personal/notes"), connection)
    await disabled.capture(event, context)
    unconfigured = MemoryLifecycle(api, settings(capture_transcript=True), connection)
    await unconfigured.capture(event, context)
    connection.call.assert_not_called()


async def test_failed_or_disabled_compaction_never_requests_write() -> None:
    api = MagicMock(spec=ExtensionAPI)
    context = MagicMock(spec=ExtensionContext)
    connection = MagicMock(spec=McpConnection)
    lifecycle = MemoryLifecycle(api, settings(project="notes"), connection)
    await lifecycle.compacted(CompactionEndEvent(reason="overflow", aborted=True), context)
    disabled = MemoryLifecycle(
        api, settings(project="notes", checkpoint_on_compact=False), connection
    )
    await disabled.compacted(CompactionEndEvent(reason="overflow"), context)
    api.send_custom_message.assert_not_called()
