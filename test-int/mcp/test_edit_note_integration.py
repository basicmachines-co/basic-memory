"""
Integration tests for edit_note MCP tool.

Tests the complete edit note workflow: MCP client -> MCP server -> FastAPI -> database
"""

import json
from pathlib import Path

import pytest
from fastmcp import Client

from basic_memory import file_utils
from basic_memory.file_utils import FileWriteError, parse_frontmatter
from basic_memory.index.note_content_materialization import drain_pending_materializations
from basic_memory.mcp.clients import KnowledgeClient
from basic_memory.repository.note_content_repository import (
    NoteContentRepository,
    NoteContentVersionConflict,
)


@pytest.mark.asyncio
async def test_edit_note_append_operation(mcp_server, app, test_project):
    """Test appending content to an existing note."""

    async with Client(mcp_server) as client:
        # First create a note
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Append Test Note",
                "directory": "test",
                "content": "# Append Test Note\n\nOriginal content here.",
                "tags": "test,append",
            },
        )

        # Test appending content
        edit_result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Append Test Note",
                "command": "append",
                "content": "\n\n## New Section\n\nThis content was appended.",
            },
        )

        # Should return successful edit summary
        assert len(edit_result.content) == 1
        edit_text = edit_result.content[0].text
        assert "(append)" in edit_text
        assert "test/append-test-note" in edit_text

        # Verify the content was actually appended
        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "Append Test Note",
                "include_frontmatter": True,
            },
        )

        content = read_result.content[0].text
        assert "Original content here." in content
        assert "## New Section" in content
        assert "This content was appended." in content


@pytest.mark.asyncio
async def test_edit_note_reports_real_checksum(mcp_server, app, test_project):
    """edit_note must report the persisted checksum, not a permanent "unknown" (#1586).

    A real SHA-256 checksum is computed and stored for every edit; the MCP
    response must surface it instead of always falling back to "unknown".
    """

    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Checksum Probe",
                "directory": "probe",
                "content": "# Checksum Probe\n\nOriginal content.",
            },
        )

        edit_result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Checksum Probe",
                "command": "append",
                "content": "\n\nAppended content.",
            },
        )

        edit_text = edit_result.content[0].text
        assert "checksum: unknown" not in edit_text
        checksum_line = next(
            line for line in edit_text.splitlines() if line.startswith("checksum: ")
        )
        reported_checksum = checksum_line.removeprefix("checksum: ")
        assert len(reported_checksum) == 64
        assert all(c in "0123456789abcdef" for c in reported_checksum)


@pytest.mark.asyncio
async def test_edit_note_prepend_operation(mcp_server, app, test_project):
    """Test prepending content to an existing note."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Prepend Test Note",
                "directory": "test",
                "content": "# Prepend Test Note\n\nExisting content.",
                "tags": "test,prepend",
            },
        )

        # Test prepending content
        edit_result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "test/prepend-test-note",
                "command": "prepend",
                "content": "## Important Update\n\nThis was added at the top.\n\n",
            },
        )

        # Should return successful edit summary
        assert len(edit_result.content) == 1
        edit_text = edit_result.content[0].text
        assert "(prepend)" in edit_text

        # Verify the content was prepended after frontmatter
        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "test/prepend-test-note",
                "include_frontmatter": True,
            },
        )

        content = read_result.content[0].text
        assert "## Important Update" in content
        assert "This was added at the top." in content
        assert "Existing content." in content
        # Check that prepended content comes before existing content
        prepend_pos = content.find("Important Update")
        existing_pos = content.find("Existing content")
        assert prepend_pos < existing_pos


@pytest.mark.asyncio
async def test_edit_note_find_replace_operation(mcp_server, app, test_project):
    """Test find and replace operation on an existing note."""

    async with Client(mcp_server) as client:
        # Create a note with content to replace
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Find Replace Test",
                "directory": "test",
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
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Find Replace Test",
                "command": "find_replace",
                "content": "v1.2.0",
                "find_text": "v1.0.0",
                "expected_replacements": 3,
            },
        )

        # Should return successful edit summary
        assert len(edit_result.content) == 1
        edit_text = edit_result.content[0].text
        assert "(find_replace)" in edit_text

        # Verify the replacements were made
        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "Find Replace Test",
                "include_frontmatter": True,
            },
        )

        content = read_result.content[0].text
        assert "v1.2.0" in content
        assert "v1.0.0" not in content  # Should be completely replaced
        assert content.count("v1.2.0") == 3  # Should have exactly 3 occurrences


@pytest.mark.asyncio
async def test_edit_note_replace_section_operation(mcp_server, app, test_project):
    """Test replacing content under a specific section header."""

    async with Client(mcp_server) as client:
        # Create a note with sections
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Section Replace Test",
                "directory": "test",
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
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "test/section-replace-test",
                "command": "replace_section",
                "content": """New implementation approach using microservices.

