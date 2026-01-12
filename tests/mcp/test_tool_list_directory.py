"""Tests for the list_directory MCP tool."""

import pytest

from basic_memory.mcp.tools.list_directory import list_directory
from basic_memory.mcp.tools.write_note import write_note


@pytest.mark.asyncio
async def test_list_directory_empty(client, test_project):
    """Test listing directory when no entities exist."""
    result = await list_directory.fn(project=test_project.name)

    assert isinstance(result, str)
    assert "No files found in directory '/'" in result


@pytest.mark.asyncio
async def test_list_directory_with_test_graph(client, test_graph, test_project):
    """Test listing directory with test_graph fixture."""
    # test_graph provides:
    # /test/Connected Entity 1.md
    # /test/Connected Entity 2.md
    # /test/Deep Entity.md
    # /test/Deeper Entity.md
    # /test/Root.md

    # List root directory
    result = await list_directory.fn(project=test_project.name)

    assert isinstance(result, str)
    assert "Contents of '/' (depth 1):" in result
    assert "📁 test" in result
    assert "Total: 1 items (1 directory)" in result


@pytest.mark.asyncio
async def test_list_directory_specific_path(client, test_graph, test_project):
    """Test listing specific directory path."""
    # List the test directory
    result = await list_directory.fn(project=test_project.name, dir_name="/test")

    assert isinstance(result, str)
    assert "Contents of '/test' (depth 1):" in result
    assert "📄 Connected Entity 1.md" in result
    assert "📄 Connected Entity 2.md" in result
    assert "📄 Deep Entity.md" in result
    assert "📄 Deeper Entity.md" in result
    assert "📄 Root.md" in result
    assert "Total: 5 items (5 files)" in result


@pytest.mark.asyncio
async def test_list_directory_with_glob_filter(client, test_graph, test_project):
    """Test listing directory with glob filtering."""
    # Filter for files containing "Connected"
    result = await list_directory.fn(
        project=test_project.name, dir_name="/test", file_name_glob="*Connected*"
    )

    assert isinstance(result, str)
    assert "Files in '/test' matching '*Connected*' (depth 1):" in result
    assert "📄 Connected Entity 1.md" in result
    assert "📄 Connected Entity 2.md" in result
    # Should not contain other files
    assert "Deep Entity.md" not in result
    assert "Deeper Entity.md" not in result
    assert "Root.md" not in result
    assert "Total: 2 items (2 files)" in result


@pytest.mark.asyncio
async def test_list_directory_with_markdown_filter(client, test_graph, test_project):
    """Test listing directory with markdown file filter."""
    result = await list_directory.fn(
        project=test_project.name, dir_name="/test", file_name_glob="*.md"
    )

    assert isinstance(result, str)
    assert "Files in '/test' matching '*.md' (depth 1):" in result
    # All files in test_graph are markdown files
    assert "📄 Connected Entity 1.md" in result
    assert "📄 Connected Entity 2.md" in result
    assert "📄 Deep Entity.md" in result
    assert "📄 Deeper Entity.md" in result
    assert "📄 Root.md" in result
    assert "Total: 5 items (5 files)" in result


@pytest.mark.asyncio
async def test_list_directory_with_depth_control(client, test_graph, test_project):
    """Test listing directory with depth control."""
    # Depth 1: should return only the test directory
    result_depth_1 = await list_directory.fn(project=test_project.name, dir_name="/", depth=1)

    assert isinstance(result_depth_1, str)
    assert "Contents of '/' (depth 1):" in result_depth_1
    assert "📁 test" in result_depth_1
    assert "Total: 1 items (1 directory)" in result_depth_1

    # Depth 2: should return directory + its files
    result_depth_2 = await list_directory.fn(project=test_project.name, dir_name="/", depth=2)

    assert isinstance(result_depth_2, str)
    assert "Contents of '/' (depth 2):" in result_depth_2
    assert "📁 test" in result_depth_2
    assert "📄 Connected Entity 1.md" in result_depth_2
    assert "📄 Connected Entity 2.md" in result_depth_2
    assert "📄 Deep Entity.md" in result_depth_2
    assert "📄 Deeper Entity.md" in result_depth_2
    assert "📄 Root.md" in result_depth_2
    assert "Total: 6 items (1 directory, 5 files)" in result_depth_2


