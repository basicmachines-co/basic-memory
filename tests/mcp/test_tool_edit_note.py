"""Tests for the edit_note MCP tool."""

from unittest.mock import patch

import pytest

from basic_memory.mcp.tools.edit_note import edit_note
from basic_memory.mcp.tools.read_note import read_note
from basic_memory.mcp.tools.write_note import write_note


def test_edit_note_workspace_project_route_helper():
    """workspace/project routing should be explicit and deterministic."""
    import importlib

    edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")

    assert (
        edit_note_module._compose_workspace_project_route(
            workspace=None,
            project="docs/setup",
            project_id=None,
        )
        == "docs/setup"
    )
    assert (
        edit_note_module._compose_workspace_project_route(
            workspace="docs",
            project="setup",
            project_id=None,
        )
        == "docs/setup"
    )


@pytest.mark.parametrize(
    ("route_kwargs", "message"),
    [
        (
            {"workspace": " ", "project": "setup", "project_id": None},
            "workspace must not be empty",
        ),
        (
            {"workspace": "docs/setup", "project": "setup", "project_id": None},
            "workspace must be a single workspace",
        ),
        (
            {"workspace": "docs", "project": "setup", "project_id": "project-id"},
            "workspace cannot be combined with project_id",
        ),
        (
            {"workspace": "docs", "project": None, "project_id": None},
            "workspace requires an explicit project",
        ),
        (
            {"workspace": "docs", "project": "setup/install", "project_id": None},
            "not both",
        ),
    ],
)
def test_edit_note_workspace_project_route_helper_rejects_invalid_inputs(
    route_kwargs,
    message,
):
    """Ambiguous workspace/project argument combinations should fail before routing."""
    import importlib

    edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")

    with pytest.raises(ValueError, match=message):
        edit_note_module._compose_workspace_project_route(**route_kwargs)


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
async def test_edit_note_find_replace_rejects_fuzzy_match(client, test_project):
    """find_replace must reject nonexistent identifiers, not fuzzy-match to a similar note."""
    # Create two notes that could be fuzzy-matched
    await write_note(
        project=test_project.name,
        title="Routing Test A",
        directory="test",
        content="# Routing Test A\nContent A.",
    )
    await write_note(
        project=test_project.name,
        title="Routing Test B",
        directory="test",
        content="# Routing Test B\nContent B.",
    )

    # Attempt to edit a nonexistent note — should error, not silently edit A or B
    result = await edit_note(
        project=test_project.name,
        identifier="Routing Test NONEXISTENT",
        operation="find_replace",
        content="replaced",
        find_text="Content",
    )

    assert isinstance(result, str)
    assert "# Edit Failed" in result

    # Verify neither A nor B was modified
    content_a = await read_note("Routing Test A", project=test_project.name)
    assert "Content A" in content_a
    content_b = await read_note("Routing Test B", project=test_project.name)
    assert "Content B" in content_b


@pytest.mark.asyncio
async def test_edit_note_append_autocreate_not_fuzzy_match(client, test_project):
    """append to a nonexistent note should auto-create it, not fuzzy-match an existing note."""
    await write_note(
        project=test_project.name,
        title="Existing Note Alpha",
        directory="test",
        content="# Existing Note Alpha\nOriginal content.",
    )

    # Append to a nonexistent note — should create a new note, not edit "Existing Note Alpha"
    result = await edit_note(
        project=test_project.name,
        identifier="Existing Note ZZZZZ",
        operation="append",
        content="# New Note\nBrand new content.",
    )

    assert isinstance(result, str)
    assert "Created note (append)" in result
    assert "fileCreated: true" in result

    # Verify original note was NOT modified
    content = await read_note("Existing Note Alpha", project=test_project.name)
    assert "Original content" in content
    assert "Brand new content" not in content


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


