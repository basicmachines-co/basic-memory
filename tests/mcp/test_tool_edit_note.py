"""Tests for the edit_note MCP tool."""

import pytest

from basic_memory.mcp.tools.edit_note import edit_note
from basic_memory.mcp.tools.write_note import write_note


@pytest.mark.asyncio
async def test_edit_note_append_operation(client, test_project):
    """Test appending content to an existing note."""
    # Create initial note
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test Note\nOriginal content here.",
    )

    # Append content
    result = await edit_note(
        project=test_project.name,
        identifier="test/test-note",
        operation="append",
        content="\n## New Section\nAppended content here.",
    )

    assert isinstance(result, str)
    assert "Edited note (append)" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Test Note.md" in result
    assert f"permalink: {test_project.name}/test/test-note" in result
    assert "Added 3 lines to end of note" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_edit_note_prepend_operation(client, test_project):
    """Test prepending content to an existing note."""
    # Create initial note
    await write_note(
        project=test_project.name,
        title="Meeting Notes",
        directory="meetings",
        content="# Meeting Notes\nExisting content.",
    )

    # Prepend content
    result = await edit_note(
        project=test_project.name,
        identifier="meetings/meeting-notes",
        operation="prepend",
        content="## 2025-05-25 Update\nNew meeting notes.\n",
    )

    assert isinstance(result, str)
    assert "Edited note (prepend)" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: meetings/Meeting Notes.md" in result
    assert f"permalink: {test_project.name}/meetings/meeting-notes" in result
    assert "Added 3 lines to beginning of note" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_edit_note_find_replace_operation(client, test_project):
    """Test find and replace operation."""
    # Create initial note with version info
    await write_note(
        project=test_project.name,
        title="Config Document",
        directory="config",
        content="# Configuration\nVersion: v0.12.0\nSettings for v0.12.0 release.",
    )

    # Replace version - expecting 2 replacements
    result = await edit_note(
        project=test_project.name,
        identifier="config/config-document",
        operation="find_replace",
        content="v0.13.0",
        find_text="v0.12.0",
        expected_replacements=2,
    )

    assert isinstance(result, str)
    assert "Edited note (find_replace)" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: config/Config Document.md" in result
    assert "operation: Find and replace operation completed" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_edit_note_replace_section_operation(client, test_project):
    """Test replacing content under a specific section."""
    # Create initial note with sections
    await write_note(
        project=test_project.name,
        title="API Specification",
        directory="specs",
        content="# API Spec\n\n## Overview\nAPI overview here.\n\n## Implementation\nOld implementation details.\n\n## Testing\nTest info here.",
    )

    # Replace implementation section
    result = await edit_note(
        project=test_project.name,
        identifier="specs/api-specification",
        operation="replace_section",
        content="New implementation approach using FastAPI.\nImproved error handling.\n",
        section="## Implementation",
    )

    assert isinstance(result, str)
    assert "Edited note (replace_section)" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: specs/API Specification.md" in result
    assert "Replaced content under section '## Implementation'" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_edit_note_nonexistent_note_find_replace(client, test_project):
    """Test find_replace on a note that doesn't exist - should return helpful guidance."""
    result = await edit_note(
        project=test_project.name,
        identifier="nonexistent/note",
        operation="find_replace",
        content="replacement",
        find_text="old text",
    )

    assert isinstance(result, str)
    assert "# Edit Failed" in result
    assert "search_notes" in result  # Should suggest searching
    assert "append" in result  # Should suggest using append/prepend instead


@pytest.mark.asyncio
async def test_edit_note_nonexistent_note_replace_section(client, test_project):
    """Test replace_section on a note that doesn't exist - should return helpful guidance."""
    result = await edit_note(
        project=test_project.name,
        identifier="nonexistent/note",
        operation="replace_section",
        content="new section content",
        section="## Missing Section",
    )

    assert isinstance(result, str)
    assert "# Edit Failed" in result
    assert "search_notes" in result  # Should suggest searching


@pytest.mark.asyncio
async def test_edit_note_append_creates_note_if_not_found(client, test_project):
    """append to a non-existent note should create it automatically."""
    result = await edit_note(
        project=test_project.name,
        identifier="auto-created-note",
        operation="append",
        content="# New Note\n\nCreated via append.",
    )

    assert isinstance(result, str)
    assert "Created note (append)" in result
    assert "fileCreated: true" in result
    assert f"project: {test_project.name}" in result


