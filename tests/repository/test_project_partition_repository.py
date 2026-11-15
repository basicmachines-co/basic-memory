"""Database regressions for strict project partition positions."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import AcceptedProjectNoteChange, Project
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.runtime.project_partition import (
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)


_ACCEPTED_AT = datetime(2026, 8, 29, 20, 15, tzinfo=UTC)


def test_project_delete_delegates_partition_journal_cleanup_to_database() -> None:
    relationship = inspect(Project).relationships["accepted_note_changes"]

    assert relationship.passive_deletes is True


def _accepted_change(
    project: Project,
    *,
    partition_position: int,
    entity_id: int = 42,
    note_external_id: str | None = None,
    db_version: int = 3,
) -> RuntimeAcceptedProjectNoteChange:
    return RuntimeAcceptedProjectNoteChange(
        project_id=project.id,
        project_external_id=project.external_id,
        partition_position=partition_position,
        entity_id=entity_id,
        note_external_id=note_external_id or f"note-{entity_id}",
        permalink=f"accepted-evidence-{entity_id}",
        title="Accepted evidence",
        operation=RuntimeProjectNoteOperation.updated,
        file_path="notes/accepted-evidence.md",
        accepted_at=_ACCEPTED_AT,
        source="api",
        db_version=db_version,
        db_checksum=f"accepted-checksum-{db_version}",
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
        assert changes[0].permalink == "accepted-evidence-42"
        assert changes[0].db_checksum == "accepted-checksum-3"
        assert changes[0].accepted_at.tzinfo is not None
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
        assert materialized.materialized_at.tzinfo is not None
        assert not await repository.mark_accepted_note_change_materialized(
            session,
            test_project.id,
            1,
            materialized_at=_ACCEPTED_AT,
        )


@pytest.mark.asyncio
async def test_materializing_newer_note_change_satisfies_superseded_positions(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    repository = ProjectRepository()

    async with db.scoped_session(session_maker) as session:
        for position, entity_id in ((1, 42), (2, 99), (3, 42)):
            claimed_position = await repository.advance_partition_position(
                session,
                test_project.id,
            )
            assert claimed_position == position
            await repository.record_accepted_note_change(
                session,
                _accepted_change(
                    test_project,
                    partition_position=position,
                    entity_id=entity_id,
                    db_version=position,
                ),
            )

        assert await repository.mark_accepted_note_change_materialized(
            session,
            test_project.id,
            3,
            materialized_at=_ACCEPTED_AT,
        )

    async with db.scoped_session(session_maker) as session:
        changes = await repository.list_accepted_note_changes(session, test_project.id)
        assert [change.materialized_at is not None for change in changes] == [True, False, True]


@pytest.mark.asyncio
async def test_materializing_note_change_does_not_match_reused_entity_id(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    repository = ProjectRepository()

    async with db.scoped_session(session_maker) as session:
        for position, note_external_id in ((1, "deleted-note"), (2, "replacement-note")):
            claimed_position = await repository.advance_partition_position(
                session,
                test_project.id,
            )
            assert claimed_position == position
            await repository.record_accepted_note_change(
                session,
                _accepted_change(
                    test_project,
                    partition_position=position,
                    entity_id=42,
                    note_external_id=note_external_id,
                    db_version=position,
                ),
            )

        assert await repository.mark_accepted_note_change_materialized(
            session,
            test_project.id,
            2,
            materialized_at=_ACCEPTED_AT,
        )

    async with db.scoped_session(session_maker) as session:
        changes = await repository.list_accepted_note_changes(session, test_project.id)
        assert [change.materialized_at is not None for change in changes] == [False, True]


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


@pytest.mark.asyncio
async def test_list_accepted_note_changes_limit_keeps_lowest_positions(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    repository = ProjectRepository()

    async with db.scoped_session(session_maker) as session:
        for _ in range(4):
            position = await repository.advance_partition_position(session, test_project.id)
            await repository.record_accepted_note_change(
                session,
                _accepted_change(test_project, partition_position=position, db_version=position),
            )

    async with db.scoped_session(session_maker) as session:
        assert await repository.get_partition_position(session, test_project.id) == 4
        first_page = await repository.list_accepted_note_changes(
            session, test_project.id, after_position=0, through_position=4, limit=3
        )
        assert [change.partition_position for change in first_page] == [1, 2, 3]
        second_page = await repository.list_accepted_note_changes(
            session, test_project.id, after_position=3, through_position=4, limit=3
        )
        assert [change.partition_position for change in second_page] == [4]
        unlimited = await repository.list_accepted_note_changes(session, test_project.id)
        assert [change.partition_position for change in unlimited] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_get_partition_position_is_none_for_missing_project(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with db.scoped_session(session_maker) as session:
        assert await ProjectRepository().get_partition_position(session, 999999) is None


@pytest.mark.asyncio
async def test_bulk_partition_claim_returns_last_of_consecutive_positions(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    repository = ProjectRepository()

    async with db.scoped_session(session_maker) as session:
        assert await repository.advance_partition_position(session, test_project.id) == 1
        # Claims 2, 3 and 4 in one statement; the head is the last of them.
        assert await repository.advance_partition_position_by(session, test_project.id, 3) == 4
        assert await repository.advance_partition_position(session, test_project.id) == 5

    async with db.scoped_session(session_maker) as session:
        assert await repository.get_partition_position(session, test_project.id) == 5


@pytest.mark.asyncio
async def test_bulk_partition_claim_rejects_non_positive_count_and_missing_project(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    repository = ProjectRepository()

    async with db.scoped_session(session_maker) as session:
        with pytest.raises(ValueError, match="must be positive"):
            await repository.advance_partition_position_by(session, test_project.id, 0)
        with pytest.raises(RuntimeError, match="project_id=999999"):
            await repository.advance_partition_position_by(session, 999999, 2)
        assert await repository.get_partition_position(session, test_project.id) == 0


@pytest.mark.asyncio
async def test_bulk_record_persists_every_change_with_its_columns(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    repository = ProjectRepository()
    changes = [
        _accepted_change(test_project, partition_position=1, entity_id=7),
        _accepted_change(test_project, partition_position=2, entity_id=8, db_version=5),
    ]

    async with db.scoped_session(session_maker) as session:
        await repository.advance_partition_position_by(session, test_project.id, 2)
        await repository.record_accepted_note_changes(session, changes)
        # An empty batch is a no-op, not an empty INSERT.
        await repository.record_accepted_note_changes(session, [])

    async with db.scoped_session(session_maker) as session:
        persisted = await repository.list_accepted_note_changes(session, test_project.id)

    assert [
        (
            row.partition_position,
            row.entity_id,
            row.note_external_id,
            row.operation,
            row.db_version,
            row.db_checksum,
            row.accepted_at.tzinfo is not None,
            row.materialized_at,
        )
        for row in persisted
    ] == [
        (1, 7, "note-7", "updated", 3, "accepted-checksum-3", True, None),
        (2, 8, "note-8", "updated", 5, "accepted-checksum-5", True, None),
    ]


@pytest.mark.asyncio
async def test_bulk_record_can_write_rows_already_materialized(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """A change that is already in storage is written settled, so no projector waits on it."""
    repository = ProjectRepository()
    materialized_at = datetime(2026, 8, 29, 20, 16, tzinfo=UTC)

    async with db.scoped_session(session_maker) as session:
        await repository.advance_partition_position_by(session, test_project.id, 2)
        await repository.record_accepted_note_changes(
            session,
            [
                _accepted_change(test_project, partition_position=1, entity_id=7),
                _accepted_change(test_project, partition_position=2, entity_id=8),
            ],
            materialized_at=materialized_at,
        )

    async with db.scoped_session(session_maker) as session:
        persisted = await repository.list_accepted_note_changes(session, test_project.id)

    # SQLite drops the zone on write, so compare against the row's own accepted_at,
    # which round-trips the same way, rather than against an absolute instant.
    settled_after_acceptance: list[tuple[int, timedelta]] = []
    for row in persisted:
        assert row.materialized_at is not None
        settled_after_acceptance.append(
            (row.partition_position, row.materialized_at - row.accepted_at)
        )
    assert settled_after_acceptance == [
        (1, materialized_at - _ACCEPTED_AT),
        (2, materialized_at - _ACCEPTED_AT),
    ]


@pytest.mark.asyncio
async def test_bulk_materialization_settles_targets_and_their_older_generations(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """Each target position settles itself and older rows of the same note only.

    Rows of other notes, and newer rows of a target's note, stay unsettled.
    """
    repository = ProjectRepository()
    # position -> note: 1 a, 2 b, 3 a, 4 c, 5 b
    layout = ((1, 1), (2, 2), (3, 1), (4, 3), (5, 2))

    async with db.scoped_session(session_maker) as session:
        await repository.advance_partition_position_by(session, test_project.id, len(layout))
        await repository.record_accepted_note_changes(
            session,
            [
                _accepted_change(
                    test_project,
                    partition_position=position,
                    entity_id=entity_id,
                    db_version=position,
                )
                for position, entity_id in layout
            ],
        )

    async with db.scoped_session(session_maker) as session:
        settled = await repository.mark_accepted_note_changes_materialized(
            session,
            test_project.id,
            [3, 2, 3],
            materialized_at=_ACCEPTED_AT,
        )
        # Nothing new to settle the second time.
        assert (
            await repository.mark_accepted_note_changes_materialized(
                session,
                test_project.id,
                [3, 2],
                materialized_at=_ACCEPTED_AT,
            )
            == 0
        )
        assert (
            await repository.mark_accepted_note_changes_materialized(
                session,
                test_project.id,
                [],
                materialized_at=_ACCEPTED_AT,
            )
            == 0
        )

    async with db.scoped_session(session_maker) as session:
        changes = await repository.list_accepted_note_changes(session, test_project.id)

    assert settled == 3
    assert [change.materialized_at is not None for change in changes] == [
        True,  # 1: older generation of note a (target 3)
        True,  # 2: target
        True,  # 3: target
        False,  # 4: note c, not targeted
        False,  # 5: newer generation of note b than target 2
    ]


@pytest.mark.asyncio
async def test_settle_unmaterialized_deletes_retires_deleted_notes_only(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """Unsettled `deleted` rows settle with their note's older rows; other notes stay."""
    repository = ProjectRepository()
    # position -> (note, operation): 1 a updated, 2 b updated, 3 a deleted, 4 b updated
    layout = (
        (1, 1, RuntimeProjectNoteOperation.updated),
        (2, 2, RuntimeProjectNoteOperation.updated),
        (3, 1, RuntimeProjectNoteOperation.deleted),
        (4, 2, RuntimeProjectNoteOperation.updated),
    )

    async with db.scoped_session(session_maker) as session:
        await repository.advance_partition_position_by(session, test_project.id, len(layout))
        await repository.record_accepted_note_changes(
            session,
            [
                replace(
                    _accepted_change(
                        test_project,
                        partition_position=position,
                        entity_id=entity_id,
                        db_version=position,
                    ),
                    operation=operation,
                )
                for position, entity_id, operation in layout
            ],
        )

    async with db.scoped_session(session_maker) as session:
        settled = await repository.settle_unmaterialized_deletes(
            session,
            test_project.id,
            materialized_at=_ACCEPTED_AT,
        )
        # Nothing is left to settle the second time.
        assert (
            await repository.settle_unmaterialized_deletes(
                session,
                test_project.id,
                materialized_at=_ACCEPTED_AT,
            )
            == 0
        )

    async with db.scoped_session(session_maker) as session:
        changes = await repository.list_accepted_note_changes(session, test_project.id)

    assert settled == 2
    assert [change.materialized_at is not None for change in changes] == [
        True,  # 1: older generation of the deleted note a
        False,  # 2: note b, not deleted
        True,  # 3: the delete
        False,  # 4: note b, not deleted
    ]
