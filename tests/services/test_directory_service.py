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
