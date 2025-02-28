"""Tests for MCP prompts."""

import pytest

from basic_memory.mcp.prompts.continue_conversation import continue_conversation


@pytest.mark.asyncio
async def test_continue_conversation_with_topic(client, test_graph):
    """Test continue_conversation with a topic."""
    # We can use the test_graph fixture which already has relevant content

    # Call the function with a topic that should match existing content
    result = await continue_conversation(topic="Root", timeframe="1w")

    # Check that the result contains expected content
    assert "Continuing conversation on: Root" in result
    assert "This is a memory retrieval session" in result
    assert "Start by executing one of the suggested commands" in result
    assert "read_note" in result


@pytest.mark.asyncio
async def test_continue_conversation_with_recent_activity(client, test_graph):
    """Test continue_conversation with no topic, using recent activity."""
    # Call the function without a topic
    result = await continue_conversation(timeframe="1w")

    # Check that the result contains expected content for recent activity
    assert "Continuing conversation on: Recent Activity" in result
    assert "This is a memory retrieval session" in result
    assert "Please use the available basic-memory tools" in result
    assert "Next Steps" in result


@pytest.mark.asyncio
async def test_continue_conversation_no_results(client):
    """Test continue_conversation when no results are found."""
    # Call with a non-existent topic
    result = await continue_conversation(topic="NonExistentTopic", timeframe="1w")

    # Check the response indicates no results found
    assert "Continuing conversation on: NonExistentTopic" in result
    assert "I couldn't find any recent work specifically on this topic" in result
    assert "Try a different search term" in result


@pytest.mark.asyncio
async def test_continue_conversation_creates_structured_suggestions(client, test_graph):
    """Test that continue_conversation generates structured tool usage suggestions."""
    # Call the function with a topic that should match existing content
    result = await continue_conversation(topic="Root", timeframe="1w")

    # Verify the response includes clear tool usage instructions
    assert "start by executing one of the suggested commands" in result.lower()

    # Check that the response contains tool call examples
    assert "read_note" in result
    assert "search" in result
    assert "recent_activity" in result
    assert "build_context" in result
