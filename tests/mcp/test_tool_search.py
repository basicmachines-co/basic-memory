"""Tests for search MCP tools."""

import pytest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from basic_memory.mcp.tools import write_note
from basic_memory.mcp.tools.search import (
    search_notes,
    _format_search_error_response,
    _format_search_markdown,
)
from basic_memory.schemas.search import SearchResponse


@pytest.mark.asyncio
async def test_search_text(client, test_project):
    """Test basic search functionality."""
    # Create a test note
    result = await write_note(
        project=test_project.name,
        title="Test Search Note",
        directory="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )
    assert result

    # Search for it (use json format to inspect structured results)
    response = await search_notes(
        project=test_project.name, query="searchable", output_format="json"
    )

    # Verify results - handle both success and error cases
    if isinstance(response, dict):
        # Success case - verify dict response
        assert len(response["results"]) > 0
        assert any(
            r["permalink"] == f"{test_project.name}/test/test-search-note"
            for r in response["results"]
        )
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_title(client, test_project):
    """Test basic search functionality."""
    # Create a test note
    result = await write_note(
        project=test_project.name,
        title="Test Search Note",
        directory="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )
    assert result

    # Search for it (use json format to inspect structured results)
    response = await search_notes(
        project=test_project.name, query="Search Note", search_type="title", output_format="json"
    )

    # Verify results - handle both success and error cases
    if isinstance(response, dict):
        # Success case - verify dict response
        assert len(response["results"]) > 0
        assert any(
            r["permalink"] == f"{test_project.name}/test/test-search-note"
            for r in response["results"]
        )
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_permalink(client, test_project):
    """Test basic search functionality."""
    # Create a test note
    result = await write_note(
        project=test_project.name,
        title="Test Search Note",
        directory="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )
    assert result

    # Search for it (use json format to inspect structured results)
    response = await search_notes(
        project=test_project.name,
        query=f"{test_project.name}/test/test-search-note",
        search_type="permalink",
        output_format="json",
    )

    # Verify results - handle both success and error cases
    if isinstance(response, dict):
        # Success case - verify dict response
        assert len(response["results"]) > 0
        assert any(
            r["permalink"] == f"{test_project.name}/test/test-search-note"
            for r in response["results"]
        )
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_permalink_match(client, test_project):
    """Test basic search functionality."""
    # Create a test note
    result = await write_note(
        project=test_project.name,
        title="Test Search Note",
        directory="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )
    assert result

    # Search for it (use json format to inspect structured results)
    response = await search_notes(
        project=test_project.name,
        query=f"{test_project.name}/test/test-search-*",
        search_type="permalink",
        output_format="json",
    )

    # Verify results - handle both success and error cases
    if isinstance(response, dict):
        # Success case - verify dict response
        assert len(response["results"]) > 0
        assert any(
            r["permalink"] == f"{test_project.name}/test/test-search-note"
            for r in response["results"]
        )
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_memory_url_with_project_prefix(client, test_project):
    """Test searching with a memory:// URL that includes the project prefix."""
    result = await write_note(
        project=test_project.name,
        title="Memory URL Search Note",
        directory="test",
        content="# Memory URL Search\nThis note should be found via memory URL search",
    )
    assert result

    response = await search_notes(
        query=f"memory://{test_project.name}/test/memory-url-search-note", output_format="json"
    )

    if isinstance(response, dict):
        assert len(response["results"]) > 0
        assert any(
            r["permalink"] == f"{test_project.name}/test/memory-url-search-note"
            for r in response["results"]
        )
    else:
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_pagination(client, test_project):
    """Test basic search functionality."""
    # Create a test note
    result = await write_note(
        project=test_project.name,
        title="Test Search Note",
        directory="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )
    assert result

    # Search for it (use json format to inspect structured results)
    response = await search_notes(
        project=test_project.name, query="searchable", page=1, page_size=1, output_format="json"
    )

    # Verify results - handle both success and error cases
    if isinstance(response, dict):
        # Success case - verify dict response
        assert len(response["results"]) == 1
        assert any(
            r["permalink"] == f"{test_project.name}/test/test-search-note"
            for r in response["results"]
        )
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_with_type_filter(client, test_project):
    """Test search with note type filter."""
    # Create test content
    await write_note(
        project=test_project.name,
        title="Note Type Test",
        directory="test",
        content="# Test\nFiltered by type",
    )

    # Search with note type filter (use json format to inspect structured results)
    response = await search_notes(
        project=test_project.name, query="type", note_types=["note"], output_format="json"
    )

    # Verify results - handle both success and error cases
    if isinstance(response, dict):
        # Success case - verify all results are entities
        assert all(r["type"] == "entity" for r in response["results"])
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_with_entity_type_filter(client, test_project):
    """Test search with entity_types (SearchItemType) filter."""
    # Create test content
    await write_note(
        project=test_project.name,
        title="Entity Type Test",
        directory="test",
        content="# Test\nFiltered by type",
    )

    # Search with entity_types (SearchItemType) filter (use json format)
    response = await search_notes(
        project=test_project.name, query="type", entity_types=["entity"], output_format="json"
    )

    # Verify results - handle both success and error cases
    if isinstance(response, dict):
        # Success case - verify all results are entities
        assert all(r["type"] == "entity" for r in response["results"])
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_with_date_filter(client, test_project):
    """Test search with date filter."""
    # Create test content
    await write_note(
        project=test_project.name,
        title="Recent Note",
        directory="test",
        content="# Test\nRecent content",
    )

    # Search with date filter (use json format to inspect structured results)
    one_hour_ago = datetime.now() - timedelta(hours=1)
    response = await search_notes(
        project=test_project.name,
        query="recent",
        after_date=one_hour_ago.isoformat(),
        output_format="json",
    )

    # Verify results - handle both success and error cases
    if isinstance(response, dict):
        # Success case - verify we get results within timeframe
        assert len(response["results"]) > 0
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


class TestSearchErrorFormatting:
    """Test search error formatting for better user experience."""

    def test_format_search_error_fts5_syntax(self):
        """Test formatting for FTS5 syntax errors."""
        result = _format_search_error_response(
            "test-project", "syntax error in FTS5", "test query("
        )

        assert "# Search Failed - Invalid Syntax" in result
        assert "The search query 'test query(' contains invalid syntax" in result
        assert "Special characters" in result
        assert "test query" in result  # Clean query without special chars

    def test_format_search_error_no_results(self):
        """Test formatting for no results found."""
        result = _format_search_error_response(
            "test-project", "no results found", "very specific query"
        )

        assert "# Search Complete - No Results Found" in result
        assert "No content found matching 'very specific query'" in result
        assert "Broaden your search" in result
        assert "very" in result  # Simplified query

    def test_format_search_error_server_error(self):
        """Test formatting for server errors."""
        result = _format_search_error_response(
            "test-project", "internal server error", "test query"
        )

        assert "# Search Failed - Server Error" in result
        assert "The search service encountered an error while processing 'test query'" in result
        assert "Try again" in result
        assert "Check project status" in result

    def test_format_search_error_permission_denied(self):
        """Test formatting for permission errors."""
        result = _format_search_error_response("test-project", "permission denied", "test query")

        assert "# Search Failed - Access Error" in result
        assert "You don't have permission to search" in result
        assert "Check your project access" in result

    def test_format_search_error_project_not_found(self):
        """Test formatting for project not found errors."""
        result = _format_search_error_response(
            "test-project", "current project not found", "test query"
        )

        assert "# Search Failed - Project Not Found" in result
        assert "The current project is not accessible" in result
        assert "Check available projects" in result

    def test_format_search_error_semantic_disabled(self):
        """Test formatting for semantic-search-disabled errors."""
        result = _format_search_error_response(
            "test-project",
            "Semantic search is disabled. Set BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true.",
            "semantic query",
            "vector",
        )

        assert "# Search Failed - Semantic Search Disabled" in result
        assert "BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true" in result
        assert 'search_type="text"' in result

    def test_format_search_error_semantic_dependencies_missing(self):
        """Test formatting for missing semantic dependencies."""
        result = _format_search_error_response(
            "test-project",
            "fastembed package is missing. Install/update basic-memory to include semantic dependencies: pip install -U basic-memory",
            "semantic query",
            "hybrid",
        )

        assert "# Search Failed - Semantic Dependencies Missing" in result
        assert "pip install -U basic-memory" in result

    def test_format_search_error_generic(self):
        """Test formatting for generic errors."""
        result = _format_search_error_response("test-project", "unknown error", "test query")

        assert "# Search Failed" in result
        assert "Error searching for 'test query': unknown error" in result
        assert "## Troubleshooting steps:" in result


class TestSearchToolErrorHandling:
    """Test search tool exception handling."""

    @pytest.mark.asyncio
    async def test_search_notes_exception_handling(self, monkeypatch):
        """Test exception handling in search_notes."""
        import importlib

        search_mod = importlib.import_module("basic_memory.mcp.tools.search")
        clients_mod = importlib.import_module("basic_memory.mcp.clients")

        class StubProject:
            name = "test-project"
            external_id = "test-external-id"

        @asynccontextmanager
        async def fake_get_project_client(*args, **kwargs):
            yield (object(), StubProject())

        async def fake_resolve_project_and_path(
            client, identifier, project=None, context=None, headers=None
        ):
            return StubProject(), identifier, False

        # Mock SearchClient to raise an exception
        class MockSearchClient:
            def __init__(self, *args, **kwargs):
                pass

            async def search(self, *args, **kwargs):
                raise Exception("syntax error")

        monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
        monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
        # Patch at the clients module level where the import happens
        monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

        result = await search_mod.search_notes(project="test-project", query="test query")
        assert isinstance(result, str)
        assert "# Search Failed - Invalid Syntax" in result

    @pytest.mark.asyncio
    async def test_search_notes_permission_error(self, monkeypatch):
        """Test search_notes with permission error."""
        import importlib

        search_mod = importlib.import_module("basic_memory.mcp.tools.search")
        clients_mod = importlib.import_module("basic_memory.mcp.clients")

        class StubProject:
            name = "test-project"
            external_id = "test-external-id"

        @asynccontextmanager
        async def fake_get_project_client(*args, **kwargs):
            yield (object(), StubProject())

        async def fake_resolve_project_and_path(
            client, identifier, project=None, context=None, headers=None
        ):
            return StubProject(), identifier, False

        # Mock SearchClient to raise a permission error
        class MockSearchClient:
            def __init__(self, *args, **kwargs):
                pass

            async def search(self, *args, **kwargs):
                raise Exception("permission denied")

        monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
        monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
        # Patch at the clients module level where the import happens
        monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

        result = await search_mod.search_notes(project="test-project", query="test query")
        assert isinstance(result, str)
        assert "# Search Failed - Access Error" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("search_type", ["vector", "semantic", "hybrid"])
async def test_search_notes_sets_retrieval_mode_for_semantic_types(monkeypatch, search_type):
    """Vector/hybrid search types should populate retrieval_mode in API payload."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        project_url = "http://test"
        name = "test-project"
        id = 1
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        project="test-project",
        query="semantic lookup",
        search_type=search_type,
    )

    # Default text format returns a formatted string for empty results
    assert isinstance(result, str)
    assert captured_payload["text"] == "semantic lookup"
    # "semantic" is an alias for "vector" retrieval mode
    expected_mode = "vector" if search_type in ("vector", "semantic") else search_type
    assert captured_payload["retrieval_mode"] == expected_mode


# --- Tests for metadata_filters / tags / status params (lines 440-444) ------


@pytest.mark.asyncio
async def test_search_notes_passes_metadata_filters(monkeypatch):
    """metadata_filters param propagates to the search query."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    await search_mod.search_notes(
        project="test-project",
        query="test",
        metadata_filters={"status": "active"},
        tags=["important"],
        status="published",
    )

    assert captured_payload["metadata_filters"] == {"status": "active"}
    assert captured_payload["tags"] == ["important"]
    assert captured_payload["status"] == "published"


# --- Tests for filter-only search (query=None) --------------------------------


@pytest.mark.asyncio
async def test_search_notes_filter_only_metadata(monkeypatch):
    """search_notes with metadata_filters only (no query) sends correct payload."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        project="test-project",
        metadata_filters={"status": "in-progress"},
    )

    # Default text format returns a formatted string for empty results
    assert isinstance(result, str)
    assert captured_payload["metadata_filters"] == {"status": "in-progress"}
    # No text/title/permalink should be set
    assert captured_payload.get("text") is None
    assert captured_payload.get("title") is None
    assert captured_payload.get("permalink") is None


@pytest.mark.asyncio
async def test_search_notes_filter_only_tags(monkeypatch):
    """search_notes with tags only (no query) sends correct payload."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        project="test-project",
        tags=["security", "oauth"],
    )

    # Default text format returns a formatted string for empty results
    assert isinstance(result, str)
    assert captured_payload["tags"] == ["security", "oauth"]
    assert captured_payload.get("text") is None


@pytest.mark.asyncio
async def test_search_notes_no_criteria_returns_error(monkeypatch):
    """search_notes with no args at all returns a helpful error string."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)

    result = await search_mod.search_notes(project="test-project")

    assert isinstance(result, str)
    assert "No Search Criteria" in result


@pytest.mark.asyncio
async def test_search_notes_invalid_search_type_returns_error(monkeypatch):
    """Invalid search_type values should return an error message listing valid options."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, *args, **kwargs):
            pytest.fail("SearchClient.search should not be called for invalid search_type")

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        project="test-project",
        query="test query",
        search_type="bogus",
    )

    # The ValueError is caught by the generic exception handler and formatted
    assert isinstance(result, str)
    assert "Invalid search_type" in result
    assert "bogus" in result


@pytest.mark.asyncio
async def test_search_notes_passes_min_similarity(monkeypatch):
    """min_similarity param propagates to the SearchQuery payload."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    await search_mod.search_notes(
        project="test-project",
        query="test",
        search_type="vector",
        min_similarity=0.0,
    )

    assert captured_payload["min_similarity"] == 0.0
    assert captured_payload["retrieval_mode"] == "vector"


@pytest.mark.asyncio
async def test_search_notes_defaults_to_hybrid_when_semantic_enabled(monkeypatch):
    """When search_type is omitted, semantic-enabled configs should default to hybrid."""
    import importlib
    from dataclasses import dataclass

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    # Stub get_container to return a config with semantic_search_enabled=True
    @dataclass
    class StubConfig:
        semantic_search_enabled: bool = True

    @dataclass
    class StubContainer:
        config: StubConfig | None = None

        def __post_init__(self):
            if self.config is None:
                self.config = StubConfig()

    monkeypatch.setattr(search_mod, "get_container", lambda: StubContainer())

    await search_mod.search_notes(
        project="test-project",
        query="test query",
    )

    # Default mode should be hybrid when semantic search is enabled
    assert captured_payload["retrieval_mode"] == "hybrid"
    assert captured_payload["text"] == "test query"


@pytest.mark.asyncio
async def test_search_notes_defaults_to_fts_when_semantic_disabled(monkeypatch):
    """When search_type is omitted, semantic-disabled configs should default to FTS."""
    import importlib
    from dataclasses import dataclass

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    # Stub get_container to return a config with semantic_search_enabled=False
    @dataclass
    class StubConfig:
        semantic_search_enabled: bool = False

    @dataclass
    class StubContainer:
        config: StubConfig | None = None

        def __post_init__(self):
            if self.config is None:
                self.config = StubConfig()

    monkeypatch.setattr(search_mod, "get_container", lambda: StubContainer())

    await search_mod.search_notes(
        project="test-project",
        query="test query",
    )

    # Default mode should be FTS when semantic search is disabled
    assert captured_payload["retrieval_mode"] == "fts"
    assert captured_payload["text"] == "test query"


@pytest.mark.asyncio
async def test_search_notes_explicit_text_stays_fts_when_semantic_enabled(monkeypatch):
    """Explicit text mode should preserve FTS behavior even when semantic is enabled."""
    import importlib
    from dataclasses import dataclass

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    @dataclass
    class StubConfig:
        semantic_search_enabled: bool = True

    @dataclass
    class StubContainer:
        config: StubConfig | None = None

        def __post_init__(self):
            if self.config is None:
                self.config = StubConfig()

    monkeypatch.setattr(search_mod, "get_container", lambda: StubContainer())

    await search_mod.search_notes(
        project="test-project",
        query="test query",
        search_type="text",
    )

    assert captured_payload["retrieval_mode"] == "fts"
    assert captured_payload["text"] == "test query"


@pytest.mark.asyncio
async def test_search_notes_defaults_to_hybrid_when_container_not_initialized(monkeypatch):
    """CLI fallback config should still default omitted search_type to hybrid."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    # Stub get_container to raise RuntimeError (container not initialized)
    def raise_runtime_error():
        raise RuntimeError("MCP container not initialized")

    monkeypatch.setattr(search_mod, "get_container", raise_runtime_error)
    monkeypatch.setattr(
        search_mod,
        "ConfigManager",
        lambda: type(
            "StubConfigManager",
            (),
            {"config": type("Cfg", (), {"semantic_search_enabled": True})()},
        )(),
    )

    await search_mod.search_notes(
        project="test-project",
        query="test query",
    )

    # Should upgrade using ConfigManager fallback
    assert captured_payload["retrieval_mode"] == "hybrid"
    assert captured_payload["text"] == "test query"


@pytest.mark.asyncio
async def test_search_notes_defaults_to_fts_when_container_not_initialized_and_semantic_disabled(
    monkeypatch,
):
    """CLI fallback config should default omitted search_type to FTS when semantic is disabled."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    def raise_runtime_error():
        raise RuntimeError("MCP container not initialized")

    monkeypatch.setattr(search_mod, "get_container", raise_runtime_error)
    monkeypatch.setattr(
        search_mod,
        "ConfigManager",
        lambda: type(
            "StubConfigManager",
            (),
            {"config": type("Cfg", (), {"semantic_search_enabled": False})()},
        )(),
    )

    await search_mod.search_notes(
        project="test-project",
        query="test query",
    )

    assert captured_payload["retrieval_mode"] == "fts"
    assert captured_payload["text"] == "test query"


# --- Tests for default entity_types (issue #31) --------------------------------


@pytest.mark.asyncio
async def test_search_notes_defaults_entity_types_to_entity(monkeypatch):
    """search_notes defaults entity_types to ['entity'] when not explicitly provided.

    This prevents individual observations/relations from appearing as separate
    search results, since the entity row already indexes full file content.
    """
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    await search_mod.search_notes(
        project="test-project",
        query="test",
    )

    # entity_types should default to ["entity"]
    assert captured_payload["entity_types"] == ["entity"]


@pytest.mark.asyncio
async def test_search_notes_explicit_entity_types_overrides_default(monkeypatch):
    """Explicit entity_types parameter overrides the default ['entity'] filter."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    await search_mod.search_notes(
        project="test-project",
        query="test",
        entity_types=["observation"],
    )

    # Explicit entity_types should be used, not the default
    assert captured_payload["entity_types"] == ["observation"]


# --- Tests for note_types case-insensitivity ------------------------------------


@pytest.mark.asyncio
async def test_search_notes_note_types_lowercased(monkeypatch):
    """note_types values are lowercased so 'Chapter' matches stored 'chapter'."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    await search_mod.search_notes(
        project="test-project",
        query="test",
        note_types=["Chapter", "Person"],
    )

    # note_types should be lowercased
    assert captured_payload["note_types"] == ["chapter", "person"]


# --- Tests for tag: prefix parsing (issue #30) ---------------------------------


@pytest.mark.asyncio
async def test_search_notes_tag_prefix_converts_to_tags_filter(monkeypatch):
    """query='tag:security' should be converted to a tags filter with no text query."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        project="test-project",
        query="tag:security",
    )

    # Default text format returns a formatted string for empty results
    assert isinstance(result, str)
    assert captured_payload["tags"] == ["security"]
    # No text query should be set — tag: prefix was consumed
    assert captured_payload.get("text") is None


