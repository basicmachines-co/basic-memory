"""Tests for the EntityRepository."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from basic_memory import db
from basic_memory.models import Entity, Observation, Relation, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.utils import generate_permalink


@pytest_asyncio.fixture
async def entity_with_observations(session_maker, sample_entity):
    """Create an entity with observations."""
    async with db.scoped_session(session_maker) as session:
        observations = [
            Observation(
                project_id=sample_entity.project_id,
                entity_id=sample_entity.id,
                content="First observation",
            ),
            Observation(
                project_id=sample_entity.project_id,
                entity_id=sample_entity.id,
                content="Second observation",
            ),
        ]
        session.add_all(observations)
        return sample_entity


@pytest_asyncio.fixture
async def related_results(session_maker, test_project: Project):
    """Create entities with relations between them."""
    async with db.scoped_session(session_maker) as session:
        source = Entity(
            project_id=test_project.id,
            title="source",
            entity_type="test",
            permalink="source/source",
            file_path="source/source.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        target = Entity(
            project_id=test_project.id,
            title="target",
            entity_type="test",
            permalink="target/target",
            file_path="target/target.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(source)
        session.add(target)
        await session.flush()

        relation = Relation(
            project_id=test_project.id,
            from_id=source.id,
            to_id=target.id,
            to_name=target.title,
            relation_type="connects_to",
        )
        session.add(relation)

        return source, target, relation


@pytest.mark.asyncio
async def test_create_entity(entity_repository: EntityRepository):
    """Test creating a new entity"""
    entity_data = {
        "project_id": entity_repository.project_id,
        "title": "Test",
        "entity_type": "test",
        "permalink": "test/test",
        "file_path": "test/test.md",
        "content_type": "text/markdown",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    entity = await entity_repository.create(entity_data)

    # Verify returned object
    assert entity.id is not None
    assert entity.title == "Test"
    assert isinstance(entity.created_at, datetime)
    assert isinstance(entity.updated_at, datetime)

    # Verify in database
    found = await entity_repository.find_by_id(entity.id)
    assert found is not None
    assert found.id is not None
    assert found.id == entity.id
    assert found.title == entity.title

    # assert relations are eagerly loaded
    assert len(entity.observations) == 0
    assert len(entity.relations) == 0


@pytest.mark.asyncio
async def test_create_all(entity_repository: EntityRepository):
    """Test creating a new entity"""
    entity_data = [
        {
            "project_id": entity_repository.project_id,
            "title": "Test_1",
            "entity_type": "test",
            "permalink": "test/test-1",
            "file_path": "test/test_1.md",
            "content_type": "text/markdown",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
        {
            "project_id": entity_repository.project_id,
            "title": "Test-2",
            "entity_type": "test",
            "permalink": "test/test-2",
            "file_path": "test/test_2.md",
            "content_type": "text/markdown",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    ]
    entities = await entity_repository.create_all(entity_data)

    assert len(entities) == 2
    entity = entities[0]

    # Verify in database
    found = await entity_repository.find_by_id(entity.id)
    assert found is not None
    assert found.id is not None
    assert found.id == entity.id
    assert found.title == entity.title

    # assert relations are eagerly loaded
    assert len(entity.observations) == 0
    assert len(entity.relations) == 0


@pytest.mark.asyncio
async def test_find_by_id(entity_repository: EntityRepository, sample_entity: Entity):
    """Test finding an entity by ID"""
    found = await entity_repository.find_by_id(sample_entity.id)
    assert found is not None
    assert found.id == sample_entity.id
    assert found.title == sample_entity.title

    # Verify against direct database query
    async with db.scoped_session(entity_repository.session_maker) as session:
        stmt = select(Entity).where(Entity.id == sample_entity.id)
        result = await session.execute(stmt)
        db_entity = result.scalar_one()
        assert db_entity.id == found.id
        assert db_entity.title == found.title


@pytest.mark.asyncio
async def test_update_entity(entity_repository: EntityRepository, sample_entity: Entity):
    """Test updating an entity"""
    updated = await entity_repository.update(sample_entity.id, {"title": "Updated title"})
    assert updated is not None
    assert updated.title == "Updated title"

    # Verify in database
    async with db.scoped_session(entity_repository.session_maker) as session:
        stmt = select(Entity).where(Entity.id == sample_entity.id)
        result = await session.execute(stmt)
        db_entity = result.scalar_one()
        assert db_entity.title == "Updated title"


@pytest.mark.asyncio
async def test_update_entity_returns_with_relations_and_observations(
    entity_repository: EntityRepository, entity_with_observations, test_project: Project
):
    """Test that update() returns entity with observations and relations eagerly loaded."""
    entity = entity_with_observations

    # Create a target entity and relation
    async with db.scoped_session(entity_repository.session_maker) as session:
        target = Entity(
            project_id=test_project.id,
            title="target",
            entity_type="test",
            permalink="target/target",
            file_path="target/target.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(target)
        await session.flush()

        relation = Relation(
            project_id=test_project.id,
            from_id=entity.id,
            to_id=target.id,
            to_name=target.title,
            relation_type="connects_to",
        )
        session.add(relation)

    # Now update the entity
    updated = await entity_repository.update(entity.id, {"title": "Updated with relations"})

    # Verify returned entity has observations and relations accessible
    # (would raise DetachedInstanceError if not eagerly loaded)
    assert updated is not None
    assert updated.title == "Updated with relations"

    # Access observations - should NOT raise DetachedInstanceError
    assert len(updated.observations) == 2
    assert updated.observations[0].content in ["First observation", "Second observation"]

    # Access relations - should NOT raise DetachedInstanceError
    assert len(updated.relations) == 1
    assert updated.relations[0].relation_type == "connects_to"
    assert updated.relations[0].to_name == "target"


@pytest.mark.asyncio
async def test_delete_entity(entity_repository: EntityRepository, sample_entity):
    """Test deleting an entity."""
    result = await entity_repository.delete(sample_entity.id)
    assert result is True

    # Verify deletion
    deleted = await entity_repository.find_by_id(sample_entity.id)
    assert deleted is None


@pytest.mark.asyncio
async def test_delete_entity_with_observations(
    entity_repository: EntityRepository, entity_with_observations
):
    """Test deleting an entity cascades to its observations."""
    entity = entity_with_observations

    result = await entity_repository.delete(entity.id)
    assert result is True

    # Verify entity deletion
    deleted = await entity_repository.find_by_id(entity.id)
    assert deleted is None

    # Verify observations were cascaded
    async with db.scoped_session(entity_repository.session_maker) as session:
        query = select(Observation).filter(Observation.entity_id == entity.id)
        result = await session.execute(query)
        remaining_observations = result.scalars().all()
        assert len(remaining_observations) == 0


@pytest.mark.asyncio
async def test_delete_entities_by_type(entity_repository: EntityRepository, sample_entity):
    """Test deleting entities by type."""
    result = await entity_repository.delete_by_fields(entity_type=sample_entity.entity_type)
    assert result is True

    # Verify deletion
    async with db.scoped_session(entity_repository.session_maker) as session:
        query = select(Entity).filter(Entity.entity_type == sample_entity.entity_type)
        result = await session.execute(query)
        remaining = result.scalars().all()
        assert len(remaining) == 0


@pytest.mark.asyncio
async def test_delete_entity_with_relations(entity_repository: EntityRepository, related_results):
    """Test deleting an entity cascades to its relations."""
    source, target, relation = related_results

    # Delete source entity
    result = await entity_repository.delete(source.id)
    assert result is True

    # Verify relation was cascaded
    async with db.scoped_session(entity_repository.session_maker) as session:
        query = select(Relation).filter(Relation.from_id == source.id)
        result = await session.execute(query)
        remaining_relations = result.scalars().all()
        assert len(remaining_relations) == 0

        # Verify target entity still exists
        target_exists = await entity_repository.find_by_id(target.id)
        assert target_exists is not None


@pytest.mark.asyncio
async def test_delete_nonexistent_entity(entity_repository: EntityRepository):
    """Test deleting an entity that doesn't exist."""
    result = await entity_repository.delete(0)
    assert result is False


