"""
Integration tests for read_content MCP tool.

Comprehensive tests covering text files, binary files, images, error cases,
and memory:// URL handling via the complete MCP client-server flow.
"""

import json
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


def parse_read_content_response(mcp_result):
    """Helper function to parse read_content MCP response."""
    assert len(mcp_result.content) == 1
    assert mcp_result.content[0].type == "text"
    return json.loads(mcp_result.content[0].text)


@pytest.mark.asyncio
async def test_read_content_markdown_file(mcp_server, app, test_project):
    """Test reading a markdown file created by write_note."""

    async with Client(mcp_server) as client:
        # First create a note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Content Test",
                "folder": "test",
                "content": "# Content Test\n\nThis is test content with **markdown**.",
                "tags": "test,content",
            },
        )

        # Then read the raw file content
        read_result = await client.call_tool(
            "read_content",
            {
                "project": test_project.name,
                "path": "test/Content Test.md",
            },
        )

        # Parse the response
        response_data = parse_read_content_response(read_result)

        assert response_data["type"] == "text"
        assert response_data["content_type"] == "text/markdown; charset=utf-8"
        assert response_data["encoding"] == "utf-8"

        content = response_data["text"]

        # Should contain the raw markdown with frontmatter
        assert "# Content Test" in content
        assert "This is test content with **markdown**." in content
        assert "tags:" in content  # frontmatter
        assert "- test" in content  # tags are in YAML list format
        assert "- content" in content


@pytest.mark.asyncio
async def test_read_content_by_permalink(mcp_server, app, test_project):
    """Test reading content using permalink instead of file path."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Permalink Test",
                "folder": "docs",
                "content": "# Permalink Test\n\nTesting permalink-based content reading.",
            },
        )

        # Read by permalink (without .md extension)
        read_result = await client.call_tool(
            "read_content",
            {
                "project": test_project.name,
                "path": "docs/permalink-test",
            },
        )

        # Parse the response
        response_data = parse_read_content_response(read_result)
        content = response_data["text"]

        assert "# Permalink Test" in content
        assert "Testing permalink-based content reading." in content


@pytest.mark.asyncio
async def test_read_content_memory_url(mcp_server, app, test_project):
    """Test reading content using memory:// URL format."""

    async with Client(mcp_server) as client:
        # Create a note
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Memory URL Test",
                "folder": "test",
                "content": "# Memory URL Test\n\nTesting memory:// URL handling.",
                "tags": "memory,url",
            },
        )

        # Read using memory:// URL
        read_result = await client.call_tool(
            "read_content",
            {
                "project": test_project.name,
                "path": "memory://test/memory-url-test",
            },
        )

        # Parse the response
        response_data = parse_read_content_response(read_result)
        content = response_data["text"]

        assert "# Memory URL Test" in content
        assert "Testing memory:// URL handling." in content


@pytest.mark.asyncio
async def test_read_content_unicode_file(mcp_server, app, test_project):
    """Test reading content with unicode characters and emojis."""

    async with Client(mcp_server) as client:
        # Create a note with unicode content
        unicode_content = (
            "# Unicode Test 🚀\n\nThis note has emoji 🎉 and unicode ♠♣♥♦\n\n测试中文内容"
        )

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Unicode Content Test",
                "folder": "test",
                "content": unicode_content,
                "tags": "unicode,emoji",
            },
        )

        # Read the content back
        read_result = await client.call_tool(
            "read_content",
            {
                "project": test_project.name,
                "path": "test/Unicode Content Test.md",
            },
        )

        # Parse the response
        response_data = parse_read_content_response(read_result)
        content = response_data["text"]

        # All unicode content should be preserved
        assert "🚀" in content
        assert "🎉" in content
        assert "♠♣♥♦" in content
        assert "测试中文内容" in content


