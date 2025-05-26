"""Tests for directory service."""

import pytest

from basic_memory.services.directory_service import DirectoryService


@pytest.mark.asyncio
async def test_directory_tree_empty(directory_service: DirectoryService):
    """Test getting empty directory tree."""

    # When no entities exist, result should just be the root
    result = await directory_service.get_directory_tree()
    assert result is not None
    assert len(result.children) == 0

    assert result.name == "Root"
    assert result.directory_path == "/"
    assert result.has_children is False


@pytest.mark.asyncio
async def test_directory_tree(directory_service: DirectoryService, test_graph):
    # test_graph files:
    # /
    # ├── test
    # │   ├── Connected Entity 1.md
    # │   ├── Connected Entity 2.md
    # │   ├── Deep Entity.md
    # │   ├── Deeper Entity.md
    # │   └── Root.md

    result = await directory_service.get_directory_tree()
    assert result is not None
    assert len(result.children) == 1

    node_0 = result.children[0]
    assert node_0.name == "test"
    assert node_0.type == "directory"
    assert node_0.content_type is None
    assert node_0.entity_id is None
    assert node_0.entity_type is None
    assert node_0.title is None
    assert node_0.directory_path == "/test"
    assert node_0.has_children is True
    assert len(node_0.children) == 5

    # assert one file node
    node_file = node_0.children[0]
    assert node_file.name == "Deeper Entity.md"
    assert node_file.type == "file"
    assert node_file.content_type == "text/markdown"
    assert node_file.entity_id == 1
    assert node_file.entity_type == "deeper"
    assert node_file.title == "Deeper Entity"
    assert node_file.permalink == "test/deeper-entity"
    assert node_file.directory_path == "/test/Deeper Entity.md"
    assert node_file.file_path == "test/Deeper Entity.md"
    assert node_file.has_children is False
    assert len(node_file.children) == 0


@pytest.mark.asyncio
async def test_list_directory_empty(directory_service: DirectoryService):
    """Test listing directory with no entities."""
    result = await directory_service.list_directory()
    assert result == []


@pytest.mark.asyncio
async def test_list_directory_root(directory_service: DirectoryService, test_graph):
    """Test listing root directory contents."""
    result = await directory_service.list_directory(dir_name="/")

    # Should return immediate children of root (the "test" directory)
    assert len(result) == 1
    assert result[0].name == "test"
    assert result[0].type == "directory"
    assert result[0].directory_path == "/test"


@pytest.mark.asyncio
async def test_list_directory_specific_path(directory_service: DirectoryService, test_graph):
    """Test listing specific directory contents."""
    result = await directory_service.list_directory(dir_name="/test")

    # Should return the 5 files in the test directory
    assert len(result) == 5
    file_names = {node.name for node in result}
    expected_files = {
        "Connected Entity 1.md",
        "Connected Entity 2.md",
        "Deep Entity.md",
        "Deeper Entity.md",
        "Root.md",
    }
    assert file_names == expected_files

    # All should be files
    for node in result:
        assert node.type == "file"


@pytest.mark.asyncio
async def test_list_directory_nonexistent_path(directory_service: DirectoryService, test_graph):
    """Test listing nonexistent directory."""
    result = await directory_service.list_directory(dir_name="/nonexistent")
    assert result == []


@pytest.mark.asyncio
async def test_list_directory_with_glob_filter(directory_service: DirectoryService, test_graph):
    """Test listing directory with glob pattern filtering."""
    # Filter for files containing "Connected"
    result = await directory_service.list_directory(dir_name="/test", file_name_glob="*Connected*")

    assert len(result) == 2
    file_names = {node.name for node in result}
    assert file_names == {"Connected Entity 1.md", "Connected Entity 2.md"}


@pytest.mark.asyncio
async def test_list_directory_with_markdown_filter(directory_service: DirectoryService, test_graph):
    """Test listing directory with markdown file filter."""
    result = await directory_service.list_directory(dir_name="/test", file_name_glob="*.md")

    # All files in test_graph are markdown files
    assert len(result) == 5


@pytest.mark.asyncio
async def test_list_directory_with_specific_file_filter(
    directory_service: DirectoryService, test_graph
):
    """Test listing directory with specific file pattern."""
    result = await directory_service.list_directory(dir_name="/test", file_name_glob="Root.*")

    assert len(result) == 1
    assert result[0].name == "Root.md"


@pytest.mark.asyncio
async def test_list_directory_depth_control(directory_service: DirectoryService, test_graph):
    """Test listing directory with depth control."""
    # Depth 1 should only return immediate children
    result_depth_1 = await directory_service.list_directory(dir_name="/", depth=1)
    assert len(result_depth_1) == 1  # Just the "test" directory

    # Depth 2 should return directory + its contents
    result_depth_2 = await directory_service.list_directory(dir_name="/", depth=2)
    assert len(result_depth_2) == 6  # "test" directory + 5 files in it


@pytest.mark.asyncio
async def test_list_directory_path_normalization(directory_service: DirectoryService, test_graph):
    """Test that directory paths are normalized correctly."""
    # Test various path formats that should all be equivalent
    paths_to_test = ["/test", "test", "/test/", "test/"]

    base_result = await directory_service.list_directory(dir_name="/test")

    for path in paths_to_test:
        result = await directory_service.list_directory(dir_name=path)
        assert len(result) == len(base_result)
        # Compare by name since the objects might be different instances
        result_names = {node.name for node in result}
        base_names = {node.name for node in base_result}
        assert result_names == base_names


@pytest.mark.asyncio
async def test_list_directory_glob_no_matches(directory_service: DirectoryService, test_graph):
    """Test listing directory with glob that matches nothing."""
    result = await directory_service.list_directory(
        dir_name="/test", file_name_glob="*.nonexistent"
    )
    assert result == []


@pytest.mark.asyncio
async def test_list_directory_default_parameters(directory_service: DirectoryService, test_graph):
    """Test listing directory with default parameters."""
    # Should default to root directory, depth 1, no glob filter
    result = await directory_service.list_directory()

    assert len(result) == 1
    assert result[0].name == "test"
    assert result[0].type == "directory"
