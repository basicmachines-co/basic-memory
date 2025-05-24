"""Tests for the SearchRepository."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.models.project import Project
from basic_memory.repository.search_repository import SearchRepository, SearchIndexRow
from basic_memory.schemas.search import SearchItemType


@pytest_asyncio.fixture
async def search_entity(session_maker, test_project: Project):
    """Create a test entity for search testing."""
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="Search Test Entity",
            entity_type="test",
            permalink="test/search-test-entity",
            file_path="test/search_test_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()
        return entity


@pytest_asyncio.fixture
async def second_project(project_repository):
    """Create a second project for testing project isolation."""
    project_data = {
        "name": "Second Test Project",
        "description": "Another project for testing",
        "path": "/second/project/path",
        "is_active": True,
        "is_default": None,
    }
    return await project_repository.create(project_data)


@pytest_asyncio.fixture
async def second_project_repository(session_maker, second_project):
    """Create a repository for the second project."""
    return SearchRepository(session_maker, project_id=second_project.id)


@pytest_asyncio.fixture
async def second_entity(session_maker, second_project: Project):
    """Create a test entity in the second project."""
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=second_project.id,
            title="Second Project Entity",
            entity_type="test",
            permalink="test/second-project-entity",
            file_path="test/second_project_entity.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()
        return entity


@pytest.mark.asyncio
async def test_init_search_index(search_repository):
    """Test that search index can be initialized."""
    await search_repository.init_search_index()

    # Verify search_index table exists
    async with db.scoped_session(search_repository.session_maker) as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='search_index';")
        )
        assert result.scalar() == "search_index"


@pytest.mark.asyncio
async def test_index_item(search_repository, search_entity):
    """Test indexing an item with project_id."""
    # Create search index row for the entity
    search_row = SearchIndexRow(
        id=search_entity.id,
        type=SearchItemType.ENTITY.value,
        title=search_entity.title,
        content_stems="search test entity content",
        content_snippet="This is a test entity for search",
        permalink=search_entity.permalink,
        file_path=search_entity.file_path,
        entity_id=search_entity.id,
        metadata={"entity_type": search_entity.entity_type},
        created_at=search_entity.created_at,
        updated_at=search_entity.updated_at,
        project_id=search_repository.project_id,
    )

    # Index the item
    await search_repository.index_item(search_row)

    # Search for the item
    results = await search_repository.search(search_text="search test")

    # Verify we found the item
    assert len(results) == 1
    assert results[0].title == search_entity.title
    assert results[0].project_id == search_repository.project_id


@pytest.mark.asyncio
async def test_project_isolation(
    search_repository, second_project_repository, search_entity, second_entity
):
    """Test that search is isolated by project."""
    # Index entities in both projects
    search_row1 = SearchIndexRow(
        id=search_entity.id,
        type=SearchItemType.ENTITY.value,
        title=search_entity.title,
        content_stems="unique first project content",
        content_snippet="This is a test entity in the first project",
        permalink=search_entity.permalink,
        file_path=search_entity.file_path,
        entity_id=search_entity.id,
        metadata={"entity_type": search_entity.entity_type},
        created_at=search_entity.created_at,
        updated_at=search_entity.updated_at,
        project_id=search_repository.project_id,
    )

    search_row2 = SearchIndexRow(
        id=second_entity.id,
        type=SearchItemType.ENTITY.value,
        title=second_entity.title,
        content_stems="unique second project content",
        content_snippet="This is a test entity in the second project",
        permalink=second_entity.permalink,
        file_path=second_entity.file_path,
        entity_id=second_entity.id,
        metadata={"entity_type": second_entity.entity_type},
        created_at=second_entity.created_at,
        updated_at=second_entity.updated_at,
        project_id=second_project_repository.project_id,
    )

    # Index items in their respective repositories
    await search_repository.index_item(search_row1)
    await second_project_repository.index_item(search_row2)

    # Search in first project
    results1 = await search_repository.search(search_text="unique first")
    assert len(results1) == 1
    assert results1[0].title == search_entity.title
    assert results1[0].project_id == search_repository.project_id

    # Search in second project
    results2 = await second_project_repository.search(search_text="unique second")
    assert len(results2) == 1
    assert results2[0].title == second_entity.title
    assert results2[0].project_id == second_project_repository.project_id

    # Make sure first project can't see second project's content
    results_cross1 = await search_repository.search(search_text="unique second")
    assert len(results_cross1) == 0

    # Make sure second project can't see first project's content
    results_cross2 = await second_project_repository.search(search_text="unique first")
    assert len(results_cross2) == 0


@pytest.mark.asyncio
async def test_delete_by_permalink(search_repository, search_entity):
    """Test deleting an item by permalink respects project isolation."""
    # Index the item
    search_row = SearchIndexRow(
        id=search_entity.id,
        type=SearchItemType.ENTITY.value,
        title=search_entity.title,
        content_stems="content to delete",
        content_snippet="This content should be deleted",
        permalink=search_entity.permalink,
        file_path=search_entity.file_path,
        entity_id=search_entity.id,
        metadata={"entity_type": search_entity.entity_type},
        created_at=search_entity.created_at,
        updated_at=search_entity.updated_at,
        project_id=search_repository.project_id,
    )

    await search_repository.index_item(search_row)

    # Verify it exists
    results = await search_repository.search(search_text="content to delete")
    assert len(results) == 1

    # Delete by permalink
    await search_repository.delete_by_permalink(search_entity.permalink)

    # Verify it's gone
    results_after = await search_repository.search(search_text="content to delete")
    assert len(results_after) == 0


@pytest.mark.asyncio
async def test_delete_by_entity_id(search_repository, search_entity):
    """Test deleting an item by entity_id respects project isolation."""
    # Index the item
    search_row = SearchIndexRow(
        id=search_entity.id,
        type=SearchItemType.ENTITY.value,
        title=search_entity.title,
        content_stems="entity to delete",
        content_snippet="This entity should be deleted",
        permalink=search_entity.permalink,
        file_path=search_entity.file_path,
        entity_id=search_entity.id,
        metadata={"entity_type": search_entity.entity_type},
        created_at=search_entity.created_at,
        updated_at=search_entity.updated_at,
        project_id=search_repository.project_id,
    )

    await search_repository.index_item(search_row)

    # Verify it exists
    results = await search_repository.search(search_text="entity to delete")
    assert len(results) == 1

    # Delete by entity_id
    await search_repository.delete_by_entity_id(search_entity.id)

    # Verify it's gone
    results_after = await search_repository.search(search_text="entity to delete")
    assert len(results_after) == 0


@pytest.mark.asyncio
async def test_to_insert_includes_project_id(search_repository):
    """Test that the to_insert method includes project_id."""
    # Create a search index row with project_id
    row = SearchIndexRow(
        id=1234,
        type=SearchItemType.ENTITY.value,
        title="Test Title",
        content_stems="test content",
        content_snippet="test snippet",
        permalink="test/permalink",
        file_path="test/file.md",
        metadata={"test": "metadata"},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=search_repository.project_id,
    )

    # Get insert data
    insert_data = row.to_insert()

    # Verify project_id is included
    assert "project_id" in insert_data
    assert insert_data["project_id"] == search_repository.project_id


def test_directory_property():
    """Test the directory property of SearchIndexRow."""
    # Test a file in a nested directory
    row1 = SearchIndexRow(
        id=1,
        type=SearchItemType.ENTITY.value,
        file_path="projects/notes/ideas.md",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=1,
    )
    assert row1.directory == "/projects/notes"

    # Test a file at the root level
    row2 = SearchIndexRow(
        id=2,
        type=SearchItemType.ENTITY.value,
        file_path="README.md",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=1,
    )
    assert row2.directory == "/"

    # Test a non-entity type with empty file_path
    row3 = SearchIndexRow(
        id=3,
        type=SearchItemType.OBSERVATION.value,
        file_path="",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        project_id=1,
    )
    assert row3.directory == ""
