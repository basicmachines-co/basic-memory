"""Tests for ChatGPT-compatible MCP tools."""

import json
from typing import Any, cast

import pytest

from basic_memory.mcp.client_info import MCP_CLIENT_INFO_STATE_KEY
from basic_memory.mcp.tools import write_note
from basic_memory.schemas.search import SearchResponse, SearchResult, SearchItemType


async def _openai_mcp_context(context_state) -> Any:
    await context_state.set_state(
        MCP_CLIENT_INFO_STATE_KEY,
        {"name": "openai-mcp", "title": None, "version": "1.0.0"},
    )
    return cast(Any, context_state)


@pytest.mark.asyncio
async def test_search_successful_results(client, test_project, context_state):
    """Test search with successful results returns proper MCP content array format."""
    await write_note(
        project=test_project.name,
        title="Test Document 1",
        directory="docs",
        content="# Test Document 1\n\nThis is test content for document 1",
    )
    await write_note(
        project=test_project.name,
        title="Test Document 2",
        directory="docs",
        content="# Test Document 2\n\nThis is test content for document 2",
    )

    from basic_memory.mcp.tools.chatgpt_tools import search

    context = await _openai_mcp_context(context_state)
    result = await search("test content", context=context)

    # Verify MCP content array format
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "text"

    # Parse the JSON content
    content = json.loads(result[0]["text"])
    assert "results" in content
    assert content["query"] == "test content"

    # Verify individual result format
    assert any(r["id"] == f"{test_project.name}/docs/test-document-1" for r in content["results"])
    assert any(r["id"] == f"{test_project.name}/docs/test-document-2" for r in content["results"])


