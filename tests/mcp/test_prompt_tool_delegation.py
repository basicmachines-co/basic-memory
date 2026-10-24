"""Tests for prompt → tool delegation pattern.

All MCP prompts should delegate to their corresponding MCP tool and wrap
the output with prompt-specific guidance. These tests verify the delegation
using monkeypatching (no real services needed).
"""

import pytest
from fastmcp.exceptions import ToolError

from basic_memory.mcp.prompts.search import search_prompt
from basic_memory.mcp.prompts.continue_conversation import continue_conversation


# --- search_prompt ---


@pytest.mark.asyncio
async def test_search_prompt_delegates_to_bm_search(monkeypatch):
    """Search prompt should call bm_search tool and wrap output."""
    captured_kwargs = {}

    # Prompts use output_format="json", so mock returns a dict
    fake_result = {
        "results": [
            {
                "type": "entity",
                "title": "Test Note",
                "permalink": "test-note",
                "file_path": "test-note.md",
                "score": 0.95,
            }
        ],
        "current_page": 1,
        "page_size": 10,
    }

    async def fake_bm_search(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_result

    monkeypatch.setattr("basic_memory.mcp.prompts.search.bm_search", fake_bm_search)

    out = await search_prompt("my query", timeframe="1w")  # pyright: ignore[reportGeneralTypeIssues]

    # Verify delegation
    assert captured_kwargs["query"] == "my query"
    assert captured_kwargs["since"] == "1w"
    assert captured_kwargs["output_format"] == "json"

    # Verify output wrapping
    assert 'Search Results: "my query"' in out
    assert "Test Note" in out
    assert "bm_read" in out


@pytest.mark.asyncio
async def test_search_prompt_handles_no_results(monkeypatch):
    """Search prompt should handle empty results gracefully."""
    fake_result = {"results": [], "current_page": 1, "page_size": 10}

    async def fake_bm_search(**kwargs):
        return fake_result

    monkeypatch.setattr("basic_memory.mcp.prompts.search.bm_search", fake_bm_search)

    out = await search_prompt("nonexistent")  # pyright: ignore[reportGeneralTypeIssues]

    assert "No results found" in out
    assert "0 results" in out


@pytest.mark.asyncio
async def test_search_prompt_propagates_tool_error(monkeypatch):
    """A failed search must not be presented as an empty successful search."""

    async def fake_bm_search(**kwargs):
        raise ToolError("INVALID_ARGUMENTS: invalid query")

    monkeypatch.setattr("basic_memory.mcp.prompts.search.bm_search", fake_bm_search)

    with pytest.raises(ToolError, match="INVALID_ARGUMENTS"):
        await search_prompt("bad(query")


# --- continue_conversation ---


@pytest.mark.asyncio
async def test_continue_conversation_delegates_to_bm_search(monkeypatch):
    """Continue conversation with topic should call bm_search."""
    captured_kwargs = {}

    # Prompts use output_format="json", so mock returns a dict
    fake_result = {
        "results": [
            {
                "type": "entity",
                "title": "Previous Discussion",
                "permalink": "discussions/previous",
                "file_path": "discussions/previous.md",
                "score": 0.9,
            }
        ],
        "current_page": 1,
        "page_size": 10,
    }

    async def fake_bm_search(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_result

    monkeypatch.setattr("basic_memory.mcp.prompts.continue_conversation.bm_search", fake_bm_search)

    out = await continue_conversation(topic="my topic", timeframe="3d")  # pyright: ignore[reportGeneralTypeIssues]

    assert captured_kwargs["query"] == "my topic"
    assert captured_kwargs["since"] == "3d"
    assert captured_kwargs["output_format"] == "json"

    assert "'my topic'" in out
    assert "Previous Discussion" in out
    assert "bm_read" in out


@pytest.mark.asyncio
async def test_continue_conversation_delegates_to_recent_activity(monkeypatch):
    """Continue conversation without topic should call recent_activity."""
    captured_kwargs = {}

    async def fake_recent_activity(**kwargs):
        captured_kwargs.update(kwargs)
        return "## Recent Activity: test-project (7d)\n\n**Items:** 3 found"

    monkeypatch.setattr(
        "basic_memory.mcp.prompts.continue_conversation.bm_project", fake_recent_activity
    )

    out = await continue_conversation(timeframe="7d")  # pyright: ignore[reportGeneralTypeIssues]

    assert captured_kwargs["since"] == "7d"
    assert "recent activity" in out
    assert "Recent Activity: test-project" in out


@pytest.mark.asyncio
async def test_continue_conversation_no_topic_default_timeframe(monkeypatch):
    """Continue conversation without topic or timeframe defaults to 7d."""
    captured_kwargs = {}

    async def fake_recent_activity(**kwargs):
        captured_kwargs.update(kwargs)
        return "## Recent Activity Summary"

    monkeypatch.setattr(
        "basic_memory.mcp.prompts.continue_conversation.bm_project", fake_recent_activity
    )

    await continue_conversation()  # pyright: ignore[reportGeneralTypeIssues]

    assert captured_kwargs["since"] == "7d"


@pytest.mark.asyncio
async def test_continue_conversation_no_results_for_topic(monkeypatch):
    """Continue conversation should show capture opportunity when no results found."""
    fake_result = {"results": [], "current_page": 1, "page_size": 10}

    async def fake_bm_search(**kwargs):
        return fake_result

    monkeypatch.setattr("basic_memory.mcp.prompts.continue_conversation.bm_search", fake_bm_search)

    out = await continue_conversation(topic="unknown topic")  # pyright: ignore[reportGeneralTypeIssues]

    assert "No previous context found" in out
    assert "bm_write" in out
