"""Tests for the RelationRepository."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from basic_memory import db
from basic_memory.models import Entity, Project, Relation
from basic_memory.repository.relation_repository import (
    AcceptedRelationWrite,
    RelationRepository,
)


@pytest_asyncio.fixture
async def source_entity(session_maker, test_project: Project):
    """Create a source entity for testing relations."""
    entity = Entity(
        project_id=test_project.id,
        title="test_source",
        note_type="test",
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
        note_type="test",
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
async def test_relations(session_maker, source_entity, target_entity, test_project: Project):
    """Create test relations."""
    relations = [
        Relation(
            project_id=test_project.id,
            from_id=source_entity.id,
            to_id=target_entity.id,
            to_name=target_entity.title,
            relation_type="connects_to",
        ),
        Relation(
            project_id=test_project.id,
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
async def related_entity(entity_repository, session_maker):
    """Create a second entity for testing relations"""
    entity_data = {
        "title": "Related Entity",
        "note_type": "test",
        "permalink": "test/related-entity",
        "file_path": "test/related_entity.md",
        "summary": "A related test entity",
        "content_type": "text/markdown",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    async with db.scoped_session(session_maker) as session:
        return await entity_repository.create(session, entity_data)


@pytest_asyncio.fixture(scope="function")
async def sample_relation(
    relation_repository: RelationRepository,
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Create a sample relation for testing"""
    relation_data = {
        "from_id": sample_entity.id,
        "to_id": related_entity.id,
        "to_name": related_entity.title,
        "relation_type": "test_relation",
        "context": "test-context",
    }
    async with db.scoped_session(session_maker) as session:
        return await relation_repository.create(session, relation_data)