@pytest.mark.asyncio
async def test_search_with_error_response(monkeypatch, client, test_project, context_state):
    """Test search when underlying search_notes returns an error string."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    error_message = "# Search Failed - Invalid Syntax\n\nThe search query contains errors..."

    async def fake_search_notes_fn(*args, **kwargs):
        return error_message

    monkeypatch.setattr(chatgpt_tools, "search_notes", fake_search_notes_fn)

    context = await _openai_mcp_context(context_state)
    result = await chatgpt_tools.search("invalid query", context=context)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "text"

    content = json.loads(result[0]["text"])
    assert content["results"] == []
    assert content["error"] == "Search failed"
    assert "error_details" in content


@pytest.mark.asyncio
async def test_search_uses_dynamic_default_search_type(
    monkeypatch, client, test_project, context_state
):
    """ChatGPT adapter should not hardcode search_type so search_notes can pick defaults."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    captured_kwargs: dict = {}

    async def fake_search_notes_fn(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return {"results": []}

    monkeypatch.setattr(chatgpt_tools, "search_notes", fake_search_notes_fn)

    context = await _openai_mcp_context(context_state)
    result = await chatgpt_tools.search("default search mode query", context=context)

    assert isinstance(result, list)
    assert "search_type" not in captured_kwargs


@pytest.mark.asyncio
async def test_search_delegates_to_search_notes_without_project_iteration(
    monkeypatch, client, test_project, context_state
):
    """ChatGPT search is only a compatibility wrapper around search_notes."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    captured_kwargs: dict = {}

    async def fake_search_notes_fn(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return {"results": []}

    monkeypatch.setattr(chatgpt_tools, "search_notes", fake_search_notes_fn)

    context = await _openai_mcp_context(context_state)
    result = await chatgpt_tools.search("MCP Test Note", context=context)

    content = json.loads(result[0]["text"])
    assert content["results"] == []
    assert content["query"] == "MCP Test Note"
    assert captured_kwargs == {
        "query": "MCP Test Note",
        "page": 1,
        "page_size": 10,
        "output_format": "json",
        "context": context,
    }


@pytest.mark.asyncio
async def test_fetch_successful_document(client, test_project, context_state):
    """Test fetch with successful document retrieval."""
    await write_note(
        project=test_project.name,
        title="Test Document",
        directory="docs",
        content="# Test Document\n\nThis is the content of a test document.",
    )

    from basic_memory.mcp.tools.chatgpt_tools import fetch

    context = await _openai_mcp_context(context_state)
    result = await fetch("docs/test-document", context=context)

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
async def test_fetch_document_not_found(client, test_project, context_state):
    """Test fetch when document is not found."""
    from basic_memory.mcp.tools.chatgpt_tools import fetch

    context = await _openai_mcp_context(context_state)
    result = await fetch("nonexistent-doc", context=context)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["type"] == "text"

    content = json.loads(result[0]["text"])
    assert content["id"] == "nonexistent-doc"
    assert content["metadata"]["error"] == "Document not found"


@pytest.mark.asyncio
async def test_fetch_routes_path_ids_as_memory_urls(
    monkeypatch, client, test_project, context_state
):
    """Workspace-qualified search ids need memory URL routing during fetch."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    captured: dict[str, str] = {}

    async def fake_read_note(*, identifier: str, context=None):
        captured["identifier"] = identifier
        return "# MCP Test Note\n\nFetched from the requested workspace."

    monkeypatch.setattr(chatgpt_tools, "read_note", fake_read_note)

    context = await _openai_mcp_context(context_state)
    result = await chatgpt_tools.fetch("team-paul/main/tests/mcp-test-note", context=context)

    content = json.loads(result[0]["text"])
    assert captured["identifier"] == "memory://team-paul/main/tests/mcp-test-note"
    assert content["id"] == "team-paul/main/tests/mcp-test-note"
    assert content["title"] == "MCP Test Note"


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


def test_format_search_results_for_chatgpt_handles_dict_payloads():
    """Dict payloads must still normalize to the ChatGPT result array shape."""
    from basic_memory.mcp.tools.chatgpt_tools import _format_search_results_for_chatgpt

    formatted = _format_search_results_for_chatgpt(
        {"results": [{"title": "Document One", "permalink": "docs/doc-one"}]}
    )

    assert formatted == [
        {
            "id": "docs/doc-one",
            "title": "Document One",
            "url": "docs/doc-one",
        }
    ]
    assert _format_search_results_for_chatgpt({"results": "not a list"}) == []


def test_format_search_results_for_chatgpt_rejects_unknown_rows():
    """Unexpected row shapes should fail loudly instead of producing bad IDs."""
    from basic_memory.mcp.tools.chatgpt_tools import _format_search_results_for_chatgpt

    with pytest.raises(TypeError, match="Unexpected result type: object"):
        _format_search_results_for_chatgpt(cast(Any, [object()]))


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
async def test_search_internal_exception_returns_error_payload(
    monkeypatch, client, test_project, context_state
):
    """search() should return a structured error payload if an unexpected exception occurs."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(chatgpt_tools, "search_notes", boom)

    context = await _openai_mcp_context(context_state)
    result = await chatgpt_tools.search("anything", context=context)
    assert isinstance(result, list)
    content = json.loads(result[0]["text"])
    assert content["error"] == "Internal search error"
    assert "error_message" in content


@pytest.mark.asyncio
async def test_fetch_internal_exception_returns_error_payload(
    monkeypatch, client, test_project, context_state
):
    """fetch() should return a structured error payload if an unexpected exception occurs."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(chatgpt_tools, "read_note", boom)

    context = await _openai_mcp_context(context_state)
    result = await chatgpt_tools.fetch("docs/test", context=context)
    assert isinstance(result, list)
    content = json.loads(result[0]["text"])
    assert content["id"] == "docs/test"
    assert content["metadata"]["error"] == "Fetch failed"


@pytest.mark.asyncio
async def test_search_rejects_non_openai_mcp_client(monkeypatch, context_state):
    """Only OpenAI's MCP client can use the ChatGPT compatibility search tool."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    called = False

    async def fake_search_notes_fn(*args, **kwargs):
        nonlocal called
        called = True
        return {"results": []}

    await context_state.set_state(
        MCP_CLIENT_INFO_STATE_KEY,
        {"name": "claude-code", "title": "Claude Code", "version": "1.2.3"},
    )
    monkeypatch.setattr(chatgpt_tools, "search_notes", fake_search_notes_fn)

    result = await chatgpt_tools.search("anything", context=cast(Any, context_state))

    content = json.loads(result[0]["text"])
    assert called is False
    assert content["results"] == []
    assert content["error"] == "Unsupported MCP client"
    assert "search_notes" in content["error_message"]


@pytest.mark.asyncio
async def test_fetch_rejects_missing_client_info(monkeypatch, context_state):
    """A missing clientInfo state is not enough to use the ChatGPT fetch shim."""
    import basic_memory.mcp.tools.chatgpt_tools as chatgpt_tools

    called = False

    async def fake_read_note(*args, **kwargs):
        nonlocal called
        called = True
        return "# Should Not Be Read"

    monkeypatch.setattr(chatgpt_tools, "read_note", fake_read_note)

    result = await chatgpt_tools.fetch("docs/test", context=cast(Any, context_state))

    content = json.loads(result[0]["text"])
    assert called is False
    assert content["id"] == "docs/test"
    assert content["metadata"]["error"] == "Unsupported MCP client"
    assert "read_note" in content["text"]