@pytest.mark.asyncio
async def test_edit_note_detects_project_from_memory_url(client, test_project):
    """edit_note should detect project from memory:// URL prefix when project=None."""
    # Create a note first
    await write_note(
        project=test_project.name,
        title="URL Detection Note",
        directory="test",
        content="# URL Detection Note\nOriginal content.",
    )

    # Edit using memory:// URL with project=None — should auto-detect project
    # The memory URL uses the permalink (which includes project prefix)
    result = await edit_note(
        identifier=f"memory://{test_project.name}/test/url-detection-note",
        operation="append",
        content="\nAppended via memory URL.",
        project=None,
    )

    assert isinstance(result, str)
    # Should route to the correct project and succeed (either edit or create)
    assert f"project: {test_project.name}" in result


@pytest.mark.asyncio
async def test_edit_note_workspace_qualified_memory_url_keeps_complete_permalink(
    client,
    test_project,
):
    from basic_memory.workspace_context import workspace_permalink_context

    expected_permalink = f"team-paul/{test_project.name}/team/tool-edit-note"

    with workspace_permalink_context(workspace_slug="team-paul", workspace_type="organization"):
        await write_note(
            project=test_project.name,
            title="Tool Edit Note",
            directory="team",
            content="# Tool Edit Note\nOriginal content.",
        )

        result = await edit_note(
            project=test_project.name,
            identifier=f"memory://{expected_permalink}",
            operation="append",
            content="\nAppended in team workspace.",
        )

    assert isinstance(result, str)
    assert "Edited note (append)" in result
    assert f"permalink: {expected_permalink}" in result


@pytest.mark.asyncio
async def test_edit_note_workspace_qualified_plain_permalink_requires_explicit_route(
    monkeypatch,
    test_project,
):
    """Plain workspace-qualified write identifiers should stop before mutating."""
    import importlib
    from contextlib import asynccontextmanager

    edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")
    workspace_slug = "team-acme"
    qualified_identifier = f"{workspace_slug}/{test_project.name}/team/plain-edit-note"
    detected_identifiers: list[str] = []

    async def detect_workspace_project(identifier, config, context=None):
        detected_identifiers.append(identifier)
        return f"{workspace_slug}/{test_project.name}"

    @asynccontextmanager
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("ambiguous plain identifiers should not select a project client")
        yield

    monkeypatch.setattr(
        edit_note_module,
        "_workspace_identifier_discovery_available",
        lambda identifier, config: True,
    )
    monkeypatch.setattr(
        edit_note_module,
        "detect_project_from_workspace_identifier_prefix",
        detect_workspace_project,
        raising=False,
    )
    monkeypatch.setattr(edit_note_module, "get_project_client", fail_if_called)

    result = await edit_note(
        identifier=qualified_identifier,
        operation="append",
        content="\nAppended via plain workspace-qualified permalink.",
        project=None,
    )

    assert detected_identifiers == [qualified_identifier]
    assert isinstance(result, str)
    assert "# Edit Failed - Ambiguous Identifier" in result
    assert f"`{qualified_identifier}` could refer to a local note path" in result
    assert f'project="{workspace_slug}/{test_project.name}"' in result
    assert f"memory://{qualified_identifier}" in result


@pytest.mark.asyncio
async def test_edit_note_workspace_qualified_plain_permalink_json_error(
    monkeypatch,
    test_project,
):
    """Ambiguous plain write identifiers should stay machine-readable in JSON mode."""
    import importlib

    edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")
    workspace_slug = "team-acme"
    qualified_identifier = f"{workspace_slug}/{test_project.name}/team/plain-edit-note"

    async def detect_workspace_project(identifier, config, context=None):
        assert identifier == qualified_identifier
        return f"{workspace_slug}/{test_project.name}"

    monkeypatch.setattr(
        edit_note_module,
        "_workspace_identifier_discovery_available",
        lambda identifier, config: True,
    )
    monkeypatch.setattr(
        edit_note_module,
        "detect_project_from_workspace_identifier_prefix",
        detect_workspace_project,
        raising=False,
    )

    result = await edit_note(
        identifier=qualified_identifier,
        operation="append",
        content="\nAppended via plain workspace-qualified permalink.",
        output_format="json",
        project=None,
    )

    assert isinstance(result, dict)
    assert result["error"] == "AMBIGUOUS_IDENTIFIER"
    assert result["project"] == f"{workspace_slug}/{test_project.name}"
    assert result["fileCreated"] is False


