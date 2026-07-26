"""Tests for NoteFileVacateRepository (move-orphan gate, basic-memory-cloud#1601)."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from basic_memory.models.base import Base
from basic_memory.repository.note_file_vacate_repository import NoteFileVacateRepository


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
async def test_clear_vacate_clears_null_checksum_marker(session_maker) -> None:
    """A marker recorded with an unknown source checksum is cleared once the file is gone."""
    repo = NoteFileVacateRepository(project_id=1)
    async with session_maker() as session:
        await repo.record_vacate(
            session, entity_id=10, file_path="koncept/note.md", file_checksum=None
        )
        await session.commit()

    async with session_maker() as session:
        await repo.clear_vacate(session, file_path="koncept/note.md", file_checksum="whatever")
        await session.commit()
    async with session_maker() as session:
        assert set(await repo.load_vacate_markers(session, ["koncept/note.md"])) == set()
