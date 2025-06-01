"""
Integration tests for write_note MCP tool.

Comprehensive tests covering all scenarios including note creation, content formatting,
tag handling, error conditions, and edge cases from bug reports.
"""

from textwrap import dedent

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_write_note_basic_creation(mcp_server, app):
    """Test creating a simple note with basic content."""

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "title": "Simple Note",
                "folder": "basic",
                "content": "# Simple Note\n\nThis is a simple note for testing.",
                "tags": "simple,test",
            },
        )

        assert len(result) == 1
        assert result[0].type == "text"
        response_text = result[0].text

        assert "# Created note" in response_text
        assert "file_path: basic/Simple Note.md" in response_text
        assert "permalink: basic/simple-note" in response_text
        assert "## Tags" in response_text
        assert "- simple, test" in response_text


@pytest.mark.asyncio
async def test_write_note_no_tags(mcp_server, app):
    """Test creating a note without tags."""

    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "write_note",
            {
                "title": "No Tags Note",
                "folder": "test",
                "content": "Just some plain text without tags.",
            },
        )

        assert len(result) == 1
        assert result[0].type == "text"
        response_text = result[0].text

        assert "# Created note" in response_text
        assert "file_path: test/No Tags Note.md" in response_text
        assert "permalink: test/no-tags-note" in response_text
        # Should not have tags section when no tags provided


@pytest.mark.asyncio
async def test_write_note_update_existing(mcp_server, app):
    """Test updating an existing note."""

    async with Client(mcp_server) as client:
        # Create initial note
        result1 = await client.call_tool(
            "write_note",
            {
                "title": "Update Test",
                "folder": "test",
                "content": "# Update Test\n\nOriginal content.",
                "tags": "original",
            },
        )

        assert "# Created note" in result1[0].text

        # Update the same note
        result2 = await client.call_tool(
            "write_note",
            {
                "title": "Update Test",
                "folder": "test",
                "content": "# Update Test\n\nUpdated content with changes.",
                "tags": "updated,modified",
            },
        )

        assert len(result2) == 1
        assert result2[0].type == "text"
        response_text = result2[0].text

        assert "# Updated note" in response_text
        assert "file_path: test/Update Test.md" in response_text
        assert "permalink: test/update-test" in response_text
        assert "- updated, modified" in response_text


@pytest.mark.asyncio
async def test_write_note_tag_array(mcp_server, app):
    """Test creating a note with tag array (Issue #38 regression test)."""

    async with Client(mcp_server) as client:
        # This reproduces the exact bug from Issue #38
        result = await client.call_tool(
            "write_note",
            {
                "title": "Array Tags Test",
                "folder": "test",
                "content": "Testing tag array handling",
                "tags": ["python", "testing", "integration", "mcp"],
            },
        )

        assert len(result) == 1
        assert result[0].type == "text"
        response_text = result[0].text

        assert "# Created note" in response_text
        assert "file_path: test/Array Tags Test.md" in response_text
        assert "permalink: test/array-tags-test" in response_text
        assert "## Tags" in response_text
        assert "python" in response_text


@pytest.mark.asyncio
async def test_write_note_custom_permalink(mcp_server, app):
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
                "title": "Custom Permalink Note",
                "folder": "notes",
                "content": content_with_custom_permalink,
            },
        )

        assert len(result) == 1
        assert result[0].type == "text"
        response_text = result[0].text

        assert "# Created note" in response_text
        assert "file_path: notes/Custom Permalink Note.md" in response_text
        assert "permalink: custom/my-special-permalink" in response_text


@pytest.mark.asyncio
async def test_write_note_unicode_content(mcp_server, app):
    """Test handling unicode content including emojis."""

    async with Client(mcp_server) as client:
        unicode_content = "# Unicode Test 🚀\n\nThis note has emoji 🎉 and unicode ♠♣♥♦\n\n- [note] Testing unicode handling 测试"

        result = await client.call_tool(
            "write_note",
            {
                "title": "Unicode Test 🌟",
                "folder": "test",
                "content": unicode_content,
                "tags": "unicode,emoji,测试",
            },
        )

        assert len(result) == 1
        assert result[0].type == "text"
        response_text = result[0].text

        assert "# Created note" in response_text
        assert "file_path: test/Unicode Test 🌟.md" in response_text
        # Permalink should be sanitized
        assert "permalink: test/unicode-test" in response_text
        assert "## Tags" in response_text


@pytest.mark.asyncio
async def test_write_note_complex_content_with_observations_relations(mcp_server, app):
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
                "title": "Complex Knowledge Note",
                "folder": "knowledge",
                "content": complex_content,
                "tags": "complex,knowledge,relations",
            },
        )

        assert len(result) == 1
        assert result[0].type == "text"
        response_text = result[0].text

        assert "# Created note" in response_text
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


@pytest.mark.asyncio
async def test_write_note_preserve_frontmatter(mcp_server, app):
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
                "title": "Frontmatter Note",
                "folder": "test",
                "content": content_with_frontmatter,
                "tags": "frontmatter,preservation",
            },
        )

        assert len(result) == 1
        assert result[0].type == "text"
        response_text = result[0].text

        assert "# Created note" in response_text
        assert "file_path: test/Frontmatter Note.md" in response_text
        assert "permalink: test/frontmatter-note" in response_text
