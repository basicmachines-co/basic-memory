"""Postgres regression coverage for Entity-NoteContent materialization lock order."""

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


class PausingNoteContentRepository(NoteContentRepository):
    """Hold the materializer after NoteContent is updated but before Entity flush."""

    def __init__(self, project_id: int) -> None:
        super().__init__(project_id)
        self.update_applied = asyncio.Event()
        self.release_update = asyncio.Event()

    @override
    async def update_state_fields(
        self,
        session: AsyncSession,
        entity_id: int,
        *,
        expected_db_version: int | None = None,
        **updates: Any,
    ) -> NoteContent | None:
        note_content = await super().update_state_fields(
            session,
            entity_id,
            expected_db_version=expected_db_version,
            **updates,
        )
        self.update_applied.set()
        await self.release_update.wait()
        return note_content


@pytest.mark.asyncio
async def test_materialization_waits_for_entity_before_updating_note_content(
    engine_factory,
    test_project: Project,
) -> None:
    """An Entity-first reconciliation transaction must not deadlock materialization.

    Before the fix, the publisher reaches ``update_applied`` while this test owns
    the Entity row. An Entity-first reconciliation update would then wait on the
    publisher's NoteContent lock while the publisher waits on Entity: the exact
    deadlock cycle reported in #1187.
    """
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
    note_content_repository = PausingNoteContentRepository(test_project.id)
    publisher = RepositoryNoteMaterializationPublisher(
        session_maker=session_maker,
        session_lock=session_lock,
        note_content_store=lambda _project_id: note_content_repository,
    )

    publish_task: asyncio.Task[Any] | None = None
    async with session_maker() as reconciliation_session:
        await reconciliation_session.begin()
        try:
            locked_entity = await reconciliation_session.scalar(
                select(Entity).where(Entity.id == entity_id).with_for_update()
            )
            assert locked_entity is not None

            publish_task = asyncio.create_task(
                publisher.publish_written_file_state(
                    request,
                    prepared_write,
                    written_file,
                )
            )
            await asyncio.wait_for(session_lock.started.wait(), timeout=2)

            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    note_content_repository.update_applied.wait(),
                    timeout=0.5,
                )

            await reconciliation_session.execute(
                update(NoteContent)
                .where(NoteContent.entity_id == entity_id)
                .values(last_materialization_error="entity-first reconciliation")
            )
            await reconciliation_session.commit()

            note_content_repository.release_update.set()
            result = await asyncio.wait_for(publish_task, timeout=2)
        finally:
            note_content_repository.release_update.set()
            if reconciliation_session.in_transaction():
                await reconciliation_session.rollback()
            if publish_task is not None and not publish_task.done():
                publish_task.cancel()
                with suppress(asyncio.CancelledError):
                    await publish_task

    assert result.status is RuntimeNoteMaterializationStatus.written

    async with session_maker() as verification_session:
        note_content = await verification_session.get(NoteContent, entity_id)
        entity = await verification_session.get(Entity, entity_id)

    assert note_content is not None
    assert note_content.file_version == 1
    assert note_content.file_checksum == "materialized-checksum"
    assert note_content.file_write_status == "synced"
    assert entity is not None
    assert entity.mtime == written_file.file_updated_at.timestamp()
    assert entity.size == len(markdown.encode("utf-8"))