- Service A handles authentication
- Service B manages data processing
- Service C provides API endpoints

All services communicate via message queues.""",
                "section": "## Implementation",
            },
        )

        # Should return successful edit summary
        assert len(edit_result.content) == 1
        edit_text = edit_result.content[0].text
        assert "(replace_section)" in edit_text

        # Verify the section was replaced
        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "Section Replace Test",
                "include_frontmatter": True,
            },
        )

        content = read_result.content[0].text
        assert "New implementation approach using microservices" in content
        assert "Old implementation details here" not in content
        assert "Service A handles authentication" in content
        # Other sections should remain unchanged
        assert "Original overview content" in content
        assert "Some future work notes" in content


@pytest.mark.asyncio
async def test_edit_note_with_observations_and_relations(mcp_server, app, test_project):
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
            "bm_write",
            {
                "project": test_project.name,
                "title": "API Documentation",
                "directory": "docs",
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
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "API Documentation",
                "command": "append",
                "content": new_content,
            },
        )

        assert len(edit_result.content) == 1
        edit_text = edit_result.content[0].text
        assert "(append)" in edit_text
        assert "checksum:" in edit_text
        assert "observations:" not in edit_text
        assert "relations:" not in edit_text

        # Verify the content was added and processed
        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "API Documentation",
                "include_frontmatter": True,
            },
        )

        content = read_result.content[0].text
        assert "Added payment processing endpoints" in content
        assert "integrates_with [[Payment Gateway]]" in content

        await drain_pending_materializations()
        graph = await client.call_tool(
            "bm_context", {"identifier": "API Documentation", "project": test_project.name}
        )
        assert isinstance(graph.data, dict)
        (note,) = graph.data["results"]
        assert len(note["observations"]) == 6
        assert {row["category"] for row in note["observations"]} == {
            "feature",
            "tech",
            "status",
            "security",
        }
        assert sum(row["type"] == "relation" for row in note["related_results"]) == 5


@pytest.mark.asyncio
async def test_edit_note_error_handling_note_not_found(mcp_server, app, test_project):
    """Test error handling when using find_replace on a non-existent note."""

    async with Client(mcp_server) as client:
        # find_replace on a non-existent note should still error
        edit_result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Non-existent Note",
                "command": "find_replace",
                "content": "replacement",
                "find_text": "old text",
            },
            raise_on_error=False,
        )

        # A failed edit is an MCP error result, and its text keeps the guidance
        assert edit_result.is_error is True
        assert len(edit_result.content) == 1
        error_text = edit_result.content[0].text
        assert "NOT_FOUND" in error_text
        assert "Non-existent Note" in error_text
        assert "bm_write(" in error_text


@pytest.mark.asyncio
async def test_edit_note_rejects_blank_metadata_type(mcp_server, app, test_project):
    """A writable type field must reject an empty note classification."""
    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Type Validation Note",
                "directory": "notes",
                "content": "Original body.",
            },
        )

        edit_result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "notes/type-validation-note",
                "command": "append",
                "content": "",
                "metadata": {"type": ""},
            },
            raise_on_error=False,
        )

        # A failed edit is an MCP error result, and its text keeps the guidance
        assert edit_result.is_error is True
        assert "INVALID_ARGUMENTS" in edit_result.content[0].text
        assert "at least 1 item" in edit_result.content[0].text
        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "notes/type-validation-note",
                "include_frontmatter": True,
            },
        )
        assert parse_frontmatter(read_result.content[0].text)["type"] == "note"


@pytest.mark.asyncio
async def test_edit_note_error_handling_text_not_found(mcp_server, app, test_project):
    """Test error handling when find_text is not found in the note."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Error Test Note",
                "directory": "test",
                "content": "# Error Test Note\n\nThis note has specific content.",
                "tags": "test,error",
            },
        )

        # Try to replace text that doesn't exist
        edit_result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Error Test Note",
                "command": "find_replace",
                "content": "replacement text",
                "find_text": "non-existent text",
            },
            raise_on_error=False,
        )

        # A failed edit is an MCP error result, and its text keeps the guidance
        assert edit_result.is_error is True
        assert len(edit_result.content) == 1
        error_text = edit_result.content[0].text
        assert "INVALID_ARGUMENTS" in error_text
        assert "non-existent text" in error_text
        assert "Error Test Note" in error_text
        assert "bm_read(" in error_text


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_edit_note_refused_concurrent_write_is_an_error(
    mcp_server, app, test_project, monkeypatch, output_format
):
    """An edit the engine refuses with a 409 must reach the client as an MCP error.

    Two writers racing on one note can make the db_version compare-and-set refuse the
    second edit. Forcing that conflict deterministically: the refused append changed
    nothing, so a result that is not is_error would tell the caller it was written.
    """

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Refused Edit Note",
                "directory": "test",
                "content": "# Refused Edit Note\n\nOriginal body.",
                "output_format": "json",
            },
        )
        note = json.loads(created.content[0].text)
        path = Path(test_project.path) / note["file_path"]
        original = path.read_text(encoding="utf-8")

        async def lose_the_race(self, session, write):
            raise NoteContentVersionConflict(f"db_version advanced for {write.entity_id}")

        monkeypatch.setattr(NoteContentRepository, "accept_write", lose_the_race)

        edit_result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": note["permalink"],
                "command": "append",
                "content": "\nThis append was refused.",
                "output_format": output_format,
            },
            raise_on_error=False,
        )

        assert edit_result.is_error is True
        text = edit_result.content[0].text
        assert "modified concurrently" in text
        if output_format == "json":
            payload = json.loads(text)
            assert payload["error"] == "REVISION_CONFLICT"
        else:
            assert "REVISION_CONFLICT" in text
        assert path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_edit_note_accepted_write_is_not_reported_as_failed_when_file_write_fails(
    mcp_server, app, test_project, monkeypatch
):
    """An edit the engine accepted is reported as written, even if writing the file fails.

    The accepted content commits before the markdown file is written. On Windows the
    atomic replace fails when another request holds the file open, and the test
    runtime writes the file inside the request, so that failure used to come back as
    an error for an append that was already in the note. A caller that retries an
    error would then append it twice.
    """

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "File Write Fails Note",
                "directory": "test",
                "content": "# File Write Fails Note\n\nOriginal body.",
                "output_format": "json",
            },
        )
        note = json.loads(created.content[0].text)

        async def replace_is_refused(path, content):
            raise FileWriteError(f"Failed to write file {path}: [WinError 5] Access is denied")

        with monkeypatch.context() as patched:
            patched.setattr(file_utils, "write_file_atomic_bytes", replace_is_refused)
            edit_result = await client.call_tool(
                "bm_edit",
                {
                    "project": test_project.name,
                    "identifier": note["permalink"],
                    "command": "append",
                    "content": "\nThis append was accepted.",
                    "output_format": "json",
                },
                raise_on_error=False,
            )

        assert edit_result.is_error is False, edit_result.content[0].text
        payload = json.loads(edit_result.content[0].text)
        assert "error" not in payload

        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": note["permalink"],
                "output_format": "json",
                "include_frontmatter": True,
            },
        )
        assert "This append was accepted." in json.loads(read_result.content[0].text)["content"]