@pytest_asyncio.fixture(scope="function")
async def multiple_relations(
    relation_repository: RelationRepository,
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
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
    async with db.scoped_session(session_maker) as session:
        return [await relation_repository.create(session, data) for data in relations_data]


@pytest.mark.asyncio
async def test_create_relation(
    relation_repository: RelationRepository,
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Test creating a new relation"""
    relation_data = {
        "from_id": sample_entity.id,
        "to_id": related_entity.id,
        "to_name": related_entity.title,
        "relation_type": "test_relation",
        "context": "test-context",
    }
    async with db.scoped_session(session_maker) as session:
        relation = await relation_repository.create(session, relation_data)

    assert relation.from_id == sample_entity.id
    assert relation.to_id == related_entity.id
    assert relation.relation_type == "test_relation"
    assert relation.id is not None  # Should be auto-generated


@pytest.mark.asyncio
async def test_create_relation_entity_does_not_exist(
    relation_repository: RelationRepository,
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Test creating a new relation"""
    relation_data = {
        "from_id": 99999,  # Non-existent entity ID (integer for Postgres compatibility)
        "to_id": related_entity.id,
        "to_name": related_entity.title,
        "relation_type": "test_relation",
        "context": "test-context",
    }
    with pytest.raises(IntegrityError):
        async with db.scoped_session(session_maker) as session:
            await relation_repository.create(session, relation_data)


@pytest.mark.asyncio
async def test_find_by_entities(
    relation_repository: RelationRepository,
    sample_relation: Relation,
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Test finding relations between specific entities"""
    async with db.scoped_session(session_maker) as session:
        relations = await relation_repository.find_by_entities(
            session, sample_entity.id, related_entity.id
        )
    assert len(relations) == 1
    assert relations[0].id == sample_relation.id
    assert relations[0].relation_type == sample_relation.relation_type


@pytest.mark.asyncio
async def test_find_relation(
    relation_repository: RelationRepository, sample_relation: Relation, session_maker
):
    """Test finding relations by type"""
    async with db.scoped_session(session_maker) as session:
        relation = await relation_repository.find_relation(
            session=session,
            from_permalink=sample_relation.from_entity.permalink,
            to_permalink=sample_relation.to_entity.permalink,
            relation_type=sample_relation.relation_type,
        )
    assert relation is not None
    assert relation.id == sample_relation.id


@pytest.mark.asyncio
async def test_find_by_type(
    relation_repository: RelationRepository, sample_relation: Relation, session_maker
):
    """Test finding relations by type"""
    async with db.scoped_session(session_maker) as session:
        relations = await relation_repository.find_by_type(session, "test_relation")
    assert len(relations) == 1
    assert relations[0].id == sample_relation.id


@pytest.mark.asyncio
async def test_find_unresolved_relations(
    relation_repository: RelationRepository,
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Test creating a new relation"""
    relation_data = {
        "from_id": sample_entity.id,
        "to_id": None,
        "to_name": related_entity.title,
        "relation_type": "test_relation",
        "context": "test-context",
    }
    async with db.scoped_session(session_maker) as session:
        relation = await relation_repository.create(session, relation_data)

        assert relation.from_id == sample_entity.id
        assert relation.to_id is None

        unresolved = await relation_repository.find_unresolved_relations(session)
        assert len(unresolved) == 1
        assert unresolved[0].id == relation.id


@pytest.mark.asyncio
async def test_delete_by_fields_single_field(
    relation_repository: RelationRepository, multiple_relations: list[Relation], session_maker
):
    """Test deleting relations by a single field."""
    # Delete all relations of type 'relation_one'
    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.delete_by_fields(session, relation_type="relation_one")  # pyright: ignore [reportArgumentType]
        assert result is True

        # Verify deletion
        remaining = await relation_repository.find_by_type(session, "relation_one")
        assert len(remaining) == 0

        # Other relations should still exist
        others = await relation_repository.find_by_type(session, "relation_two")
        assert len(others) == 1


@pytest.mark.asyncio
async def test_delete_by_fields_multiple_fields(
    relation_repository: RelationRepository,
    multiple_relations: list[Relation],
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Test deleting relations by multiple fields."""
    # Delete specific relation matching both from_id and relation_type
    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.delete_by_fields(
            session,
            from_id=sample_entity.id,  # pyright: ignore [reportArgumentType]
            relation_type="relation_one",  # pyright: ignore [reportArgumentType]
        )
        assert result is True

        # Verify correct relation was deleted
        remaining = await relation_repository.find_by_entities(
            session, sample_entity.id, related_entity.id
        )
        assert len(remaining) == 1  # Only relation_two should remain
        assert remaining[0].relation_type == "relation_two"


@pytest.mark.asyncio
async def test_delete_by_fields_no_match(
    relation_repository: RelationRepository, multiple_relations: list[Relation], session_maker
):
    """Test delete_by_fields when no relations match."""
    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.delete_by_fields(
            session,
            relation_type="nonexistent_type",  # pyright: ignore [reportArgumentType]
        )
    assert result is False


@pytest.mark.asyncio
async def test_delete_by_fields_all_fields(
    relation_repository: RelationRepository,
    multiple_relations: list[Relation],
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Test deleting relation by matching all fields."""
    # Get first relation's data
    relation = multiple_relations[0]

    # Delete using all fields
    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.delete_by_fields(
            session,
            from_id=relation.from_id,  # pyright: ignore [reportArgumentType]
            to_id=relation.to_id,  # pyright: ignore [reportArgumentType]
            relation_type=relation.relation_type,  # pyright: ignore [reportArgumentType]
        )
        assert result is True

        # Verify only exact match was deleted
        remaining = await relation_repository.find_by_type(session, relation.relation_type)
        assert len(remaining) == 1  # One other relation_one should remain


@pytest.mark.asyncio
async def test_delete_relation_by_id(relation_repository, test_relations, session_maker):
    """Test deleting a relation by ID."""
    relation = test_relations[0]

    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.delete(session, relation.id)
        assert result is True

        # Verify deletion
        remaining = await relation_repository.find_one(
            session, relation_repository.select(Relation).filter(Relation.id == relation.id)
        )
        assert remaining is None


@pytest.mark.asyncio
async def test_delete_relations_by_type(relation_repository, test_relations, session_maker):
    """Test deleting relations by type."""
    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.delete_by_fields(session, relation_type="connects_to")
        assert result is True

        # Verify specific type was deleted
        remaining = await relation_repository.find_by_type(session, "connects_to")
        assert len(remaining) == 0

        # Verify other type still exists
        others = await relation_repository.find_by_type(session, "depends_on")
        assert len(others) == 1


@pytest.mark.asyncio
async def test_delete_relations_by_entities(
    relation_repository, test_relations, source_entity, target_entity, session_maker
):
    """Test deleting relations between specific entities."""
    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.delete_by_fields(
            session, from_id=source_entity.id, to_id=target_entity.id
        )
        assert result is True

        # Verify all relations between entities were deleted
        remaining = await relation_repository.find_by_entities(
            session, source_entity.id, target_entity.id
        )
        assert len(remaining) == 0


@pytest.mark.asyncio
async def test_delete_nonexistent_relation(relation_repository, session_maker):
    """Test deleting a relation that doesn't exist."""
    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.delete_by_fields(session, relation_type="nonexistent")
    assert result is False


# -------------------------------------------------------------------------
# Tests for add_all_ignore_duplicates
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_all_ignore_duplicates_basic(
    relation_repository: RelationRepository,
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Test bulk inserting relations with ON CONFLICT DO NOTHING."""
    relations = [
        Relation(
            from_id=sample_entity.id,
            to_id=related_entity.id,
            to_name=related_entity.title,
            relation_type="links_to",
        ),
        Relation(
            from_id=sample_entity.id,
            to_id=related_entity.id,
            to_name=related_entity.title,
            relation_type="references",
        ),
    ]

    async with db.scoped_session(session_maker) as session:
        inserted = await relation_repository.add_all_ignore_duplicates(session, relations)

        # Both should be inserted
        assert inserted == 2

        # Verify they exist
        found = await relation_repository.find_by_entities(
            session, sample_entity.id, related_entity.id
        )
        assert len(found) == 2
        relation_types = {r.relation_type for r in found}
        assert relation_types == {"links_to", "references"}


@pytest.mark.asyncio
async def test_add_all_ignore_duplicates_skips_duplicates(
    relation_repository: RelationRepository,
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Test that duplicate relations are silently ignored."""
    # Same relation appearing multiple times (common when same [[link]] appears twice in doc)
    relations = [
        Relation(
            from_id=sample_entity.id,
            to_id=None,  # Unresolved
            to_name="Some Target",
            relation_type="links_to",
        ),
        Relation(
            from_id=sample_entity.id,
            to_id=None,
            to_name="Some Target",  # Duplicate!
            relation_type="links_to",
        ),
        Relation(
            from_id=sample_entity.id,
            to_id=None,
            to_name="Some Target",  # Triple duplicate!
            relation_type="links_to",
        ),
    ]

    async with db.scoped_session(session_maker) as session:
        inserted = await relation_repository.add_all_ignore_duplicates(session, relations)

        # Only 1 should be inserted (duplicates ignored)
        assert inserted == 1

        # Verify only one exists
        all_relations = await relation_repository.find_all(session)
        matching = [r for r in all_relations if r.to_name == "Some Target"]
        assert len(matching) == 1


@pytest.mark.asyncio
async def test_add_all_ignore_duplicates_empty_list(
    relation_repository: RelationRepository, session_maker
):
    """Test with empty list returns 0."""
    async with db.scoped_session(session_maker) as session:
        inserted = await relation_repository.add_all_ignore_duplicates(session, [])
    assert inserted == 0


@pytest.mark.asyncio
async def test_add_all_ignore_duplicates_mixed(
    relation_repository: RelationRepository,
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Test with mix of new and duplicate relations."""
    # First, insert one relation
    first_relation = Relation(
        from_id=sample_entity.id,
        to_id=None,
        to_name="Existing Target",
        relation_type="links_to",
    )
    async with db.scoped_session(session_maker) as session:
        await relation_repository.add_all_ignore_duplicates(session, [first_relation])

        # Now try to insert a mix of new and duplicate
        relations = [
            Relation(
                from_id=sample_entity.id,
                to_id=None,
                to_name="Existing Target",  # Duplicate of first_relation
                relation_type="links_to",
            ),
            Relation(
                from_id=sample_entity.id,
                to_id=None,
                to_name="New Target 1",  # New
                relation_type="links_to",
            ),
            Relation(
                from_id=sample_entity.id,
                to_id=None,
                to_name="New Target 2",  # New
                relation_type="references",
            ),
        ]

        inserted = await relation_repository.add_all_ignore_duplicates(session, relations)

        # Only 2 new ones should be inserted
        assert inserted == 2

        # Verify total count
        all_relations = await relation_repository.find_all(session)
        from_sample = [r for r in all_relations if r.from_id == sample_entity.id]
        assert len(from_sample) == 3  # 1 existing + 2 new


@pytest.mark.asyncio
async def test_add_all_ignore_duplicates_with_context(
    relation_repository: RelationRepository,
    sample_entity: Entity,
    related_entity: Entity,
    session_maker,
):
    """Test that context field is properly inserted."""
    relations = [
        Relation(
            from_id=sample_entity.id,
            to_id=related_entity.id,
            to_name=related_entity.title,
            relation_type="links_to",
            context="some context here",
        ),
    ]

    async with db.scoped_session(session_maker) as session:
        inserted = await relation_repository.add_all_ignore_duplicates(session, relations)
        assert inserted == 1

        # Verify context was saved
        found = await relation_repository.find_by_entities(
            session, sample_entity.id, related_entity.id
        )
        assert len(found) == 1
        assert found[0].context == "some context here"


@pytest.mark.asyncio
async def test_replace_accepted_outgoing_relations_inserts_unresolved(
    relation_repository: RelationRepository,
    source_entity: Entity,
    session_maker,
):
    """Accepted-write graph persistence inserts relations unresolved (to_id None)."""
    writes = [
        AcceptedRelationWrite(
            relation_type="works_at",
            target_name="XSYS Target",
            context="employment",
        ),
        AcceptedRelationWrite(relation_type="knows", target_name="Ada", context=None),
    ]
    async with db.scoped_session(session_maker) as session:
        await relation_repository.replace_accepted_outgoing_relations(
            session, source_entity.id, writes
        )

    async with db.scoped_session(session_maker) as session:
        relations = await relation_repository.find_by_type(session, "works_at")
        knows = await relation_repository.find_by_type(session, "knows")

    assert len(relations) == 1
    works_at = relations[0]
    assert works_at.from_id == source_entity.id
    # Targets are written unresolved; the forward-reference job links to_id later.
    assert works_at.to_id is None
    assert works_at.to_name == "XSYS Target"
    assert works_at.context == "employment"
    assert len(knows) == 1
    assert knows[0].to_name == "Ada"


@pytest.mark.asyncio
async def test_replace_accepted_outgoing_relations_replaces_existing_set(
    relation_repository: RelationRepository,
    source_entity: Entity,
    session_maker,
):
    """A second accepted write replaces the prior outgoing relation set atomically."""
    async with db.scoped_session(session_maker) as session:
        await relation_repository.replace_accepted_outgoing_relations(
            session,
            source_entity.id,
            [AcceptedRelationWrite(relation_type="old_rel", target_name="Old", context=None)],
        )

    async with db.scoped_session(session_maker) as session:
        await relation_repository.replace_accepted_outgoing_relations(
            session,
            source_entity.id,
            [AcceptedRelationWrite(relation_type="new_rel", target_name="New", context=None)],
        )

    async with db.scoped_session(session_maker) as session:
        old = await relation_repository.find_by_type(session, "old_rel")
        new = await relation_repository.find_by_type(session, "new_rel")

    assert old == []
    assert [rel.to_name for rel in new] == ["New"]


@pytest.mark.asyncio
async def test_replace_accepted_outgoing_relations_clears_when_empty(
    relation_repository: RelationRepository,
    source_entity: Entity,
    session_maker,
):
    """An empty accepted relation set clears any prior outgoing rows for the entity."""
    async with db.scoped_session(session_maker) as session:
        await relation_repository.replace_accepted_outgoing_relations(
            session,
            source_entity.id,
            [AcceptedRelationWrite(relation_type="stale", target_name="Gone", context=None)],
        )

    async with db.scoped_session(session_maker) as session:
        await relation_repository.replace_accepted_outgoing_relations(session, source_entity.id, [])

    async with db.scoped_session(session_maker) as session:
        remaining = await relation_repository.find_unresolved_relations_for_entity(
            session, source_entity.id
        )

    assert remaining == []
