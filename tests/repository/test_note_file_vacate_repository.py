"""Tests for NoteFileVacateRepository (move-orphan gate, basic-memory-cloud#1601)."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from basic_memory.models.base import Base
from basic_memory.repository.note_file_vacate_repository import (
    NoteFileVacateRepository,
    RecoverableVacate,
)


@pytest_asyncio.fixture
async def session_maker() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_record_and_find_vacated_paths_is_project_scoped(session_maker) -> None:
    project_one = NoteFileVacateRepository(project_id=1)
    project_two = NoteFileVacateRepository(project_id=2)
    async with session_maker() as session:
        await project_one.record_vacate(
            session, entity_id=10, file_path="koncept/note.md", file_checksum="abc"
        )
        # Same path in another project must stay isolated.
        await project_two.record_vacate(
            session, entity_id=99, file_path="koncept/note.md", file_checksum="zzz"
        )
        await session.commit()

    async with session_maker() as session:
        markers = await project_one.load_vacate_markers(session, ["koncept/note.md", "other.md"])
        assert set(markers) == {"koncept/note.md"}
        # The marker carries the moved entity and source checksum the gate matches against.
        assert markers["koncept/note.md"].entity_id == 10
        assert markers["koncept/note.md"].file_checksum == "abc"
        assert await project_one.load_vacate_markers(session, ["other.md"]) == {}
        assert await project_one.load_vacate_markers(session, []) == {}


@pytest.mark.asyncio
async def test_record_vacate_upserts_on_same_path(session_maker) -> None:
    repo = NoteFileVacateRepository(project_id=1)
    async with session_maker() as session:
        await repo.record_vacate(
            session, entity_id=10, file_path="koncept/note.md", file_checksum="abc"
        )
        # A fresh move onto the same source path replaces the marker (no unique-constraint error).
        await repo.record_vacate(
            session, entity_id=11, file_path="koncept/note.md", file_checksum="def"
        )
        await session.commit()

    async with session_maker() as session:
        marker = await repo._get_marker(session, "koncept/note.md")
        assert marker is not None
        assert marker.entity_id == 11
        assert marker.file_checksum == "def"


@pytest.mark.asyncio
async def test_list_recoverable_vacates_is_project_scoped_and_guarded(session_maker) -> None:
    """Startup cleanup returns only checksum-backed markers from its project."""
    project_one = NoteFileVacateRepository(project_id=1)
    project_two = NoteFileVacateRepository(project_id=2)
    async with session_maker() as session:
        await project_one.record_vacate(
            session, entity_id=10, file_path="notes/ready.md", file_checksum="abc"
        )
        await project_one.record_vacate(
            session, entity_id=11, file_path="notes/unguarded.md", file_checksum=None
        )
        await project_two.record_vacate(
            session, entity_id=20, file_path="notes/other-project.md", file_checksum="xyz"
        )
        await session.commit()

    async with session_maker() as session:
        recoverable = await project_one.list_recoverable_vacates(session)

    # These lightweight repository tests use dangling entity ids, matching a
    # destination tombstone on backends that enforce ON DELETE SET NULL.
    assert recoverable == [
        RecoverableVacate(
            entity_id=None,
            file_path="notes/ready.md",
            file_checksum="abc",
            live_file_path=None,
        )
    ]


@pytest.mark.asyncio
async def test_clear_vacate_is_checksum_guarded(session_maker) -> None:
    repo = NoteFileVacateRepository(project_id=1)
    async with session_maker() as session:
        await repo.record_vacate(
            session, entity_id=10, file_path="koncept/note.md", file_checksum="def"
        )
        await session.commit()

    async with session_maker() as session:
        # A mismatched checksum (a different file now sits at the path) does not clear it.
        await repo.clear_vacate(session, file_path="koncept/note.md", file_checksum="WRONG")
        await session.commit()
    async with session_maker() as session:
        assert set(await repo.load_vacate_markers(session, ["koncept/note.md"])) == {
            "koncept/note.md"
        }

    async with session_maker() as session:
        await repo.clear_vacate(session, file_path="koncept/note.md", file_checksum="def")
        await session.commit()
    async with session_maker() as session:
        assert set(await repo.load_vacate_markers(session, ["koncept/note.md"])) == set()


@pytest.mark.asyncio
async def test_clear_vacate_does_not_delete_concurrently_refreshed_marker(session_maker) -> None:
    """A stale cleanup cannot delete a newer move marker for the same source path."""
    repo = NoteFileVacateRepository(project_id=1)
    async with session_maker() as session:
        await repo.record_vacate(
            session, entity_id=10, file_path="koncept/note.md", file_checksum="old"
        )
        await session.commit()

    async with session_maker() as cleanup_session:
        # Keep the old marker in this session's identity map, matching the stale read that made
        # the previous Python check + ORM delete sequence vulnerable.
        stale_marker = await repo._get_marker(cleanup_session, "koncept/note.md")
        assert stale_marker is not None
        assert stale_marker.file_checksum == "old"
        await cleanup_session.commit()

        async with session_maker() as new_move_session:
            await repo.record_vacate(
                new_move_session,
                entity_id=11,
                file_path="koncept/note.md",
                file_checksum="new",
            )
            await new_move_session.commit()

        await repo.clear_vacate(
            cleanup_session,
            file_path="koncept/note.md",
            file_checksum="old",
        )
        await cleanup_session.commit()

    async with session_maker() as session:
        markers = await repo.load_vacate_markers(session, ["koncept/note.md"])
    assert markers["koncept/note.md"].entity_id == 11
    assert markers["koncept/note.md"].file_checksum == "new"


@pytest.mark.asyncio
async def test_clear_vacate_clears_null_checksum_marker(session_maker) -> None:
    """A cleanup with the same unknown checksum clears an unknown-checksum marker."""
    repo = NoteFileVacateRepository(project_id=1)
    async with session_maker() as session:
        await repo.record_vacate(
            session, entity_id=10, file_path="koncept/note.md", file_checksum=None
        )
        await session.commit()

    async with session_maker() as session:
        await repo.clear_vacate(session, file_path="koncept/note.md", file_checksum=None)
        await session.commit()
    async with session_maker() as session:
        assert set(await repo.load_vacate_markers(session, ["koncept/note.md"])) == set()


@pytest.mark.asyncio
async def test_clear_vacate_path_retires_only_the_live_project_path(session_maker) -> None:
    """A successful publication supersedes any older marker for its exact project path."""
    project_one = NoteFileVacateRepository(project_id=1)
    project_two = NoteFileVacateRepository(project_id=2)
    async with session_maker() as session:
        await project_one.record_vacate(
            session, entity_id=10, file_path="notes/live.md", file_checksum="old"
        )
        await project_one.record_vacate(
            session, entity_id=11, file_path="notes/other.md", file_checksum="other"
        )
        await project_two.record_vacate(
            session, entity_id=20, file_path="notes/live.md", file_checksum="second-project"
        )
        await project_one.clear_vacate_path(session, file_path="notes/live.md")
        await session.commit()

    async with session_maker() as session:
        project_one_markers = await project_one.load_vacate_markers(
            session,
            ["notes/live.md", "notes/other.md"],
        )
        project_two_markers = await project_two.load_vacate_markers(session, ["notes/live.md"])

    assert set(project_one_markers) == {"notes/other.md"}
    assert project_two_markers["notes/live.md"].file_checksum == "second-project"
