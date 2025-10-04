"""Tests for ChatGPT-compatible MCP tools."""

import json
import pytest
from unittest.mock import AsyncMock, patch

from basic_memory.schemas.search import SearchResponse, SearchResult, SearchItemType


@pytest.mark.asyncio
async def test_search_successful_results():
    """Test search with successful results returns proper MCP content array format."""
    # Mock successful search results
    mock_results = SearchResponse(
        results=[
            SearchResult(
                title="Test Document 1",
                permalink="docs/test-doc-1",
                content="This is test content for document 1",
                type=SearchItemType.ENTITY,
                score=1.0,
                file_path="/test/docs/test-doc-1.md",
            ),
            SearchResult(
                title="Test Document 2",
                permalink="docs/test-doc-2",
                content="This is test content for document 2",
                type=SearchItemType.ENTITY,
                score=0.9,
                file_path="/test/docs/test-doc-2.md",
            ),
        ],
        current_page=1,
        page_size=10,
    )

    with patch(
        "basic_memory.mcp.tools.chatgpt_tools.search_notes.fn", new_callable=AsyncMock
    ) as mock_search:
        mock_search.return_value = mock_results

        # Import and call the actual function
        from basic_memory.mcp.tools.chatgpt_tools import search

        result = await search.fn("test query")

        # Verify MCP content array format
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "text"

        # Parse the JSON content
        content = json.loads(result[0]["text"])
        assert "results" in content
        assert "query" in content

        # Verify result structure
        assert len(content["results"]) == 2
        assert content["query"] == "test query"

        # Verify individual result format
        result_item = content["results"][0]
        assert result_item["id"] == "docs/test-doc-1"
        assert result_item["title"] == "Test Document 1"
        assert result_item["url"] == "docs/test-doc-1"


@pytest.mark.asyncio
async def test_search_with_error_response():
    """Test search when underlying search_notes returns error string."""
    error_message = "# Search Failed - Invalid Syntax\n\nThe search query contains errors..."

    with patch(
        "basic_memory.mcp.tools.chatgpt_tools.search_notes.fn", new_callable=AsyncMock
    ) as mock_search:
        mock_search.return_value = error_message

        from basic_memory.mcp.tools.chatgpt_tools import search

        result = await search.fn("invalid query")

        # Verify MCP content array format
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "text"

        # Parse the JSON content
        content = json.loads(result[0]["text"])
        assert content["results"] == []
        assert content["error"] == "Search failed"
        assert "error_details" in content


@pytest.mark.asyncio
async def test_fetch_successful_document():
    """Test fetch with successful document retrieval."""
    document_content = """# Test Document

This is the content of a test document.

## Section 1
Some content here.

## Observations
- [observation] This is a test observation

## Relations
- relates_to [[Another Document]]
"""

    with patch(
        "basic_memory.mcp.tools.chatgpt_tools.read_note.fn", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = document_content

        from basic_memory.mcp.tools.chatgpt_tools import fetch

        result = await fetch.fn("docs/test-document")

        # Verify MCP content array format
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "text"

        # Parse the JSON content
        content = json.loads(result[0]["text"])
        assert content["id"] == "docs/test-document"
        assert content["title"] == "Test Document"  # Extracted from markdown
        assert content["text"] == document_content
        assert content["url"] == "docs/test-document"
        assert content["metadata"]["format"] == "markdown"


@pytest.mark.asyncio
async def test_fetch_document_not_found():
    """Test fetch when document is not found."""
    error_content = """# Note Not Found: "nonexistent-doc"

I couldn't find any notes matching "nonexistent-doc". Here are some suggestions:

## Check Identifier Type
- If you provided a title, try using the exact permalink instead
"""

    with patch(
        "basic_memory.mcp.tools.chatgpt_tools.read_note.fn", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = error_content

        from basic_memory.mcp.tools.chatgpt_tools import fetch

        result = await fetch.fn("nonexistent-doc")

        # Verify MCP content array format
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "text"

        # Parse the JSON content
        content = json.loads(result[0]["text"])
        assert content["id"] == "nonexistent-doc"
        assert content["text"] == error_content
        assert content["metadata"]["error"] == "Document not found"


def test_format_search_results_for_chatgpt():
    """Test search results formatting."""
    from basic_memory.mcp.tools.chatgpt_tools import _format_search_results_for_chatgpt

    mock_results = SearchResponse(
        results=[
            SearchResult(
                title="Document One",
                permalink="docs/doc-one",
                content="Content for document one",
                type=SearchItemType.ENTITY,
                score=1.0,
                file_path="/test/docs/doc-one.md",
            ),
            SearchResult(
                title="",  # Test empty title handling
                permalink="docs/untitled",
                content="Content without title",
                type=SearchItemType.ENTITY,
                score=0.8,
                file_path="/test/docs/untitled.md",
            ),
        ],
        current_page=1,
        page_size=10,
    )

    formatted = _format_search_results_for_chatgpt(mock_results)

    assert len(formatted) == 2
    assert formatted[0]["id"] == "docs/doc-one"
    assert formatted[0]["title"] == "Document One"
    assert formatted[0]["url"] == "docs/doc-one"

    # Test empty title handling
    assert formatted[1]["title"] == "Untitled"


def test_format_document_for_chatgpt():
    """Test document formatting."""
    from basic_memory.mcp.tools.chatgpt_tools import _format_document_for_chatgpt

    content = "# Test Document\n\nThis is test content."
    result = _format_document_for_chatgpt(content, "docs/test")

    assert result["id"] == "docs/test"
    assert result["title"] == "Test Document"
    assert result["text"] == content
    assert result["url"] == "docs/test"
    assert result["metadata"]["format"] == "markdown"


def test_format_document_error_handling():
    """Test document formatting with error content."""
    from basic_memory.mcp.tools.chatgpt_tools import _format_document_for_chatgpt

    error_content = '# Note Not Found: "missing-doc"\n\nDocument not found.'
    result = _format_document_for_chatgpt(error_content, "missing-doc", "Missing Doc")

    assert result["id"] == "missing-doc"
    assert result["title"] == "Missing Doc"
    assert result["text"] == error_content
    assert result["metadata"]["error"] == "Document not found"