@pytest.mark.asyncio
async def test_edit_note_ambiguous_namespace_identifier_returns_guidance(
    monkeypatch,
    test_project,
):
    """Namespace-style workspace identifiers should still return ambiguity guidance."""
    import importlib

    edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")
    workspace_slug = "team-acme"
    identifier = f"{workspace_slug}::{test_project.name}/team/plain-edit-note"
    normalized_identifier = f"{workspace_slug}/{test_project.name}/team/plain-edit-note"

    async def detect_workspace_project(raw_identifier, config, context=None):
        assert raw_identifier == identifier
        return f"{workspace_slug}/{test_project.name}"

    monkeypatch.setattr(
        edit_note_module,
        "_workspace_identifier_discovery_available",
        lambda identifier, config: True,
    )
    monkeypatch.setattr(
        edit_note_module,
        "detect_project_from_workspace_identifier_prefix",
        detect_workspace_project,
        raising=False,
    )

    result = await edit_note(
        identifier=identifier,
        operation="append",
        content="\nAppended via namespace-style plain identifier.",
        project=None,
    )

    assert isinstance(result, str)
    assert "# Edit Failed - Ambiguous Identifier" in result
    assert f"`{identifier}` could refer to a local note path" in result
    assert f"memory://{normalized_identifier}" in result


