"""Tests for search MCP tools."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from basic_memory.mcp.tools import write_note
from basic_memory.mcp.tools.search import search_notes, _format_search_error_response
from basic_memory.schemas.search import SearchResponse


@pytest.mark.asyncio
async def test_search_text(client, test_project):
    """Test basic search functionality."""
    # Create a test note
    result = await write_note.fn(
        project=test_project.name,
        title="Test Search Note",
        folder="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )
    assert result

    # Search for it
    response = await search_notes.fn(project=test_project.name, query="searchable")

    # Verify results - handle both success and error cases
    if isinstance(response, SearchResponse):
        # Success case - verify SearchResponse
        assert len(response.results) > 0
        assert any(r.permalink == "test/test-search-note" for r in response.results)
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_title(client, test_project):
    """Test basic search functionality."""
    # Create a test note
    result = await write_note.fn(
        project=test_project.name,
        title="Test Search Note",
        folder="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )
    assert result

    # Search for it
    response = await search_notes.fn(
        project=test_project.name, query="Search Note", search_type="title"
    )

    # Verify results - handle both success and error cases
    if isinstance(response, str):
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")
    else:
        # Success case - verify SearchResponse
        assert len(response.results) > 0
        assert any(r.permalink == "test/test-search-note" for r in response.results)


@pytest.mark.asyncio
async def test_search_permalink(client, test_project):
    """Test basic search functionality."""
    # Create a test note
    result = await write_note.fn(
        project=test_project.name,
        title="Test Search Note",
        folder="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )
    assert result

    # Search for it
    response = await search_notes.fn(
        project=test_project.name, query="test/test-search-note", search_type="permalink"
    )

    # Verify results - handle both success and error cases
    if isinstance(response, SearchResponse):
        # Success case - verify SearchResponse
        assert len(response.results) > 0
        assert any(r.permalink == "test/test-search-note" for r in response.results)
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_permalink_match(client, test_project):
    """Test basic search functionality."""
    # Create a test note
    result = await write_note.fn(
        project=test_project.name,
        title="Test Search Note",
        folder="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )
    assert result

    # Search for it
    response = await search_notes.fn(
        project=test_project.name, query="test/test-search-*", search_type="permalink"
    )

    # Verify results - handle both success and error cases
    if isinstance(response, SearchResponse):
        # Success case - verify SearchResponse
        assert len(response.results) > 0
        assert any(r.permalink == "test/test-search-note" for r in response.results)
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_pagination(client, test_project):
    """Test basic search functionality."""
    # Create a test note
    result = await write_note.fn(
        project=test_project.name,
        title="Test Search Note",
        folder="test",
        content="# Test\nThis is a searchable test note",
        tags=["test", "search"],
    )
    assert result

    # Search for it
    response = await search_notes.fn(
        project=test_project.name, query="searchable", page=1, page_size=1
    )

    # Verify results - handle both success and error cases
    if isinstance(response, SearchResponse):
        # Success case - verify SearchResponse
        assert len(response.results) == 1
        assert any(r.permalink == "test/test-search-note" for r in response.results)
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_with_type_filter(client, test_project):
    """Test search with entity type filter."""
    # Create test content
    await write_note.fn(
        project=test_project.name,
        title="Entity Type Test",
        folder="test",
        content="# Test\nFiltered by type",
    )

    # Search with type filter
    response = await search_notes.fn(project=test_project.name, query="type", types=["note"])

    # Verify results - handle both success and error cases
    if isinstance(response, SearchResponse):
        # Success case - verify all results are entities
        assert all(r.type == "entity" for r in response.results)
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_with_entity_type_filter(client, test_project):
    """Test search with entity type filter."""
    # Create test content
    await write_note.fn(
        project=test_project.name,
        title="Entity Type Test",
        folder="test",
        content="# Test\nFiltered by type",
    )

    # Search with entity type filter
    response = await search_notes.fn(
        project=test_project.name, query="type", entity_types=["entity"]
    )

    # Verify results - handle both success and error cases
    if isinstance(response, SearchResponse):
        # Success case - verify all results are entities
        assert all(r.type == "entity" for r in response.results)
    else:
        # If search failed and returned error message, test should fail with informative message
        pytest.fail(f"Search failed with error: {response}")


@pytest.mark.asyncio
async def test_search_with_date_filter(client, test_project):
    """Test search with date filter."""
    # Create test content
    await write_note.fn(
        project=test_project.name,
        title="Recent Note",
        folder="test",
        content="# Test\nRecent content",
    )

    # Search with date filter
    one_hour_ago = datetime.now() - timedelta(hours=1)
    response = await search_notes.fn(
        project=test_project.name, query="recent", after_date=one_hour_ago.isoformat()
    )

    # Verify results - handle both success and error cases
    if isinstance(response, SearchResponse):
        # Success case - verify we get results within timeframe
        assert len(response.results) > 0
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

    def test_format_search_error_generic(self):
        """Test formatting for generic errors."""
        result = _format_search_error_response("test-project", "unknown error", "test query")

        assert "# Search Failed" in result
        assert "Error searching for 'test query': unknown error" in result
        assert "## Troubleshooting steps:" in result


class TestSearchToolErrorHandling:
    """Test search tool exception handling."""

    @pytest.mark.asyncio
    async def test_search_notes_exception_handling(self):
        """Test exception handling in search_notes."""
        with patch("basic_memory.mcp.tools.search.get_active_project") as mock_get_project:
            mock_get_project.return_value.project_url = "http://test"

            with patch(
                "basic_memory.mcp.tools.search.call_post", side_effect=Exception("syntax error")
            ):
                result = await search_notes.fn(project="test-project", query="test query")

                assert isinstance(result, str)
                assert "# Search Failed - Invalid Syntax" in result

    @pytest.mark.asyncio
    async def test_search_notes_permission_error(self):
        """Test search_notes with permission error."""
        with patch("basic_memory.mcp.tools.search.get_active_project") as mock_get_project:
            mock_get_project.return_value.project_url = "http://test"

            with patch(
                "basic_memory.mcp.tools.search.call_post",
                side_effect=Exception("permission denied"),
            ):
                result = await search_notes.fn(project="test-project", query="test query")

                assert isinstance(result, str)
                assert "# Search Failed - Access Error" in result
