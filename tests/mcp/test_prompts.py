"""Tests for MCP prompts."""

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from basic_memory.mcp.prompts.guide import basic_memory_guide, _fallback_guide
from basic_memory.mcp.prompts.session import continue_session


@pytest.mark.asyncio
async def test_basic_memory_guide_fallback():
    """Test that basic_memory_guide falls back to a minimal guide when read_note fails."""
    # Patch read_note to raise an exception
    with patch("basic_memory.mcp.prompts.guide.read_note", side_effect=Exception("Test error")):
        # Call the function
        result = await basic_memory_guide()
        
        # Check that result contains the fallback guide content
        assert "Basic Memory Quick Reference" in result
        assert "Core Tools" in result
        assert "write_note" in result
        assert "read_note" in result


@pytest.mark.asyncio
async def test_basic_memory_guide_with_focus():
    """Test that basic_memory_guide tries to get a focused guide."""
    # Mock response for specific guide
    mock_focused_content = "# Write Files\n\nThis is the write files guide."
    
    # Mock read_note to return our mock content for the specific path
    async def mock_read_note(path):
        if path == "docs/write-files":
            return mock_focused_content
        raise Exception("Unexpected path")
    
    with patch("basic_memory.mcp.prompts.guide.read_note", side_effect=mock_read_note):
        # Call with writing focus
        result = await basic_memory_guide(focus="writing")
        
        # Check that result contains the specific guide content
        assert "Basic Memory Guide: Writing" in result
        assert mock_focused_content in result


@pytest.mark.asyncio
async def test_continue_session_with_topic():
    """Test continue_session with a topic."""
    # Mock search results
    mock_search_results = MagicMock()
    mock_search_results.primary_results = [
        MagicMock(permalink="test/document", title="Test Document", type="note")
    ]
    
    # Mock context
    mock_context = MagicMock()
    mock_context.primary_results = [
        MagicMock(
            permalink="test/document", 
            title="Test Document", 
            type="note",
            created_at=MagicMock(strftime=lambda fmt: "2025-02-01 12:00")
        )
    ]
    mock_context.related_results = []
    
    # Set up our mocks
    with patch("basic_memory.mcp.prompts.session.search", return_value=mock_search_results) as mock_search, \
         patch("basic_memory.mcp.prompts.session.build_context", return_value=mock_context) as mock_build:
        
        # Call the function
        result = await continue_session(topic="test", timeframe="1d")
        
        # Check that the appropriate functions were called
        mock_search.assert_called_once()
        mock_build.assert_called_once()
        
        # Check that the result contains expected content
        assert "Continuing Work on: test" in result
        assert "Test Document" in result
        assert "read_note(\"test/document\")" in result


@pytest.mark.asyncio
async def test_continue_session_no_topic():
    """Test continue_session without a topic."""
    # Mock recent activity results
    mock_recent = MagicMock()
    mock_recent.primary_results = [
        MagicMock(
            permalink="recent/document", 
            title="Recent Document", 
            type="note",
            created_at=MagicMock(strftime=lambda fmt: "2025-02-01 12:00")
        )
    ]
    mock_recent.related_results = []
    
    # Set up our mock
    with patch("basic_memory.mcp.prompts.session.recent_activity", return_value=mock_recent) as mock_recent_activity:
        
        # Call the function
        result = await continue_session(timeframe="1d")
        
        # Check that recent_activity was called
        mock_recent_activity.assert_called_once()
        
        # Check that the result contains expected content
        assert "Continuing Work on: Recent Activity" in result