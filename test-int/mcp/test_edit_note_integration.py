"""
Integration tests for edit_note MCP tool.

Tests the complete edit note workflow: MCP client -> MCP server -> FastAPI -> database
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_edit_note_append_operation(mcp_server, app):
    """Test appending content to an existing note."""

    async with Client(mcp_server) as client:
        # First create a note
        await client.call_tool(
            "write_note",
            {
                "title": "Append Test Note",
                "folder": "test",
                "content": "# Append Test Note\n\nOriginal content here.",
                "tags": "test,append",
            },
        )

        # Test appending content
        edit_result = await client.call_tool(
            "edit_note",
            {
                "identifier": "Append Test Note",
                "operation": "append",
                "content": "\n\n## New Section\n\nThis content was appended.",
            },
        )

        # Should return successful edit summary
        assert len(edit_result) == 1
        edit_text = edit_result[0].text
        assert "Edited note (append)" in edit_text
        assert "Added 5 lines to end of note" in edit_text
        assert "test/append-test-note" in edit_text

        # Verify the content was actually appended
        read_result = await client.call_tool(
            "read_note",
            {
                "identifier": "Append Test Note",
            },
        )

        content = read_result[0].text
        assert "Original content here." in content
        assert "## New Section" in content
        assert "This content was appended." in content


@pytest.mark.asyncio
async def test_edit_note_prepend_operation(mcp_server, app):
    """Test prepending content to an existing note."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "title": "Prepend Test Note",
                "folder": "test",
                "content": "# Prepend Test Note\n\nExisting content.",
                "tags": "test,prepend",
            },
        )

        # Test prepending content
        edit_result = await client.call_tool(
            "edit_note",
            {
                "identifier": "test/prepend-test-note",
                "operation": "prepend",
                "content": "## Important Update\n\nThis was added at the top.\n\n",
            },
        )

        # Should return successful edit summary
        assert len(edit_result) == 1
        edit_text = edit_result[0].text
        assert "Edited note (prepend)" in edit_text
        assert "Added 5 lines to beginning of note" in edit_text

        # Verify the content was prepended after frontmatter
        read_result = await client.call_tool(
            "read_note",
            {
                "identifier": "test/prepend-test-note",
            },
        )

        content = read_result[0].text
        assert "## Important Update" in content
        assert "This was added at the top." in content
        assert "Existing content." in content
        # Check that prepended content comes before existing content
        prepend_pos = content.find("Important Update")
        existing_pos = content.find("Existing content")
        assert prepend_pos < existing_pos


@pytest.mark.asyncio
async def test_edit_note_find_replace_operation(mcp_server, app):
    """Test find and replace operation on an existing note."""

    async with Client(mcp_server) as client:
        # Create a note with content to replace
        await client.call_tool(
            "write_note",
            {
                "title": "Find Replace Test",
                "folder": "test",
                "content": """# Find Replace Test

This is version v1.0.0 of the system.

## Notes
- The current version is v1.0.0
- Next version will be v1.1.0

## Changes
v1.0.0 introduces new features.""",
                "tags": "test,version",
            },
        )

        # Test find and replace operation (expecting 3 replacements)
        edit_result = await client.call_tool(
            "edit_note",
            {
                "identifier": "Find Replace Test",
                "operation": "find_replace",
                "content": "v1.2.0",
                "find_text": "v1.0.0",
                "expected_replacements": 3,
            },
        )

        # Should return successful edit summary
        assert len(edit_result) == 1
        edit_text = edit_result[0].text
        assert "Edited note (find_replace)" in edit_text
        assert "Find and replace operation completed" in edit_text

        # Verify the replacements were made
        read_result = await client.call_tool(
            "read_note",
            {
                "identifier": "Find Replace Test",
            },
        )

        content = read_result[0].text
        assert "v1.2.0" in content
        assert "v1.0.0" not in content  # Should be completely replaced
        assert content.count("v1.2.0") == 3  # Should have exactly 3 occurrences


@pytest.mark.asyncio
async def test_edit_note_replace_section_operation(mcp_server, app):
    """Test replacing content under a specific section header."""

    async with Client(mcp_server) as client:
        # Create a note with sections
        await client.call_tool(
            "write_note",
            {
                "title": "Section Replace Test",
                "folder": "test",
                "content": """# Section Replace Test

## Overview
Original overview content.

## Implementation
Old implementation details here.
This will be replaced.

## Future Work
Some future work notes.""",
                "tags": "test,section",
            },
        )

        # Test replacing section content
        edit_result = await client.call_tool(
            "edit_note",
            {
                "identifier": "test/section-replace-test",
                "operation": "replace_section",
                "content": """New implementation approach using microservices.

- Service A handles authentication
- Service B manages data processing
- Service C provides API endpoints

All services communicate via message queues.""",
                "section": "## Implementation",
            },
        )

        # Should return successful edit summary
        assert len(edit_result) == 1
        edit_text = edit_result[0].text
        assert "Edited note (replace_section)" in edit_text
        assert "Replaced content under section '## Implementation'" in edit_text

        # Verify the section was replaced
        read_result = await client.call_tool(
            "read_note",
            {
                "identifier": "Section Replace Test",
            },
        )

        content = read_result[0].text
        assert "New implementation approach using microservices" in content
        assert "Old implementation details here" not in content
        assert "Service A handles authentication" in content
        # Other sections should remain unchanged
        assert "Original overview content" in content
        assert "Some future work notes" in content