@pytest.mark.asyncio
async def test_edit_note_prepend_creates_note_if_not_found(client, test_project):
    """prepend to a non-existent note should create it automatically."""
    result = await edit_note(
        project=test_project.name,
        identifier="auto-created-prepend",
        operation="prepend",
        content="# Prepended Note\n\nCreated via prepend.",
    )

    assert isinstance(result, str)
    assert "Created note (prepend)" in result
    assert "fileCreated: true" in result
    assert f"project: {test_project.name}" in result


@pytest.mark.asyncio
async def test_edit_note_append_creates_with_directory_from_identifier(client, test_project):
    """Identifier 'conversations/my-note' should create in conversations/ directory."""
    result = await edit_note(
        project=test_project.name,
        identifier="conversations/my-note",
        operation="append",
        content="# My Note\n\nCreated in conversations directory.",
    )

    assert isinstance(result, str)
    assert "Created note (append)" in result
    assert "fileCreated: true" in result
    assert "conversations/" in result


@pytest.mark.asyncio
async def test_edit_note_append_creates_at_root_when_no_directory(client, test_project):
    """Identifier 'my-note' (no slash) should create at project root."""
    result = await edit_note(
        project=test_project.name,
        identifier="root-level-note",
        operation="append",
        content="# Root Note\n\nCreated at root.",
    )

    assert isinstance(result, str)
    assert "Created note (append)" in result
    assert "fileCreated: true" in result


@pytest.mark.asyncio
async def test_edit_note_append_creates_json_format(client, test_project):
    """JSON output should include fileCreated: true when note is auto-created."""
    result = await edit_note(
        project=test_project.name,
        identifier="json-auto-create",
        operation="append",
        content="# JSON Test\n\nAuto-created.",
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["fileCreated"] is True
    assert result["title"] is not None
    assert result["operation"] == "append"


@pytest.mark.asyncio
async def test_edit_note_existing_note_json_includes_file_created_false(client, test_project):
    """JSON output for editing an existing note should include fileCreated: false."""
    # Create the note first
    await write_note(
        project=test_project.name,
        title="Existing JSON Note",
        directory="test",
        content="# Existing Note\nOriginal content.",
    )

    result = await edit_note(
        project=test_project.name,
        identifier="test/existing-json-note",
        operation="append",
        content="\nAppended content.",
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["fileCreated"] is False
    assert result["title"] == "Existing JSON Note"
    assert result["operation"] == "append"


@pytest.mark.asyncio
async def test_edit_note_invalid_operation(client, test_project):
    """Test using an invalid operation."""
    # Create a note first
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test\nContent here.",
    )

    with pytest.raises(ValueError) as exc_info:
        await edit_note(
            project=test_project.name,
            identifier="test/test-note",
            operation="invalid_op",
            content="Some content",
        )

    assert "Invalid operation 'invalid_op'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_edit_note_find_replace_missing_find_text(client, test_project):
    """Test find_replace operation without find_text parameter."""
    # Create a note first
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test\nContent here.",
    )

    with pytest.raises(ValueError) as exc_info:
        await edit_note(
            project=test_project.name,
            identifier="test/test-note",
            operation="find_replace",
            content="replacement",
        )

    assert "find_text parameter is required for find_replace operation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_edit_note_replace_section_missing_section(client, test_project):
    """Test replace_section operation without section parameter."""
    # Create a note first
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test\nContent here.",
    )

    with pytest.raises(ValueError) as exc_info:
        await edit_note(
            project=test_project.name,
            identifier="test/test-note",
            operation="replace_section",
            content="new content",
        )

    assert "section parameter is required for section-based operations" in str(exc_info.value)


@pytest.mark.asyncio
async def test_edit_note_replace_section_nonexistent_section(client, test_project):
    """Test replacing a section that doesn't exist - should append it."""
    # Create initial note without the target section
    await write_note(
        project=test_project.name,
        title="Document",
        directory="docs",
        content="# Document\n\n## Existing Section\nSome content here.",
    )

    # Try to replace non-existent section
    result = await edit_note(
        project=test_project.name,
        identifier="docs/document",
        operation="replace_section",
        content="New section content here.\n",
        section="## New Section",
    )

    assert isinstance(result, str)
    assert "Edited note (replace_section)" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: docs/Document.md" in result
    assert f"[Session: Using project '{test_project.name}']" in result
    # Should succeed - the section gets appended if it doesn't exist