@pytest.mark.asyncio
async def test_edit_note_error_handling_wrong_replacement_count(mcp_server, app, test_project):
    """Test error handling when expected_replacements doesn't match actual occurrences."""

    async with Client(mcp_server) as client:
        # Create a note with specific repeated text
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Count Test Note",
                "directory": "test",
                "content": """# Count Test Note

The word "test" appears here.
This is another test sentence.
Final test of the content.""",
                "tags": "test,count",
            },
        )

        # Try to replace "test" but expect wrong count (should be 3, not 5)
        edit_result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Count Test Note",
                "command": "find_replace",
                "content": "example",
                "find_text": "test",
                "expected_replacements": 5,
            },
            raise_on_error=False,
        )

        # A failed edit is an MCP error result, and its text keeps the guidance
        assert edit_result.is_error is True
        assert len(edit_result.content) == 1
        error_text = edit_result.content[0].text
        assert "INVALID_ARGUMENTS" in error_text
        assert "Expected 5 occurrences" in error_text
        assert "test" in error_text
        assert "bm_read" in error_text


@pytest.mark.asyncio
async def test_edit_note_invalid_operation(mcp_server, app, test_project):
    """Test error handling for invalid operation parameter."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Invalid Op Test",
                "directory": "test",
                "content": "# Invalid Op Test\n\nSome content.",
                "tags": "test",
            },
        )

        # Try to use an invalid operation - this should raise a ToolError
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "bm_edit",
                {
                    "project": test_project.name,
                    "identifier": "Invalid Op Test",
                    "command": "invalid_operation",
                    "content": "Some content",
                },
            )

        # Should contain information about invalid operation
        error_message = str(exc_info.value)
        assert "literal_error" in error_message
        assert "find_replace" in error_message


@pytest.mark.asyncio
async def test_edit_note_missing_required_parameters(mcp_server, app, test_project):
    """Test error handling when required parameters are missing."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Param Test Note",
                "directory": "test",
                "content": "# Param Test Note\n\nContent here.",
                "tags": "test",
            },
        )

        # Try find_replace without find_text parameter - this should raise a ToolError
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "bm_edit",
                {
                    "project": test_project.name,
                    "identifier": "Param Test Note",
                    "command": "find_replace",
                    "content": "replacement",
                },
            )

        # Should contain information about missing parameter
        error_message = str(exc_info.value)
        assert "find_replace needs `find_text`" in error_message


