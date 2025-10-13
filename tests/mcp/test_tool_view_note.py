"""Tests for view_note tool that exercise the full stack with SQLite."""

from textwrap import dedent
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from basic_memory.mcp.tools import write_note, view_note
from basic_memory.schemas.search import SearchResponse


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
    with patch("basic_memory.mcp.tools.read_note.search_notes.fn") as mock:
        # Default to empty results
        mock.return_value = SearchResponse(results=[], current_page=1, page_size=1)
        yield mock


@pytest.mark.asyncio
async def test_view_note_basic_functionality(app, test_project):
    """Test viewing a note creates an artifact."""
    # First create a note
    await write_note.fn(
        project=test_project.name,
        title="Test View Note",
        folder="test",
        content="# Test View Note\n\nThis is test content for viewing.",
    )

    # View the note
    result = await view_note.fn("Test View Note", project=test_project.name)

    # Should contain note retrieval message
    assert 'Note retrieved: "Test View Note"' in result
    assert "Display this note as a markdown artifact for the user" in result
    assert "Content:" in result
    assert "---" in result

    # Should contain the note content
    assert "# Test View Note" in result
    assert "This is test content for viewing." in result


@pytest.mark.asyncio
async def test_view_note_with_frontmatter_title(app, test_project):
    """Test viewing a note extracts title from frontmatter."""
    # Create note with frontmatter
    content = dedent("""
        ---
        title: "Frontmatter Title"
        tags: [test]
        ---

        # Frontmatter Title

        Content with frontmatter title.
    """).strip()

    await write_note.fn(
        project=test_project.name, title="Frontmatter Title", folder="test", content=content
    )

    # View the note
    result = await view_note.fn("Frontmatter Title", project=test_project.name)

    # Should show title in retrieval message
    assert 'Note retrieved: "Frontmatter Title"' in result
    assert "Display this note as a markdown artifact for the user" in result


@pytest.mark.asyncio
async def test_view_note_with_heading_title(app, test_project):
    """Test viewing a note extracts title from first heading when no frontmatter."""
    # Create note with heading but no frontmatter title
    content = "# Heading Title\n\nContent with heading title."

    await write_note.fn(
        project=test_project.name, title="Heading Title", folder="test", content=content
    )

    # View the note
    result = await view_note.fn("Heading Title", project=test_project.name)

    # Should show title in retrieval message
    assert 'Note retrieved: "Heading Title"' in result
    assert "Display this note as a markdown artifact for the user" in result


@pytest.mark.asyncio
async def test_view_note_unicode_content(app, test_project):
    """Test viewing a note with Unicode content."""
    content = "# Unicode Test 🚀\n\nThis note has emoji 🎉 and unicode ♠♣♥♦"

    await write_note.fn(
        project=test_project.name, title="Unicode Test 🚀", folder="test", content=content
    )

    # View the note
    result = await view_note.fn("Unicode Test 🚀", project=test_project.name)

    # Should handle Unicode properly
    assert "🚀" in result
    assert "🎉" in result
    assert "♠♣♥♦" in result
    assert 'Note retrieved: "Unicode Test 🚀"' in result


@pytest.mark.asyncio
async def test_view_note_by_permalink(app, test_project):
    """Test viewing a note by its permalink."""
    await write_note.fn(
        project=test_project.name,
        title="Permalink Test",
        folder="test",
        content="Content for permalink test.",
    )

    # View by permalink
    result = await view_note.fn("test/permalink-test", project=test_project.name)

    # Should work with permalink
    assert 'Note retrieved: "test/permalink-test"' in result
    assert "Content for permalink test." in result
    assert "Display this note as a markdown artifact for the user" in result


@pytest.mark.asyncio
async def test_view_note_with_memory_url(app, test_project):
    """Test viewing a note using a memory:// URL."""
    await write_note.fn(
        project=test_project.name,
        title="Memory URL Test",
        folder="test",
        content="Testing memory:// URL handling in view_note",
    )

    # View with memory:// URL
    result = await view_note.fn("memory://test/memory-url-test", project=test_project.name)

    # Should work with memory:// URL
    assert 'Note retrieved: "memory://test/memory-url-test"' in result
    assert "Testing memory:// URL handling in view_note" in result
    assert "Display this note as a markdown artifact for the user" in result


