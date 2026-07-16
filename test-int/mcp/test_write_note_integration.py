"""
Integration tests for write_note MCP tool.

Comprehensive tests covering all scenarios including note creation, content formatting,
tag handling, error conditions, and edge cases from bug reports.
"""

import json
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from fastmcp import Client

from basic_memory.config import ConfigManager
from basic_memory.schemas.project_info import ProjectItem


def _json_content(tool_result) -> dict[str, Any]:
    """Parse a FastMCP tool result content block into a JSON object."""
    assert len(tool_result.content) == 1
    assert tool_result.content[0].type == "text"
    payload = json.loads(tool_result.content[0].text)  # pyright: ignore [reportAttributeAccessIssue]
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_write_note_basic_creation(mcp_server, app, test_project):
    """Test creating a simple note with basic content."""

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Simple Note",
                "directory": "basic",
                "content": "# Simple Note\n\nThis is a simple note for testing.",
                "tags": "simple,test",
            },
        )

        assert len(result.content) == 1
        assert result.content[0].type == "text"
        response_text = result.content[0].text

        assert "# Created note" in response_text
        assert f"project: {test_project.name}" in response_text
        assert "file_path: basic/Simple Note.md" in response_text
        assert f"permalink: {test_project.name}/basic/simple-note" in response_text
        assert "## Tags" in response_text
        assert "- simple, test" in response_text
        assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_write_note_no_tags(mcp_server, app, test_project):
    """Test creating a note without tags."""

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "No Tags Note",
                "directory": "test",
                "content": "Just some plain text without tags.",
            },
        )

        assert len(result.content) == 1
        assert result.content[0].type == "text"
        response_text = result.content[0].text

        assert "# Created note" in response_text
        assert "file_path: test/No Tags Note.md" in response_text
        assert f"permalink: {test_project.name}/test/no-tags-note" in response_text
        # Should not have tags section when no tags provided


@pytest.mark.asyncio
async def test_write_note_update_existing(mcp_server, app, test_project):
    """Test updating an existing note."""

    async with Client(mcp_server) as client:
        # Create initial note
        result1 = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Update Test",
                "directory": "test",
                "content": "# Update Test\n\nOriginal content.",
                "tags": "original",
            },
        )

        assert "# Created note" in result1.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Update the same note (explicit overwrite)
        result2 = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Update Test",
                "directory": "test",
                "content": "# Update Test\n\nUpdated content with changes.",
                "tags": "updated,modified",
                "overwrite": True,
            },
        )

        assert len(result2.content) == 1
        assert result2.content[0].type == "text"
        response_text = result2.content[0].text

        assert "# Updated note" in response_text
        assert f"project: {test_project.name}" in response_text
        assert "file_path: test/Update Test.md" in response_text
        assert f"permalink: {test_project.name}/test/update-test" in response_text
        assert "- updated, modified" in response_text
        assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_write_note_overwrite_resolves_conflict_by_file_path_when_permalink_changes(
    mcp_server, app, test_project, monkeypatch
):
    """Overwrite resolves the conflict by strict file path through the MCP client stack."""
    from basic_memory.mcp.clients import knowledge as knowledge_mod

    original_resolve = knowledge_mod.KnowledgeClient.resolve_entity
    captured_resolve: dict[str, Any] = {}

    async def spy_resolve(self, identifier: str, *, strict: bool = False) -> str:
        captured_resolve["identifier"] = identifier
        captured_resolve["strict"] = strict
        return await original_resolve(self, identifier, strict=strict)

    monkeypatch.setattr(knowledge_mod.KnowledgeClient, "resolve_entity", spy_resolve)

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Overwrite Permalink Change",
                "directory": "overwrite-conflicts",
                "content": "# Overwrite Permalink Change\n\nOriginal body.",
                "output_format": "json",
            },
        )
        created_payload = _json_content(created)
        assert created_payload["permalink"] == (
            f"{test_project.name}/overwrite-conflicts/overwrite-permalink-change"
        )

        replacement = dedent("""
            ---
            permalink: overwrite-conflicts/custom-overwrite-permalink
            ---

            # Overwrite Permalink Change

            Replacement body.
        """).strip()

        updated = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Overwrite Permalink Change",
                "directory": "overwrite-conflicts",
                "content": replacement,
                "overwrite": True,
                "output_format": "json",
            },
        )
        updated_payload = _json_content(updated)
        assert updated_payload["action"] == "updated"
        assert updated_payload["permalink"] == "overwrite-conflicts/custom-overwrite-permalink"
        assert updated_payload["file_path"] == "overwrite-conflicts/Overwrite Permalink Change.md"
        assert captured_resolve == {
            "identifier": "overwrite-conflicts/Overwrite Permalink Change.md",
            "strict": True,
        }

        read_updated = await client.call_tool(
            "read_note",
            {
                "project": test_project.name,
                "identifier": "overwrite-conflicts/custom-overwrite-permalink",
                "output_format": "json",
            },
        )
        read_payload = _json_content(read_updated)
        assert read_payload["permalink"] == "overwrite-conflicts/custom-overwrite-permalink"
        assert "Replacement body." in read_payload["content"]
        assert "Original body." not in read_payload["content"]