@pytest.mark.asyncio
async def test_edit_note_special_characters_in_content(mcp_server, app, test_project):
    """Test editing notes with special characters, Unicode, and markdown formatting."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Special Chars Test",
                "directory": "test",
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
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Special Chars Test",
                "command": "append",
                "content": special_content,
            },
        )

        # Should successfully handle special characters
        assert len(edit_result.content) == 1
        edit_text = edit_result.content[0].text
        assert "(append)" in edit_text
        assert "checksum:" in edit_text
        assert "observations:" not in edit_text

        # Verify the special content was added correctly
        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "Special Chars Test",
                "include_frontmatter": True,
            },
        )

        content = read_result.content[0].text
        assert "🚀" in content
        assert "测试中文" in content
        assert "∑∏∂∇∆Ω" in content
        assert "def test_function():" in content
        assert "[[Unicode Standards]]" in content

        await drain_pending_materializations()
        graph = await client.call_tool(
            "bm_context", {"identifier": "Special Chars Test", "project": test_project.name}
        )
        assert isinstance(graph.data, dict)
        (note,) = graph.data["results"]
        assert {row["category"] for row in note["observations"]} == {"unicode", "markdown"}
        assert sum(row["type"] == "relation" for row in note["related_results"]) == 1


@pytest.mark.asyncio
async def test_edit_note_using_different_identifiers(mcp_server, app, test_project):
    """Test editing notes using different identifier formats (title, permalink, folder/title)."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Identifier Test Note",
                "directory": "docs",
                "content": "# Identifier Test Note\n\nOriginal content.",
                "tags": "test,identifier",
            },
        )

        # Test editing by title
        edit_result1 = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Identifier Test Note",
                "command": "append",
                "content": "\n\nEdited by title.",
            },
        )
        assert "(append)" in edit_result1.content[0].text

        # Test editing by permalink
        edit_result2 = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "docs/identifier-test-note",
                "command": "append",
                "content": "\n\nEdited by permalink.",
            },
        )
        assert "(append)" in edit_result2.content[0].text

        # Test editing by folder/title format
        edit_result3 = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "docs/Identifier Test Note",
                "command": "append",
                "content": "\n\nEdited by folder/title.",
            },
        )
        assert "(append)" in edit_result3.content[0].text

        # Verify all edits were applied
        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "docs/identifier-test-note",
                "include_frontmatter": True,
            },
        )

        content = read_result.content[0].text
        assert "Edited by title." in content
        assert "Edited by permalink." in content
        assert "Edited by folder/title." in content


