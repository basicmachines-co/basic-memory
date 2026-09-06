import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolResult
from tau.bridge import McpConnection
from tau.extension import execute_tool


async def test_cancellation_token_stops_inflight_request() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_call(*args: object) -> CallToolResult:
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()
        return CallToolResult(content=[])

    connection = MagicMock(spec=McpConnection)
    connection.call = AsyncMock(side_effect=slow_call)
    signal = MagicMock()
    signal.is_cancelled.return_value = False
    pending = asyncio.create_task(execute_tool(connection, "write_note", "id", {}, signal=signal))
    await started.wait()
    signal.is_cancelled.return_value = True
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert cancelled.is_set()
    connection.call.assert_awaited_once()


async def test_uncancelled_token_returns_result() -> None:
    connection = MagicMock(spec=McpConnection)
    connection.call = AsyncMock(return_value=CallToolResult(content=[]))
    signal = MagicMock()
    signal.is_cancelled.return_value = False
    result = await execute_tool(connection, "read_note", "id", {}, signal=signal)
    assert result.content == []
