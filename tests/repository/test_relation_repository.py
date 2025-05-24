"""Tests for the RelationRepository."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
import sqlalchemy

from basic_memory import db
from basic_memory.models import Entity, Relation, Project
from basic_memory.repository.relation_repository import RelationRepository


@pytest_asyncio.fixture
async def source_entity(session_maker, test_project: Project):
    """Create a source entity for testing relations."""
    entity = Entity(
        project_id=test_project.id,
        title="test_source",
        entity_type="test",
        permalink="source/test-source",
        file_path="source/test_source.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    async with db.scoped_session(session_maker) as session:
        session.add(entity)
        await session.flush()
        return entity


@pytest_asyncio.fixture
async def target_entity(session_maker, test_project: Project):
    """Create a target entity for testing relations."""
    entity = Entity(
        project_id=test_project.id,
        title="test_target",
        entity_type="test",
        permalink="target/test-target",
        file_path="target/test_target.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    async with db.scoped_session(session_maker) as session:
        session.add(entity)
        await session.flush()
        return entity


@pytest_asyncio.fixture
async def test_relations(session_maker, source_entity, target_entity):
    """Create test relations."""
    relations = [
        Relation(
            from_id=source_entity.id,
            to_id=target_entity.id,
            to_name=target_entity.title,
            relation_type="connects_to",
        ),
        Relation(
            from_id=source_entity.id,
            to_id=target_entity.id,
            to_name=target_entity.title,
            relation_type="depends_on",
        ),
    ]
    async with db.scoped_session(session_maker) as session:
        session.add_all(relations)
        await session.flush()
        return relations


@pytest_asyncio.fixture(scope="function")
async def related_entity(entity_repository):
    """Create a second entity for testing relations"""
    entity_data = {
        "title": "Related Entity",
        "entity_type": "test",
        "permalink": "test/related-entity",
        "file_path": "test/related_entity.md",
        "summary": "A related test entity",
        "content_type": "text/markdown",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    return await entity_repository.create(entity_data)


@pytest_asyncio.fixture(scope="function")
async def sample_relation(
    relation_repository: RelationRepository, sample_entity: Entity, related_entity: Entity
):
    """Create a sample relation for testing"""
    relation_data = {
        "from_id": sample_entity.id,
        "to_id": related_entity.id,
        "to_name": related_entity.title,
        "relation_type": "test_relation",
        "context": "test-context",
    }
    return await relation_repository.create(relation_data)


@pytest_asyncio.fixture(scope="function")
async def multiple_relations(
    relation_repository: RelationRepository, sample_entity: Entity, related_entity: Entity
):
    """Create multiple relations for testing"""
    relations_data = [
        {
            "from_id": sample_entity.id,
            "to_id": related_entity.id,
            "to_name": related_entity.title,
            "relation_type": "relation_one",
            "context": "context_one",
        },
        {
            "from_id": sample_entity.id,
            "to_id": related_entity.id,
            "to_name": related_entity.title,
            "relation_type": "relation_two",
            "context": "context_two",
        },
        {
            "from_id": related_entity.id,
            "to_id": sample_entity.id,
            "to_name": related_entity.title,
            "relation_type": "relation_one",
            "context": "context_three",
        },
    ]
    return [await relation_repository.create(data) for data in relations_data]


@pytest.mark.asyncio
async def test_create_relation(
    relation_repository: RelationRepository, sample_entity: Entity, related_entity: Entity
):
    """Test creating a new relation"""
    relation_data = {
        "from_id": sample_entity.id,
        "to_id": related_entity.id,
        "to_name": related_entity.title,
        "relation_type": "test_relation",
        "context": "test-context",
    }
    relation = await relation_repository.create(relation_data)

    assert relation.from_id == sample_entity.id
    assert relation.to_id == related_entity.id
    assert relation.relation_type == "test_relation"
    assert relation.id is not None  # Should be auto-generated


@pytest.mark.asyncio
async def test_create_relation_entity_does_not_exist(
    relation_repository: RelationRepository, sample_entity: Entity, related_entity: Entity
):
    """Test creating a new relation"""
    relation_data = {
        "from_id": "not_exist",
        "to_id": related_entity.id,
        "to_name": related_entity.title,
        "relation_type": "test_relation",
        "context": "test-context",
    }
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        await relation_repository.create(relation_data)


@pytest.mark.asyncio
async def test_find_by_entities(
    relation_repository: RelationRepository,
    sample_relation: Relation,
    sample_entity: Entity,
    related_entity: Entity,
):
    """Test finding relations between specific entities"""
    relations = await relation_repository.find_by_entities(sample_entity.id, related_entity.id)
    assert len(relations) == 1
    assert relations[0].id == sample_relation.id
    assert relations[0].relation_type == sample_relation.relation_type


@pytest.mark.asyncio
async def test_find_relation(relation_repository: RelationRepository, sample_relation: Relation):
    """Test finding relations by type"""
    relation = await relation_repository.find_relation(
        from_permalink=sample_relation.from_entity.permalink,
        to_permalink=sample_relation.to_entity.permalink,
        relation_type=sample_relation.relation_type,
    )
    assert relation.id == sample_relation.id


@pytest.mark.asyncio
async def test_find_by_type(relation_repository: RelationRepository, sample_relation: Relation):
    """Test finding relations by type"""
    relations = await relation_repository.find_by_type("test_relation")
    assert len(relations) == 1
    assert relations[0].id == sample_relation.id


@pytest.mark.asyncio
async def test_find_unresolved_relations(
    relation_repository: RelationRepository, sample_entity: Entity, related_entity: Entity
):
    """Test creating a new relation"""
    relation_data = {
        "from_id": sample_entity.id,
        "to_id": None,
        "to_name": related_entity.title,
        "relation_type": "test_relation",
        "context": "test-context",
    }
    relation = await relation_repository.create(relation_data)

    assert relation.from_id == sample_entity.id
    assert relation.to_id is None

    unresolved = await relation_repository.find_unresolved_relations()
    assert len(unresolved) == 1
    assert unresolved[0].id == relation.id


@pytest.mark.asyncio
async def test_delete_by_fields_single_field(
    relation_repository: RelationRepository, multiple_relations: list[Relation]
):
    """Test deleting relations by a single field."""
    # Delete all relations of type 'relation_one'
    result = await relation_repository.delete_by_fields(relation_type="relation_one")  # pyright: ignore [reportArgumentType]
    assert result is True

    # Verify deletion
    remaining = await relation_repository.find_by_type("relation_one")
    assert len(remaining) == 0

    # Other relations should still exist
    others = await relation_repository.find_by_type("relation_two")
    assert len(others) == 1


@pytest.mark.asyncio
async def test_delete_by_fields_multiple_fields(
    relation_repository: RelationRepository,
    multiple_relations: list[Relation],
    sample_entity: Entity,
    related_entity: Entity,
):
    """Test deleting relations by multiple fields."""
    # Delete specific relation matching both from_id and relation_type
    result = await relation_repository.delete_by_fields(
        from_id=sample_entity.id,  # pyright: ignore [reportArgumentType]
        relation_type="relation_one",  # pyright: ignore [reportArgumentType]
    )
    assert result is True

    # Verify correct relation was deleted
    remaining = await relation_repository.find_by_entities(sample_entity.id, related_entity.id)
    assert len(remaining) == 1  # Only relation_two should remain
    assert remaining[0].relation_type == "relation_two"


@pytest.mark.asyncio
async def test_delete_by_fields_no_match(
    relation_repository: RelationRepository, multiple_relations: list[Relation]
):
    """Test delete_by_fields when no relations match."""
    result = await relation_repository.delete_by_fields(
        relation_type="nonexistent_type"  # pyright: ignore [reportArgumentType]
    )
    assert result is False


@pytest.mark.asyncio
async def test_delete_by_fields_all_fields(
    relation_repository: RelationRepository,
    multiple_relations: list[Relation],
    sample_entity: Entity,
    related_entity: Entity,
):
    """Test deleting relation by matching all fields."""
    # Get first relation's data
    relation = multiple_relations[0]

    # Delete using all fields
    result = await relation_repository.delete_by_fields(
        from_id=relation.from_id,  # pyright: ignore [reportArgumentType]
        to_id=relation.to_id,  # pyright: ignore [reportArgumentType]
        relation_type=relation.relation_type,  # pyright: ignore [reportArgumentType]
    )
    assert result is True

    # Verify only exact match was deleted
    remaining = await relation_repository.find_by_type(relation.relation_type)
    assert len(remaining) == 1  # One other relation_one should remain


@pytest.mark.asyncio
async def test_delete_relation_by_id(relation_repository, test_relations):
    """Test deleting a relation by ID."""
    relation = test_relations[0]

    result = await relation_repository.delete(relation.id)
    assert result is True

    # Verify deletion
    remaining = await relation_repository.find_one(
        relation_repository.select(Relation).filter(Relation.id == relation.id)
    )
    assert remaining is None


@pytest.mark.asyncio
async def test_delete_relations_by_type(relation_repository, test_relations):
    """Test deleting relations by type."""
    result = await relation_repository.delete_by_fields(relation_type="connects_to")
    assert result is True

    # Verify specific type was deleted
    remaining = await relation_repository.find_by_type("connects_to")
    assert len(remaining) == 0

    # Verify other type still exists
    others = await relation_repository.find_by_type("depends_on")
    assert len(others) == 1


@pytest.mark.asyncio
async def test_delete_relations_by_entities(
    relation_repository, test_relations, source_entity, target_entity
):
    """Test deleting relations between specific entities."""
    result = await relation_repository.delete_by_fields(
        from_id=source_entity.id, to_id=target_entity.id
    )
    assert result is True

    # Verify all relations between entities were deleted
    remaining = await relation_repository.find_by_entities(source_entity.id, target_entity.id)
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_relation(relation_repository):
    """Test deleting a relation that doesn't exist."""
    result = await relation_repository.delete_by_fields(relation_type="nonexistent")
    assert result is False
