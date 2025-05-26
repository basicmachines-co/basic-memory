"""Tests for the edit_note MCP tool."""

import pytest

from basic_memory.mcp.tools.edit_note import edit_note
from basic_memory.mcp.tools.write_note import write_note


@pytest.mark.asyncio
async def test_edit_note_append_operation(client):
    """Test appending content to an existing note."""
    # Create initial note
    await write_note(
        title="Test Note",
        folder="test",
        content="# Test Note\nOriginal content here.",
    )

    # Append content
    result = await edit_note(
        identifier="test/test-note",
        operation="append",
        content="\n## New Section\nAppended content here.",
    )

    assert isinstance(result, str)
    assert "Edited note (append)" in result
    assert "file_path: test/Test Note.md" in result
    assert "permalink: test/test-note" in result
    assert "Added 3 lines to end of note" in result


@pytest.mark.asyncio
async def test_edit_note_prepend_operation(client):
    """Test prepending content to an existing note."""
    # Create initial note
    await write_note(
        title="Meeting Notes",
        folder="meetings",
        content="# Meeting Notes\nExisting content.",
    )

    # Prepend content
    result = await edit_note(
        identifier="meetings/meeting-notes",
        operation="prepend",
        content="## 2025-05-25 Update\nNew meeting notes.\n",
    )

    assert isinstance(result, str)
    assert "Edited note (prepend)" in result
    assert "file_path: meetings/Meeting Notes.md" in result
    assert "permalink: meetings/meeting-notes" in result
    assert "Added 3 lines to beginning of note" in result


@pytest.mark.asyncio
async def test_edit_note_find_replace_operation(client):
    """Test find and replace operation."""
    # Create initial note with version info
    await write_note(
        title="Config Document",
        folder="config",
        content="# Configuration\nVersion: v0.12.0\nSettings for v0.12.0 release.",
    )

    # Replace version - expecting 2 replacements
    result = await edit_note(
        identifier="config/config-document",
        operation="find_replace",
        content="v0.13.0",
        find_text="v0.12.0",
        expected_replacements=2,
    )

    assert isinstance(result, str)
    assert "Edited note (find_replace)" in result
    assert "file_path: config/Config Document.md" in result
    assert "operation: Find and replace operation completed" in result


@pytest.mark.asyncio
async def test_edit_note_replace_section_operation(client):
    """Test replacing content under a specific section."""
    # Create initial note with sections
    await write_note(
        title="API Specification",
        folder="specs",
        content="# API Spec\n\n## Overview\nAPI overview here.\n\n## Implementation\nOld implementation details.\n\n## Testing\nTest info here.",
    )

    # Replace implementation section
    result = await edit_note(
        identifier="specs/api-specification",
        operation="replace_section",
        content="New implementation approach using FastAPI.\nImproved error handling.\n",
        section="## Implementation",
    )

    assert isinstance(result, str)
    assert "Edited note (replace_section)" in result
    assert "file_path: specs/API Specification.md" in result
    assert "Replaced content under section '## Implementation'" in result


@pytest.mark.asyncio
async def test_edit_note_nonexistent_note(client):
    """Test editing a note that doesn't exist - should return helpful guidance."""
    result = await edit_note(
        identifier="nonexistent/note", operation="append", content="Some content"
    )

    assert isinstance(result, str)
    assert "# Edit Failed" in result
    assert "search_notes" in result  # Should suggest searching
    assert "read_note" in result  # Should suggest reading to verify


@pytest.mark.asyncio
async def test_edit_note_invalid_operation(client):
    """Test using an invalid operation."""
    # Create a note first
    await write_note(
        title="Test Note",
        folder="test",
        content="# Test\nContent here.",
    )

    with pytest.raises(ValueError) as exc_info:
        await edit_note(identifier="test/test-note", operation="invalid_op", content="Some content")

    assert "Invalid operation 'invalid_op'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_edit_note_find_replace_missing_find_text(client):
    """Test find_replace operation without find_text parameter."""
    # Create a note first
    await write_note(
        title="Test Note",
        folder="test",
        content="# Test\nContent here.",
    )

    with pytest.raises(ValueError) as exc_info:
        await edit_note(
            identifier="test/test-note", operation="find_replace", content="replacement"
        )

    assert "find_text parameter is required for find_replace operation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_edit_note_replace_section_missing_section(client):
    """Test replace_section operation without section parameter."""
    # Create a note first
    await write_note(
        title="Test Note",
        folder="test",
        content="# Test\nContent here.",
    )

    with pytest.raises(ValueError) as exc_info:
        await edit_note(
            identifier="test/test-note", operation="replace_section", content="new content"
        )

    assert "section parameter is required for replace_section operation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_edit_note_replace_section_nonexistent_section(client):
    """Test replacing a section that doesn't exist - should append it."""
    # Create initial note without the target section
    await write_note(
        title="Document",
        folder="docs",
        content="# Document\n\n## Existing Section\nSome content here.",
    )

    # Try to replace non-existent section
    result = await edit_note(
        identifier="docs/document",
        operation="replace_section",
        content="New section content here.\n",
        section="## New Section",
    )

    assert isinstance(result, str)
    assert "Edited note (replace_section)" in result
    assert "file_path: docs/Document.md" in result
    # Should succeed - the section gets appended if it doesn't exist


