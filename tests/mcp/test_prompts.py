"""Tests for MCP prompts."""

from datetime import timezone, datetime

import pytest

from basic_memory.mcp.prompts.continue_conversation import continue_conversation
from basic_memory.mcp.prompts.search import search_prompt
from basic_memory.mcp.prompts.recent_activity import recent_activity_prompt


@pytest.mark.asyncio
async def test_continue_conversation_with_topic(client, test_graph):
    """Test continue_conversation with a topic."""
    result = await continue_conversation(topic="Root", timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    assert "Continuing conversation on:" in result  # pyright: ignore [reportOperatorIssue]
    assert "'Root'" in result  # pyright: ignore [reportOperatorIssue]
    assert "This is a memory retrieval session" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_continue_conversation_with_recent_activity(client, test_graph):
    """Test continue_conversation with no topic, using recent activity."""
    result = await continue_conversation(timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    assert "Continuing conversation on: recent activity" in result  # pyright: ignore [reportOperatorIssue]
    assert "This is a memory retrieval session" in result  # pyright: ignore [reportOperatorIssue]
    assert "Next Steps" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_continue_conversation_no_results(client):
    """Test continue_conversation when no results are found."""
    result = await continue_conversation(topic="NonExistentTopic", timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    assert "NonExistentTopic" in result  # pyright: ignore [reportOperatorIssue]
    assert "No previous context found" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_continue_conversation_creates_structured_suggestions(client, test_graph):
    """Test that continue_conversation generates structured tool usage suggestions."""
    result = await continue_conversation(topic="Root", timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    assert "start by executing one of the suggested commands" in result.lower()  # pyright: ignore [reportAttributeAccessIssue]
    assert "read_note" in result  # pyright: ignore [reportOperatorIssue]
    assert "search" in result  # pyright: ignore [reportOperatorIssue]


# Search prompt tests


@pytest.mark.asyncio
async def test_search_prompt_with_results(client, test_graph):
    """Test search_prompt with a query that returns results."""
    result = await search_prompt("Root")  # pyright: ignore [reportGeneralTypeIssues]

    assert 'Search Results: "Root"' in result  # pyright: ignore [reportOperatorIssue]
    assert "Found" in result  # pyright: ignore [reportOperatorIssue]
    assert "read_note" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_search_prompt_with_timeframe(client, test_graph):
    """Test search_prompt with a timeframe."""
    result = await search_prompt("Root", timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    assert 'Search Results: "Root"' in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_search_prompt_no_results(client):
    """Test search_prompt when no results are found."""
    result = await search_prompt("XYZ123NonExistentQuery")  # pyright: ignore [reportGeneralTypeIssues]

    assert 'Search Results: "XYZ123NonExistentQuery"' in result  # pyright: ignore [reportOperatorIssue]
    assert "No results found" in result  # pyright: ignore [reportOperatorIssue]


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
        external_id="550e8400-e29b-41d4-a716-446655440000",
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


def test_prompt_context_with_related_results_but_no_primary_results():
    """Related context remains renderable when an item has no primary result."""
    from basic_memory.mcp.prompts.utils import (
        format_prompt_context,
        PromptContext,
        PromptContextItem,
    )
    from basic_memory.schemas.memory import EntitySummary

    related_entity = EntitySummary(
        external_id="550e8400-e29b-41d4-a716-446655440000",
        entity_id=1,
        type="entity",
        title="Related File",
        permalink=None,
        file_path="related_file.pdf",
        created_at=datetime.now(timezone.utc),
    )
    context = PromptContext(
        topic="Test Topic",
        timeframe="1d",
        results=[
            PromptContextItem(
                primary_results=[],
                related_results=[related_entity],
            )
        ],
    )

    result = format_prompt_context(context)

    assert "## Related Context" in result
    assert "Related File" in result
    assert 'read_file("related_file.pdf")' in result


# Recent activity prompt tests


@pytest.mark.asyncio
async def test_recent_activity_prompt_discovery_mode(client, test_project, test_graph):
    """Test recent_activity_prompt in discovery mode (no project)."""
    result = await recent_activity_prompt(timeframe="1w")  # pyright: ignore [reportGeneralTypeIssues]

    assert "Recent Activity Context" in result  # pyright: ignore [reportOperatorIssue]
    assert "all projects" in result  # pyright: ignore [reportOperatorIssue]
    assert "Next Steps" in result  # pyright: ignore [reportOperatorIssue]
    assert "write_note" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_recent_activity_prompt_project_specific(client, test_project, test_graph):
    """Test recent_activity_prompt in project-specific mode."""
    result = await recent_activity_prompt(timeframe="1w", project=test_project.name)  # pyright: ignore [reportGeneralTypeIssues]

    assert "Recent Activity Context" in result  # pyright: ignore [reportOperatorIssue]
    assert test_project.name in result  # pyright: ignore [reportOperatorIssue]
    assert "Next Steps" in result  # pyright: ignore [reportOperatorIssue]
    assert "write_note" in result  # pyright: ignore [reportOperatorIssue]


@pytest.mark.asyncio
async def test_recent_activity_prompt_with_custom_timeframe(client, test_project, test_graph):
    """Test recent_activity_prompt with custom timeframe."""
    result = await recent_activity_prompt(timeframe="1d")  # pyright: ignore [reportGeneralTypeIssues]

    assert "1d" in result  # pyright: ignore [reportOperatorIssue]
    assert "Recent Activity Context" in result  # pyright: ignore [reportOperatorIssue]