@pytest.mark.asyncio
async def test_edit_note_missing_target_does_not_fuzzy_match(mcp_server, app, test_project):
    """Reproduces #649: edit_note append must auto-create, not fuzzy-match to an existing note.

    Creates two notes, then attempts to append to a nonexistent identifier.
    The tool should create a new note, and neither existing note should be modified.
    """

    async with Client(mcp_server) as client:
        # Create two notes that could be fuzzy-matched
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Routing Test A",
                "directory": "test",
                "content": "# Routing Test A\n\nContent A.",
            },
        )
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Routing Test B",
                "directory": "test",
                "content": "# Routing Test B\n\nContent B.",
            },
        )

        # Attempt to edit a nonexistent note — should error, not silently edit A or B
        edit_result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Routing Test NONEXISTENT",
                "command": "append",
                "content": "\n\nThis should NOT appear in any note.",
            },
            raise_on_error=False,
        )
        assert edit_result.is_error

        edit_text = edit_result.content[0].text
        # append to nonexistent creates a new note — verify it did NOT edit A or B
        assert "NOT_FOUND" in edit_text

        # Verify neither A nor B was modified
        read_a = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "Routing Test A",
                "include_frontmatter": True,
            },
        )
        content_a = read_a.content[0].text
        assert "Content A" in content_a
        assert "This should NOT appear" not in content_a

        read_b = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "Routing Test B",
                "include_frontmatter": True,
            },
        )
        content_b = read_b.content[0].text
        assert "Content B" in content_b
        assert "This should NOT appear" not in content_b

        # Now test find_replace on nonexistent — should error
        edit_result2 = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "Routing Test NONEXISTENT AGAIN",
                "command": "find_replace",
                "content": "replaced",
                "find_text": "Content",
            },
            raise_on_error=False,
        )

        assert edit_result2.is_error is True
        error_text = edit_result2.content[0].text
        assert "NOT_FOUND" in error_text


@pytest.mark.asyncio
async def test_edit_note_unindexed_file_is_not_modified(mcp_server, app, test_project):
    note_path = Path(test_project.path) / "direct/disk-note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    original = "# Disk Note\n\nstatus: draft\n"
    note_path.write_text(original)
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "bm_edit",
            {
                "identifier": "direct/disk-note",
                "command": "find_replace",
                "find_text": "draft",
                "content": "final",
                "project": test_project.name,
                "output_format": "json",
            },
            raise_on_error=False,
        )
        assert result.is_error
        assert json.loads(result.content[0].text)["error"] == "NOT_FOUND"
    assert note_path.read_text() == original


