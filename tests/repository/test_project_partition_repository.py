"""Database regressions for strict project partition positions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import AcceptedProjectNoteChange, Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)


_ACCEPTED_AT = datetime(2026, 8, 29, 20, 15, tzinfo=UTC)


def _accepted_change(
    project: Project,
    *,
    partition_position: int,
) -> RuntimeAcceptedProjectNoteChange:
    return RuntimeAcceptedProjectNoteChange(
        project_id=project.id,
        project_external_id=project.external_id,
        partition_position=partition_position,
        entity_id=42,
        note_external_id="note-42",
        title="Accepted evidence",
        operation=RuntimeProjectNoteOperation.updated,
        file_path="notes/accepted-evidence.md",
        accepted_at=_ACCEPTED_AT,
        source="api",
        db_version=3,
        db_checksum="accepted-checksum",
    )


@pytest.mark.asyncio
async def test_project_partition_positions_advance_in_transaction_order(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    repository = ProjectRepository()

    async with db.scoped_session(session_maker) as session:
        first = await repository.advance_partition_position(session, test_project.id)
        second = await repository.advance_partition_position(session, test_project.id)

    assert (first, second) == (1, 2)
    async with db.scoped_session(session_maker) as session:
        persisted = await session.get(Project, test_project.id)
        assert persisted is not None
        assert persisted.partition_position == 2


@pytest.mark.asyncio
async def test_accepted_project_note_change_is_replayable_and_materialization_aware(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    repository = ProjectRepository()

    async with db.scoped_session(session_maker) as session:
        position = await repository.advance_partition_position(session, test_project.id)
        await repository.record_accepted_note_change(
            session,
            _accepted_change(test_project, partition_position=position),
        )

    async with db.scoped_session(session_maker) as session:
        changes = await repository.list_accepted_note_changes(
            session,
            test_project.id,
            after_position=0,
            through_position=1,
        )
        assert len(changes) == 1
        assert changes[0].operation == RuntimeProjectNoteOperation.updated.value
        assert changes[0].db_checksum == "accepted-checksum"
        assert changes[0].materialized_at is None
        assert await repository.mark_accepted_note_change_materialized(
            session,
            test_project.id,
            1,
            materialized_at=_ACCEPTED_AT,
        )

    async with db.scoped_session(session_maker) as session:
        [materialized] = await repository.list_accepted_note_changes(
            session,
            test_project.id,
        )
        assert materialized.materialized_at is not None
        assert not await repository.mark_accepted_note_change_materialized(
            session,
            test_project.id,
            1,
            materialized_at=_ACCEPTED_AT,
        )


@pytest.mark.asyncio
async def test_project_partition_position_rolls_back_with_rejected_transaction(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    repository = ProjectRepository()

    with pytest.raises(RuntimeError, match="reject accepted mutation"):
        async with db.scoped_session(session_maker) as session:
            position = await repository.advance_partition_position(session, test_project.id)
            await repository.record_accepted_note_change(
                session,
                _accepted_change(test_project, partition_position=position),
            )
            raise RuntimeError("reject accepted mutation")

    async with db.scoped_session(session_maker) as session:
        persisted = await session.get(Project, test_project.id)
        assert persisted is not None
        assert persisted.partition_position == 0
        rolled_back_changes = (
            await session.scalars(
                select(AcceptedProjectNoteChange).where(
                    AcceptedProjectNoteChange.project_id == test_project.id
                )
            )
        ).all()
        assert rolled_back_changes == []
        assert await repository.advance_partition_position(session, test_project.id) == 1


@pytest.mark.asyncio
async def test_project_partition_advance_rejects_missing_project(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    repository = ProjectRepository()

    with pytest.raises(RuntimeError, match="project_id=999999"):
        async with db.scoped_session(session_maker) as session:
            await repository.advance_partition_position(session, 999999)
