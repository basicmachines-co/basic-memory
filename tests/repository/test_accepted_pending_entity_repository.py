"""Tests for DB-accepted entity rows that are waiting on file materialization."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from basic_memory import db
from basic_memory.models import Entity, Project
from basic_memory.repository.entity_repository import (
    AcceptedPendingEntityWrite,
    EntityRepository,
)


@pytest.mark.asyncio
async def test_create_pending_accepted_entity_inserts_unmaterialized_entity(
    session_maker,
    test_project: Project,
):
    """Accepted DB-first note writes should create an entity without a file checksum."""
    repository = EntityRepository(project_id=test_project.id)
    now = datetime.now(timezone.utc)
    external_id = str(uuid4())

    async with db.scoped_session(session_maker) as session:
        created = await repository.create_pending_accepted_entity(
            session,
            AcceptedPendingEntityWrite(
                title="Accepted Note",
                note_type="note",
                entity_metadata={"status": "draft"},
                content_type="text/markdown",
                permalink="accepted-note",
                file_path="notes/accepted.md",
                created_at=now,
                updated_at=now,
                created_by="user-profile-id",
                last_updated_by="user-profile-id",
                external_id=external_id,
            ),
        )

        found = await session.get(Entity, created.id)

    assert created.id is not None
    assert found is not None
    assert found.external_id == external_id
    assert found.title == "Accepted Note"
    assert found.note_type == "note"
    assert found.entity_metadata == {"status": "draft"}
    assert found.project_id == test_project.id
    assert found.permalink == "accepted-note"
    assert found.file_path == "notes/accepted.md"
    assert found.checksum is None
    assert found.created_at == now
    assert found.updated_at == now
    assert found.created_by == "user-profile-id"
    assert found.last_updated_by == "user-profile-id"


@pytest.mark.asyncio
async def test_create_pending_accepted_entity_allows_generated_external_id(
    session_maker,
    test_project: Project,
):
    """Local callers can omit external_id and let the entity model create one."""
    repository = EntityRepository(project_id=test_project.id)
    now = datetime.now(timezone.utc)

    async with db.scoped_session(session_maker) as session:
        created = await repository.create_pending_accepted_entity(
            session,
            AcceptedPendingEntityWrite(
                title="Generated External Id",
                note_type="note",
                entity_metadata=None,
                content_type="text/markdown",
                permalink="generated-external-id",
                file_path="notes/generated.md",
                created_at=now,
                updated_at=now,
                created_by=None,
                last_updated_by=None,
            ),
        )

    assert created.external_id
    assert created.checksum is None
    assert created.project_id == test_project.id