@pytest.mark.asyncio
async def test_edit_note_workspace_project_args_compose_explicit_route(monkeypatch):
    """workspace plus project should route like project='workspace/project'."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import importlib

    edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")
    captured_routes: list[tuple[str | None, str | None]] = []

    @asynccontextmanager
    async def fake_get_project_client(project, context=None, project_id=None):
        captured_routes.append((project, project_id))
        yield object(), SimpleNamespace(name="setup")

    monkeypatch.setattr(edit_note_module, "get_project_client", fake_get_project_client)

    with pytest.raises(ValueError, match="Invalid operation"):
        await edit_note(
            identifier="install",
            operation="invalid",
            content="content",
            workspace="docs",
            project="setup",
        )

    assert captured_routes == [("docs/setup", None)]


@pytest.mark.asyncio
async def test_edit_note_plain_workspace_route_returns_guidance_with_local_config(
    monkeypatch,
    config_manager,
    test_project,
):
    """Mixed local+cloud configs should still stop ambiguous plain write routes."""
    from contextlib import asynccontextmanager
    import importlib

    import basic_memory.mcp.project_context as project_context
    from basic_memory.config import ProjectEntry
    from basic_memory.mcp.project_context import (
        WorkspaceProjectEntry,
        _build_workspace_project_index,
    )
    from basic_memory.schemas.cloud import WorkspaceInfo
    from basic_memory.schemas.project_info import ProjectItem

    edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")
    config = config_manager.load_config()
    config.projects["hermes-memory"] = ProjectEntry(
        path=str(config_manager.config_dir.parent / "hermes-memory")
    )
    config.cloud_api_key = "bmc_test123"
    config_manager.save_config(config)

    personal = WorkspaceInfo(
        tenant_id="personal-tenant",
        workspace_type="personal",
        slug="personal",
        name="Personal",
        role="owner",
        is_default=True,
    )
    index = _build_workspace_project_index(
        (personal,),
        (
            WorkspaceProjectEntry(
                workspace=personal,
                project=ProjectItem(
                    id=1,
                    external_id="11111111-1111-1111-1111-111111111111",
                    name="main",
                    path="/tmp/main",
                    is_default=False,
                ),
            ),
        ),
    )

    async def fake_index(context=None):
        return index

    @asynccontextmanager
    async def fail_if_called(*args, **kwargs):
        raise AssertionError("ambiguous plain identifiers should not select a project client")
        yield

    monkeypatch.setattr(project_context, "_ensure_workspace_project_index", fake_index)
    monkeypatch.setattr("basic_memory.mcp.async_client.is_factory_mode", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._explicit_routing", lambda: False)
    monkeypatch.setattr("basic_memory.mcp.async_client._force_local_mode", lambda: False)
    monkeypatch.setattr(edit_note_module, "get_project_client", fail_if_called)

    result = await edit_note(
        identifier="personal/main/team/plain-edit-note",
        operation="append",
        content="\nAppended via plain workspace-qualified permalink.",
        project=None,
    )

    assert isinstance(result, str)
    assert "# Edit Failed - Ambiguous Identifier" in result
    assert 'project="personal/main"' in result


@pytest.mark.asyncio
async def test_edit_note_three_segment_plain_path_stays_local_without_workspace_discovery(
    monkeypatch,
    client,
    test_project,
):
    """Three-segment local paths should stay on the active project without discovery."""
    import importlib

    edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")

    await write_note(
        project=test_project.name,
        title="Local Three Segment",
        directory="folder/subdir",
        content="# Local Three Segment\nOriginal content.",
    )

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("local three-segment paths should not trigger workspace detection")

    monkeypatch.setattr(
        edit_note_module,
        "_workspace_identifier_discovery_available",
        lambda identifier, config: False,
    )
    monkeypatch.setattr(
        edit_note_module,
        "detect_project_from_workspace_identifier_prefix",
        fail_if_called,
    )

    result = await edit_note(
        identifier="folder/subdir/local-three-segment",
        operation="append",
        content="\nAppended locally.",
        project=None,
    )

    assert isinstance(result, str)
    assert "Edited note (append)" in result
    assert f"project: {test_project.name}" in result

    updated = await read_note(
        identifier="folder/subdir/local-three-segment",
        project=test_project.name,
    )
    assert "Appended locally." in updated


@pytest.mark.asyncio
async def test_edit_note_skips_detection_for_plain_path(client, test_project):
    """edit_note should NOT call detect_project_from_url_prefix for plain path identifiers.

    A plain path like 'research/note' should not be misrouted to a project
    named 'research' — the 'research' segment is a directory, not a project.
    """
    with (
        patch("basic_memory.mcp.tools.edit_note.detect_project_from_memory_url_prefix") as mock_url,
        patch(
            "basic_memory.mcp.tools.edit_note.detect_project_from_workspace_identifier_prefix"
        ) as mock_workspace,
    ):
        # Use a plain path (no memory:// prefix) — detection should not be called
        await edit_note(
            identifier="test/some-note",
            operation="append",
            content="content",
            project=None,
        )

        mock_url.assert_not_called()
        mock_workspace.assert_not_called()


@pytest.mark.asyncio
async def test_edit_note_skips_detection_when_project_provided(client, test_project):
    """edit_note should skip URL detection when project is explicitly provided."""
    with (
        patch("basic_memory.mcp.tools.edit_note.detect_project_from_memory_url_prefix") as mock_url,
        patch(
            "basic_memory.mcp.tools.edit_note.detect_project_from_workspace_identifier_prefix"
        ) as mock_workspace,
    ):
        await edit_note(
            identifier=f"memory://{test_project.name}/test/some-note",
            operation="append",
            content="content",
            project=test_project.name,
        )

        mock_url.assert_not_called()
        mock_workspace.assert_not_called()


@pytest.mark.asyncio
async def test_edit_note_skips_detection_when_project_id_provided(
    monkeypatch,
    client,
    test_project,
):
    """project_id is authoritative, so memory URL discovery must not run first."""
    await write_note(
        project=test_project.name,
        title="Project ID Memory URL Edit",
        directory="test",
        content="# Project ID Memory URL Edit\nOriginal content.",
    )

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("project_id routing should bypass URL discovery")

    import importlib

    edit_note_module = importlib.import_module("basic_memory.mcp.tools.edit_note")
    monkeypatch.setattr(edit_note_module, "detect_project_from_memory_url_prefix", fail_if_called)
    monkeypatch.setattr(
        edit_note_module,
        "detect_project_from_workspace_identifier_prefix",
        fail_if_called,
    )

    result = await edit_note(
        identifier=f"memory://{test_project.name}/test/project-id-memory-url-edit",
        project_id=test_project.external_id,
        operation="append",
        content="\nAppended via project_id.",
    )

    assert isinstance(result, str)
    assert "Edited note (append)" in result
    assert f"project: {test_project.name}" in result