@pytest.mark.asyncio
async def test_edit_note_metadata_merges_frontmatter(mcp_server, app, test_project):
    """metadata merges frontmatter fields independent of `operation` (issue #1011)."""

    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Metadata Merge Ticket",
                "directory": "tickets",
                "content": "# Metadata Merge Ticket\n\nTicket body.",
                "metadata": {"status": "open", "opened_at": "2026-06-18T09:14:00Z"},
            },
        )

        edit_result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "tickets/metadata-merge-ticket",
                "command": "append",
                "content": "\n\nResolution notes.",
                "metadata": {"status": "resolved", "closed_at": "2026-06-18T10:42:00Z"},
            },
        )

        edit_text = edit_result.content[0].text
        assert "(append)" in edit_text

        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "tickets/metadata-merge-ticket",
                "include_frontmatter": True,
            },
        )
        content = read_result.content[0].text

        # New key added, existing key overwritten, unrelated frontmatter and body preserved.
        frontmatter = parse_frontmatter(content)
        assert frontmatter["status"] == "resolved"
        assert frontmatter["closed_at"] == "2026-06-18T10:42:00Z"
        assert frontmatter["opened_at"] == "2026-06-18T09:14:00Z"
        assert "Ticket body." in content
        assert "Resolution notes." in content


@pytest.mark.asyncio
async def test_edit_note_metadata_rejects_identity_fields(mcp_server, app, test_project):
    """title/permalink in `metadata` are ignored rather than hijacking the note's identity."""

    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Identity Guard Note",
                "directory": "tickets",
                "content": "# Identity Guard Note\n\nBody.",
            },
        )

        refused = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "tickets/identity-guard-note",
                "command": "append",
                "content": "",
                "metadata": {
                    "title": "Hijacked Title",
                    "permalink": "hijacked/permalink",
                    "status": "resolved",
                },
            },
            raise_on_error=False,
        )
        assert refused.is_error
        assert "INVALID_ARGUMENTS" in refused.content[0].text

        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "tickets/identity-guard-note",
                "include_frontmatter": True,
            },
        )
        content = read_result.content[0].text
        frontmatter = parse_frontmatter(content)

        assert "status" not in frontmatter
        assert frontmatter["title"] == "Identity Guard Note"
        # permalink is preserved, not hijacked to the metadata value. Assert on the note's
        # own slug rather than the exact string, since the harness prefixes the project name.
        assert frontmatter["permalink"] != "hijacked/permalink"
        assert frontmatter["permalink"].endswith("tickets/identity-guard-note")
        assert frontmatter["type"] == "note"
        assert "status: draft" not in content


@pytest.mark.asyncio
async def test_edit_note_metadata_sets_note_type(mcp_server, app, test_project):
    """`type` in `metadata` reaches both the file's frontmatter and the indexed entity."""

    async with Client(mcp_server) as client:
        await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Type Change Note",
                "directory": "tickets",
                "content": "# Type Change Note\n\nBody.",
            },
        )

        await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "tickets/type-change-note",
                "command": "append",
                "content": "",
                "metadata": {"type": "decision"},
            },
        )

        read_result = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "tickets/type-change-note",
                "include_frontmatter": True,
            },
        )
        content = read_result.content[0].text
        assert parse_frontmatter(content)["type"] == "decision"
        assert "Body." in content

        # The index has to agree with the file, otherwise the note reverts on the next sync.
        search_result = await client.call_tool(
            "bm_search",
            {"project": test_project.name, "query": "Type Change Note", "note_types": ["decision"]},
        )
        assert "tickets/Type Change Note.md" in search_result.content[0].text