@pytest.mark.asyncio
async def test_write_note_tag_array(mcp_server, app, test_project):
    """Test creating a note with tag array (Issue #38 regression test)."""

    async with Client(mcp_server) as client:
        # This reproduces the exact bug from Issue #38
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Array Tags Test",
                "directory": "test",
                "content": "Testing tag array handling",
                "tags": ["python", "testing", "integration", "mcp"],
            },
        )

        assert len(result.content) == 1
        assert result.content[0].type == "text"
        response_text = result.content[0].text

        assert "# Created note" in response_text
        assert f"project: {test_project.name}" in response_text
        assert "file_path: test/Array Tags Test.md" in response_text
        assert f"permalink: {test_project.name}/test/array-tags-test" in response_text
        assert "## Tags" in response_text
        assert "python" in response_text
        assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_write_note_custom_permalink(mcp_server, app, test_project):
    """Test custom permalink handling (Issue #93 regression test)."""

    async with Client(mcp_server) as client:
        content_with_custom_permalink = dedent("""
            ---
            permalink: custom/my-special-permalink
            ---

            # Custom Permalink Note

            This note has a custom permalink in frontmatter.

            - [note] Testing custom permalink preservation
        """).strip()

        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Custom Permalink Note",
                "directory": "notes",
                "content": content_with_custom_permalink,
            },
        )

        assert len(result.content) == 1
        assert result.content[0].type == "text"
        response_text = result.content[0].text

        assert "# Created note" in response_text
        assert f"project: {test_project.name}" in response_text
        assert "file_path: notes/Custom Permalink Note.md" in response_text
        assert "permalink: custom/my-special-permalink" in response_text
        assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_write_note_unicode_content(mcp_server, app, test_project):
    """Test handling Unicode content including emojis."""

    async with Client(mcp_server) as client:
        unicode_content = "# Unicode Test 🚀\n\nThis note has emoji 🎉 and unicode ♠♣♥♦\n\n- [note] Testing unicode handling 测试"

        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Unicode Test 🌟",
                "directory": "test",
                "content": unicode_content,
                "tags": "unicode,emoji,测试",
            },
        )

        assert len(result.content) == 1
        assert result.content[0].type == "text"
        response_text = result.content[0].text

        assert "# Created note" in response_text
        assert f"project: {test_project.name}" in response_text
        assert "file_path: test/Unicode Test 🌟.md" in response_text
        # Permalink should be sanitized
        assert f"permalink: {test_project.name}/test/unicode-test" in response_text
        assert "## Tags" in response_text
        assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_write_note_complex_content_with_observations_relations(
    mcp_server, app, test_project
):
    """Test creating note with complex content including observations and relations."""

    async with Client(mcp_server) as client:
        complex_content = dedent("""
            # Complex Note

            This note demonstrates the full knowledge format.

            ## Observations
            - [tech] Uses Python and FastAPI
            - [design] Follows MCP protocol specification
            - [note] Integration tests are comprehensive

            ## Relations
            - implements [[MCP Protocol]]
            - depends_on [[FastAPI Framework]]
            - tested_by [[Integration Tests]]

            ## Additional Content

            Some more regular markdown content here.
        """).strip()

        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Complex Knowledge Note",
                "directory": "knowledge",
                "content": complex_content,
                "tags": "complex,knowledge,relations",
            },
        )

        assert len(result.content) == 1
        assert result.content[0].type == "text"
        response_text = result.content[0].text

        assert "# Created note" in response_text
        assert f"project: {test_project.name}" in response_text
        assert "file_path: knowledge/Complex Knowledge Note.md" in response_text
        assert f"permalink: {test_project.name}/knowledge/complex-knowledge-note" in response_text

        # Should show observation and relation counts
        assert "## Observations" in response_text
        assert "tech: 1" in response_text
        assert "design: 1" in response_text
        assert "note: 1" in response_text

        assert "## Relations" in response_text
        # Should show outgoing relations

        assert "## Tags" in response_text
        assert "complex, knowledge, relations" in response_text
        assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_write_note_preserve_frontmatter(mcp_server, app, test_project):
    """Test that custom frontmatter is preserved when updating notes."""

    async with Client(mcp_server) as client:
        content_with_frontmatter = dedent("""
            ---
            title: Frontmatter Note
            type: note
            version: 1.0
            author: Test Author
            status: draft
            ---

            # Frontmatter Note

            This note has custom frontmatter that should be preserved.
        """).strip()

        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Frontmatter Note",
                "directory": "test",
                "content": content_with_frontmatter,
                "tags": "frontmatter,preservation",
            },
        )

        assert len(result.content) == 1
        assert result.content[0].type == "text"
        response_text = result.content[0].text

        assert "# Created note" in response_text
        assert f"project: {test_project.name}" in response_text
        assert "file_path: test/Frontmatter Note.md" in response_text
        assert f"permalink: {test_project.name}/test/frontmatter-note" in response_text
        assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_write_note_kebab_filenames_basic(mcp_server, app, test_project, app_config):
    """Test note creation with kebab_filenames=True and invalid filename characters."""

    app_config.kebab_filenames = True
    ConfigManager().save_config(app_config)

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "My Note: With/Invalid|Chars?",
                "directory": "my-folder",
                "content": "Testing kebab-case and invalid characters.",
                "tags": "kebab,invalid,filename",
            },
        )

        assert len(result.content) == 1
        response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # File path and permalink should be kebab-case and sanitized
        assert f"project: {test_project.name}" in response_text
        assert "file_path: my-folder/my-note-with-invalid-chars.md" in response_text
        assert (
            f"permalink: {test_project.name}/my-folder/my-note-with-invalid-chars" in response_text
        )
        assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_write_note_kebab_filenames_repeat_invalid(mcp_server, app, test_project, app_config):
    """Test note creation with multiple invalid and repeated characters."""

    app_config.kebab_filenames = True
    ConfigManager().save_config(app_config)

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": 'Crazy<>:"|?*Note/Name',
                "directory": "my-folder",
                "content": "Should be fully kebab-case and safe.",
                "tags": "crazy,filename,test",
            },
        )

        assert len(result.content) == 1
        response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        assert f"project: {test_project.name}" in response_text
        assert "file_path: my-folder/crazy-note-name.md" in response_text
        assert f"permalink: {test_project.name}/my-folder/crazy-note-name" in response_text
        assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_write_note_rejects_equivalent_legacy_markdown_filename(
    mcp_server,
    app,
    test_project,
    app_config,
):
    """A filename-mode change must not silently create a duplicate note (issue #1077)."""
    app_config.kebab_filenames = True
    app_config.permalinks_include_project = False
    ConfigManager().save_config(app_config)

    async with Client(mcp_server) as client:
        created = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Site Roadmap",
                "directory": "filename-conflicts",
                "content": "# Site Roadmap\n\nLegacy body.",
            },
        )
        assert "# Created note" in created.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "file_path: filename-conflicts/site-roadmap.md" in created.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        app_config.kebab_filenames = False
        ConfigManager().save_config(app_config)

        rejected = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Site Roadmap",
                "directory": "filename-conflicts",
                "content": "# Site Roadmap\n\nReplacement body.",
            },
        )
        rejected_text = rejected.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "# Error: Note already exists" in rejected_text
        assert "edit_note" in rejected_text

    note_paths = sorted(
        path.relative_to(test_project.path).as_posix()
        for path in Path(test_project.path).rglob("*.md")
    )
    assert note_paths == ["filename-conflicts/site-roadmap.md"]


