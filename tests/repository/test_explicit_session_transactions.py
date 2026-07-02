"""Regression coverage for caller-owned repository transactions."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from basic_memory.models import Entity, NoteContent, Observation, Project, Relation
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.relation_repository import RelationRepository


def _entity_payload(project_id: int, title: str, file_path: str) -> dict:
    """Build the minimal entity payload used by transaction tests."""
    return {
        "project_id": project_id,
        "title": title,
        "note_type": "note",
        "permalink": file_path.removesuffix(".md"),
        "file_path": file_path,
        "content_type": "text/markdown",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }


def _note_content_payload(entity_id: int) -> dict:
    """Build the minimal note_content payload used by transaction tests."""
    return {
        "entity_id": entity_id,
        "markdown_content": "# Draft content",
        "db_version": 1,
        "db_checksum": "db-checksum",
        "file_write_status": "pending",
        "last_source": "api",
        "updated_at": datetime.now(timezone.utc),
    }


@pytest.mark.asyncio
async def test_repositories_share_uncommitted_state_in_one_session(session_maker, config_home):
    """Later repository calls should see earlier uncommitted writes in the same session."""
    project_repository = ProjectRepository()

    async with session_maker() as session:
        project = await project_repository.create(
            session,
            {
                "name": "explicit-session-project",
                "path": str(config_home / "explicit-session-project"),
                "is_active": True,
            },
        )
        entity_repository = EntityRepository(project_id=project.id)
        note_content_repository = NoteContentRepository(project_id=project.id)
        observation_repository = ObservationRepository(project_id=project.id)
        relation_repository = RelationRepository(project_id=project.id)

        source = await entity_repository.create(
            session, _entity_payload(project.id, "Source Note", "notes/source.md")
        )
        target = await entity_repository.create(
            session, _entity_payload(project.id, "Target Note", "notes/target.md")
        )
        note_content = await note_content_repository.upsert(
            session, _note_content_payload(source.id)
        )
        observation = await observation_repository.create(
            session,
            {
                "entity_id": source.id,
                "content": "Uncommitted observation",
                "category": "note",
            },
        )
        relation = await relation_repository.create(
            session,
            {
                "from_id": source.id,
                "to_id": target.id,
                "to_name": target.title,
                "relation_type": "links_to",
            },
        )

        found_project = await project_repository.get_by_external_id(session, project.external_id)
        found_source = await entity_repository.get_by_file_path(session, "notes/source.md")
        found_note_content = await note_content_repository.get_by_entity_id(session, source.id)
        found_observations = await observation_repository.find_by_entity(session, source.id)
        found_relations = await relation_repository.find_by_entities(session, source.id, target.id)

        assert found_project is not None
        assert found_project.id == project.id
        assert found_source is not None
        assert found_source.id == source.id
        assert found_note_content is not None
        assert found_note_content.entity_id == note_content.entity_id
        assert [item.id for item in found_observations] == [observation.id]
        assert [item.id for item in found_relations] == [relation.id]


@pytest.mark.asyncio
async def test_rollback_discards_related_repository_writes(session_maker, config_home):
    """A caller-owned rollback should discard all related repository writes."""
    project_repository = ProjectRepository()

    async with session_maker() as session:
        transaction = await session.begin()
        project = await project_repository.create(
            session,
            {
                "name": "rollback-explicit-session",
                "path": str(config_home / "rollback-explicit-session"),
                "is_active": True,
            },
        )
        entity_repository = EntityRepository(project_id=project.id)
        note_content_repository = NoteContentRepository(project_id=project.id)
        observation_repository = ObservationRepository(project_id=project.id)
        relation_repository = RelationRepository(project_id=project.id)

        source = await entity_repository.create(
            session, _entity_payload(project.id, "Rollback Source", "rollback/source.md")
        )
        target = await entity_repository.create(
            session, _entity_payload(project.id, "Rollback Target", "rollback/target.md")
        )
        await note_content_repository.upsert(session, _note_content_payload(source.id))
        await observation_repository.create(
            session,
            {
                "entity_id": source.id,
                "content": "Rolled back observation",
                "category": "note",
            },
        )
        await relation_repository.create(
            session,
            {
                "from_id": source.id,
                "to_id": target.id,
                "to_name": target.title,
                "relation_type": "links_to",
            },
        )

        await transaction.rollback()

    async with session_maker() as session:
        assert (
            await session.scalar(select(Project).where(Project.name == "rollback-explicit-session"))
            is None
        )
        assert (
            await session.scalar(select(Entity).where(Entity.file_path == "rollback/source.md"))
            is None
        )
        assert await session.scalar(select(NoteContent)) is None
        assert await session.scalar(select(Observation)) is None
        assert await session.scalar(select(Relation)) is None