@pytest.mark.asyncio
async def test_read_content_complex_frontmatter(mcp_server, app, test_project):
    """Test reading content with complex frontmatter and markdown."""

    async with Client(mcp_server) as client:
        # Create a note with complex content
        complex_content = """---
title: Complex Note
type: document
version: 1.0
author: Test Author
metadata:
  status: draft
  priority: high
---

# Complex Note

This note has complex frontmatter and various markdown elements.

## Observations
- [tech] Uses YAML frontmatter
- [design] Structured content format

## Relations
- related_to [[Other Note]]
- depends_on [[Framework]]

Regular markdown content continues here."""

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Complex Note",
                "folder": "docs",
                "content": complex_content,
                "tags": "complex,frontmatter",
            },
        )

        # Read the content back
        read_result = await client.call_tool(
            "read_content",
            {
                "project": test_project.name,
                "path": "docs/Complex Note.md",
            },
        )

        # Parse the response
        response_data = parse_read_content_response(read_result)
        content = response_data["text"]

        # Should preserve all frontmatter and content structure
        assert "version: 1.0" in content
        assert "author: Test Author" in content
        assert "status: draft" in content
        assert "[tech] Uses YAML frontmatter" in content
        assert "[[Other Note]]" in content


@pytest.mark.asyncio
async def test_read_content_missing_file(mcp_server, app, test_project):
    """Test reading a file that doesn't exist."""

    async with Client(mcp_server) as client:
        try:
            await client.call_tool(
                "read_content",
                {
                    "project": test_project.name,
                    "path": "nonexistent/file.md",
                },
            )
            # Should not reach here - expecting an error
            assert False, "Expected error for missing file"
        except ToolError as e:
            # Should get an appropriate error message
            error_msg = str(e).lower()
            assert "not found" in error_msg or "does not exist" in error_msg


@pytest.mark.asyncio
async def test_read_content_empty_file(mcp_server, app, test_project):
    """Test reading an empty file."""

    async with Client(mcp_server) as client:
        # Create a note with minimal content
        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Empty Test",
                "folder": "test",
                "content": "",  # Empty content
            },
        )

        # Read the content back
        read_result = await client.call_tool(
            "read_content",
            {
                "project": test_project.name,
                "path": "test/Empty Test.md",
            },
        )

        # Parse the response
        response_data = parse_read_content_response(read_result)
        content = response_data["text"]

        # Should still have frontmatter even with empty content
        assert "title: Empty Test" in content
        assert "permalink: test/empty-test" in content


@pytest.mark.asyncio
async def test_read_content_large_file(mcp_server, app, test_project):
    """Test reading a file with substantial content."""

    async with Client(mcp_server) as client:
        # Create a note with substantial content
        large_content = "# Large Content Test\n\n"

        # Add multiple sections with substantial text
        for i in range(10):
            large_content += f"""
## Section {i + 1}

This is section {i + 1} with substantial content. Lorem ipsum dolor sit amet,
consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et
dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation.

- [note] This is observation {i + 1}
- related_to [[Section {i}]]

Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore
eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident.

"""

        await client.call_tool(
            "write_note",
            {
                "project": test_project.name,
                "title": "Large Content Note",
                "folder": "test",
                "content": large_content,
                "tags": "large,content,test",
            },
        )

        # Read the content back
        read_result = await client.call_tool(
            "read_content",
            {
                "project": test_project.name,
                "path": "test/Large Content Note.md",
            },
        )

        # Parse the response
        response_data = parse_read_content_response(read_result)
        content = response_data["text"]

        # Should contain all sections
        assert "Section 1" in content
        assert "Section 10" in content
        assert "Lorem ipsum" in content
        assert len(content) > 1000  # Should be substantial


@pytest.mark.asyncio
async def test_read_content_special_characters_in_filename(mcp_server, app, test_project):
    """Test reading files with special characters in the filename."""

    async with Client(mcp_server) as client:
        # Create notes with special characters in titles
        test_cases = [
            ("File with spaces", "test"),
            ("File-with-dashes", "test"),
            ("File_with_underscores", "test"),
            ("File (with parentheses)", "test"),
            ("File & Symbols!", "test"),
        ]

        for title, folder in test_cases:
            await client.call_tool(
                "write_note",
                {
                    "project": test_project.name,
                    "title": title,
                    "folder": folder,
                    "content": f"# {title}\n\nContent for {title}",
                },
            )

            # Read the content back using the exact filename
            read_result = await client.call_tool(
                "read_content",
                {
                    "project": test_project.name,
                    "path": f"{folder}/{title}.md",
                },
            )

            assert len(read_result.content) == 1
            assert read_result.content[0].type == "text"
            content = read_result.content[0].text

            assert f"# {title}" in content
            assert f"Content for {title}" in content