@pytest.mark.asyncio
async def test_write_note_file_path_os_path_join(mcp_server, app, test_project, app_config):
    """Test that os.path.join logic in Entity.file_path works for various folder/title combinations."""

    app_config.kebab_filenames = True
    ConfigManager().save_config(app_config)

    test_cases = [
        # (folder, title, expected file_path, expected permalink)
        ("my-folder", "Test Note", "my-folder/test-note.md", "my-folder/test-note"),
        (
            "nested/folder",
            "Another Note",
            "nested/folder/another-note.md",
            "nested/folder/another-note",
        ),
        ("", "Root Note", "root-note.md", "root-note"),
        ("/", "Root Slash Note", "root-slash-note.md", "root-slash-note"),
        (
            "folder with spaces",
            "Note Title",
            "folder with spaces/note-title.md",
            "folder-with-spaces/note-title",
        ),
        ("folder//subfolder", "Note", "folder/subfolder/note.md", "folder/subfolder/note"),
    ]

    async with Client(mcp_server) as client:
        for folder, title, expected_path, expected_permalink in test_cases:
            result = await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": title,
                    "directory": folder,
                    "content": "Testing os.path.join logic.",
                    "tags": "integration,ospath",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
            print(response_text)

            assert f"project: {test_project.name}" in response_text
            assert f"file_path: {expected_path}" in response_text
            assert f"permalink: {test_project.name}/{expected_permalink}" in response_text
            assert f"[Session: Using project '{test_project.name}']" in response_text


