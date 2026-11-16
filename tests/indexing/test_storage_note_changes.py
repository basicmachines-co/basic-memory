"""Journal rows for note changes that arrive through storage.

These tests drive the shared writer against a real database: the rows it writes,
the positions it claims, and the changes it leaves out.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.storage_note_changes import (
    StorageNoteChange,
    StorageNoteChangeJournal,
    StorageNoteIdentity,
    load_storage_note_identities,
    record_storage_note_changes,
    settle_stored_note_changes,
)
from basic_memory.models import Entity, NoteContent, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.runtime.project_partition import (
    ACCEPTED_STORAGE_CHANGE_SOURCE,
    RuntimeAcceptedProjectNoteChange,
    RuntimeProjectNoteOperation,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


async def create_entity(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    *,
    file_path: str,
    permalink: str | None,
    content_type: str = "text/markdown",
) -> Entity:
    async with db.scoped_session(session_maker) as session:
        return await EntityRepository(project_id=project.id).create(
            session,
            {
                "project_id": project.id,
                "title": file_path.rsplit("/", 1)[-1],
                "note_type": "note",
                "permalink": permalink,
                "file_path": file_path,
                "content_type": content_type,
                "created_at": NOW,
                "updated_at": NOW,
            },
        )


async def store_note_content(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    entity: Entity,
    *,
    db_version: int,
    db_checksum: str,
) -> None:
    async with db.scoped_session(session_maker) as session:
        await NoteContentRepository(project_id=project.id).create(
            session,
            NoteContent(
                entity_id=entity.id,
                markdown_content="# Note\n",
                db_version=db_version,
                db_checksum=db_checksum,
                file_version=db_version,
                file_checksum=db_checksum,
                file_write_status="synced",
                last_source="index",
                updated_at=NOW,
                file_updated_at=NOW,
            ),
        )


async def journal_rows(session_maker: async_sessionmaker[AsyncSession], project: Project):
    async with db.scoped_session(session_maker) as session:
        return await ProjectRepository().list_accepted_note_changes(session, project.id)


async def partition_head(session_maker: async_sessionmaker[AsyncSession], project: Project) -> int:
    async with db.scoped_session(session_maker) as session:
        head = await ProjectRepository().get_partition_position(session, project.id)
    assert head is not None
    return head


async def record_api_row(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    entity: Entity,
    operation: RuntimeProjectNoteOperation,
) -> None:
    """Journal one API-side change for ``entity``, as an accepted mutation would."""
    repository = ProjectRepository()
    async with db.scoped_session(session_maker) as session:
        position = await repository.advance_partition_position(session, project.id)
        await repository.record_accepted_note_change(
            session,
            RuntimeAcceptedProjectNoteChange(
                project_id=project.id,
                project_external_id=project.external_id,
                partition_position=position,
                entity_id=entity.id,
                note_external_id=entity.external_id,
                permalink=entity.permalink or "unused",
                title=entity.title,
                operation=operation,
                file_path=entity.file_path,
                accepted_at=NOW,
                source="api",
            ),
        )


# --- The shared writer ---


@pytest.mark.asyncio
async def test_writer_claims_consecutive_positions_in_change_order(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    deleted = await create_entity(
        session_maker, test_project, file_path="archive/b.md", permalink="archive/b"
    )
    moved = await create_entity(
        session_maker, test_project, file_path="archive/a.md", permalink="archive/a"
    )
    await store_note_content(
        session_maker, test_project, moved, db_version=4, db_checksum="moved-checksum"
    )

    async with db.scoped_session(session_maker) as session:
        notes = await load_storage_note_identities(
            session,
            project_id=test_project.id,
            entity_ids=(moved.id, deleted.id),
        )
        written = await record_storage_note_changes(
            session,
            project_id=test_project.id,
            changes=(
                StorageNoteChange(
                    note=notes[deleted.id], operation=RuntimeProjectNoteOperation.deleted
                ),
                StorageNoteChange(
                    note=notes[moved.id],
                    operation=RuntimeProjectNoteOperation.moved,
                    previous_file_path="notes/a.md",
                ),
            ),
        )

    rows = await journal_rows(session_maker, test_project)
    assert [row.partition_position for row in written] == [1, 2]
    assert [
        (
            row.partition_position,
            row.operation,
            row.entity_id,
            row.note_external_id,
            row.permalink,
            row.title,
            row.file_path,
            row.previous_file_path,
            row.db_version,
            row.db_checksum,
            row.source,
            row.actor_user_profile_id,
            row.actor_kind,
            row.actor_name,
            row.project_external_id,
        )
        for row in rows
    ] == [
        (
            1,
            "deleted",
            deleted.id,
            deleted.external_id,
            "archive/b",
            "b.md",
            "archive/b.md",
            None,
            None,
            None,
            ACCEPTED_STORAGE_CHANGE_SOURCE,
            None,
            None,
            None,
            test_project.external_id,
        ),
        (
            2,
            "moved",
            moved.id,
            moved.external_id,
            "archive/a",
            "a.md",
            "archive/a.md",
            "notes/a.md",
            4,
            "moved-checksum",
            ACCEPTED_STORAGE_CHANGE_SOURCE,
            None,
            None,
            None,
            test_project.external_id,
        ),
    ]
    # Storage changes are already in storage: every row is settled from the start.
    assert all(row.materialized_at == row.accepted_at for row in rows)
    assert len({row.accepted_at for row in rows}) == 1
    assert await partition_head(session_maker, test_project) == 2


@pytest.mark.asyncio
async def test_writer_skips_non_markdown_files_and_claims_nothing_for_them(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    image = await create_entity(
        session_maker,
        test_project,
        file_path="assets/photo.png",
        permalink="assets/photo",
        content_type="image/png",
    )

    async with db.scoped_session(session_maker) as session:
        notes = await load_storage_note_identities(
            session, project_id=test_project.id, entity_ids=(image.id,)
        )
        written = await record_storage_note_changes(
            session,
            project_id=test_project.id,
            changes=(
                StorageNoteChange(
                    note=notes[image.id], operation=RuntimeProjectNoteOperation.deleted
                ),
            ),
        )
        assert (
            await record_storage_note_changes(session, project_id=test_project.id, changes=()) == ()
        )

    assert written == ()
    assert await journal_rows(session_maker, test_project) == ()
    assert await partition_head(session_maker, test_project) == 0


@pytest.mark.asyncio
async def test_writer_skips_note_without_permalink_with_a_warning(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """A legacy note with no permalink cannot name a journal row; the change still goes ahead."""
    legacy = StorageNoteIdentity(
        entity_id=7,
        note_external_id="legacy-note",
        permalink=None,
        title="Legacy",
        file_path="legacy.md",
        content_type="text/markdown",
        db_version=None,
        db_checksum=None,
    )
    warnings: list[str] = []
    sink_id = logger.add(lambda message: warnings.append(message), level="WARNING")
    try:
        async with db.scoped_session(session_maker) as session:
            written = await record_storage_note_changes(
                session,
                project_id=test_project.id,
                changes=(
                    StorageNoteChange(note=legacy, operation=RuntimeProjectNoteOperation.deleted),
                ),
            )
    finally:
        logger.remove(sink_id)

    assert written == ()
    assert await partition_head(session_maker, test_project) == 0
    assert any("without a permalink" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_identity_loader_reads_nothing_for_no_entities_or_vanished_entities(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    async with db.scoped_session(session_maker) as session:
        assert (
            await load_storage_note_identities(session, project_id=test_project.id, entity_ids=())
            == {}
        )
        assert (
            await load_storage_note_identities(
                session, project_id=test_project.id, entity_ids=(987654,)
            )
            == {}
        )


# --- Rows whose bytes reach storage after the commit ---


async def record_storage_rows(
    session_maker: async_sessionmaker[AsyncSession],
    project: Project,
    *changes: tuple[Entity, RuntimeProjectNoteOperation, bool],
) -> tuple[RuntimeAcceptedProjectNoteChange, ...]:
    """Journal (entity, operation, awaits_storage_write) storage changes in one transaction.

    A move names a previous path under ``old/``.
    """
    async with db.scoped_session(session_maker) as session:
        notes = await load_storage_note_identities(
            session,
            project_id=project.id,
            entity_ids=[entity.id for entity, _, _ in changes],
        )
        return await record_storage_note_changes(
            session,
            project_id=project.id,
            changes=[
                StorageNoteChange(
                    note=notes[entity.id],
                    operation=operation,
                    previous_file_path=(
                        f"old/{entity.file_path}"
                        if operation is RuntimeProjectNoteOperation.moved
                        else None
                    ),
                    awaits_storage_write=awaits_storage_write,
                )
                for entity, operation, awaits_storage_write in changes
            ],
        )


async def settled_positions(
    session_maker: async_sessionmaker[AsyncSession], project: Project
) -> list[tuple[int, bool]]:
    return [
        (row.partition_position, row.materialized_at is not None)
        for row in await journal_rows(session_maker, project)
    ]


@pytest.mark.asyncio
async def test_writer_leaves_a_row_unsettled_while_its_bytes_await_a_storage_write(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    waiting = await create_entity(
        session_maker, test_project, file_path="notes/a.md", permalink="notes/a"
    )
    stored = await create_entity(
        session_maker, test_project, file_path="notes/b.md", permalink="notes/b"
    )

    written = await record_storage_rows(
        session_maker,
        test_project,
        (waiting, RuntimeProjectNoteOperation.moved, True),
        (stored, RuntimeProjectNoteOperation.moved, False),
    )

    assert [(row.partition_position, row.entity_id) for row in written] == [
        (1, waiting.id),
        (2, stored.id),
    ]
    assert await settled_positions(session_maker, test_project) == [(1, False), (2, True)]

    # Once storage holds the bytes, the writer of those bytes settles the row.
    assert (
        await settle_stored_note_changes(
            session_maker, project_id=test_project.id, partition_positions=()
        )
        == 0
    )
    assert (
        await settle_stored_note_changes(
            session_maker, project_id=test_project.id, partition_positions=(1,)
        )
        == 1
    )
    assert await settled_positions(session_maker, test_project) == [(1, True), (2, True)]


@pytest.mark.parametrize(
    "operation",
    [
        RuntimeProjectNoteOperation.created,
        RuntimeProjectNoteOperation.updated,
        RuntimeProjectNoteOperation.deleted,
    ],
)
@pytest.mark.asyncio
async def test_settled_storage_version_or_delete_settles_older_rows_of_the_same_note(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    operation: RuntimeProjectNoteOperation,
) -> None:
    """A newer settled row of a note settles its older unsettled rows; other notes keep theirs."""
    note = await create_entity(
        session_maker, test_project, file_path="notes/a.md", permalink="notes/a"
    )
    other = await create_entity(
        session_maker, test_project, file_path="notes/b.md", permalink="notes/b"
    )
    await record_storage_rows(
        session_maker,
        test_project,
        (note, RuntimeProjectNoteOperation.moved, True),
        (other, RuntimeProjectNoteOperation.moved, True),
    )
    await record_api_row(session_maker, test_project, note, RuntimeProjectNoteOperation.updated)

    await record_storage_rows(session_maker, test_project, (note, operation, False))

    assert await settled_positions(session_maker, test_project) == [
        (1, True),
        (2, False),
        (3, True),
        (4, True),
    ]


@pytest.mark.asyncio
async def test_settled_storage_move_leaves_older_rows_of_the_note_unsettled(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """A move shows that a file moved, not that an earlier API write reached storage."""
    note = await create_entity(
        session_maker, test_project, file_path="notes/a.md", permalink="notes/a"
    )
    await record_api_row(session_maker, test_project, note, RuntimeProjectNoteOperation.updated)

    await record_storage_rows(
        session_maker, test_project, (note, RuntimeProjectNoteOperation.moved, False)
    )

    assert await settled_positions(session_maker, test_project) == [(1, False), (2, True)]


# --- Versions accepted by note-content reconciliation ---


@pytest.mark.asyncio
async def test_first_version_of_an_unjournaled_note_is_created(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    entity = await create_entity(
        session_maker, test_project, file_path="notes/new.md", permalink="notes/new"
    )
    await store_note_content(
        session_maker, test_project, entity, db_version=1, db_checksum="first-checksum"
    )

    async with db.scoped_session(session_maker) as session:
        change = await StorageNoteChangeJournal().record_storage_note_version(
            session,
            project_id=test_project.id,
            entity_id=entity.id,
            first_version=True,
        )

    assert change is not None
    assert change.operation is RuntimeProjectNoteOperation.created
    assert (change.db_version, change.db_checksum) == (1, "first-checksum")
    [row] = await journal_rows(session_maker, test_project)
    assert (row.operation, row.db_version, row.db_checksum) == ("created", 1, "first-checksum")


@pytest.mark.asyncio
async def test_first_version_of_a_journaled_note_is_updated(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """A note_content row that was lost and rebuilt never gives a note a second `created` row."""
    entity = await create_entity(
        session_maker, test_project, file_path="notes/rebuilt.md", permalink="notes/rebuilt"
    )
    await record_api_row(session_maker, test_project, entity, RuntimeProjectNoteOperation.created)
    await store_note_content(
        session_maker, test_project, entity, db_version=1, db_checksum="rebuilt-checksum"
    )

    async with db.scoped_session(session_maker) as session:
        change = await StorageNoteChangeJournal().record_storage_note_version(
            session,
            project_id=test_project.id,
            entity_id=entity.id,
            first_version=True,
        )

    assert change is not None
    assert change.operation is RuntimeProjectNoteOperation.updated
    assert [row.operation for row in await journal_rows(session_maker, test_project)] == [
        "created",
        "updated",
    ]


@pytest.mark.asyncio
async def test_first_version_after_a_journaled_delete_is_created(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    """A note whose last journal row is a delete comes back as a new note."""
    entity = await create_entity(
        session_maker, test_project, file_path="notes/back.md", permalink="notes/back"
    )
    await record_api_row(session_maker, test_project, entity, RuntimeProjectNoteOperation.created)
    await record_api_row(session_maker, test_project, entity, RuntimeProjectNoteOperation.deleted)
    await store_note_content(
        session_maker, test_project, entity, db_version=1, db_checksum="back-checksum"
    )

    async with db.scoped_session(session_maker) as session:
        change = await StorageNoteChangeJournal().record_storage_note_version(
            session,
            project_id=test_project.id,
            entity_id=entity.id,
            first_version=True,
        )

    assert change is not None
    assert change.operation is RuntimeProjectNoteOperation.created
    assert change.partition_position == 3


@pytest.mark.asyncio
async def test_later_version_is_updated_even_without_journal_history(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    entity = await create_entity(
        session_maker, test_project, file_path="notes/legacy.md", permalink="notes/legacy"
    )
    await store_note_content(
        session_maker, test_project, entity, db_version=2, db_checksum="second-checksum"
    )

    async with db.scoped_session(session_maker) as session:
        change = await StorageNoteChangeJournal().record_storage_note_version(
            session,
            project_id=test_project.id,
            entity_id=entity.id,
            first_version=False,
        )

    assert change is not None
    assert change.operation is RuntimeProjectNoteOperation.updated
    assert (change.db_version, change.db_checksum) == (2, "second-checksum")


@pytest.mark.asyncio
async def test_version_of_a_note_without_permalink_writes_no_row(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    entity = await create_entity(
        session_maker, test_project, file_path="notes/no-permalink.md", permalink=None
    )
    await store_note_content(
        session_maker, test_project, entity, db_version=1, db_checksum="checksum"
    )

    async with db.scoped_session(session_maker) as session:
        change = await StorageNoteChangeJournal().record_storage_note_version(
            session,
            project_id=test_project.id,
            entity_id=entity.id,
            first_version=True,
        )

    assert change is None
    assert await partition_head(session_maker, test_project) == 0


@pytest.mark.asyncio
async def test_version_for_a_missing_entity_fails_fast(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> None:
    async with db.scoped_session(session_maker) as session:
        with pytest.raises(RuntimeError, match="no entity row for entity_id=987654"):
            await StorageNoteChangeJournal().record_storage_note_version(
                session,
                project_id=test_project.id,
                entity_id=987654,
                first_version=False,
            )
