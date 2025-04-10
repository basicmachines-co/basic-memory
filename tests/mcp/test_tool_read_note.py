"""Tests for note tools that exercise the full stack with SQLite."""

from textwrap import dedent

import pytest

from basic_memory.mcp.tools import write_note, read_note

import pytest_asyncio
from unittest.mock import MagicMock, patch

from basic_memory.schemas.search import SearchResponse, SearchItemType


@pytest_asyncio.fixture
async def mock_call_get():
    """Mock for call_get to simulate different responses."""
    with patch("basic_memory.mcp.tools.read_note.call_get") as mock:
        # Default to 404 - not found
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock.return_value = mock_response
        yield mock


@pytest_asyncio.fixture
async def mock_search():
    """Mock for search tool."""
    with patch("basic_memory.mcp.tools.read_note.search_notes") as mock:
        # Default to empty results
        mock.return_value = SearchResponse(results=[], current_page=1, page_size=1)
        yield mock


@pytest.mark.asyncio
async def test_read_note_by_title(app):
    """Test reading a note by its title."""
    # First create a note
    await write_note(title="Special Note", folder="test", content="Note content here")

    # Should be able to read it by title
    content = await read_note("Special Note")
    assert "Note content here" in content


@pytest.mark.asyncio
async def test_note_unicode_content(app):
    """Test handling of unicode content in"""
    content = "# Test 🚀\nThis note has emoji 🎉 and unicode ♠♣♥♦"
    result = await write_note(title="Unicode Test", folder="test", content=content)

    assert (
        dedent("""
        # Created note
        file_path: test/Unicode Test.md
        permalink: test/unicode-test
        checksum: 272389cd
        """).strip()
        in result
    )

    # Read back should preserve unicode
    result = await read_note("test/unicode-test")
    assert content in result


@pytest.mark.asyncio
async def test_multiple_notes(app):
    """Test creating and managing multiple"""
    # Create several notes
    notes_data = [
        ("test/note-1", "Note 1", "test", "Content 1", ["tag1"]),
        ("test/note-2", "Note 2", "test", "Content 2", ["tag1", "tag2"]),
        ("test/note-3", "Note 3", "test", "Content 3", []),
    ]

    for _, title, folder, content, tags in notes_data:
        await write_note(title=title, folder=folder, content=content, tags=tags)

    # Should be able to read each one
    for permalink, title, folder, content, _ in notes_data:
        note = await read_note(permalink)
        assert content in note

    # read multiple notes at once

    result = await read_note("test/*")

    # note we can't compare times
    assert "--- memory://test/note-1" in result
    assert "Content 1" in result

    assert "--- memory://test/note-2" in result
    assert "Content 2" in result

    assert "--- memory://test/note-3" in result
    assert "Content 3" in result


@pytest.mark.asyncio
async def test_multiple_notes_pagination(app):
    """Test creating and managing multiple"""
    # Create several notes
    notes_data = [
        ("test/note-1", "Note 1", "test", "Content 1", ["tag1"]),
        ("test/note-2", "Note 2", "test", "Content 2", ["tag1", "tag2"]),
        ("test/note-3", "Note 3", "test", "Content 3", []),
    ]

    for _, title, folder, content, tags in notes_data:
        await write_note(title=title, folder=folder, content=content, tags=tags)

    # Should be able to read each one
    for permalink, title, folder, content, _ in notes_data:
        note = await read_note(permalink)
        assert content in note

    # read multiple notes at once with pagination
    result = await read_note("test/*", page=1, page_size=2)

    # note we can't compare times
    assert "--- memory://test/note-1" in result
    assert "Content 1" in result

    assert "--- memory://test/note-2" in result
    assert "Content 2" in result


@pytest.mark.asyncio
async def test_read_note_memory_url(app):
    """Test reading a note using a memory:// URL.

    Should:
    - Handle memory:// URLs correctly
    - Normalize the URL before resolving
    - Return the note content
    """
    # First create a note
    result = await write_note(
        title="Memory URL Test",
        folder="test",
        content="Testing memory:// URL handling",
    )
    assert result

    # Should be able to read it with a memory:// URL
    memory_url = "memory://test/memory-url-test"
    content = await read_note(memory_url)
    assert "Testing memory:// URL handling" in content