@pytest.mark.asyncio
async def test_write_note_project_path_validation(mcp_server, app, test_project):
    """Test that ProjectItem.home uses expanded path, not name (Issue #340).

    Regression test verifying that:
    1. ProjectItem.home returns Path(self.path).expanduser()
    2. Not Path(self.name) which was the bug

    This test verifies the fix works correctly even though in the test environment
    the project name and path happen to be the same. The fix in src/basic_memory/schemas/project_info.py:186
    ensures .expanduser() is called, which is critical for paths with ~ like "~/Documents/Test BiSync".
    """

    # Test the fix directly: ProjectItem.home should expand tilde paths
    project_with_tilde = ProjectItem(
        id=1,
        external_id="test-project-with-tilde",
        name="Test BiSync",  # Name differs from path structure
        path="~/Documents/Test BiSync",  # Path with tilde
        is_default=False,
    )

    # Before fix: Path("Test BiSync") - wrong!
    # After fix: Path("~/Documents/Test BiSync").expanduser() - correct!
    home_path = project_with_tilde.home

    # Verify it's a Path object
    assert isinstance(home_path, Path)

    # Verify tilde was expanded (won't contain ~)
    assert "~" not in str(home_path)

    # Verify it ends with the expected structure (use Path.parts for cross-platform)
    assert home_path.parts[-2:] == ("Documents", "Test BiSync")

    # Also test that write_note works with regular project
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Validation Test",
                "directory": "documents",
                "content": "Testing path validation",
                "tags": "test",
            },
        )

        response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Should successfully create without path validation errors
        assert "# Created note" in response_text
        assert "not allowed" not in response_text


@pytest.mark.asyncio
async def test_write_note_overwrite_guard_via_mcp_client(mcp_server, app, test_project):
    """End-to-end test: overwrite guard works through the MCP Client protocol."""

    async with Client(mcp_server) as client:
        # Create initial note
        result1 = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "MCP Guard Test",
                "directory": "guard",
                "content": "# MCP Guard Test\n\nOriginal content via MCP.",
            },
        )
        assert "# Created note" in result1.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Second write without overwrite should be blocked
        result2 = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "MCP Guard Test",
                "directory": "guard",
                "content": "# MCP Guard Test\n\nReplacement content via MCP.",
            },
        )
        response_text = result2.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "# Error: Note already exists" in response_text
        assert "edit_note" in response_text

        # Overwrite with explicit flag should succeed
        result3 = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "MCP Guard Test",
                "directory": "guard",
                "content": "# MCP Guard Test\n\nReplacement content via MCP.",
                "overwrite": True,
            },
        )
        response_text3 = result3.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
        assert "# Updated note" in response_text3
