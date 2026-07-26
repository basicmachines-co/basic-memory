"""Integration tests for atomic move-vacate marker retirement."""

import asyncio
from datetime import datetime, timezone

import pytest

from basic_memory.models.knowledge import Entity
from basic_memory.models.project import Project
from basic_memory.repository.note_file_vacate_repository import NoteFileVacateRepository


def _moved_entity(project_id: int) -> Entity:
    return Entity(
        project_id=project_id,
        title="Moved Twice",
        note_type="note",
        permalink="koncept/moved-twice",
        file_path="archive/moved-twice.md",
        content_type="text/markdown",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_stale_cleanup_preserves_concurrently_refreshed_marker(
    engine_factory,
    test_project: Project,
) -> None:
    """A cleanup holding stale ORM state cannot delete the next move's marker."""
    _, session_maker = engine_factory
    repo = NoteFileVacateRepository(project_id=test_project.id)
    file_path = "koncept/reused-source.md"

    async with session_maker() as session:
        entity = _moved_entity(test_project.id)
        session.add(entity)
        await session.flush()
        entity_id = entity.id
        await repo.record_vacate(
            session,
            entity_id=entity_id,
            file_path=file_path,
            file_checksum="old",
        )
        await session.commit()

    async with session_maker() as cleanup_session:
        # Model the old read/check/delete race: cleanup observed checksum "old", then another
        # transaction refreshed the same marker before cleanup attempted to retire it.
        stale_marker = await repo._get_marker(cleanup_session, file_path)
        assert stale_marker is not None
        assert stale_marker.file_checksum == "old"
        await cleanup_session.commit()

        async with session_maker() as new_move_session:
            await repo.record_vacate(
                new_move_session,
                entity_id=entity_id,
                file_path=file_path,
                file_checksum="new",
            )
            await new_move_session.commit()

        await repo.clear_vacate(
            cleanup_session,
            file_path=file_path,
            file_checksum="old",
        )
        await cleanup_session.commit()

    async with session_maker() as session:
        markers = await repo.load_vacate_markers(session, [file_path])
    assert markers[file_path].file_checksum == "new"


@pytest.mark.asyncio
async def test_concurrent_cleanup_cannot_break_marker_refresh(
    engine_factory,
    test_project: Project,
) -> None:
    """A refresh succeeds when cleanup retires the previous marker at the same time."""
    _, session_maker = engine_factory
    repo = NoteFileVacateRepository(project_id=test_project.id)
    file_path = "koncept/reused-source.md"

    async with session_maker() as session:
        entity = _moved_entity(test_project.id)
        session.add(entity)
        await session.flush()
        entity_id = entity.id
        await repo.record_vacate(
            session,
            entity_id=entity_id,
            file_path=file_path,
            file_checksum="old",
        )
        await session.commit()

    async with session_maker() as refresh_session:
        await repo.record_vacate(
            refresh_session,
            entity_id=entity_id,
            file_path=file_path,
            file_checksum="new",
        )

        cleanup_started = asyncio.Event()

        async def retire_previous_marker() -> None:
            async with session_maker() as cleanup_session:
                cleanup_started.set()
                await repo.clear_vacate(
                    cleanup_session,
                    file_path=file_path,
                    file_checksum="old",
                )
                await cleanup_session.commit()

        cleanup_task = asyncio.create_task(retire_previous_marker())
        await cleanup_started.wait()
        await asyncio.sleep(0.05)
        await refresh_session.commit()
        await cleanup_task

    async with session_maker() as session:
        markers = await repo.load_vacate_markers(session, [file_path])
    assert markers[file_path].file_checksum == "new"
