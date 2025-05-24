"""Tests for MCP prompts."""

from datetime import timezone, datetime

import pytest

from basic_memory.mcp.prompts.continue_conversation import continue_conversation
from basic_memory.mcp.prompts.search import search_prompt
from basic_memory.mcp.prompts.recent_activity import recent_activity_prompt


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
    assert "The supplied query did not return any information" in result


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


# Search prompt tests


@pytest.mark.asyncio
async def test_search_prompt_with_results(client, test_graph):
    """Test search_prompt with a query that returns results."""
    # Call the function with a query that should match existing content
    result = await search_prompt("Root")

    # Check the response contains expected content
    assert 'Search Results for: "Root"' in result
    assert "I found " in result
    assert "You can view this content with: `read_note" in result
    assert "Synthesize and Capture Knowledge" in result


@pytest.mark.asyncio
async def test_search_prompt_with_timeframe(client, test_graph):
    """Test search_prompt with a timeframe."""
    # Call the function with a query and timeframe
    result = await search_prompt("Root", timeframe="1w")

    # Check the response includes timeframe information
    assert 'Search Results for: "Root" (after 7d)' in result
    assert "I found " in result


@pytest.mark.asyncio
async def test_search_prompt_no_results(client):
    """Test search_prompt when no results are found."""
    # Call with a query that won't match anything
    result = await search_prompt("XYZ123NonExistentQuery")

    # Check the response indicates no results found
    assert 'Search Results for: "XYZ123NonExistentQuery"' in result
    assert "I couldn't find any results for this query" in result
    assert "Opportunity to Capture Knowledge" in result
    assert "write_note" in result


# Test utils


def test_prompt_context_with_file_path_no_permalink():
    """Test format_prompt_context with items that have file_path but no permalink."""
    from basic_memory.mcp.prompts.utils import (
        format_prompt_context,
        PromptContext,
        PromptContextItem,
    )
    from basic_memory.schemas.memory import EntitySummary

    # Create a mock context with a file that has no permalink (like a binary file)
    test_entity = EntitySummary(
        type="file",
        title="Test File",
        permalink=None,  # No permalink
        file_path="test_file.pdf",
        created_at=datetime.now(timezone.utc),
    )

    context = PromptContext(
        topic="Test Topic",
        timeframe="1d",
        results=[
            PromptContextItem(
                primary_results=[test_entity],
                related_results=[test_entity],  # Also use as related
            )
        ],
    )

    # Format the context
    result = format_prompt_context(context)

    # Check that file_path is used when permalink is missing
    assert "test_file.pdf" in result
    assert "read_file" in result


# Recent activity prompt tests


@pytest.mark.asyncio
async def test_recent_activity_prompt(client, test_graph):
    """Test recent_activity_prompt."""
    # Call the function
    result = await recent_activity_prompt(timeframe="1w")

    # Check the response contains expected content
    assert "Recent Activity" in result
    assert "Opportunity to Capture Activity Summary" in result
    assert "write_note" in result


@pytest.mark.asyncio
async def test_recent_activity_prompt_with_custom_timeframe(client, test_graph):
    """Test recent_activity_prompt with custom timeframe."""
    # Call the function with a custom timeframe
    result = await recent_activity_prompt(timeframe="1d")

    # Check the response includes the custom timeframe
    assert "Recent Activity from (1d)" in result
