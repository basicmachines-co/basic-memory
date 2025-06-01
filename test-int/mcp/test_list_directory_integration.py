"""
Integration tests for list_directory MCP tool.

Tests the complete list directory workflow: MCP client -> MCP server -> FastAPI -> database -> file system
"""

import pytest
from fastmcp import Client


@pytest.mark.asyncio
async def test_list_directory_basic_operation(mcp_server, app):
    """Test basic list_directory operation showing root contents."""

    async with Client(mcp_server) as client:
        # Create some test files and directories first
        await client.call_tool(
            "write_note",
            {
                "title": "Root Note",
                "folder": "",  # Root folder
                "content": "# Root Note\n\nThis is in the root directory.",
                "tags": "test,root",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Project Planning",
                "folder": "projects",
                "content": "# Project Planning\n\nPlanning document for projects.",
                "tags": "planning,project",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Meeting Notes",
                "folder": "meetings",
                "content": "# Meeting Notes\n\nNotes from the meeting.",
                "tags": "meeting,notes",
            },
        )

        # List root directory
        list_result = await client.call_tool(
            "list_directory",
            {
                "dir_name": "/",
                "depth": 1,
            },
        )

        # Should return formatted directory listing
        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should show the structure
        assert "Contents of '/' (depth 1):" in list_text
        assert "📁 meetings" in list_text
        assert "📁 projects" in list_text
        assert "📄 Root Note.md" in list_text
        assert "Root Note" in list_text  # Title should be shown
        assert "Total:" in list_text
        assert "directories" in list_text
        assert "file" in list_text


@pytest.mark.asyncio
async def test_list_directory_specific_folder(mcp_server, app):
    """Test listing contents of a specific folder."""

    async with Client(mcp_server) as client:
        # Create nested structure
        await client.call_tool(
            "write_note",
            {
                "title": "Task List",
                "folder": "work",
                "content": "# Task List\n\nWork tasks for today.",
                "tags": "work,tasks",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Project Alpha",
                "folder": "work/projects",
                "content": "# Project Alpha\n\nAlpha project documentation.",
                "tags": "project,alpha",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Daily Standup",
                "folder": "work/meetings",
                "content": "# Daily Standup\n\nStandup meeting notes.",
                "tags": "meeting,standup",
            },
        )

        # List specific folder
        list_result = await client.call_tool(
            "list_directory",
            {
                "dir_name": "/work",
                "depth": 1,
            },
        )

        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should show work folder contents
        assert "Contents of '/work' (depth 1):" in list_text
        assert "📁 meetings" in list_text
        assert "📁 projects" in list_text
        assert "📄 Task List.md" in list_text
        assert "work/Task List.md" in list_text  # Path should be shown without leading slash


@pytest.mark.asyncio
async def test_list_directory_with_depth(mcp_server, app):
    """Test recursive directory listing with depth control."""

    async with Client(mcp_server) as client:
        # Create deep nested structure
        await client.call_tool(
            "write_note",
            {
                "title": "Deep Note",
                "folder": "research/ml/algorithms/neural-networks",
                "content": "# Deep Note\n\nDeep learning research.",
                "tags": "research,ml,deep",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "ML Overview",
                "folder": "research/ml",
                "content": "# ML Overview\n\nMachine learning overview.",
                "tags": "research,ml,overview",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Research Index",
                "folder": "research",
                "content": "# Research Index\n\nIndex of research topics.",
                "tags": "research,index",
            },
        )

        # List with depth=3 to see nested structure
        list_result = await client.call_tool(
            "list_directory",
            {
                "dir_name": "/research",
                "depth": 3,
            },
        )

        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should show nested structure within depth=3
        assert "Contents of '/research' (depth 3):" in list_text
        assert "📁 ml" in list_text
        assert "📄 Research Index.md" in list_text
        assert "📄 ML Overview.md" in list_text
        assert "📁 algorithms" in list_text  # Should show nested dirs within depth


@pytest.mark.asyncio
async def test_list_directory_with_glob_pattern(mcp_server, app):
    """Test directory listing with glob pattern filtering."""

    async with Client(mcp_server) as client:
        # Create files with different patterns
        await client.call_tool(
            "write_note",
            {
                "title": "Meeting 2025-01-15",
                "folder": "meetings",
                "content": "# Meeting 2025-01-15\n\nMonday meeting notes.",
                "tags": "meeting,january",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Meeting 2025-01-22",
                "folder": "meetings",
                "content": "# Meeting 2025-01-22\n\nMonday meeting notes.",
                "tags": "meeting,january",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Project Status",
                "folder": "meetings",
                "content": "# Project Status\n\nProject status update.",
                "tags": "meeting,project",
            },
        )

        # List with glob pattern for meeting files
        list_result = await client.call_tool(
            "list_directory",
            {
                "dir_name": "/meetings",
                "depth": 1,
                "file_name_glob": "Meeting*",
            },
        )

        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should show only matching files
        assert "Files in '/meetings' matching 'Meeting*' (depth 1):" in list_text
        assert "📄 Meeting 2025-01-15.md" in list_text
        assert "📄 Meeting 2025-01-22.md" in list_text
        assert "Project Status" not in list_text  # Should be filtered out