@pytest.mark.asyncio
async def test_search_notes_tag_prefix_merges_with_explicit_tags(monkeypatch):
    """query='tag:security' with tags=['oauth'] should merge both tag values."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        project="test-project",
        query="tag:security",
        tags=["oauth"],
    )

    # Default text format returns a formatted string for empty results
    assert isinstance(result, str)
    assert set(captured_payload["tags"]) == {"security", "oauth"}
    assert captured_payload.get("text") is None


@pytest.mark.asyncio
async def test_search_notes_multiple_tag_prefixes(monkeypatch):
    """query='tag:coffee AND tag:brewing' should extract both tags."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        project="test-project",
        query="tag:coffee AND tag:brewing",
    )

    # Default text format returns a formatted string for empty results
    assert isinstance(result, str)
    assert set(captured_payload["tags"]) == {"coffee", "brewing"}
    # Boolean connector AND should be stripped, leaving no text query
    assert captured_payload.get("text") is None


@pytest.mark.asyncio
async def test_search_notes_tag_prefix_with_remaining_text(monkeypatch):
    """query='authentication tag:security' should keep text and extract tag."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    # Remaining text query triggers resolve_project_and_path, so stub it too
    async def fake_resolve(client, query, project, context):
        return project, query, False

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        project="test-project",
        query="authentication tag:security",
    )

    # Default text format returns a formatted string for empty results
    assert isinstance(result, str)
    assert captured_payload["tags"] == ["security"]
    # Remaining text should be preserved as the query
    assert captured_payload["text"] == "authentication"


# --- Tests for text output format (#641) -----------------------------------


def test_format_search_markdown_with_results():
    """_format_search_markdown returns readable markdown for non-empty results."""
    from basic_memory.schemas.search import SearchResult, SearchItemType

    result = SearchResponse(
        results=[
            SearchResult(
                title="My Note",
                type=SearchItemType.ENTITY,
                score=0.85,
                permalink="docs/my-note",
                file_path="docs/My Note.md",
                matched_chunk="This is a matching snippet",
            ),
            SearchResult(
                title="Other Note",
                type=SearchItemType.ENTITY,
                score=0.42,
                permalink="docs/other-note",
                file_path="docs/Other Note.md",
            ),
        ],
        current_page=1,
        page_size=10,
    )

    text = _format_search_markdown(result, "test-project", "my query")
    assert isinstance(text, str)
    assert "# Search Results: my query" in text
    assert "test-project" in text
    assert "### My Note" in text
    assert "permalink: docs/my-note" in text
    assert "0.8500" in text
    assert "match: This is a matching snippet" in text
    assert "### Other Note" in text
    assert "2 results" in text
    assert "page 1" in text


def test_format_search_markdown_empty_results():
    """_format_search_markdown returns a no-results message when results are empty."""
    result = SearchResponse(results=[], current_page=1, page_size=10)
    text = _format_search_markdown(result, "test-project", "missing")
    assert isinstance(text, str)
    assert "No results found" in text
    assert "missing" in text


@pytest.mark.asyncio
async def test_search_notes_text_format_returns_string(monkeypatch):
    """search_notes with output_format='text' returns a formatted markdown string."""
    import importlib

    from basic_memory.schemas.search import SearchResult, SearchItemType

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            return SearchResponse(
                results=[
                    SearchResult(
                        title="Found Note",
                        type=SearchItemType.ENTITY,
                        score=0.9,
                        permalink="docs/found-note",
                        file_path="docs/Found Note.md",
                        matched_chunk="snippet",
                    ),
                ],
                current_page=page,
                page_size=page_size,
            )

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    result = await search_mod.search_notes(
        project="test-project",
        query="test",
        output_format="text",
    )

    assert isinstance(result, str)
    assert "# Search Results: test" in result
    assert "### Found Note" in result
    assert "permalink: docs/found-note" in result


# --- Tests for metadata_filters key aliasing (#642) ----------------------------


@pytest.mark.asyncio
async def test_search_notes_metadata_filters_aliases_note_type(monkeypatch):
    """metadata_filters={'note_type': 'note'} is aliased to {'type': 'note'}."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    await search_mod.search_notes(
        project="test-project",
        query="test",
        metadata_filters={"note_type": "note"},
    )

    # "note_type" should be aliased to "type" in the payload
    assert captured_payload["metadata_filters"] == {"type": "note"}