@pytest_asyncio.fixture
async def test_entities(session_maker, test_project: Project):
    """Create multiple test entities."""
    async with db.scoped_session(session_maker) as session:
        entities = [
            Entity(
                project_id=test_project.id,
                title="entity1",
                entity_type="test",
                permalink="type1/entity1",
                file_path="type1/entity1.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=test_project.id,
                title="entity2",
                entity_type="test",
                permalink="type1/entity2",
                file_path="type1/entity2.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=test_project.id,
                title="entity3",
                entity_type="test",
                permalink="type2/entity3",
                file_path="type2/entity3.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        ]
        session.add_all(entities)
        return entities


@pytest.mark.asyncio
async def test_find_by_permalinks(entity_repository: EntityRepository, test_entities):
    """Test finding multiple entities by their type/name pairs."""
    # Test finding multiple entities
    permalinks = [e.permalink for e in test_entities]
    found = await entity_repository.find_by_permalinks(permalinks)
    assert len(found) == 3
    names = {e.title for e in found}
    assert names == {"entity1", "entity2", "entity3"}

    # Test finding subset of entities
    permalinks = [e.permalink for e in test_entities if e.title != "entity2"]
    found = await entity_repository.find_by_permalinks(permalinks)
    assert len(found) == 2
    names = {e.title for e in found}
    assert names == {"entity1", "entity3"}

    # Test with non-existent entities
    permalinks = ["type1/entity1", "type3/nonexistent"]
    found = await entity_repository.find_by_permalinks(permalinks)
    assert len(found) == 1
    assert found[0].title == "entity1"

    # Test empty input
    found = await entity_repository.find_by_permalinks([])
    assert len(found) == 0


@pytest.mark.asyncio
async def test_generate_permalink_from_file_path():
    """Test permalink generation from different file paths."""
    test_cases = [
        ("docs/My Feature.md", "docs/my-feature"),
        ("specs/API (v2).md", "specs/api-v2"),
        ("notes/2024/Q1 Planning!!!.md", "notes/2024/q1-planning"),
        ("test/Über File.md", "test/uber-file"),
        ("docs/my_feature_name.md", "docs/my-feature-name"),
        ("specs/multiple--dashes.md", "specs/multiple-dashes"),
        ("notes/trailing/space/ file.md", "notes/trailing/space/file"),
    ]

    for input_path, expected in test_cases:
        result = generate_permalink(input_path)
        assert result == expected, f"Failed for {input_path}"
        # Verify the result passes validation
        Entity(
            title="test",
            entity_type="test",
            permalink=result,
            file_path=input_path,
            content_type="text/markdown",
        )  # This will raise ValueError if invalid


@pytest.mark.asyncio
async def test_get_by_title(entity_repository: EntityRepository, session_maker):
    """Test getting an entity by title."""
    # Create test entities
    async with db.scoped_session(session_maker) as session:
        entities = [
            Entity(
                project_id=entity_repository.project_id,
                title="Unique Title",
                entity_type="test",
                permalink="test/unique-title",
                file_path="test/unique-title.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="Another Title",
                entity_type="test",
                permalink="test/another-title",
                file_path="test/another-title.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="Another Title",
                entity_type="test",
                permalink="test/another-title-1",
                file_path="test/another-title-1.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        ]
        session.add_all(entities)
        await session.flush()

    # Test getting by exact title
    found = await entity_repository.get_by_title("Unique Title")
    assert found is not None
    assert len(found) == 1
    assert found[0].title == "Unique Title"

    # Test case sensitivity
    found = await entity_repository.get_by_title("unique title")
    assert not found  # Should be case-sensitive

    # Test non-existent title
    found = await entity_repository.get_by_title("Non Existent")
    assert not found

    # Test multiple rows found
    found = await entity_repository.get_by_title("Another Title")
    assert len(found) == 2


@pytest.mark.asyncio
async def test_get_by_file_path(entity_repository: EntityRepository, session_maker):
    """Test getting an entity by title."""
    # Create test entities
    async with db.scoped_session(session_maker) as session:
        entities = [
            Entity(
                project_id=entity_repository.project_id,
                title="Unique Title",
                entity_type="test",
                permalink="test/unique-title",
                file_path="test/unique-title.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        ]
        session.add_all(entities)
        await session.flush()

    # Test getting by file_path
    found = await entity_repository.get_by_file_path("test/unique-title.md")
    assert found is not None
    assert found.title == "Unique Title"

    # Test non-existent file_path
    found = await entity_repository.get_by_file_path("not/a/real/file.md")
    assert found is None


@pytest.mark.asyncio
async def test_get_distinct_directories(entity_repository: EntityRepository, session_maker):
    """Test getting distinct directory paths from entity file paths."""
    # Create test entities with various directory structures
    async with db.scoped_session(session_maker) as session:
        entities = [
            Entity(
                project_id=entity_repository.project_id,
                title="File 1",
                entity_type="test",
                permalink="docs/guides/file1",
                file_path="docs/guides/file1.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="File 2",
                entity_type="test",
                permalink="docs/guides/file2",
                file_path="docs/guides/file2.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="File 3",
                entity_type="test",
                permalink="docs/api/file3",
                file_path="docs/api/file3.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="File 4",
                entity_type="test",
                permalink="specs/file4",
                file_path="specs/file4.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="File 5",
                entity_type="test",
                permalink="notes/2024/q1/file5",
                file_path="notes/2024/q1/file5.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        ]
        session.add_all(entities)
        await session.flush()

    # Get distinct directories
    directories = await entity_repository.get_distinct_directories()

    # Verify directories are extracted correctly
    assert isinstance(directories, list)
    assert len(directories) > 0

    # Should include all parent directories but not filenames
    expected_dirs = {
        "docs",
        "docs/guides",
        "docs/api",
        "notes",
        "notes/2024",
        "notes/2024/q1",
        "specs",
    }
    assert set(directories) == expected_dirs

    # Verify results are sorted
    assert directories == sorted(directories)

    # Verify no file paths are included
    for dir_path in directories:
        assert not dir_path.endswith(".md")


@pytest.mark.asyncio
async def test_get_distinct_directories_empty_db(entity_repository: EntityRepository):
    """Test getting distinct directories when database is empty."""
    directories = await entity_repository.get_distinct_directories()
    assert directories == []


@pytest.mark.asyncio
async def test_find_by_directory_prefix(entity_repository: EntityRepository, session_maker):
    """Test finding entities by directory prefix."""
    # Create test entities in various directories
    async with db.scoped_session(session_maker) as session:
        entities = [
            Entity(
                project_id=entity_repository.project_id,
                title="File 1",
                entity_type="test",
                permalink="docs/file1",
                file_path="docs/file1.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="File 2",
                entity_type="test",
                permalink="docs/guides/file2",
                file_path="docs/guides/file2.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="File 3",
                entity_type="test",
                permalink="docs/api/file3",
                file_path="docs/api/file3.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="File 4",
                entity_type="test",
                permalink="specs/file4",
                file_path="specs/file4.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        ]
        session.add_all(entities)
        await session.flush()

    # Test finding all entities in "docs" directory and subdirectories
    docs_entities = await entity_repository.find_by_directory_prefix("docs")
    assert len(docs_entities) == 3
    file_paths = {e.file_path for e in docs_entities}
    assert file_paths == {"docs/file1.md", "docs/guides/file2.md", "docs/api/file3.md"}

    # Test finding entities in "docs/guides" subdirectory
    guides_entities = await entity_repository.find_by_directory_prefix("docs/guides")
    assert len(guides_entities) == 1
    assert guides_entities[0].file_path == "docs/guides/file2.md"

    # Test finding entities in "specs" directory
    specs_entities = await entity_repository.find_by_directory_prefix("specs")
    assert len(specs_entities) == 1
    assert specs_entities[0].file_path == "specs/file4.md"

    # Test with root directory (empty string)
    all_entities = await entity_repository.find_by_directory_prefix("")
    assert len(all_entities) == 4

    # Test with root directory (slash)
    all_entities = await entity_repository.find_by_directory_prefix("/")
    assert len(all_entities) == 4

    # Test with non-existent directory
    nonexistent = await entity_repository.find_by_directory_prefix("nonexistent")
    assert len(nonexistent) == 0


@pytest.mark.asyncio
async def test_find_by_directory_prefix_basic_fields_only(
    entity_repository: EntityRepository, session_maker
):
    """Test that find_by_directory_prefix returns basic entity fields.

    Note: This method uses use_query_options=False for performance,
    so it doesn't eager load relationships. Directory trees only need
    basic entity fields.
    """
    # Create test entity
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=entity_repository.project_id,
            title="Test Entity",
            entity_type="test",
            permalink="docs/test",
            file_path="docs/test.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity)
        await session.flush()

    # Query entity by directory prefix
    entities = await entity_repository.find_by_directory_prefix("docs")
    assert len(entities) == 1

    # Verify basic fields are present (all we need for directory trees)
    entity = entities[0]
    assert entity.title == "Test Entity"
    assert entity.file_path == "docs/test.md"
    assert entity.permalink == "docs/test"
    assert entity.entity_type == "test"
    assert entity.content_type == "text/markdown"
    assert entity.updated_at is not None


@pytest.mark.asyncio
async def test_get_all_file_paths(entity_repository: EntityRepository, session_maker):
    """Test getting all file paths for deletion detection during sync."""
    # Create test entities with various file paths
    async with db.scoped_session(session_maker) as session:
        entities = [
            Entity(
                project_id=entity_repository.project_id,
                title="File 1",
                entity_type="test",
                permalink="docs/file1",
                file_path="docs/file1.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="File 2",
                entity_type="test",
                permalink="specs/file2",
                file_path="specs/file2.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
            Entity(
                project_id=entity_repository.project_id,
                title="File 3",
                entity_type="test",
                permalink="notes/file3",
                file_path="notes/file3.md",
                content_type="text/markdown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            ),
        ]
        session.add_all(entities)
        await session.flush()

    # Get all file paths
    file_paths = await entity_repository.get_all_file_paths()

    # Verify results
    assert isinstance(file_paths, list)
    assert len(file_paths) == 3
    assert set(file_paths) == {"docs/file1.md", "specs/file2.md", "notes/file3.md"}


@pytest.mark.asyncio
async def test_get_all_file_paths_empty_db(entity_repository: EntityRepository):
    """Test getting all file paths when database is empty."""
    file_paths = await entity_repository.get_all_file_paths()
    assert file_paths == []


@pytest.mark.asyncio
async def test_get_all_file_paths_performance(entity_repository: EntityRepository, session_maker):
    """Test that get_all_file_paths doesn't load entities or relationships.

    This method is optimized for deletion detection during streaming sync.
    It should only query file_path strings, not full entity objects.
    """
    # Create test entity with observations and relations
    async with db.scoped_session(session_maker) as session:
        # Create entities
        entity1 = Entity(
            project_id=entity_repository.project_id,
            title="Entity 1",
            entity_type="test",
            permalink="test/entity1",
            file_path="test/entity1.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        entity2 = Entity(
            project_id=entity_repository.project_id,
            title="Entity 2",
            entity_type="test",
            permalink="test/entity2",
            file_path="test/entity2.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add_all([entity1, entity2])
        await session.flush()

        # Add observations to entity1
        observation = Observation(
            project_id=entity_repository.project_id,
            entity_id=entity1.id,
            content="Test observation",
            category="note",
        )
        session.add(observation)

        # Add relation between entities
        relation = Relation(
            project_id=entity_repository.project_id,
            from_id=entity1.id,
            to_id=entity2.id,
            to_name=entity2.title,
            relation_type="relates_to",
        )
        session.add(relation)
        await session.flush()

    # Get all file paths - should be fast and not load relationships
    file_paths = await entity_repository.get_all_file_paths()

    # Verify results - just file paths, no entities or relationships loaded
    assert len(file_paths) == 2
    assert set(file_paths) == {"test/entity1.md", "test/entity2.md"}

    # Result should be list of strings, not entity objects
    for path in file_paths:
        assert isinstance(path, str)


@pytest.mark.asyncio
async def test_get_all_file_paths_project_isolation(
    entity_repository: EntityRepository, session_maker
):
    """Test that get_all_file_paths only returns paths from the current project."""
    # Create entities in the repository's project
    async with db.scoped_session(session_maker) as session:
        entity1 = Entity(
            project_id=entity_repository.project_id,
            title="Project 1 File",
            entity_type="test",
            permalink="test/file1",
            file_path="test/file1.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity1)
        await session.flush()

        # Create a second project
        project2 = Project(name="other-project", path="/tmp/other")
        session.add(project2)
        await session.flush()

        # Create entity in different project
        entity2 = Entity(
            project_id=project2.id,
            title="Project 2 File",
            entity_type="test",
            permalink="test/file2",
            file_path="test/file2.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity2)
        await session.flush()

    # Get all file paths for project 1
    file_paths = await entity_repository.get_all_file_paths()

    # Should only include files from project 1
    assert len(file_paths) == 1
    assert file_paths == ["test/file1.md"]


# -------------------------------------------------------------------------
# Tests for lightweight permalink resolution methods
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permalink_exists(entity_repository: EntityRepository, sample_entity: Entity):
    """Test checking if a permalink exists without loading full entity."""
    # Existing permalink should return True
    assert await entity_repository.permalink_exists(sample_entity.permalink) is True  # pyright: ignore [reportArgumentType]

    # Non-existent permalink should return False
    assert await entity_repository.permalink_exists("nonexistent/permalink") is False


@pytest.mark.asyncio
async def test_permalink_exists_project_isolation(
    entity_repository: EntityRepository, session_maker
):
    """Test that permalink_exists respects project isolation."""
    async with db.scoped_session(session_maker) as session:
        # Create entity in repository's project
        entity1 = Entity(
            project_id=entity_repository.project_id,
            title="Project 1 Entity",
            entity_type="test",
            permalink="test/entity1",
            file_path="test/entity1.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity1)

        # Create a second project with same permalink
        project2 = Project(name="other-project", path="/tmp/other")
        session.add(project2)
        await session.flush()

        entity2 = Entity(
            project_id=project2.id,
            title="Project 2 Entity",
            entity_type="test",
            permalink="test/entity2",
            file_path="test/entity2.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(entity2)

    # Should find entity1's permalink in project 1
    assert await entity_repository.permalink_exists("test/entity1") is True

    # Should NOT find entity2's permalink (it's in project 2)
    assert await entity_repository.permalink_exists("test/entity2") is False


@pytest.mark.asyncio
async def test_get_file_path_for_permalink(
    entity_repository: EntityRepository, sample_entity: Entity
):
    """Test getting file_path for a permalink without loading full entity."""
    # Existing permalink should return file_path
    file_path = await entity_repository.get_file_path_for_permalink(sample_entity.permalink)  # pyright: ignore [reportArgumentType]
    assert file_path == sample_entity.file_path

    # Non-existent permalink should return None
    result = await entity_repository.get_file_path_for_permalink("nonexistent/permalink")
    assert result is None


@pytest.mark.asyncio
async def test_get_permalink_for_file_path(
    entity_repository: EntityRepository, sample_entity: Entity
):
    """Test getting permalink for a file_path without loading full entity."""
    # Existing file_path should return permalink
    permalink = await entity_repository.get_permalink_for_file_path(sample_entity.file_path)
    assert permalink == sample_entity.permalink

    # Non-existent file_path should return None
    result = await entity_repository.get_permalink_for_file_path("nonexistent/path.md")
    assert result is None


@pytest.mark.asyncio
async def test_get_all_permalinks(entity_repository: EntityRepository, session_maker):
    """Test getting all permalinks without loading full entities."""
    async with db.scoped_session(session_maker) as session:
        entity1 = Entity(
            project_id=entity_repository.project_id,
            title="Entity 1",
            entity_type="test",
            permalink="test/entity1",
            file_path="test/entity1.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        entity2 = Entity(
            project_id=entity_repository.project_id,
            title="Entity 2",
            entity_type="test",
            permalink="test/entity2",
            file_path="test/entity2.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add_all([entity1, entity2])

    permalinks = await entity_repository.get_all_permalinks()

    assert len(permalinks) == 2
    assert set(permalinks) == {"test/entity1", "test/entity2"}

    # Results should be strings, not entities
    for permalink in permalinks:
        assert isinstance(permalink, str)


@pytest.mark.asyncio
async def test_get_permalink_to_file_path_map(entity_repository: EntityRepository, session_maker):
    """Test getting permalink -> file_path mapping for bulk operations."""
    async with db.scoped_session(session_maker) as session:
        entity1 = Entity(
            project_id=entity_repository.project_id,
            title="Entity 1",
            entity_type="test",
            permalink="test/entity1",
            file_path="test/entity1.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        entity2 = Entity(
            project_id=entity_repository.project_id,
            title="Entity 2",
            entity_type="test",
            permalink="test/entity2",
            file_path="test/entity2.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add_all([entity1, entity2])

    mapping = await entity_repository.get_permalink_to_file_path_map()

    assert len(mapping) == 2
    assert mapping["test/entity1"] == "test/entity1.md"
    assert mapping["test/entity2"] == "test/entity2.md"


@pytest.mark.asyncio
async def test_get_file_path_to_permalink_map(entity_repository: EntityRepository, session_maker):
    """Test getting file_path -> permalink mapping for bulk operations."""
    async with db.scoped_session(session_maker) as session:
        entity1 = Entity(
            project_id=entity_repository.project_id,
            title="Entity 1",
            entity_type="test",
            permalink="test/entity1",
            file_path="test/entity1.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        entity2 = Entity(
            project_id=entity_repository.project_id,
            title="Entity 2",
            entity_type="test",
            permalink="test/entity2",
            file_path="test/entity2.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add_all([entity1, entity2])

    mapping = await entity_repository.get_file_path_to_permalink_map()

    assert len(mapping) == 2
    assert mapping["test/entity1.md"] == "test/entity1"
    assert mapping["test/entity2.md"] == "test/entity2"