@pytest.mark.asyncio
async def test_edit_note_metadata_null_values_rejected_before_auto_create(
    mcp_server, app, test_project
):
    """Null metadata values fail up front — including on the append auto-create path.

    Without the tool-level guard an auto-created note would be written with a
    YAML null that indexing silently filters out of entity_metadata.
    """
    async with Client(mcp_server) as client:
        with pytest.raises(Exception) as exc_info:
            await client.call_tool(
                "bm_edit",
                {
                    "project": test_project.name,
                    "identifier": "conversations/never-created",
                    "command": "append",
                    "content": "",
                    "metadata": {"status": None},
                },
            )

        error_message = str(exc_info.value)
        assert "removing keys is not supported" in error_message
        assert "status" in error_message


@pytest.mark.asyncio
async def test_edit_note_conflict_preserves_the_winning_write(
    mcp_server, app, test_project, monkeypatch
):
    """A stale edit cannot overwrite the winning retitle (#1719)."""
    async with Client(mcp_server) as client:
        created = await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Old Edit Title",
                "directory": "test",
                "content": "# Old Edit Title\n\nBody.",
                "output_format": "json",
            },
        )
        revision_a = json.loads(created.content[0].text)["checksum"]

        original_patch = KnowledgeClient.patch_entity
        retitled = False

        async def retitle_before_patch(self, entity_id, patch_data, *, base_checksum=None):
            # Another writer retitles the note (through its H1) after edit_note
            # resolved it but before the guarded PATCH lands.
            nonlocal retitled
            if not retitled:
                retitled = True
                await original_patch(
                    self,
                    entity_id,
                    {
                        "operation": "find_replace",
                        "find_text": "# Old Edit Title",
                        "content": "# New Edit Title",
                    },
                )
            return await original_patch(self, entity_id, patch_data, base_checksum=base_checksum)

        monkeypatch.setattr(KnowledgeClient, "patch_entity", retitle_before_patch)
        result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "test/old-edit-title",
                "command": "append",
                "content": "\nStale edit.",
                "expected_checksum": revision_a,
                "output_format": "json",
            },
            raise_on_error=False,
        )

        assert result.is_error is True
        payload = json.loads(result.content[0].text)
        assert payload["error"] == "REVISION_CONFLICT"
        read = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "test/Old Edit Title.md",
                "output_format": "json",
            },
        )
        current = json.loads(read.content[0].text)
        assert "# New Edit Title" in current["content"]
        assert "Stale edit." not in current["content"]
        assert current["checksum"] != revision_a
        assert current["checksum"] in payload["message"]


@pytest.mark.asyncio
async def test_edit_note_expected_checksum_edits_only_the_revision_read(
    mcp_server, app, test_project
):
    """A checksum-guarded edit lands on the read revision and refuses a stale one (#1552)."""
    async with Client(mcp_server) as client:
        created = await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Guarded Edit",
                "directory": "test",
                "content": "Revision A.",
                "output_format": "json",
            },
        )
        revision_a = json.loads(created.content[0].text)["checksum"]

        # A guarded edit on the current revision lands and moves the note to B.
        updated = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "test/guarded-edit",
                "command": "append",
                "content": "\nRevision B.",
                "expected_checksum": revision_a,
                "output_format": "json",
            },
        )
        revision_b = json.loads(updated.content[0].text)["checksum"]
        assert revision_b != revision_a

        # An edit still conditioned on A is refused and names B as the current revision.
        stale_json = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "test/guarded-edit",
                "command": "append",
                "content": "\nStale edit.",
                "expected_checksum": revision_a,
                "output_format": "json",
            },
            raise_on_error=False,
        )
        assert stale_json.is_error is True
        payload = json.loads(stale_json.content[0].text)
        assert payload["error"] == "REVISION_CONFLICT"

        # Same shape as write_note's conflict (#1719): the note is named, nothing written.

        stale_text = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "test/guarded-edit",
                "command": "find_replace",
                "find_text": "Revision A.",
                "content": "Stale replacement.",
                "expected_checksum": revision_a,
            },
            raise_on_error=False,
        )
        assert stale_text.is_error is True
        text = stale_text.content[0].text
        assert "REVISION_CONFLICT" in text
        assert revision_b in text

        read = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "test/guarded-edit",
                "include_frontmatter": True,
            },
        )
        content = read.content[0].text
        assert "Revision A." in content
        assert "Revision B." in content
        assert "Stale" not in content


