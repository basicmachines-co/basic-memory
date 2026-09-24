"""Tests for MCP prompts."""

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

    assert "when a snippet is not enough to respond" in result  # pyright: ignore [reportOperatorIssue]
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
async def test_search_prompt_no_results(client, indexed_project):
    """Test search_prompt when no results are found."""
    result = await search_prompt("XYZ123NonExistentQuery")  # pyright: ignore [reportGeneralTypeIssues]

    assert 'Search Results: "XYZ123NonExistentQuery"' in result  # pyright: ignore [reportOperatorIssue]
    assert "No results found" in result  # pyright: ignore [reportOperatorIssue]


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
