"""Tests for directory service."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.repository import EntityRepository
from basic_memory.schemas.directory import DirectoryNode, DirectorySortOrder
from basic_memory.services.directory_service import DirectoryService, _required_file_updated_at


async def _create_sortable_directory_entries(
    entity_repository: EntityRepository,
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 1, 2, tzinfo=UTC)
    entries = (
        Entity(
            title="Nested alpha",
            note_type="note",
            content_type="text/markdown",
            file_path="alpha-folder/nested.md",
            checksum="nested-alpha",
            created_at=older,
            updated_at=older,
        ),
        Entity(
            title="Nested zulu",
            note_type="note",
            content_type="text/markdown",
            file_path="zulu-folder/nested.md",
            checksum="nested-zulu",
            created_at=older,
            updated_at=older,
        ),
        Entity(
            title="Zulu",
            note_type="note",
            content_type="text/markdown",
            file_path="a-file.md",
            checksum="root-zulu",
            created_at=older,
            updated_at=older,
        ),
        Entity(
            title="Bravo",
            note_type="note",
            content_type="text/markdown",
            file_path="middle-file.md",
            checksum="root-bravo",
            created_at=newer,
            updated_at=newer,
        ),
        Entity(
            title="Alpha",
            note_type="note",
            content_type="text/markdown",
            file_path="z-file.md",
            checksum="root-alpha",
            created_at=older,
            updated_at=older,
        ),
    )
    async with db.scoped_session(session_maker) as session:
        for entry in entries:
            await entity_repository.add(session, entry)


def test_updated_sort_requires_file_timestamp() -> None:
    node = DirectoryNode(
        name="missing-timestamp.md",
        directory_path="/missing-timestamp.md",
        type="file",
    )

    with pytest.raises(ValueError, match="missing updated_at"):
        _required_file_updated_at(node)


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
    assert node_0.note_type is None
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
    assert node_file.note_type == "deeper"
    assert node_file.title == "Deeper Entity"
    assert node_file.permalink == "test-project/test/deeper-entity"
    assert node_file.directory_path == "/test/Deeper Entity.md"
    assert node_file.file_path == "test/Deeper Entity.md"
    assert node_file.has_children is False
    assert len(node_file.children) == 0


@pytest.mark.asyncio
async def test_list_directory_empty(directory_service: DirectoryService):
    """Test listing directory with no entities."""
    result = await directory_service.list_directory()
    assert result.nodes == []
    assert result.total == 0
    assert result.has_more is False


@pytest.mark.asyncio
async def test_list_directory_root(directory_service: DirectoryService, test_graph):
    """Test listing root directory contents."""
    result = await directory_service.list_directory(dir_name="/")

    # Should return immediate children of root (the "test" directory)
    assert len(result.nodes) == 1
    assert result.nodes[0].name == "test"
    assert result.nodes[0].type == "directory"
    assert result.nodes[0].directory_path == "/test"
    assert result.nodes[0].children == []


@pytest.mark.asyncio
async def test_list_directory_page_does_not_embed_descendants(
    directory_service: DirectoryService,
    test_graph,
):
    """A paginated directory node must not smuggle its full child tree into JSON."""
    result = await directory_service.list_directory(dir_name="/", depth=1, page_size=1)

    assert len(result.nodes) == 1
    assert result.nodes[0].name == "test"
    assert result.nodes[0].children == []
    assert "Connected Entity 1.md" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_list_directory_specific_path(directory_service: DirectoryService, test_graph):
    """Test listing specific directory contents."""
    result = await directory_service.list_directory(dir_name="/test")

    # Should return the 5 files in the test directory
    assert len(result.nodes) == 5
    file_names = {node.name for node in result.nodes}
    expected_files = {
        "Connected Entity 1.md",
        "Connected Entity 2.md",
        "Deep Entity.md",
        "Deeper Entity.md",
        "Root.md",
    }
    assert file_names == expected_files

    # All should be files
    for node in result.nodes:
        assert node.type == "file"


@pytest.mark.asyncio
async def test_list_directory_nonexistent_path(directory_service: DirectoryService, test_graph):
    """Test listing nonexistent directory."""
    result = await directory_service.list_directory(dir_name="/nonexistent")
    assert result.nodes == []


@pytest.mark.asyncio
async def test_list_directory_with_glob_filter(directory_service: DirectoryService, test_graph):
    """Test listing directory with glob pattern filtering."""
    # Filter for files containing "Connected"
    result = await directory_service.list_directory(dir_name="/test", file_name_glob="*Connected*")

    assert len(result.nodes) == 2
    file_names = {node.name for node in result.nodes}
    assert file_names == {"Connected Entity 1.md", "Connected Entity 2.md"}


@pytest.mark.asyncio
async def test_list_directory_with_markdown_filter(directory_service: DirectoryService, test_graph):
    """Test listing directory with markdown file filter."""
    result = await directory_service.list_directory(dir_name="/test", file_name_glob="*.md")

    # All files in test_graph are markdown files
    assert len(result.nodes) == 5


@pytest.mark.asyncio
async def test_list_directory_with_specific_file_filter(
    directory_service: DirectoryService, test_graph
):
    """Test listing directory with specific file pattern."""
    result = await directory_service.list_directory(dir_name="/test", file_name_glob="Root.*")

    assert len(result.nodes) == 1
    assert result.nodes[0].name == "Root.md"


@pytest.mark.asyncio
async def test_list_directory_depth_control(directory_service: DirectoryService, test_graph):
    """Test listing directory with depth control."""
    # Depth 1 should only return immediate children
    result_depth_1 = await directory_service.list_directory(dir_name="/", depth=1)
    assert len(result_depth_1.nodes) == 1  # Just the "test" directory

    # Depth 2 should return directory + its contents
    result_depth_2 = await directory_service.list_directory(dir_name="/", depth=2)
    assert len(result_depth_2.nodes) == 6  # "test" directory + 5 files in it


@pytest.mark.asyncio
async def test_list_directory_path_normalization(directory_service: DirectoryService, test_graph):
    """Test that directory paths are normalized correctly."""
    # Test various path formats that should all be equivalent
    paths_to_test = ["/test", "test", "/test/", "test/"]

    base_result = await directory_service.list_directory(dir_name="/test")

    for path in paths_to_test:
        result = await directory_service.list_directory(dir_name=path)
        assert len(result.nodes) == len(base_result.nodes)
        # Compare by name since the objects might be different instances
        result_names = {node.name for node in result.nodes}
        base_names = {node.name for node in base_result.nodes}
        assert result_names == base_names


@pytest.mark.asyncio
async def test_list_directory_dot_slash_prefix_normalization(
    directory_service: DirectoryService, test_graph
):
    """Test that ./ prefixed directory paths are normalized correctly."""
    # This test reproduces the bug report issue where ./dirname fails
    base_result = await directory_service.list_directory(dir_name="/test")

    # Test paths with ./ prefix that should be equivalent to /test
    dot_paths_to_test = ["./test", "./test/"]

    for path in dot_paths_to_test:
        result = await directory_service.list_directory(dir_name=path)
        assert len(result.nodes) == len(base_result.nodes), (
            f"Path '{path}' returned {len(result.nodes)} results, expected {len(base_result.nodes)}"
        )
        # Compare by name since the objects might be different instances
        result_names = {node.name for node in result.nodes}
        base_names = {node.name for node in base_result.nodes}
        assert result_names == base_names, f"Path '{path}' returned different files than expected"


@pytest.mark.asyncio
async def test_list_directory_glob_no_matches(directory_service: DirectoryService, test_graph):
    """Test listing directory with glob that matches nothing."""
    result = await directory_service.list_directory(
        dir_name="/test", file_name_glob="*.nonexistent"
    )
    assert result.nodes == []


@pytest.mark.asyncio
async def test_list_directory_default_parameters(directory_service: DirectoryService, test_graph):
    """Test listing directory with default parameters."""
    # Should default to root directory, depth 1, no glob filter
    result = await directory_service.list_directory()

    assert len(result.nodes) == 1
    assert result.nodes[0].name == "test"
    assert result.nodes[0].type == "directory"


@pytest.mark.asyncio
async def test_list_directory_paginates_with_stable_order(
    directory_service: DirectoryService,
    test_graph,
):
    """Pages use one stable order and expose exact continuation metadata."""
    first = await directory_service.list_directory(dir_name="/test", page=1, page_size=2)
    second = await directory_service.list_directory(dir_name="/test", page=2, page_size=2)
    third = await directory_service.list_directory(dir_name="/test", page=3, page_size=2)

    assert [node.name for node in first.nodes] == [
        "Connected Entity 1.md",
        "Connected Entity 2.md",
    ]
    assert [node.name for node in second.nodes] == ["Deep Entity.md", "Deeper Entity.md"]
    assert [node.name for node in third.nodes] == ["Root.md"]
    assert first.model_dump(exclude={"nodes"}) == {
        "page": 1,
        "page_size": 2,
        "total": 5,
        "has_more": True,
    }
    assert second.has_more is True
    assert third.has_more is False


@pytest.mark.asyncio
async def test_list_directory_orders_directories_before_files_across_depth(
    directory_service: DirectoryService,
    test_graph,
):
    """Recursive pages keep directories before files regardless of repository row order."""
    result = await directory_service.list_directory(dir_name="/", depth=2, page_size=10)

    assert [node.type for node in result.nodes] == [
        "directory",
        "file",
        "file",
        "file",
        "file",
        "file",
    ]
    assert result.nodes[0].name == "test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sort", "expected_names"),
    [
        (
            None,
            ["alpha-folder", "zulu-folder", "a-file.md", "middle-file.md", "z-file.md"],
        ),
        (
            "title_asc",
            ["alpha-folder", "zulu-folder", "z-file.md", "middle-file.md", "a-file.md"],
        ),
        (
            "title_desc",
            ["zulu-folder", "alpha-folder", "a-file.md", "middle-file.md", "z-file.md"],
        ),
        (
            "updated_asc",
            ["alpha-folder", "zulu-folder", "z-file.md", "a-file.md", "middle-file.md"],
        ),
        (
            "updated_desc",
            ["alpha-folder", "zulu-folder", "middle-file.md", "z-file.md", "a-file.md"],
        ),
    ],
)
async def test_list_directory_sort_variants_are_stable_across_pages(
    directory_service: DirectoryService,
    entity_repository: EntityRepository,
    session_maker: async_sessionmaker[AsyncSession],
    sort: DirectorySortOrder | None,
    expected_names: list[str],
) -> None:
    await _create_sortable_directory_entries(entity_repository, session_maker)

    pages = [
        await directory_service.list_directory(
            dir_name="/",
            sort=sort,
            page=page,
            page_size=2,
        )
        for page in range(1, 4)
    ]

    assert [node.name for page in pages for node in page.nodes] == expected_names
    assert [page.total for page in pages] == [5, 5, 5]
    assert [page.has_more for page in pages] == [True, True, False]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page", "page_size", "message"),
    [
        (0, 10, "page must be >= 1"),
        (1, 0, "page_size must be >= 1"),
        (1, 201, "page_size must be <= 200"),
    ],
)
async def test_list_directory_rejects_invalid_pagination(
    directory_service: DirectoryService,
    page: int,
    page_size: int,
    message: str,
):
    with pytest.raises(ValueError, match=message):
        await directory_service.list_directory(page=page, page_size=page_size)


@pytest.mark.asyncio
async def test_directory_structure_empty(directory_service: DirectoryService):
    """Test getting empty directory structure."""
    # When no entities exist, result should just be the root
    result = await directory_service.get_directory_structure()
    assert result is not None
    assert len(result.children) == 0

    assert result.name == "Root"
    assert result.directory_path == "/"
    assert result.type == "directory"
    assert result.has_children is False


@pytest.mark.asyncio
async def test_directory_structure(directory_service: DirectoryService, test_graph):
    """Test getting directory structure with folders only (no files)."""
    # test_graph files:
    # /
    # ├── test
    # │   ├── Connected Entity 1.md
    # │   ├── Connected Entity 2.md
    # │   ├── Deep Entity.md
    # │   ├── Deeper Entity.md
    # │   └── Root.md

    result = await directory_service.get_directory_structure()
    assert result is not None
    assert len(result.children) == 1

    # Should only have the "test" directory, not the files
    node_0 = result.children[0]
    assert node_0.name == "test"
    assert node_0.type == "directory"
    assert node_0.directory_path == "/test"
    assert node_0.has_children is False  # No subdirectories, only files

    # Verify no file metadata is present
    assert node_0.content_type is None
    assert node_0.entity_id is None
    assert node_0.note_type is None
    assert node_0.title is None
    assert node_0.permalink is None

    # No file nodes should be present
    assert len(node_0.children) == 0