@pytest.mark.asyncio
async def test_edit_note_with_observations_and_relations(client, test_project):
    """Test editing a note that contains observations and relations."""
    # Create note with semantic content
    await write_note(
        project=test_project.name,
        title="Feature Spec",
        directory="features",
        content="# Feature Spec\n\n- [design] Initial design thoughts #architecture\n- implements [[Base System]]\n\nOriginal content.",
    )

    # Append more semantic content
    result = await edit_note(
        project=test_project.name,
        identifier="features/feature-spec",
        operation="append",
        content="\n## Updates\n\n- [implementation] Added new feature #development\n- relates_to [[User Guide]]",
    )

    assert isinstance(result, str)
    assert "Edited note (append)" in result
    assert "## Observations" in result
    assert "## Relations" in result


@pytest.mark.asyncio
async def test_edit_note_identifier_variations(client, test_project):
    """Test that various identifier formats work."""
    # Create a note
    await write_note(
        project=test_project.name,
        title="Test Document",
        directory="docs",
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
            project=test_project.name,
            identifier=identifier,
            operation="append",
            content=f"\n## Update via {identifier}",
        )

        assert isinstance(result, str)
        assert "Edited note (append)" in result
        assert f"project: {test_project.name}" in result
        assert "file_path: docs/Test Document.md" in result


@pytest.mark.asyncio
async def test_edit_note_find_replace_no_matches(client, test_project):
    """Test find_replace when the find_text doesn't exist - should return error."""
    # Create initial note
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test Note\nSome content here.",
    )

    # Try to replace text that doesn't exist - should fail with default expected_replacements=1
    result = await edit_note(
        project=test_project.name,
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
async def test_edit_note_empty_content_operations(client, test_project):
    """Test operations with empty content."""
    # Create initial note
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test Note\nOriginal content.",
    )

    # Test append with empty content
    result = await edit_note(
        project=test_project.name, identifier="test/test-note", operation="append", content=""
    )

    assert isinstance(result, str)
    assert "Edited note (append)" in result
    # Should still work, just adding empty content


@pytest.mark.asyncio
async def test_edit_note_find_replace_wrong_count(client, test_project):
    """Test find_replace when replacement count doesn't match expected."""
    # Create initial note with version info
    await write_note(
        project=test_project.name,
        title="Config Document",
        directory="config",
        content="# Configuration\nVersion: v0.12.0\nSettings for v0.12.0 release.",
    )

    # Try to replace expecting 1 occurrence, but there are actually 2
    result = await edit_note(
        project=test_project.name,
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
async def test_edit_note_replace_section_multiple_sections(client, test_project):
    """Test replace_section with multiple sections having same header - should return helpful error."""
    # Create note with duplicate section headers
    await write_note(
        project=test_project.name,
        title="Sample Note",
        directory="docs",
        content="# Main Title\n\n## Section 1\nFirst instance\n\n## Section 2\nSome content\n\n## Section 1\nSecond instance",
    )

    # Try to replace section when multiple exist
    result = await edit_note(
        project=test_project.name,
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
async def test_edit_note_find_replace_empty_find_text(client, test_project):
    """Test find_replace with empty/whitespace find_text - should return helpful error."""
    # Create initial note
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test Note\nSome content here.",
    )

    # Try with whitespace-only find_text - this should be caught by service validation
    result = await edit_note(
        project=test_project.name,
        identifier="test/test-note",
        operation="find_replace",
        content="replacement",
        find_text="   ",  # whitespace only
    )

    assert isinstance(result, str)
    assert "# Edit Failed" in result
    # Should contain helpful guidance about the error


@pytest.mark.asyncio
async def test_edit_note_append_with_null_optional_fields(client, test_project):
    """Regression test: MCP clients may send explicit null for unused optional fields.

    When an MCP client sends find_text=None, section=None, expected_replacements=None
    for an append operation, the tool should accept them without validation errors.
    """
    # Create initial note
    await write_note(
        project=test_project.name,
        title="Null Fields Test",
        directory="test",
        content="# Null Fields Test\nOriginal content.",
    )

    # Call edit_note with explicit None for all optional fields (simulates MCP null)
    result = await edit_note(
        project=test_project.name,
        identifier="test/null-fields-test",
        operation="append",
        content="\nAppended content.",
        find_text=None,
        section=None,
        expected_replacements=None,
    )

    assert isinstance(result, str)
    assert "Edited note (append)" in result
    assert f"project: {test_project.name}" in result
    assert "file_path: test/Null Fields Test.md" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_edit_note_preserves_permalink_when_frontmatter_missing(client, test_project):
    """Test that editing a note preserves the permalink when frontmatter doesn't contain one.

    This is a regression test for issue #170 where edit_note would fail with a validation error
    because the permalink was being set to None when the markdown file didn't have a permalink
    in its frontmatter.
    """
    # Create initial note
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test Note\nOriginal content here.",
    )

    # Verify the note was created with a permalink
    first_result = await edit_note(
        project=test_project.name,
        identifier="test/test-note",
        operation="append",
        content="\nFirst edit.",
    )

    assert isinstance(first_result, str)
    assert f"permalink: {test_project.name}/test/test-note" in first_result

    # Perform another edit - this should preserve the permalink even if the
    # file doesn't have a permalink in its frontmatter
    second_result = await edit_note(
        project=test_project.name,
        identifier="test/test-note",
        operation="append",
        content="\nSecond edit.",
    )

    assert isinstance(second_result, str)
    assert "Edited note (append)" in second_result
    assert f"project: {test_project.name}" in second_result
    assert f"permalink: {test_project.name}/test/test-note" in second_result
    assert f"[Session: Using project '{test_project.name}']" in second_result
    # The edit should succeed without validation errors