@pytest.mark.asyncio
async def test_list_directory_empty_directory(mcp_server, app):
    """Test listing an empty directory."""

    async with Client(mcp_server) as client:
        # List non-existent/empty directory
        list_result = await client.call_tool(
            "list_directory",
            {
                "dir_name": "/empty",
                "depth": 1,
            },
        )

        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should indicate no files found
        assert "No files found in directory '/empty'" in list_text


@pytest.mark.asyncio
async def test_list_directory_glob_no_matches(mcp_server, app):
    """Test glob pattern that matches no files."""

    async with Client(mcp_server) as client:
        # Create some files
        await client.call_tool(
            "write_note",
            {
                "title": "Document One",
                "folder": "docs",
                "content": "# Document One\n\nFirst document.",
                "tags": "doc",
            },
        )

        # List with glob pattern that won't match
        list_result = await client.call_tool(
            "list_directory",
            {
                "dir_name": "/docs",
                "depth": 1,
                "file_name_glob": "*.py",  # No Python files
            },
        )

        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should indicate no matches for the pattern
        assert "No files found in directory '/docs' matching '*.py'" in list_text


@pytest.mark.asyncio
async def test_list_directory_various_file_types(mcp_server, app):
    """Test listing directories with various file types and metadata display."""

    async with Client(mcp_server) as client:
        # Create files with different characteristics
        await client.call_tool(
            "write_note",
            {
                "title": "Simple Note",
                "folder": "mixed",
                "content": "# Simple Note\n\nA simple note.",
                "tags": "simple",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Complex Document with Long Title",
                "folder": "mixed",
                "content": "# Complex Document with Long Title\n\nA more complex document.",
                "tags": "complex,long",
            },
        )

        # List the mixed directory
        list_result = await client.call_tool(
            "list_directory",
            {
                "dir_name": "/mixed",
                "depth": 1,
            },
        )

        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should show file names, paths, and titles
        assert "📄 Simple Note.md" in list_text
        assert "mixed/Simple Note.md" in list_text
        assert "📄 Complex Document with Long Title.md" in list_text
        assert "mixed/Complex Document with Long Title.md" in list_text
        assert "Total: 2 items (2 files)" in list_text


@pytest.mark.asyncio
async def test_list_directory_default_parameters(mcp_server, app):
    """Test list_directory with default parameters (root, depth=1)."""

    async with Client(mcp_server) as client:
        # Create some content
        await client.call_tool(
            "write_note",
            {
                "title": "Default Test",
                "folder": "default-test",
                "content": "# Default Test\n\nTesting default parameters.",
                "tags": "default",
            },
        )

        # List with minimal parameters (should use defaults)
        list_result = await client.call_tool(
            "list_directory",
            {},  # Use all defaults
        )

        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should show root directory with depth 1
        assert "Contents of '/' (depth 1):" in list_text
        assert "📁 default-test" in list_text
        assert "Total:" in list_text


@pytest.mark.asyncio
async def test_list_directory_deep_recursion(mcp_server, app):
    """Test directory listing with maximum depth."""

    async with Client(mcp_server) as client:
        # Create very deep structure
        await client.call_tool(
            "write_note",
            {
                "title": "Level 5 Note",
                "folder": "level1/level2/level3/level4/level5",
                "content": "# Level 5 Note\n\nVery deep note.",
                "tags": "deep,level5",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Level 3 Note",
                "folder": "level1/level2/level3",
                "content": "# Level 3 Note\n\nMid-level note.",
                "tags": "medium,level3",
            },
        )

        # List with maximum depth (depth=10)
        list_result = await client.call_tool(
            "list_directory",
            {
                "dir_name": "/level1",
                "depth": 10,  # Maximum allowed depth
            },
        )

        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should show deep structure
        assert "Contents of '/level1' (depth 10):" in list_text
        assert "📁 level2" in list_text
        assert "📄 Level 3 Note.md" in list_text
        assert "📄 Level 5 Note.md" in list_text


@pytest.mark.asyncio
async def test_list_directory_complex_glob_patterns(mcp_server, app):
    """Test various glob patterns for file filtering."""

    async with Client(mcp_server) as client:
        # Create files with different naming patterns
        await client.call_tool(
            "write_note",
            {
                "title": "Project Alpha Plan",
                "folder": "patterns",
                "content": "# Project Alpha Plan\n\nAlpha planning.",
                "tags": "project,alpha",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Project Beta Plan",
                "folder": "patterns",
                "content": "# Project Beta Plan\n\nBeta planning.",
                "tags": "project,beta",
            },
        )

        await client.call_tool(
            "write_note",
            {
                "title": "Meeting Minutes",
                "folder": "patterns",
                "content": "# Meeting Minutes\n\nMeeting notes.",
                "tags": "meeting",
            },
        )

        # Test wildcard pattern
        list_result = await client.call_tool(
            "list_directory",
            {
                "dir_name": "/patterns",
                "file_name_glob": "Project*",
            },
        )

        assert len(list_result) == 1
        list_text = list_result[0].text

        # Should show only Project files
        assert "Project Alpha Plan.md" in list_text
        assert "Project Beta Plan.md" in list_text
        assert "Meeting Minutes" not in list_text
        assert "matching 'Project*'" in list_text
