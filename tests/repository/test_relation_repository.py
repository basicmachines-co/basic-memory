"""Tests for the RelationRepository."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError

from basic_memory import db
from basic_memory.models import Entity, NoteContent, Project, Relation
from basic_memory.repository.relation_repository import (
    AcceptedRelationWrite,
    RelationRepository,
    ResolvedRelationWrite,
    ResolvedRelationWriteResult,
    current_relation_generation_statement,
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


async def set_note_content_generation(
    session_maker,
    *,
    source_entity: Entity,
    test_project: Project,
    generation: int,
) -> None:
    """Create or advance the authoritative generation for one relation source."""
    async with db.scoped_session(session_maker) as session:
        note_content = await session.get(NoteContent, source_entity.id)
        if note_content is None:
            session.add(
                NoteContent(
                    entity_id=source_entity.id,
                    project_id=test_project.id,
                    external_id=source_entity.external_id,
                    file_path=source_entity.file_path,
                    markdown_content=f"# Generation {generation}\n",
                    db_version=generation,
                    db_checksum=f"checksum-{generation}",
                    file_write_status="synced",
                )
            )
            return

        note_content.markdown_content = f"# Generation {generation}\n"
        note_content.db_version = generation
        note_content.db_checksum = f"checksum-{generation}"


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


def test_current_relation_generation_fence_is_portable() -> None:
    """Postgres locks the source row while SQLite safely omits unsupported syntax."""
    statement = current_relation_generation_statement(
        project_id=1,
        entity_id=2,
        generation=3,
    )

    postgres_sql = str(statement.compile(dialect=postgresql.dialect()))
    sqlite_sql = str(statement.compile(dialect=sqlite.dialect()))

    assert "FOR UPDATE" in postgres_sql
    assert "FOR UPDATE" not in sqlite_sql


@pytest.mark.asyncio
async def test_upsert_relation_generation_resets_resolver_owned_target(
    relation_repository: RelationRepository,
    source_entity: Entity,
    target_entity: Entity,
    test_project: Project,
    session_maker,
) -> None:
    """A newer accepted generation owns context while resolution remains derived."""
    await set_note_content_generation(
        session_maker,
        source_entity=source_entity,
        test_project=test_project,
        generation=1,
    )
    write = AcceptedRelationWrite(
        relation_type="documents",
        target_name="Target Alias",
        context="generation one",
    )
    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.upsert_relation_generation(
            session,
            entity_id=source_entity.id,
            generation=1,
            relations=[write],
        )

    assert result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        relation = (await relation_repository.find_by_type(session, "documents"))[0]
        relation.to_id = target_entity.id

    await set_note_content_generation(
        session_maker,
        source_entity=source_entity,
        test_project=test_project,
        generation=2,
    )
    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.upsert_relation_generation(
            session,
            entity_id=source_entity.id,
            generation=2,
            relations=[
                AcceptedRelationWrite(
                    relation_type="documents",
                    target_name="Target Alias",
                    context="generation two",
                )
            ],
        )

    assert result.generation_is_current
    async with db.scoped_session(session_maker) as session:
        relation = (await relation_repository.find_by_type(session, "documents"))[0]

    assert relation.generation == 2
    assert relation.context == "generation two"
    assert relation.to_id is None
    assert relation.to_name == "Target Alias"


@pytest.mark.asyncio
async def test_stale_relation_generation_cannot_reinsert_absent_key_or_cleanup_newer_rows(
    relation_repository: RelationRepository,
    source_entity: Entity,
    test_project: Project,
    session_maker,
) -> None:
    """The source fence makes every statement from an already-stale writer inert."""
    await set_note_content_generation(
        session_maker,
        source_entity=source_entity,
        test_project=test_project,
        generation=2,
    )
    async with db.scoped_session(session_maker) as session:
        current = await relation_repository.upsert_relation_generation(
            session,
            entity_id=source_entity.id,
            generation=2,
            relations=[
                AcceptedRelationWrite(
                    relation_type="current",
                    target_name="Current Target",
                    context=None,
                )
            ],
        )
    assert current.generation_is_current

    async with db.scoped_session(session_maker) as session:
        stale_upsert = await relation_repository.upsert_relation_generation(
            session,
            entity_id=source_entity.id,
            generation=1,
            relations=[
                AcceptedRelationWrite(
                    relation_type="removed",
                    target_name="Removed Target",
                    context=None,
                )
            ],
        )
    async with db.scoped_session(session_maker) as session:
        stale_cleanup = await relation_repository.cleanup_relation_generations(
            session,
            entity_id=source_entity.id,
            generation=1,
        )

    assert not stale_upsert.generation_is_current
    assert not stale_cleanup.generation_is_current
    async with db.scoped_session(session_maker) as session:
        relations = await relation_repository.find_all(session)

    assert [(relation.relation_type, relation.generation) for relation in relations] == [
        ("current", 2)
    ]


@pytest.mark.asyncio
async def test_newer_relation_generation_replaces_then_cleans_older_set(
    relation_repository: RelationRepository,
    source_entity: Entity,
    test_project: Project,
    session_maker,
) -> None:
    """A later generation updates retained identities before removing superseded rows."""
    await set_note_content_generation(
        session_maker,
        source_entity=source_entity,
        test_project=test_project,
        generation=1,
    )
    async with db.scoped_session(session_maker) as session:
        await relation_repository.upsert_relation_generation(
            session,
            entity_id=source_entity.id,
            generation=1,
            relations=[
                AcceptedRelationWrite("old", "Old Target", None),
                AcceptedRelationWrite("shared", "Shared Target", "old context"),
            ],
        )
    async with db.scoped_session(session_maker) as session:
        await relation_repository.cleanup_relation_generations(
            session,
            entity_id=source_entity.id,
            generation=1,
        )

    await set_note_content_generation(
        session_maker,
        source_entity=source_entity,
        test_project=test_project,
        generation=2,
    )
    async with db.scoped_session(session_maker) as session:
        current = await relation_repository.upsert_relation_generation(
            session,
            entity_id=source_entity.id,
            generation=2,
            relations=[
                AcceptedRelationWrite("new", "New Target", None),
                AcceptedRelationWrite("shared", "Shared Target", "new context"),
            ],
        )
    async with db.scoped_session(session_maker) as session:
        cleanup = await relation_repository.cleanup_relation_generations(
            session,
            entity_id=source_entity.id,
            generation=2,
        )

    assert current.generation_is_current
    assert cleanup.generation_is_current
    async with db.scoped_session(session_maker) as session:
        relations = sorted(await relation_repository.find_all(session), key=lambda row: row.to_name)

    assert [
        (relation.relation_type, relation.to_name, relation.context, relation.generation)
        for relation in relations
    ] == [
        ("new", "New Target", None, 2),
        ("shared", "Shared Target", "new context", 2),
    ]


@pytest.mark.asyncio
async def test_empty_relation_generation_cleanup_removes_the_older_set(
    relation_repository: RelationRepository,
    source_entity: Entity,
    test_project: Project,
    session_maker,
) -> None:
    """An accepted empty relation set still publishes through guarded cleanup."""
    await set_note_content_generation(
        session_maker,
        source_entity=source_entity,
        test_project=test_project,
        generation=2,
    )
    async with db.scoped_session(session_maker) as session:
        await relation_repository.add(
            session,
            Relation(
                project_id=test_project.id,
                from_id=source_entity.id,
                to_name="Removed Target",
                relation_type="removed",
                generation=1,
            ),
        )

    async with db.scoped_session(session_maker) as session:
        cleanup = await relation_repository.cleanup_relation_generations(
            session,
            entity_id=source_entity.id,
            generation=2,
        )

    assert cleanup.generation_is_current
    async with db.scoped_session(session_maker) as session:
        assert await relation_repository.find_all(session) == []


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
async def test_apply_resolved_targets_batches_updates_and_duplicate_cleanup(
    relation_repository: RelationRepository,
    source_entity: Entity,
    target_entity: Entity,
    related_entity: Entity,
    test_project: Project,
    session_maker,
):
    """Canonical targets update together while redundant resolved edges are removed."""
    accepted_target = Relation(
        project_id=test_project.id,
        from_id=source_entity.id,
        to_id=None,
        to_name="Target Alias",
        relation_type="documents",
    )
    accepted_related = Relation(
        project_id=test_project.id,
        from_id=source_entity.id,
        to_id=None,
        to_name="Related Alias",
        relation_type="references",
    )
    existing_edge = Relation(
        project_id=test_project.id,
        from_id=source_entity.id,
        to_id=target_entity.id,
        to_name=target_entity.title,
        relation_type="links_to",
    )
    redundant_unresolved = Relation(
        project_id=test_project.id,
        from_id=source_entity.id,
        to_id=None,
        to_name="Duplicate Target Alias",
        relation_type="links_to",
    )
    async with db.scoped_session(session_maker) as session:
        session.add_all([accepted_target, accepted_related, existing_edge, redundant_unresolved])
        await session.flush()
        accepted_target_id = accepted_target.id
        accepted_related_id = accepted_related.id
        redundant_unresolved_id = redundant_unresolved.id

    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.apply_resolved_targets(
            session,
            [
                ResolvedRelationWrite(
                    relation_id=accepted_related_id,
                    from_id=source_entity.id,
                    original_target_name="Related Alias",
                    target_id=related_entity.id,
                    target_external_id=related_entity.external_id,
                    target_name=related_entity.title,
                    relation_type="references",
                ),
                ResolvedRelationWrite(
                    relation_id=redundant_unresolved_id,
                    from_id=source_entity.id,
                    original_target_name="Duplicate Target Alias",
                    target_id=target_entity.id,
                    target_external_id=target_entity.external_id,
                    target_name=target_entity.title,
                    relation_type="links_to",
                ),
                ResolvedRelationWrite(
                    relation_id=accepted_target_id,
                    from_id=source_entity.id,
                    original_target_name="Target Alias",
                    target_id=target_entity.id,
                    target_external_id=target_entity.external_id,
                    target_name=target_entity.title,
                    relation_type="documents",
                ),
            ],
        )

    assert result == ResolvedRelationWriteResult(
        affected_entity_ids=frozenset({source_entity.id}),
        duplicate_relation_ids=(redundant_unresolved_id,),
    )

    async with db.scoped_session(session_maker) as session:
        documents = await relation_repository.find_by_type(session, "documents")
        references = await relation_repository.find_by_type(session, "references")
        links = await relation_repository.find_by_type(session, "links_to")
        redundant = await relation_repository.find_by_id(session, redundant_unresolved_id)

    assert [(relation.to_id, relation.to_name) for relation in documents] == [
        (target_entity.id, target_entity.title)
    ]
    assert [(relation.to_id, relation.to_name) for relation in references] == [
        (related_entity.id, related_entity.title)
    ]
    assert [(relation.to_id, relation.to_name) for relation in links] == [
        (target_entity.id, target_entity.title)
    ]
    assert redundant is None


@pytest.mark.asyncio
async def test_apply_resolved_targets_preserves_canonical_name_swaps(
    relation_repository: RelationRepository,
    source_entity: Entity,
    target_entity: Entity,
    related_entity: Entity,
    test_project: Project,
    session_maker,
):
    """Relations exchanging occupied names remain valid when planned together."""
    first_relation = Relation(
        project_id=test_project.id,
        from_id=source_entity.id,
        to_id=None,
        to_name=target_entity.title,
        relation_type="renames_to",
    )
    second_relation = Relation(
        project_id=test_project.id,
        from_id=source_entity.id,
        to_id=None,
        to_name=related_entity.title,
        relation_type="renames_to",
    )
    async with db.scoped_session(session_maker) as session:
        session.add_all([first_relation, second_relation])
        await session.flush()
        first_relation_id = first_relation.id
        second_relation_id = second_relation.id

    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.apply_resolved_targets(
            session,
            [
                ResolvedRelationWrite(
                    relation_id=first_relation_id,
                    from_id=source_entity.id,
                    original_target_name=target_entity.title,
                    target_id=related_entity.id,
                    target_external_id=related_entity.external_id,
                    target_name=related_entity.title,
                    relation_type="renames_to",
                ),
                ResolvedRelationWrite(
                    relation_id=second_relation_id,
                    from_id=source_entity.id,
                    original_target_name=related_entity.title,
                    target_id=target_entity.id,
                    target_external_id=target_entity.external_id,
                    target_name=target_entity.title,
                    relation_type="renames_to",
                ),
            ],
        )

    assert result.duplicate_relation_ids == ()
    async with db.scoped_session(session_maker) as session:
        relations = await relation_repository.find_by_type(session, "renames_to")

    assert {(relation.id, relation.to_id, relation.to_name) for relation in relations} == {
        (first_relation_id, related_entity.id, related_entity.title),
        (second_relation_id, target_entity.id, target_entity.title),
    }


@pytest.mark.asyncio
async def test_apply_resolved_targets_bounds_sql_for_oversized_collision_domain(
    relation_repository: RelationRepository,
    source_entity: Entity,
    test_project: Project,
    session_maker,
):
    """One large name-swap domain stays atomic without unbounded SQL expressions."""
    domain_size = 1_100
    now = datetime.now(timezone.utc)
    target_entities = [
        Entity(
            project_id=test_project.id,
            title=f"Large Domain Target {index}",
            note_type="test",
            permalink=f"large-domain/target-{index}",
            file_path=f"large-domain/target-{index}.md",
            content_type="text/markdown",
            created_at=now,
            updated_at=now,
        )
        for index in range(domain_size)
    ]
    async with db.scoped_session(session_maker) as session:
        session.add_all(target_entities)
        await session.flush()
        unresolved_relations = [
            Relation(
                project_id=test_project.id,
                from_id=source_entity.id,
                to_id=None,
                to_name=target_entities[(index + 1) % domain_size].title,
                relation_type="large_domain",
            )
            for index in range(domain_size)
        ]
        session.add_all(unresolved_relations)
        await session.flush()
        writes = [
            ResolvedRelationWrite(
                relation_id=relation.id,
                from_id=source_entity.id,
                original_target_name=relation.to_name,
                target_id=target.id,
                target_external_id=target.external_id,
                target_name=target.title,
                relation_type=relation.relation_type,
            )
            for relation, target in zip(unresolved_relations, target_entities, strict=True)
        ]
        expected_targets = {(target.id, target.title) for target in target_entities}

    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.apply_resolved_targets(session, writes)

    assert result == ResolvedRelationWriteResult(
        affected_entity_ids=frozenset({source_entity.id}),
        duplicate_relation_ids=(),
    )
    async with db.scoped_session(session_maker) as session:
        resolved_targets = set(
            (
                await session.execute(
                    select(Relation.to_id, Relation.to_name).where(
                        Relation.project_id == test_project.id,
                        Relation.relation_type == "large_domain",
                    )
                )
            )
            .tuples()
            .all()
        )

    assert resolved_targets == expected_targets


@pytest.mark.asyncio
async def test_apply_resolved_targets_skips_reused_target_identity(
    relation_repository: RelationRepository,
    source_entity: Entity,
    target_entity: Entity,
    test_project: Project,
    session_maker,
):
    """A stale target ID cannot connect an edge to a replacement entity."""
    unresolved_relation = Relation(
        project_id=test_project.id,
        from_id=source_entity.id,
        to_id=None,
        to_name="Original Target Alias",
        relation_type="documents",
    )
    async with db.scoped_session(session_maker) as session:
        session.add(unresolved_relation)
        await session.flush()
        relation_id = unresolved_relation.id

    stale_write = ResolvedRelationWrite(
        relation_id=relation_id,
        from_id=source_entity.id,
        original_target_name=unresolved_relation.to_name,
        target_id=target_entity.id,
        target_external_id=target_entity.external_id,
        target_name=target_entity.title,
        relation_type=unresolved_relation.relation_type,
    )

    # Trigger: the snapshotted target is deleted before relation repair commits.
    # Why: SQLite can reuse its numeric ID for an unrelated entity.
    # Outcome: the replacement has the same database ID but a new stable external ID.
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        await session.execute(delete(Entity).where(Entity.id == target_entity.id))
        replacement = Entity(
            id=target_entity.id,
            project_id=test_project.id,
            title="Replacement Target",
            note_type="test",
            permalink="target/replacement-target",
            file_path="target/replacement_target.md",
            content_type="text/markdown",
            created_at=now,
            updated_at=now,
        )
        session.add(replacement)
        await session.flush()
        replacement_external_id = replacement.external_id

    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.apply_resolved_targets(session, [stale_write])

    assert result == ResolvedRelationWriteResult(
        affected_entity_ids=frozenset(),
        duplicate_relation_ids=(),
        stale_relation_ids=(relation_id,),
    )
    assert replacement_external_id != stale_write.target_external_id
    async with db.scoped_session(session_maker) as session:
        relation = await relation_repository.find_by_id(session, relation_id)

    assert relation is not None
    assert relation.to_id is None
    assert relation.to_name == "Original Target Alias"


@pytest.mark.asyncio
async def test_apply_resolved_targets_skips_reused_relation_identity(
    relation_repository: RelationRepository,
    source_entity: Entity,
    target_entity: Entity,
    test_project: Project,
    session_maker,
):
    """A stale command cannot resolve a replacement row that reused its database ID."""
    original_relation = Relation(
        project_id=test_project.id,
        from_id=source_entity.id,
        to_id=None,
        to_name="Original Alias",
        relation_type="documents",
    )
    async with db.scoped_session(session_maker) as session:
        session.add(original_relation)
        await session.flush()
        reused_relation_id = original_relation.id

    stale_write = ResolvedRelationWrite(
        relation_id=reused_relation_id,
        from_id=source_entity.id,
        original_target_name=original_relation.to_name,
        target_id=target_entity.id,
        target_external_id=target_entity.external_id,
        target_name=target_entity.title,
        relation_type=original_relation.relation_type,
    )

    # Trigger: accepted-note replacement deletes the old unresolved row before repair writes.
    # Why: SQLite may reuse the deleted row ID for a different outgoing relation.
    # Outcome: the replacement deliberately carries the same ID but a different identity.
    async with db.scoped_session(session_maker) as session:
        assert await relation_repository.delete(session, reused_relation_id)
        session.add(
            Relation(
                id=reused_relation_id,
                project_id=test_project.id,
                from_id=source_entity.id,
                to_id=None,
                to_name="Replacement Alias",
                relation_type="supersedes",
            )
        )

    async with db.scoped_session(session_maker) as session:
        result = await relation_repository.apply_resolved_targets(session, [stale_write])

    assert result == ResolvedRelationWriteResult(
        affected_entity_ids=frozenset(),
        duplicate_relation_ids=(),
        stale_relation_ids=(reused_relation_id,),
    )
    async with db.scoped_session(session_maker) as session:
        replacement = await relation_repository.find_by_id(session, reused_relation_id)
        pending_refreshes = await relation_repository.list_pending_search_refreshes(session)

    assert replacement is not None
    assert replacement.to_id is None
    assert replacement.to_name == "Replacement Alias"
    assert replacement.relation_type == "supersedes"
    assert pending_refreshes == []
