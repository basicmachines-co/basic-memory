"""Tests for ChatGPT-compatible MCP tools."""

import json
import pytest

from basic_memory.mcp.tools import write_note
from basic_memory.schemas.search import SearchResponse, SearchResult, SearchItemType


@pytest.mark.asyncio
async def test_search_successful_results(client, test_project):
    """Test search with successful results returns proper MCP content array format."""
    await write_note.fn(
        project=test_project.name,
        title="Test Document 1",
        directory="docs",
        content="# Test Document 1\n\nThis is test content for document 1",
    )
    await write_note.fn(
        project=test_project.name,
        title="Test Document 2",
        directory="docs",
        content="# Test Document 2\n\nThis is test content for document 2",
    )

    from basic_memory.mcp.tools.chatgpt_tools import search

    result = await search.fn("test content")

    # Verify MCP content array format
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "text"

    # Parse the JSON content
    content = json.loads(result[0]["text"])
    assert "results" in content
    assert content["query"] == "test content"

    # Verify individual result format
    assert any(r["id"] == "docs/test-document-1" for r in content["results"])
    assert any(r["id"] == "docs/test-document-2" for r in content["results"])


@pytest.mark.asyncio
async def test_search_with_error_response(monkeypatch, client, test_project):
    """Test search when underlying search_notes returns an error string."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    error_message = "# Search Failed - Invalid Syntax\n\nThe search query contains errors..."

    async def fake_search_notes_fn(*args, **kwargs):
        return error_message

    monkeypatch.setattr(chatgpt_tools.search_notes, "fn", fake_search_notes_fn)

    result = await chatgpt_tools.search.fn("invalid query")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "text"

    content = json.loads(result[0]["text"])
    assert content["results"] == []
    assert content["error"] == "Search failed"
    assert "error_details" in content


@pytest.mark.asyncio
async def test_fetch_successful_document(client, test_project):
    """Test fetch with successful document retrieval."""
    await write_note.fn(
        project=test_project.name,
        title="Test Document",
        directory="docs",
        content="# Test Document\n\nThis is the content of a test document.",
    )

    from basic_memory.mcp.tools.chatgpt_tools import fetch

    result = await fetch.fn("docs/test-document")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "text"

    content = json.loads(result[0]["text"])
    assert content["id"] == "docs/test-document"
    assert content["title"] == "Test Document"
    assert "This is the content of a test document." in content["text"]
    assert content["url"] == "docs/test-document"
    assert content["metadata"]["format"] == "markdown"


@pytest.mark.asyncio
async def test_fetch_document_not_found(client, test_project):
    """Test fetch when document is not found."""
    from basic_memory.mcp.tools.chatgpt_tools import fetch

    result = await fetch.fn("nonexistent-doc")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "text"

    content = json.loads(result[0]["text"])
    assert content["id"] == "nonexistent-doc"
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


def test_format_document_untitled_fallback_for_empty_identifier():
    """If identifier is empty and content has no H1, we still return a stable title."""
    from basic_memory.mcp.tools.chatgpt_tools import _format_document_for_chatgpt

    result = _format_document_for_chatgpt("no title here", "")
    assert result["title"] == "Untitled Document"


@pytest.mark.asyncio
async def test_search_internal_exception_returns_error_payload(monkeypatch, client, test_project):
    """search() should return a structured error payload if an unexpected exception occurs."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(chatgpt_tools.search_notes, "fn", boom)

    result = await chatgpt_tools.search.fn("anything")
    assert isinstance(result, list)
    content = json.loads(result[0]["text"])
    assert content["error"] == "Internal search error"
    assert "error_message" in content


@pytest.mark.asyncio
async def test_fetch_internal_exception_returns_error_payload(monkeypatch, client, test_project):
    """fetch() should return a structured error payload if an unexpected exception occurs."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(chatgpt_tools.read_note, "fn", boom)

    result = await chatgpt_tools.fetch.fn("docs/test")
    assert isinstance(result, list)
    content = json.loads(result[0]["text"])
    assert content["id"] == "docs/test"
    assert content["metadata"]["error"] == "Fetch failed"
