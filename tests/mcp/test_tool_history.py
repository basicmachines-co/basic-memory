"""Tests for tool call history tracking."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, patch

import pytest

from basic_memory.mcp.tool_history import (
    ToolCall,
    ToolHistoryTracker,
    track_tool_call,
    get_tracker,
)
from basic_memory.mcp.tools.tool_history import (
    tool_history,
    get_tool_call,
    clear_tool_history,
)


@pytest.fixture
async def tracker():
    """Get a fresh tracker instance for testing."""
    tracker = ToolHistoryTracker()
    await tracker.clear_history()
    return tracker


@pytest.mark.asyncio
async def test_singleton_tracker():
    """Test that ToolHistoryTracker is a singleton."""
    tracker1 = ToolHistoryTracker()
    tracker2 = ToolHistoryTracker()
    assert tracker1 is tracker2


@pytest.mark.asyncio
async def test_get_tracker():
    """Test the get_tracker convenience function."""
    tracker1 = get_tracker()
    tracker2 = get_tracker()
    assert tracker1 is tracker2


@pytest.mark.asyncio
async def test_add_and_update_call(tracker):
    """Test adding and updating a tool call."""
    # Add a call
    call_id = await tracker.add_call(
        tool_name="test_tool",
        input_params={"param1": "value1", "param2": 42}
    )

    assert call_id.startswith("call_")

    # Get the call
    calls = await tracker.get_history(limit=1)
    assert len(calls) == 1
    assert calls[0].tool_name == "test_tool"
    assert calls[0].status == "running"
    assert calls[0].input == {"param1": "value1", "param2": 42}

    # Update the call
    await tracker.update_call(
        call_id=call_id,
        status="success",
        execution_time_ms=123.45,
        output="Result data"
    )

    # Check update
    updated_call = await tracker.get_call_by_id(call_id)
    assert updated_call is not None
    assert updated_call.status == "success"
    assert updated_call.execution_time_ms == 123.45
    assert updated_call.output == "Result data"


@pytest.mark.asyncio
async def test_track_tool_call_decorator():
    """Test the track_tool_call decorator."""

    @track_tool_call
    async def sample_tool(param1: str, param2: int) -> str:
        """A sample tool for testing."""
        await asyncio.sleep(0.01)  # Simulate some work
        return f"Processed {param1} with {param2}"

    # Clear history first
    tracker = get_tracker()
    await tracker.clear_history()

    # Call the decorated function
    result = await sample_tool(param1="test", param2=123)
    assert result == "Processed test with 123"

    # Check that it was tracked
    calls = await tracker.get_history(limit=1)
    assert len(calls) == 1
    assert calls[0].tool_name == "sample_tool"
    assert calls[0].status == "success"
    assert calls[0].input == {"param1": "test", "param2": 123}
    assert "Processed test with 123" in str(calls[0].output)


@pytest.mark.asyncio
async def test_track_tool_call_with_error():
    """Test the track_tool_call decorator with an error."""

    @track_tool_call
    async def failing_tool(param1: str) -> str:
        """A tool that fails."""
        raise ValueError("Test error")

    tracker = get_tracker()
    await tracker.clear_history()

    # Call the decorated function and expect error
    with pytest.raises(ValueError, match="Test error"):
        await failing_tool(param1="test")

    # Check that error was tracked
    calls = await tracker.get_history(limit=1)
    assert len(calls) == 1
    assert calls[0].tool_name == "failing_tool"
    assert calls[0].status == "error"
    assert calls[0].error == "Test error"


@pytest.mark.asyncio
async def test_filter_by_tool_name(tracker):
    """Test filtering history by tool name."""
    # Add calls for different tools
    await tracker.add_call("tool_a", {"param": 1})
    await tracker.add_call("tool_b", {"param": 2})
    await tracker.add_call("tool_a", {"param": 3})

    # Filter by tool name
    calls_a = await tracker.get_history(tool_name="tool_a")
    assert len(calls_a) == 2
    assert all(call.tool_name == "tool_a" for call in calls_a)

    calls_b = await tracker.get_history(tool_name="tool_b")
    assert len(calls_b) == 1
    assert calls_b[0].tool_name == "tool_b"


@pytest.mark.asyncio
async def test_filter_by_time(tracker):
    """Test filtering history by time."""
    # Add an old call
    old_call_id = await tracker.add_call("old_tool", {"param": "old"})

    # Update timestamp to be 2 hours ago
    for call in tracker.history:
        if call.id == old_call_id:
            call.timestamp = time.time() - 7200  # 2 hours ago
            break

    # Add a recent call
    await tracker.add_call("recent_tool", {"param": "recent"})

    # Filter by time
    recent_calls = await tracker.get_history(since="1h ago")
    assert len(recent_calls) == 1
    assert recent_calls[0].tool_name == "recent_tool"

    all_calls = await tracker.get_history(since="3h ago")
    assert len(all_calls) == 2


@pytest.mark.asyncio
async def test_max_history_limit(tracker):
    """Test that history respects max size limit."""
    # Create a tracker with small limit
    small_tracker = ToolHistoryTracker()
    small_tracker.max_history = 3
    await small_tracker.clear_history()

    # Add more calls than the limit
    for i in range(5):
        await small_tracker.add_call(f"tool_{i}", {"index": i})

    # Check that only the last 3 are kept
    calls = await small_tracker.get_history(limit=10)
    assert len(calls) == 3
    # Most recent should be tool_4
    assert calls[0].tool_name == "tool_4"


@pytest.mark.asyncio
async def test_tool_history_mcp_tool():
    """Test the tool_history MCP tool."""
    tracker = get_tracker()
    await tracker.clear_history()

    # Add some test calls
    call_id1 = await tracker.add_call("write_note", {"title": "Test", "content": "Hello"})
    await tracker.update_call(call_id1, "success", 100.5, "Note created")

    call_id2 = await tracker.add_call("search", {"query": "test"})
    await tracker.update_call(call_id2, "error", 50.2, error="Search failed")

    # Test tool_history function
    result = await tool_history(limit=5, include_inputs=True, include_outputs=True)

    assert "Tool Call History" in result
    assert "write_note" in result
    assert "search" in result
    assert "Test" in result  # Input parameter
    assert "Note created" in result  # Output
    assert "Search failed" in result  # Error
    assert "2 calls (1 successful, 1 errors)" in result  # Summary


@pytest.mark.asyncio
async def test_get_tool_call_mcp_tool():
    """Test the get_tool_call MCP tool."""
    tracker = get_tracker()
    await tracker.clear_history()

    # Add a test call
    call_id = await tracker.add_call("test_tool", {"param": "value"})
    await tracker.update_call(call_id, "success", 75.3, "Test output")

    # Get the specific call
    result = await get_tool_call(call_id)

    assert f"Tool Call Details: {call_id}" in result
    assert "test_tool" in result
    assert "75.30ms" in result
    assert '"param": "value"' in result
    assert "Test output" in result

    # Test non-existent call
    result = await get_tool_call("non_existent_id")
    assert "not found" in result


@pytest.mark.asyncio
async def test_clear_tool_history_mcp_tool():
    """Test the clear_tool_history MCP tool."""
    tracker = get_tracker()

    # Add some calls
    await tracker.add_call("tool1", {})
    await tracker.add_call("tool2", {})

    # Verify calls exist
    calls = await tracker.get_history()
    assert len(calls) > 0

    # Clear history
    result = await clear_tool_history()
    assert "cleared" in result.lower()

    # Verify history is empty
    calls = await tracker.get_history()
    assert len(calls) == 0


@pytest.mark.asyncio
async def test_parse_time_filter():
    """Test various time filter formats."""
    tracker = ToolHistoryTracker()

    # Test relative time formats
    now = time.time()

    # Hours ago
    result = tracker._parse_time_filter("1h ago")
    assert abs(result - (now - 3600)) < 1

    result = tracker._parse_time_filter("2h ago")
    assert abs(result - (now - 7200)) < 1

    # Days ago
    result = tracker._parse_time_filter("1d ago")
    assert abs(result - (now - 86400)) < 1

    # Minutes ago
    result = tracker._parse_time_filter("30m ago")
    assert abs(result - (now - 1800)) < 1

    # Weeks ago
    result = tracker._parse_time_filter("1w ago")
    assert abs(result - (now - 604800)) < 1

    # Invalid format should return current time
    result = tracker._parse_time_filter("invalid")
    assert abs(result - now) < 1


@pytest.mark.asyncio
async def test_tool_call_to_dict():
    """Test ToolCall.to_dict method."""
    tool_call = ToolCall(
        id="test_id",
        timestamp=1700000000.0,
        tool_name="test_tool",
        status="success",
        execution_time_ms=123.45,
        input={"param": "value"},
        output="result",
        error=None,
    )

    result = tool_call.to_dict()

    assert result["id"] == "test_id"
    assert result["tool_name"] == "test_tool"
    assert result["status"] == "success"
    assert result["execution_time_ms"] == 123.45
    assert result["input"] == {"param": "value"}
    assert result["output"] == "result"
    assert result["error"] is None
    # Check timestamp is converted to ISO format
    assert "T" in result["timestamp"]
    assert result["timestamp"].endswith("Z")