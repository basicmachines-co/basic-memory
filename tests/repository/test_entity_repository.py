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
                entity_id=sample_entity.id,
                content="First observation",
            ),
            Observation(
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
