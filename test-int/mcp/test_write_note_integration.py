"""
Integration tests for write_note MCP tool.

Comprehensive tests covering all scenarios including note creation, content formatting,
tag handling, error conditions, and edge cases from bug reports.
"""

from textwrap import dedent

import pytest
from fastmcp import Client

from basic_memory.config import ConfigManager
from basic_memory.schemas.project_info import ProjectItem
from pathlib import Path


@pytest.mark.asyncio
async def test_write_note_basic_creation(mcp_server, app, test_project):
    """Test creating a simple note with basic content."""

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Simple Note",
                "folder": "basic",
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
        assert "permalink: basic/simple-note" in response_text
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
                "folder": "test",
                "content": "Just some plain text without tags.",
            },
        )

        assert len(result.content) == 1
        assert result.content[0].type == "text"
        response_text = result.content[0].text

        assert "# Created note" in response_text
        assert "file_path: test/No Tags Note.md" in response_text
        assert "permalink: test/no-tags-note" in response_text
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
                "folder": "test",
                "content": "# Update Test\n\nOriginal content.",
                "tags": "original",
            },
        )

        assert "# Created note" in result1.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Update the same note
        result2 = await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Update Test",
                "folder": "test",
                "content": "# Update Test\n\nUpdated content with changes.",
                "tags": "updated,modified",
            },
        )

        assert len(result2.content) == 1
        assert result2.content[0].type == "text"
        response_text = result2.content[0].text

        assert "# Updated note" in response_text
        assert f"project: {test_project.name}" in response_text
        assert "file_path: test/Update Test.md" in response_text
        assert "permalink: test/update-test" in response_text
        assert "- updated, modified" in response_text
        assert f"[Session: Using project '{test_project.name}']" in response_text


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
                "folder": "test",
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
        assert "permalink: test/array-tags-test" in response_text
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
                "folder": "notes",
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
                "folder": "test",
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
        assert "permalink: test/unicode-test" in response_text
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
                "folder": "knowledge",
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
        assert "permalink: knowledge/complex-knowledge-note" in response_text

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
                "folder": "test",
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
        assert "permalink: test/frontmatter-note" in response_text
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
                "folder": "my-folder",
                "content": "Testing kebab-case and invalid characters.",
                "tags": "kebab,invalid,filename",
            },
        )

        assert len(result.content) == 1
        response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # File path and permalink should be kebab-case and sanitized
        assert f"project: {test_project.name}" in response_text
        assert "file_path: my-folder/my-note-with-invalid-chars.md" in response_text
        assert "permalink: my-folder/my-note-with-invalid-chars" in response_text
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
                "folder": "my-folder",
                "content": "Should be fully kebab-case and safe.",
                "tags": "crazy,filename,test",
            },
        )

        assert len(result.content) == 1
        response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        assert f"project: {test_project.name}" in response_text
        assert "file_path: my-folder/crazy-note-name.md" in response_text
        assert "permalink: my-folder/crazy-note-name" in response_text
        assert f"[Session: Using project '{test_project.name}']" in response_text


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
                    "folder": folder,
                    "content": "Testing os.path.join logic.",
                    "tags": "integration,ospath",
                },
            )

            assert len(result.content) == 1
            response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]
            print(response_text)

            assert f"project: {test_project.name}" in response_text
            assert f"file_path: {expected_path}" in response_text
            assert f"permalink: {expected_permalink}" in response_text
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
                "folder": "documents",
                "content": "Testing path validation",
                "tags": "test",
            },
        )

        response_text = result.content[0].text  # pyright: ignore [reportAttributeAccessIssue]

        # Should successfully create without path validation errors
        assert "# Created note" in response_text
        assert "not allowed" not in response_text