@pytest.mark.asyncio
async def test_edit_note_expected_checksum_never_auto_creates(mcp_server, app, test_project):
    """A precondition on a missing note is a conflict, not an append auto-create."""
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": "test/never-written",
                "command": "append",
                "content": "Should not exist.",
                "expected_checksum": "a" * 64,
                "output_format": "json",
            },
            raise_on_error=False,
        )
        assert result.is_error is True
        payload = json.loads(result.content[0].text)
        assert payload["error"] == "REVISION_CONFLICT"

        missing = await client.call_tool(
            "bm_read",
            {
                "project": test_project.name,
                "identifier": "test/never-written.md",
                "output_format": "json",
                "include_frontmatter": True,
            },
            raise_on_error=False,
        )
        assert missing.is_error
        assert json.loads(missing.content[0].text)["error"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_edit_note_expected_checksum_never_recovers_a_deleted_note_from_disk(
    mcp_server, app, test_project, monkeypatch
):
    """A guarded miss must not index a file that outlived its note's delete.

    The delete commits before its file cleanup runs. Disk recovery would index
    that stale file under the caller's own checksum, the precondition would
    accept it, and the edit would resurrect the deleted note.
    """
    index_calls: list[str] = []
    index_file = KnowledgeClient.index_file

    async def record_index_file(self, file_path):
        index_calls.append(file_path)
        return await index_file(self, file_path)

    monkeypatch.setattr(KnowledgeClient, "index_file", record_index_file)

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            "bm_write",
            {
                "project": test_project.name,
                "title": "Deleted Then Edited",
                "directory": "test",
                "content": "# Deleted Then Edited\n\nOriginal body.",
                "output_format": "json",
            },
        )
        note = json.loads(created.content[0].text)
        path = Path(test_project.path) / note["file_path"]
        stale_bytes = path.read_bytes()

        await client.call_tool(
            "bm_delete",
            {"project": test_project.name, "identifier": note["permalink"]},
        )
        # The file cleanup has not run yet: the deleted note's bytes are still on disk.
        path.write_bytes(stale_bytes)

        result = await client.call_tool(
            "bm_edit",
            {
                "project": test_project.name,
                "identifier": note["file_path"],
                "command": "append",
                "content": "\nResurrected?",
                "expected_checksum": note["checksum"],
                "output_format": "json",
            },
            raise_on_error=False,
        )

    assert result.is_error is True
    payload = json.loads(result.content[0].text)
    assert payload["error"] == "REVISION_CONFLICT"

    assert index_calls == []
    assert path.read_bytes() == stale_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["append", "prepend"])
@pytest.mark.parametrize("metadata", [None, {"type": "decision"}])
async def test_edit_missing_note_never_creates(mcp_server, app, test_project, command, metadata):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "bm_edit",
            {
                "identifier": "notes/missing.md",
                "command": command,
                "content": "Never created.",
                "metadata": metadata,
                "project": test_project.name,
                "output_format": "json",
            },
            raise_on_error=False,
        )
        assert result.is_error
        assert json.loads(result.content[0].text)["error"] == "NOT_FOUND"
        read = await client.call_tool(
            "bm_read",
            {
                "identifier": "notes/missing.md",
                "project": test_project.name,
                "output_format": "json",
            },
            raise_on_error=False,
        )
        assert read.is_error
        assert json.loads(read.content[0].text)["error"] == "NOT_FOUND"
        assert not (Path(test_project.path) / "notes/missing.md").exists()