@pytest.mark.asyncio
async def test_edit_note_with_observations_and_relations(client):
    """Test editing a note that contains observations and relations."""
    # Create note with semantic content
    await write_note(
        title="Feature Spec",
        folder="features",
        content="# Feature Spec\n\n- [design] Initial design thoughts #architecture\n- implements [[Base System]]\n\nOriginal content.",
    )

    # Append more semantic content
    result = await edit_note(
        identifier="features/feature-spec",
        operation="append",
        content="\n## Updates\n\n- [implementation] Added new feature #development\n- relates_to [[User Guide]]",
    )

    assert isinstance(result, str)
    assert "Edited note (append)" in result
    assert "## Observations" in result
    assert "## Relations" in result


@pytest.mark.asyncio
async def test_edit_note_identifier_variations(client):
    """Test that various identifier formats work."""
    # Create a note
    await write_note(
        title="Test Document",
        folder="docs",
        content="# Test Document\nOriginal content.",
    )

    # Test different identifier formats
    identifiers_to_test = [
        "docs/test-document",  # permalink
        "Test Document",  # title
        "docs/Test Document",  # folder/title
    ]

    for identifier in identifiers_to_test:
        result = await edit_note(
            identifier=identifier, operation="append", content=f"\n## Update via {identifier}"
        )

        assert isinstance(result, str)
        assert "Edited note (append)" in result
        assert "file_path: docs/Test Document.md" in result


@pytest.mark.asyncio
async def test_edit_note_find_replace_no_matches(client):
    """Test find_replace when the find_text doesn't exist - should return error."""
    # Create initial note
    await write_note(
        title="Test Note",
        folder="test",
        content="# Test Note\nSome content here.",
    )

    # Try to replace text that doesn't exist - should fail with default expected_replacements=1
    result = await edit_note(
        identifier="test/test-note",
        operation="find_replace",
        content="replacement",
        find_text="nonexistent_text",
    )

    assert isinstance(result, str)
    assert "# Edit Failed - Text Not Found" in result
    assert "read_note" in result  # Should suggest reading the note first
    assert "Alternative approaches" in result  # Should suggest alternatives


@pytest.mark.asyncio
async def test_edit_note_empty_content_operations(client):
    """Test operations with empty content."""
    # Create initial note
    await write_note(
        title="Test Note",
        folder="test",
        content="# Test Note\nOriginal content.",
    )

    # Test append with empty content
    result = await edit_note(identifier="test/test-note", operation="append", content="")

    assert isinstance(result, str)
    assert "Edited note (append)" in result
    # Should still work, just adding empty content


@pytest.mark.asyncio
async def test_edit_note_find_replace_wrong_count(client):
    """Test find_replace when replacement count doesn't match expected."""
    # Create initial note with version info
    await write_note(
        title="Config Document",
        folder="config",
        content="# Configuration\nVersion: v0.12.0\nSettings for v0.12.0 release.",
    )

    # Try to replace expecting 1 occurrence, but there are actually 2
    result = await edit_note(
        identifier="config/config-document",
        operation="find_replace",
        content="v0.13.0",
        find_text="v0.12.0",
        expected_replacements=1,  # Wrong! There are actually 2 occurrences
    )

    assert isinstance(result, str)
    assert "# Edit Failed - Wrong Replacement Count" in result
    assert "Expected 1 occurrences" in result
    assert "but found 2" in result
    assert "Update expected_replacements" in result  # Should suggest the fix
    assert "expected_replacements=2" in result  # Should suggest the exact fix


@pytest.mark.asyncio
async def test_edit_note_replace_section_multiple_sections(client):
    """Test replace_section with multiple sections having same header - should return helpful error."""
    # Create note with duplicate section headers
    await write_note(
        title="Sample Note",
        folder="docs",
        content="# Main Title\n\n## Section 1\nFirst instance\n\n## Section 2\nSome content\n\n## Section 1\nSecond instance",
    )

    # Try to replace section when multiple exist
    result = await edit_note(
        identifier="docs/sample-note",
        operation="replace_section",
        content="New content",
        section="## Section 1",
    )

    assert isinstance(result, str)
    assert "# Edit Failed - Duplicate Section Headers" in result
    assert "Multiple sections found" in result
    assert "read_note" in result  # Should suggest reading the note first
    assert "Make headers unique" in result  # Should suggest making headers unique


@pytest.mark.asyncio
async def test_edit_note_find_replace_empty_find_text(client):
    """Test find_replace with empty/whitespace find_text - should return helpful error."""
    # Create initial note
    await write_note(
        title="Test Note",
        folder="test",
        content="# Test Note\nSome content here.",
    )

    # Try with whitespace-only find_text - this should be caught by service validation
    result = await edit_note(
        identifier="test/test-note",
        operation="find_replace",
        content="replacement",
        find_text="   ",  # whitespace only
    )

    assert isinstance(result, str)
    assert "# Edit Failed" in result
    # Should contain helpful guidance about the error