@pytest.mark.asyncio
async def test_edit_note_insert_before_section_operation(client, test_project):
    """Test inserting content before a section heading."""
    # Create initial note with sections
    await write_note(
        project=test_project.name,
        title="Insert Before Doc",
        directory="docs",
        content="# Doc\n\n## Overview\nOverview content.\n\n## Details\nDetail content.",
    )

    result = await edit_note(
        project=test_project.name,
        identifier="docs/insert-before-doc",
        operation="insert_before_section",
        content="--- inserted divider ---",
        section="## Details",
    )

    assert isinstance(result, str)
    assert "Edited note (insert_before_section)" in result
    assert f"project: {test_project.name}" in result
    assert "Inserted content before section '## Details'" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_edit_note_insert_after_section_operation(client, test_project):
    """Test inserting content after a section heading."""
    # Create initial note with sections
    await write_note(
        project=test_project.name,
        title="Insert After Doc",
        directory="docs",
        content="# Doc\n\n## Overview\nOverview content.\n\n## Details\nDetail content.",
    )

    result = await edit_note(
        project=test_project.name,
        identifier="docs/insert-after-doc",
        operation="insert_after_section",
        content="Inserted after overview heading",
        section="## Overview",
    )

    assert isinstance(result, str)
    assert "Edited note (insert_after_section)" in result
    assert f"project: {test_project.name}" in result
    assert "Inserted content after section '## Overview'" in result
    assert f"[Session: Using project '{test_project.name}']" in result


@pytest.mark.asyncio
async def test_edit_note_insert_before_section_missing_section(client, test_project):
    """Test insert_before_section without section parameter raises ValueError."""
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test\nContent here.",
    )

    with pytest.raises(ValueError, match="section parameter is required"):
        await edit_note(
            project=test_project.name,
            identifier="test/test-note",
            operation="insert_before_section",
            content="new content",
        )


@pytest.mark.asyncio
async def test_edit_note_insert_before_section_not_found(client, test_project):
    """Test insert_before_section when section doesn't exist returns error."""
    await write_note(
        project=test_project.name,
        title="Test Note",
        directory="test",
        content="# Test\n\n## Existing\nContent here.",
    )

    result = await edit_note(
        project=test_project.name,
        identifier="test/test-note",
        operation="insert_before_section",
        content="new content",
        section="## Nonexistent",
    )

    assert isinstance(result, str)
    assert "# Edit Failed" in result
