"""Tests for prompt → tool delegation pattern.

All MCP prompts should delegate to their corresponding MCP tool and wrap
the output with prompt-specific guidance. These tests verify the delegation
using monkeypatching (no real services needed).
"""

import pytest

from basic_memory.mcp.prompts.search import search_prompt
from basic_memory.mcp.prompts.continue_conversation import continue_conversation


# --- search_prompt ---


@pytest.mark.asyncio
async def test_search_prompt_delegates_to_search_notes(monkeypatch):
    """Search prompt should call search_notes tool and wrap output."""
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

    async def fake_search_notes(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_result

    monkeypatch.setattr("basic_memory.mcp.prompts.search.search_notes", fake_search_notes)

    out = await search_prompt("my query", timeframe="1w")  # pyright: ignore[reportGeneralTypeIssues]

    # Verify delegation
    assert captured_kwargs["query"] == "my query"
    assert captured_kwargs["after_date"] == "1w"
    assert captured_kwargs["output_format"] == "json"

    # Verify output wrapping
    assert 'Search Results: "my query"' in out
    assert "Test Note" in out
    assert "read_note" in out


@pytest.mark.asyncio
async def test_search_prompt_handles_no_results(monkeypatch):
    """Search prompt should handle empty results gracefully."""
    fake_result = {"results": [], "current_page": 1, "page_size": 10}

    async def fake_search_notes(**kwargs):
        return fake_result

    monkeypatch.setattr("basic_memory.mcp.prompts.search.search_notes", fake_search_notes)

    out = await search_prompt("nonexistent")  # pyright: ignore[reportGeneralTypeIssues]

    assert "No results found" in out
    assert "0 results" in out


@pytest.mark.asyncio
async def test_search_prompt_handles_error_string(monkeypatch):
    """Search prompt should handle error string from search tool."""

    async def fake_search_notes(**kwargs):
        return "# Search Failed - Invalid Syntax\n\nThe query contains errors."

    monkeypatch.setattr("basic_memory.mcp.prompts.search.search_notes", fake_search_notes)

    out = await search_prompt("bad(query")  # pyright: ignore[reportGeneralTypeIssues]

    assert "Search Failed" in out
    assert "0 results" in out


# --- continue_conversation ---


@pytest.mark.asyncio
async def test_continue_conversation_delegates_to_search_notes(monkeypatch):
    """Continue conversation with topic should call search_notes."""
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

    async def fake_search_notes(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_result

    monkeypatch.setattr(
        "basic_memory.mcp.prompts.continue_conversation.search_notes", fake_search_notes
    )

    out = await continue_conversation(topic="my topic", timeframe="3d")  # pyright: ignore[reportGeneralTypeIssues]

    assert captured_kwargs["query"] == "my topic"
    assert captured_kwargs["after_date"] == "3d"
    assert captured_kwargs["output_format"] == "json"

    assert "'my topic'" in out
    assert "Previous Discussion" in out
    assert "read_note" in out


@pytest.mark.asyncio
async def test_continue_conversation_delegates_to_recent_activity(monkeypatch):
    """Continue conversation without topic should call recent_activity."""
    captured_kwargs = {}

    async def fake_recent_activity(**kwargs):
        captured_kwargs.update(kwargs)
        return "## Recent Activity: test-project (7d)\n\n**Items:** 3 found"

    monkeypatch.setattr(
        "basic_memory.mcp.prompts.continue_conversation.recent_activity", fake_recent_activity
    )

    out = await continue_conversation(timeframe="7d")  # pyright: ignore[reportGeneralTypeIssues]

    assert captured_kwargs["timeframe"] == "7d"
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
        "basic_memory.mcp.prompts.continue_conversation.recent_activity", fake_recent_activity
    )

    await continue_conversation()  # pyright: ignore[reportGeneralTypeIssues]

    assert captured_kwargs["timeframe"] == "7d"


@pytest.mark.asyncio
async def test_continue_conversation_no_results_for_topic(monkeypatch):
    """Continue conversation should show capture opportunity when no results found."""
    fake_result = {"results": [], "current_page": 1, "page_size": 10}

    async def fake_search_notes(**kwargs):
        return fake_result

    monkeypatch.setattr(
        "basic_memory.mcp.prompts.continue_conversation.search_notes", fake_search_notes
    )

    out = await continue_conversation(topic="unknown topic")  # pyright: ignore[reportGeneralTypeIssues]

    assert "No previous context found" in out
    assert "write_note" in out