@pytest.mark.asyncio
async def test_view_note_not_found(app, test_project):
    """Test viewing a non-existent note returns error without artifact."""
    # Try to view non-existent note
    result = await view_note.fn("NonExistent Note", project=test_project.name)

    # Should return error message without artifact instructions
    assert "# Note Not Found" in result
    assert "NonExistent Note" in result
    assert "Display this note as a markdown artifact" not in result  # No artifact for errors
    assert "Check Identifier Type" in result
    assert "Search Instead" in result


@pytest.mark.asyncio
async def test_view_note_pagination(app, test_project):
    """Test viewing a note with pagination parameters."""
    await write_note.fn(
        project=test_project.name,
        title="Pagination Test",
        folder="test",
        content="Content for pagination test.",
    )

    # View with pagination
    result = await view_note.fn("Pagination Test", page=1, page_size=5, project=test_project.name)

    # Should work with pagination
    assert 'Note retrieved: "Pagination Test"' in result
    assert "Content for pagination test." in result
    assert "Display this note as a markdown artifact for the user" in result


@pytest.mark.asyncio
async def test_view_note_project_parameter(app, test_project):
    """Test viewing a note with project parameter."""
    await write_note.fn(
        project=test_project.name,
        title="Project Test",
        folder="test",
        content="Content for project test.",
    )

    # View with explicit project
    result = await view_note.fn("Project Test", project=test_project.name)

    # Should work with project parameter
    assert 'Note retrieved: "Project Test"' in result
    assert "Content for project test." in result
    assert "Display this note as a markdown artifact for the user" in result


@pytest.mark.asyncio
async def test_view_note_artifact_identifier_unique(app, test_project):
    """Test that different notes are retrieved correctly with unique identifiers."""
    # Create two notes
    await write_note.fn(
        project=test_project.name, title="Note One", folder="test", content="Content one"
    )
    await write_note.fn(
        project=test_project.name, title="Note Two", folder="test", content="Content two"
    )

    # View both notes
    result1 = await view_note.fn("Note One", project=test_project.name)
    result2 = await view_note.fn("Note Two", project=test_project.name)

    # Should have different note identifiers in retrieval messages
    assert 'Note retrieved: "Note One"' in result1
    assert 'Note retrieved: "Note Two"' in result2
    assert "Content one" in result1
    assert "Content two" in result2


@pytest.mark.asyncio
async def test_view_note_fallback_identifier_as_title(app, test_project):
    """Test that view_note uses identifier as title when no title is extractable."""
    # Create a note with no clear title structure
    await write_note.fn(
        project=test_project.name,
        title="Simple Note",
        folder="test",
        content="Just plain content with no headings or frontmatter title",
    )

    # View the note
    result = await view_note.fn("Simple Note", project=test_project.name)

    # Should use identifier as title in retrieval message
    assert 'Note retrieved: "Simple Note"' in result
    assert "Display this note as a markdown artifact for the user" in result


@pytest.mark.asyncio
async def test_view_note_direct_success(app, test_project, mock_call_get):
    """Test view_note with successful direct permalink lookup."""
    # Setup mock for successful response with frontmatter
    note_content = dedent("""
        ---
        title: "Test Note"
        ---
        # Test Note

        This is a test note.
    """).strip()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = note_content
    mock_call_get.return_value = mock_response

    # Call the function
    result = await view_note.fn("test/test-note", project=test_project.name)

    # Verify direct lookup was used
    mock_call_get.assert_called_once()
    assert "test/test-note" in mock_call_get.call_args[0][1]

    # Verify result contains note content
    assert 'Note retrieved: "test/test-note"' in result
    assert "Display this note as a markdown artifact for the user" in result
    assert "This is a test note." in result