@pytest.mark.asyncio
async def test_list_directory_nonexistent_path(client, test_graph, test_project):
    """Test listing nonexistent directory."""
    result = await list_directory.fn(project=test_project.name, dir_name="/nonexistent")

    assert isinstance(result, str)
    assert "No files found in directory '/nonexistent'" in result


@pytest.mark.asyncio
async def test_list_directory_glob_no_matches(client, test_graph, test_project):
    """Test listing directory with glob that matches nothing."""
    result = await list_directory.fn(
        project=test_project.name, dir_name="/test", file_name_glob="*.xyz"
    )

    assert isinstance(result, str)
    assert "No files found in directory '/test' matching '*.xyz'" in result


@pytest.mark.asyncio
async def test_list_directory_with_created_notes(client, test_project):
    """Test listing directory with dynamically created notes."""
    # Create some test notes
    await write_note.fn(
        project=test_project.name,
        title="Project Planning",
        folder="projects",
        content="# Project Planning\nThis is about planning projects.",
        tags=["planning", "project"],
    )

    await write_note.fn(
        project=test_project.name,
        title="Meeting Notes",
        folder="projects",
        content="# Meeting Notes\nNotes from the meeting.",
        tags=["meeting", "notes"],
    )

    await write_note.fn(
        project=test_project.name,
        title="Research Document",
        folder="research",
        content="# Research\nSome research findings.",
        tags=["research"],
    )

    # List root directory
    result_root = await list_directory.fn(project=test_project.name)

    assert isinstance(result_root, str)
    assert "Contents of '/' (depth 1):" in result_root
    assert "📁 projects" in result_root
    assert "📁 research" in result_root
    assert "Total: 2 items (2 directories)" in result_root

    # List projects directory
    result_projects = await list_directory.fn(project=test_project.name, dir_name="/projects")

    assert isinstance(result_projects, str)
    assert "Contents of '/projects' (depth 1):" in result_projects
    assert "📄 Project Planning.md" in result_projects
    assert "📄 Meeting Notes.md" in result_projects
    assert "Total: 2 items (2 files)" in result_projects

    # Test glob filter for "Meeting"
    result_meeting = await list_directory.fn(
        project=test_project.name, dir_name="/projects", file_name_glob="*Meeting*"
    )

    assert isinstance(result_meeting, str)
    assert "Files in '/projects' matching '*Meeting*' (depth 1):" in result_meeting
    assert "📄 Meeting Notes.md" in result_meeting
    assert "Project Planning.md" not in result_meeting
    assert "Total: 1 items (1 file)" in result_meeting


@pytest.mark.asyncio
async def test_list_directory_path_normalization(client, test_graph, test_project):
    """Test that various path formats work correctly."""
    # Test various equivalent path formats
    paths_to_test = ["/test", "test", "/test/", "test/"]

    for path in paths_to_test:
        result = await list_directory.fn(project=test_project.name, dir_name=path)
        # All should return the same number of items
        assert "Total: 5 items (5 files)" in result
        assert "📄 Connected Entity 1.md" in result


@pytest.mark.asyncio
async def test_list_directory_shows_file_metadata(client, test_graph, test_project):
    """Test that file metadata is displayed correctly."""
    result = await list_directory.fn(project=test_project.name, dir_name="/test")

    assert isinstance(result, str)
    # Should show file names
    assert "📄 Connected Entity 1.md" in result
    assert "📄 Connected Entity 2.md" in result

    # Should show directory paths
    assert "test/Connected Entity 1.md" in result
    assert "test/Connected Entity 2.md" in result

    # Files should be listed after directories (but no directories in this case)
    lines = result.split("\n")
    file_lines = [line for line in lines if "📄" in line]
    assert len(file_lines) == 5  # All 5 files from test_graph
