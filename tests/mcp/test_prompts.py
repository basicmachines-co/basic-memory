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
    result = await continue_conversation.fn(topic="Root", timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    # Check that the result contains expected content
    assert "Continuing conversation on: Root" in result  # pyright: ignore [reportOperatorIssue]
    assert "This is a memory retrieval session" in result  # pyright: ignore [reportOperatorIssue]
    assert "Start by executing one of the suggested commands" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_continue_conversation_with_recent_activity(client, test_graph):
    """Test continue_conversation with no topic, using recent activity."""
    # Call the function without a topic
    result = await continue_conversation.fn(timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    # Check that the result contains expected content for recent activity
    assert "Continuing conversation on: Recent Activity" in result  # pyright: ignore [reportOperatorIssue]
    assert "This is a memory retrieval session" in result  # pyright: ignore [reportOperatorIssue]
    assert "Please use the available basic-memory tools" in result  # pyright: ignore [reportOperatorIssue]
    assert "Next Steps" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_continue_conversation_no_results(client):
    """Test continue_conversation when no results are found."""
    # Call with a non-existent topic
    result = await continue_conversation.fn(topic="NonExistentTopic", timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    # Check the response indicates no results found
    assert "Continuing conversation on: NonExistentTopic" in result  # pyright: ignore [reportOperatorIssue]
    assert "The supplied query did not return any information" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_continue_conversation_creates_structured_suggestions(client, test_graph):
    """Test that continue_conversation generates structured tool usage suggestions."""
    # Call the function with a topic that should match existing content
    result = await continue_conversation.fn(topic="Root", timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    # Verify the response includes clear tool usage instructions
    assert "start by executing one of the suggested commands" in result.lower()  # pyright: ignore [reportAttributeAccessIssue]

    # Check that the response contains tool call examples
    assert "read_note" in result  # pyright: ignore [reportOperatorIssue]
    assert "search" in result  # pyright: ignore [reportOperatorIssue]
    assert "recent_activity" in result  # pyright: ignore [reportOperatorIssue]


# Search prompt tests


@pytest.mark.asyncio
async def test_search_prompt_with_results(client, test_graph):
    """Test search_prompt with a query that returns results."""
    # Call the function with a query that should match existing content
    result = await search_prompt.fn("Root")  # pyright: ignore [reportGeneralTypeIssues]

    # Check the response contains expected content
    assert 'Search Results for: "Root"' in result  # pyright: ignore [reportOperatorIssue]
    assert "I found " in result  # pyright: ignore [reportOperatorIssue]
    assert "You can view this content with: `read_note" in result  # pyright: ignore [reportOperatorIssue]
    assert "Synthesize and Capture Knowledge" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_search_prompt_with_timeframe(client, test_graph):
    """Test search_prompt with a timeframe."""
    # Call the function with a query and timeframe
    result = await search_prompt.fn("Root", timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    # Check the response includes timeframe information
    assert 'Search Results for: "Root" (after 7d)' in result  # pyright: ignore [reportOperatorIssue]
    assert "I found " in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_search_prompt_no_results(client):
    """Test search_prompt when no results are found."""
    # Call with a query that won't match anything
    result = await search_prompt.fn("XYZ123NonExistentQuery")  # pyright: ignore [reportGeneralTypeIssues]

    # Check the response indicates no results found
    assert 'Search Results for: "XYZ123NonExistentQuery"' in result  # pyright: ignore [reportOperatorIssue]
    assert "I couldn't find any results for this query" in result  # pyright: ignore [reportOperatorIssue]
    assert "Opportunity to Capture Knowledge" in result  # pyright: ignore [reportOperatorIssue]
    assert "write_note" in result  # pyright: ignore [reportOperatorIssue]


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
        entity_id=1,
        type="entity",
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
async def test_recent_activity_prompt_discovery_mode(client, test_project, test_graph):
    """Test recent_activity_prompt in discovery mode (no project)."""
    # Call the function in discovery mode
    result = await recent_activity_prompt.fn(timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    # Check the response contains expected discovery mode content
    assert "Recent Activity Across All Projects" in result  # pyright: ignore [reportOperatorIssue]
    assert "Cross-Project Activity Discovery" in result  # pyright: ignore [reportOperatorIssue]
    assert "write_note" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_recent_activity_prompt_project_specific(client, test_project, test_graph):
    """Test recent_activity_prompt in project-specific mode."""
    # Call the function with a specific project
    result = await recent_activity_prompt.fn(timeframe="1w", project=test_project.name)  # pyright: ignore [reportGeneralTypeIssues]

    # Check the response contains expected project-specific content
    assert f"Recent Activity in {test_project.name}" in result  # pyright: ignore [reportOperatorIssue]
    assert "Opportunity to Capture Activity Summary" in result  # pyright: ignore [reportOperatorIssue]
    assert f"recent activity in {test_project.name}" in result  # pyright: ignore [reportOperatorIssue]
    assert "write_note" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_recent_activity_prompt_with_custom_timeframe(client, test_project, test_graph):
    """Test recent_activity_prompt with custom timeframe."""
    # Call the function with a custom timeframe in discovery mode
    result = await recent_activity_prompt.fn(timeframe="1d")  # pyright: ignore [reportGeneralTypeIssues]

    # Check the response includes the custom timeframe
    assert "Recent Activity Across All Projects (1d)" in result  # pyright: ignore [reportOperatorIssue]