@pytest.mark.asyncio
async def test_search_notes_metadata_filters_preserves_non_aliased_keys(monkeypatch):
    """metadata_filters with non-aliased keys pass through unchanged."""
    import importlib

    search_mod = importlib.import_module("basic_memory.mcp.tools.search")
    clients_mod = importlib.import_module("basic_memory.mcp.clients")

    class StubProject:
        name = "test-project"
        external_id = "test-external-id"

    @asynccontextmanager
    async def fake_get_project_client(*args, **kwargs):
        yield (object(), StubProject())

    async def fake_resolve_project_and_path(
        client, identifier, project=None, context=None, headers=None
    ):
        return StubProject(), identifier, False

    captured_payload: dict = {}

    class MockSearchClient:
        def __init__(self, *args, **kwargs):
            pass

        async def search(self, payload, page, page_size):
            captured_payload.update(payload)
            return SearchResponse(results=[], current_page=page, page_size=page_size)

    monkeypatch.setattr(search_mod, "get_project_client", fake_get_project_client)
    monkeypatch.setattr(search_mod, "resolve_project_and_path", fake_resolve_project_and_path)
    monkeypatch.setattr(clients_mod, "SearchClient", MockSearchClient)

    await search_mod.search_notes(
        project="test-project",
        query="test",
        metadata_filters={"note_type": "spec", "priority": "high"},
    )

    # "note_type" aliased to "type", "priority" passes through unchanged
    assert captured_payload["metadata_filters"] == {"type": "spec", "priority": "high"}