@pytest.mark.asyncio
async def test_edit_note_with_observations_and_relations(mcp_server, app):
    """Test editing a note that has observations and relations, and verify they're updated."""

    async with Client(mcp_server) as client:
        # Create a complex note with observations and relations
        complex_content = """# API Documentation

The API provides REST endpoints for data access.

## Observations
- [feature] User authentication endpoints
- [tech] Built with FastAPI framework
- [status] Currently in beta testing

## Relations  
- implements [[Authentication System]]
- documented_in [[API Guide]]
- depends_on [[Database Schema]]

## Endpoints
Current endpoints include user management."""

        await client.call_tool(
            "write_note",
            {
                "title": "API Documentation",
                "folder": "docs",
                "content": complex_content,
                "tags": "api,docs",
            },
        )

        # Add new content with observations and relations
        new_content = """
## New Features
- [feature] Added payment processing endpoints
- [feature] Implemented rate limiting
- [security] Added OAuth2 authentication

## Additional Relations
- integrates_with [[Payment Gateway]]
- secured_by [[OAuth2 Provider]]"""

        edit_result = await client.call_tool(
            "edit_note",
            {
                "identifier": "API Documentation",
                "operation": "append",
                "content": new_content,
            },
        )

        # Should return edit summary with observation and relation counts
        assert len(edit_result) == 1
        edit_text = edit_result[0].text
        assert "Edited note (append)" in edit_text
        assert "## Observations" in edit_text
        assert "## Relations" in edit_text
        # Should have feature, tech, status, security categories
        assert "feature:" in edit_text
        assert "security:" in edit_text
        assert "tech:" in edit_text
        assert "status:" in edit_text

        # Verify the content was added and processed
        read_result = await client.call_tool(
            "read_note",
            {
                "identifier": "API Documentation",
            },
        )

        content = read_result[0].text
        assert "Added payment processing endpoints" in content
        assert "integrates_with [[Payment Gateway]]" in content


@pytest.mark.asyncio
async def test_edit_note_error_handling_note_not_found(mcp_server, app):
    """Test error handling when trying to edit a non-existent note."""

    async with Client(mcp_server) as client:
        # Try to edit a note that doesn't exist
        edit_result = await client.call_tool(
            "edit_note",
            {
                "identifier": "Non-existent Note",
                "operation": "append",
                "content": "Some content to add",
            },
        )

        # Should return helpful error message
        assert len(edit_result) == 1
        error_text = edit_result[0].text
        assert "Edit Failed - Note Not Found" in error_text
        assert "Non-existent Note" in error_text
        assert "search_notes(" in error_text
        assert "Suggestions to try:" in error_text


@pytest.mark.asyncio
async def test_edit_note_error_handling_text_not_found(mcp_server, app):
    """Test error handling when find_text is not found in the note."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "title": "Error Test Note",
                "folder": "test",
                "content": "# Error Test Note\n\nThis note has specific content.",
                "tags": "test,error",
            },
        )

        # Try to replace text that doesn't exist
        edit_result = await client.call_tool(
            "edit_note",
            {
                "identifier": "Error Test Note",
                "operation": "find_replace",
                "content": "replacement text",
                "find_text": "non-existent text",
            },
        )

        # Should return helpful error message
        assert len(edit_result) == 1
        error_text = edit_result[0].text
        assert "Edit Failed - Text Not Found" in error_text
        assert "non-existent text" in error_text
        assert "Error Test Note" in error_text
        assert "read_note(" in error_text


@pytest.mark.asyncio
async def test_edit_note_error_handling_wrong_replacement_count(mcp_server, app):
    """Test error handling when expected_replacements doesn't match actual occurrences."""

    async with Client(mcp_server) as client:
        # Create a note with specific repeated text
        await client.call_tool(
            "write_note",
            {
                "title": "Count Test Note",
                "folder": "test",
                "content": """# Count Test Note

The word "test" appears here.
This is another test sentence.
Final test of the content.""",
                "tags": "test,count",
            },
        )

        # Try to replace "test" but expect wrong count (should be 3, not 5)
        edit_result = await client.call_tool(
            "edit_note",
            {
                "identifier": "Count Test Note",
                "operation": "find_replace",
                "content": "example",
                "find_text": "test",
                "expected_replacements": 5,
            },
        )

        # Should return helpful error message about count mismatch
        assert len(edit_result) == 1
        error_text = edit_result[0].text
        assert "Edit Failed - Wrong Replacement Count" in error_text
        assert "Expected 5 occurrences" in error_text
        assert "test" in error_text
        assert "expected_replacements=" in error_text


