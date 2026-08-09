"""Postgres regression coverage for NoteContent-Entity materialization lock order."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, override

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory import db
from basic_memory.indexing.note_materialization_runner import (
    NoteMaterializationSessionLock,
    RepositoryNoteMaterializationPublisher,
)
from basic_memory.indexing.accepted_note_write_runner import (
    lock_accepted_note_content_for_entity_mutation,
)
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.runtime.note_content import (
    RuntimeNoteMaterializationJobRequest,
    RuntimeNoteMaterializationStatus,
)
from basic_memory.runtime.note_materialization import (
    RuntimeWrittenFileState,
    plan_prepared_note_write,
)


class StartedMaterializationLock(NoteMaterializationSessionLock):
    """Expose that the publisher entered its transaction without adding a DB lock."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    @override
    async def lock_note_materialization(
        self,
        session: AsyncSession,
        *,
        project_id: int,
        entity_id: int,
    ) -> None:
        self.started.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("move_during_wait", [False, True], ids=["stable-path", "moved-path"])
async def test_materialization_and_accepted_mutation_share_note_content_first_order(
    engine_factory,
    test_project: Project,
    move_during_wait: bool,
) -> None:
    """An accepted mutation can take Entity while materialization waits on NoteContent."""
    engine, session_maker = engine_factory
    if engine.dialect.name != "postgresql":
        pytest.skip("row-lock ordering requires PostgreSQL")

    file_path = "notes/lock-order.md"
    markdown = "# Lock order\n"
    db_checksum = "accepted-checksum"
    async with db.scoped_session(session_maker) as session:
        entity = Entity(
            project_id=test_project.id,
            title="Lock order",
            note_type="note",
            content_type="text/markdown",
            file_path=file_path,
            checksum="previous-file-checksum",
        )
        session.add(entity)
        await session.flush()
        entity_id = entity.id
        await NoteContentRepository(project_id=test_project.id).create(
            session,
            NoteContent(
                entity_id=entity_id,
                markdown_content=markdown,
                db_version=1,
                db_checksum=db_checksum,
                file_version=None,
                file_checksum="previous-file-checksum",
                file_write_status="writing",
            ),
        )

    request = RuntimeNoteMaterializationJobRequest(
        project_id=test_project.id,
        entity_id=entity_id,
        db_version=1,
        db_checksum=db_checksum,
        source="api",
    )
    attempted_at = datetime(2026, 8, 5, 1, 0, tzinfo=UTC)
    prepared_write = plan_prepared_note_write(
        request=request,
        file_path=file_path,
        markdown_content=markdown,
        previous_file_checksum="previous-file-checksum",
        attempted_at=attempted_at,
    )
    written_file = RuntimeWrittenFileState(
        file_path=file_path,
        file_checksum="materialized-checksum",
        file_updated_at=datetime(2026, 8, 5, 1, 1, tzinfo=UTC),
    )
    session_lock = StartedMaterializationLock()
    publisher = RepositoryNoteMaterializationPublisher(
        session_maker=session_maker,
        session_lock=session_lock,
    )

    publish_task: asyncio.Task[Any] | None = None
    async with session_maker() as mutation_session:
        await mutation_session.begin()
        try:
            await lock_accepted_note_content_for_entity_mutation(
                mutation_session,
                project_id=test_project.id,
                entity_id=entity_id,
            )

            publish_task = asyncio.create_task(
                publisher.publish_written_file_state(
                    request,
                    prepared_write,
                    written_file,
                )
            )
            await asyncio.wait_for(session_lock.started.wait(), timeout=2)
            # Let PostgreSQL enqueue the materializer's NoteContent lock. With
            # the old Entity-first order, the next lock request closes a cycle.
            await asyncio.sleep(0.1)

            locked_entity = await asyncio.wait_for(
                mutation_session.scalar(
                    select(Entity).where(Entity.id == entity_id).with_for_update()
                ),
                timeout=2,
            )
            assert locked_entity is not None

            if move_during_wait:
                locked_entity.file_path = "notes/moved-during-materialization.md"
                await mutation_session.execute(
                    update(NoteContent)
                    .where(NoteContent.entity_id == entity_id)
                    .values(file_path=locked_entity.file_path)
                )
            else:
                locked_entity.title = "Accepted mutation completed"
                await mutation_session.execute(
                    update(NoteContent)
                    .where(NoteContent.entity_id == entity_id)
                    .values(last_materialization_error="accepted mutation overlap")
                )
            await mutation_session.commit()

            result = await asyncio.wait_for(publish_task, timeout=2)
        finally:
            if mutation_session.in_transaction():
                await mutation_session.rollback()
            if publish_task is not None and not publish_task.done():
                publish_task.cancel()
                with suppress(asyncio.CancelledError):
                    await publish_task

    async with session_maker() as verification_session:
        note_content = await verification_session.get(NoteContent, entity_id)
        entity = await verification_session.get(Entity, entity_id)

    assert note_content is not None
    assert entity is not None
    if move_during_wait:
        assert result.status is RuntimeNoteMaterializationStatus.stale
        assert result.written_file_orphaned
        assert note_content.file_path == "notes/moved-during-materialization.md"
        assert note_content.file_version is None
        assert note_content.file_checksum == "previous-file-checksum"
        assert entity.file_path == "notes/moved-during-materialization.md"
        assert entity.mtime != written_file.file_updated_at.timestamp()
    else:
        assert result.status is RuntimeNoteMaterializationStatus.written
        assert entity.title == "Accepted mutation completed"
        assert note_content.file_version == 1
        assert note_content.file_checksum == "materialized-checksum"
        assert note_content.file_write_status == "synced"
        assert entity.mtime == written_file.file_updated_at.timestamp()
        assert entity.size == len(markdown.encode("utf-8"))