@pytest.mark.asyncio
async def test_read_note_direct_success(mock_call_get):
    """Test read_note with successful direct permalink lookup."""
    # Setup mock for successful response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "# Test Note\n\nThis is a test note."
    mock_call_get.return_value = mock_response

    # Call the function
    result = await read_note("test/test-note")

    # Verify direct lookup was used
    mock_call_get.assert_called_once()
    assert "test/test-note" in mock_call_get.call_args[0][1]

    # Verify result
    assert "# Test Note" in result
    assert "This is a test note." in result


@pytest.mark.asyncio
async def test_read_note_title_search_fallback(mock_call_get, mock_search):
    """Test read_note falls back to title search when direct lookup fails."""
    # Setup mock for failed direct lookup
    mock_call_get.side_effect = [
        # First call fails (direct lookup)
        MagicMock(status_code=404),
        # Second call succeeds (after title search)
        MagicMock(status_code=200, text="# Test Note\n\nThis is a test note."),
    ]

    # Setup mock for successful title search
    mock_search.return_value = SearchResponse(
        results=[
            {
                "id": 1,
                "entity": "test/test-note",
                "title": "Test Note",
                "type": SearchItemType.ENTITY,
                "permalink": "test/test-note",
                "file_path": "test/test-note.md",
                "score": 1.0,
            }
        ],
        current_page=1,
        page_size=1,
    )

    # Call the function
    result = await read_note("Test Note")

    # Verify title search was used
    mock_search.assert_called_once()
    assert mock_search.call_args[1]["query"] == "Test Note"
    assert mock_search.call_args[1]["search_type"] == "title"

    # Verify second lookup was used
    assert mock_call_get.call_count == 2
    assert "test/test-note" in mock_call_get.call_args[0][1]

    # Verify result
    assert "# Test Note" in result
    assert "This is a test note." in result


@pytest.mark.asyncio
async def test_read_note_text_search_fallback(mock_call_get, mock_search):
    """Test read_note falls back to text search and returns related results."""
    # Setup mock for failed direct and title lookups
    mock_call_get.return_value = MagicMock(status_code=404)

    # Setup mock for failed title search but successful text search
    mock_search.side_effect = [
        # First call (title search) returns no results
        SearchResponse(results=[], current_page=1, page_size=1),
        # Second call (text search) returns results
        SearchResponse(
            results=[
                {
                    "id": 1,
                    "title": "Related Note 1",
                    "entity": "notes/related-note-1",
                    "type": SearchItemType.ENTITY,
                    "permalink": "notes/related-note-1",
                    "file_path": "notes/related-note-1.md",
                    "score": 0.8,
                },
                {
                    "id": 2,
                    "title": "Related Note 2",
                    "entity": "notes/related-note-2",
                    "type": SearchItemType.ENTITY,
                    "permalink": "notes/related-note-2",
                    "file_path": "notes/related-note-2.md",
                    "score": 0.7,
                },
            ],
            current_page=1,
            page_size=1,
        ),
    ]

    # Call the function
    result = await read_note("some query")

    # Verify both search types were used
    assert mock_search.call_count == 2
    assert mock_search.call_args_list[0][1]["query"] == "some query"  # Title search
    assert mock_search.call_args_list[0][1]["search_type"] == "title"
    assert mock_search.call_args_list[1][1]["query"] == "some query"  # Text search
    assert mock_search.call_args_list[1][1]["search_type"] == "text"

    # Verify result contains helpful information
    assert "Note Not Found" in result
    assert "Related Note 1" in result
    assert "Related Note 2" in result
    assert 'read_note("notes/related-note-1")' in result
    assert "search_notes(query=" in result
    assert "write_note(" in result


@pytest.mark.asyncio
async def test_read_note_complete_fallback(mock_call_get, mock_search):
    """Test read_note with all lookups failing."""
    # Setup mock for failed direct lookup
    mock_call_get.return_value = MagicMock(status_code=404)

    # Setup mock for failed searches
    mock_search.return_value = SearchResponse(results=[], current_page=1, page_size=1)

    # Call the function
    result = await read_note("nonexistent")

    # Verify search was used
    assert mock_search.call_count == 2

    # Verify result contains helpful guidance
    assert "Note Not Found" in result
    assert "nonexistent" in result
    assert "Check Identifier Type" in result
    assert "Search Instead" in result
    assert "Recent Activity" in result
    assert "Create New Note" in result
    assert "write_note(" in result