@pytest.mark.asyncio
async def test_edit_note_invalid_operation(mcp_server, app):
    """Test error handling for invalid operation parameter."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "title": "Invalid Op Test",
                "folder": "test",
                "content": "# Invalid Op Test\n\nSome content.",
                "tags": "test",
            },
        )

        # Try to use an invalid operation - this should raise a ToolError
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "edit_note",
                {
                    "identifier": "Invalid Op Test",
                    "operation": "invalid_operation",
                    "content": "Some content",
                },
            )

        # Should contain information about invalid operation
        error_message = str(exc_info.value)
        assert "Invalid operation 'invalid_operation'" in error_message
        assert "append, prepend, find_replace, replace_section" in error_message


@pytest.mark.asyncio
async def test_edit_note_missing_required_parameters(mcp_server, app):
    """Test error handling when required parameters are missing."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "title": "Param Test Note",
                "folder": "test",
                "content": "# Param Test Note\n\nContent here.",
                "tags": "test",
            },
        )

        # Try find_replace without find_text parameter - this should raise a ToolError
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "edit_note",
                {
                    "identifier": "Param Test Note",
                    "operation": "find_replace",
                    "content": "replacement",
                    # Missing find_text parameter
                },
            )

        # Should contain information about missing parameter
        error_message = str(exc_info.value)
        assert "find_text parameter is required for find_replace operation" in error_message


@pytest.mark.asyncio
async def test_edit_note_special_characters_in_content(mcp_server, app):
    """Test editing notes with special characters, Unicode, and markdown formatting."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "title": "Special Chars Test",
                "folder": "test",
                "content": "# Special Chars Test\n\nBasic content here.",
                "tags": "test,unicode",
            },
        )

        # Add content with special characters and Unicode
        special_content = """
## Unicode Section 🚀

This section contains:
- Emojis: 🎉 💡 ⚡ 🔥 
- Languages: 测试中文 Tëst Übër
- Math symbols: ∑∏∂∇∆Ω ≠≤≥ ∞
- Special markdown: `code` **bold** *italic*
- URLs: https://example.com/path?param=value&other=123
- Code blocks:
```python
def test_function():
    return "Hello, 世界!"
```

## Observations
- [unicode] Unicode characters preserved ✓
- [markdown] Formatting maintained 📝

## Relations
- documented_in [[Unicode Standards]]"""

        edit_result = await client.call_tool(
            "edit_note",
            {
                "identifier": "Special Chars Test",
                "operation": "append",
                "content": special_content,
            },
        )

        # Should successfully handle special characters
        assert len(edit_result) == 1
        edit_text = edit_result[0].text
        assert "Edited note (append)" in edit_text
        assert "## Observations" in edit_text
        assert "unicode:" in edit_text
        assert "markdown:" in edit_text

        # Verify the special content was added correctly
        read_result = await client.call_tool(
            "read_note",
            {
                "identifier": "Special Chars Test",
            },
        )

        content = read_result[0].text
        assert "🚀" in content
        assert "测试中文" in content
        assert "∑∏∂∇∆Ω" in content
        assert "def test_function():" in content
        assert "[[Unicode Standards]]" in content


@pytest.mark.asyncio
async def test_edit_note_using_different_identifiers(mcp_server, app):
    """Test editing notes using different identifier formats (title, permalink, folder/title)."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "title": "Identifier Test Note",
                "folder": "docs",
                "content": "# Identifier Test Note\n\nOriginal content.",
                "tags": "test,identifier",
            },
        )

        # Test editing by title
        edit_result1 = await client.call_tool(
            "edit_note",
            {
                "identifier": "Identifier Test Note",  # by title
                "operation": "append",
                "content": "\n\nEdited by title.",
            },
        )
        assert "Edited note (append)" in edit_result1[0].text

        # Test editing by permalink
        edit_result2 = await client.call_tool(
            "edit_note",
            {
                "identifier": "docs/identifier-test-note",  # by permalink
                "operation": "append",
                "content": "\n\nEdited by permalink.",
            },
        )
        assert "Edited note (append)" in edit_result2[0].text

        # Test editing by folder/title format
        edit_result3 = await client.call_tool(
            "edit_note",
            {
                "identifier": "docs/Identifier Test Note",  # by folder/title
                "operation": "append",
                "content": "\n\nEdited by folder/title.",
            },
        )
        assert "Edited note (append)" in edit_result3[0].text

        # Verify all edits were applied
        read_result = await client.call_tool(
            "read_note",
            {
                "identifier": "docs/identifier-test-note",
            },
        )

        content = read_result[0].text
        assert "Edited by title." in content
        assert "Edited by permalink." in content
        assert "Edited by folder/title." in content
