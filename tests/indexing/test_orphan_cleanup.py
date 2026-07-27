"""Tests for project-index orphan entity cleanup."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.orphan_cleanup import cleanup_orphan_entities

if TYPE_CHECKING:
    from loguru import Record


@contextmanager
def capture_logs() -> Iterator[list[Record]]:
    """Capture loguru records emitted while the block runs."""
    records: list[Record] = []
    sink_id = logger.add(lambda message: records.append(message.record), level="INFO")
    try:
        yield records
    finally:
        logger.remove(sink_id)


@dataclass(frozen=True, slots=True)
class FakeEntity:
    id: int


@dataclass(slots=True)
class FakeEntityRepository:
    file_paths: Sequence[str]
    entities_by_path: dict[str, FakeEntity]
    changed_paths: set[str] = field(default_factory=set)
    get_all_calls: int = 0
    get_calls: list[str] = field(default_factory=list)
    delete_calls: list[tuple[int, str]] = field(default_factory=list)

    async def get_all_file_paths(self, session: AsyncSession) -> Sequence[str]:
        _ = session
        self.get_all_calls += 1
        return self.file_paths

    async def get_by_file_path(self, session: AsyncSession, file_path: str) -> FakeEntity | None:
        _ = session
        self.get_calls.append(file_path)
        return self.entities_by_path.get(file_path)

    async def delete_by_fields(
        self,
        session: AsyncSession,
        *,
        id: int,
        file_path: str,
    ) -> bool:
        _ = session
        self.delete_calls.append((id, file_path))
        return file_path not in self.changed_paths


@dataclass(slots=True)
class RecordingSearchService:
    deleted_entities: list[FakeEntity] = field(default_factory=list)

    async def handle_delete(self, entity: FakeEntity) -> None:
        self.deleted_entities.append(entity)


@asynccontextmanager
async def fake_session_scope(
    _session_maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    yield cast(AsyncSession, object())


@dataclass(slots=True)
class RecordingScopedSession:
    """Stand-in for ``db.scoped_session`` that records each open."""

    opened_session_makers: list[async_sessionmaker[AsyncSession]] = field(default_factory=list)

    def __call__(
        self,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AbstractAsyncContextManager[AsyncSession]:
        self.opened_session_makers.append(session_maker)
        return fake_session_scope(session_maker)


async def test_cleanup_orphan_entities_returns_empty_result_when_storage_matches_db() -> None:
    repository = FakeEntityRepository(
        file_paths=["notes/current.md"],
        entities_by_path={},
    )
    search_service = RecordingSearchService()
    scoped_session = RecordingScopedSession()

    with capture_logs() as records, pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.orphan_cleanup.db.scoped_session", scoped_session
        )
        result = await cleanup_orphan_entities(
            session_maker=cast(async_sessionmaker[AsyncSession], object()),
            entity_repository=repository,
            search_service=search_service,
            current_paths={"notes/current.md"},
        )

    assert result.orphan_paths == ()
    assert result.deleted_paths == ()
    assert result.deleted_count == 0
    assert repository.get_calls == []
    assert repository.delete_calls == []
    assert search_service.deleted_entities == []
    # No orphans means the cleanup returns before emitting any diagnostics.
    assert records == []


async def test_cleanup_orphan_entities_uses_scoped_session_for_each_db_step() -> None:
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    scoped_session = RecordingScopedSession()
    repository = FakeEntityRepository(
        file_paths=["notes/delete.md"],
        entities_by_path={"notes/delete.md": FakeEntity(id=10)},
    )
    search_service = RecordingSearchService()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.orphan_cleanup.db.scoped_session", scoped_session
        )
        result = await cleanup_orphan_entities(
            session_maker=session_maker,
            entity_repository=repository,
            search_service=search_service,
            current_paths=set(),
        )

    assert result.deleted_paths == ("notes/delete.md",)
    assert scoped_session.opened_session_makers == [session_maker, session_maker]


async def test_cleanup_orphan_entities_deletes_only_stale_entity_rows() -> None:
    delete_entity = FakeEntity(id=10)
    changed_entity = FakeEntity(id=11)
    repository = FakeEntityRepository(
        file_paths=[
            "notes/current.md",
            "notes/delete.md",
            "notes/missing.md",
            "notes/changed.md",
        ],
        entities_by_path={
            "notes/delete.md": delete_entity,
            "notes/changed.md": changed_entity,
        },
        changed_paths={"notes/changed.md"},
    )
    search_service = RecordingSearchService()
    scoped_session = RecordingScopedSession()

    with capture_logs() as records, pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.orphan_cleanup.db.scoped_session", scoped_session
        )
        result = await cleanup_orphan_entities(
            session_maker=cast(async_sessionmaker[AsyncSession], object()),
            entity_repository=repository,
            search_service=search_service,
            current_paths={"notes/current.md"},
        )

    assert result.orphan_paths == (
        "notes/changed.md",
        "notes/delete.md",
        "notes/missing.md",
    )
    assert result.deleted_paths == ("notes/delete.md",)
    assert result.skipped_changed_paths == ("notes/changed.md",)
    assert result.skipped_missing_paths == ("notes/missing.md",)
    assert result.deleted_count == 1
    assert repository.get_all_calls == 1
    assert repository.get_calls == [
        "notes/changed.md",
        "notes/delete.md",
        "notes/missing.md",
    ]
    assert repository.delete_calls == [
        (11, "notes/changed.md"),
        (10, "notes/delete.md"),
    ]
    assert search_service.deleted_entities == [delete_entity]
    warnings = [record for record in records if record["level"].name == "WARNING"]
    infos = [record for record in records if record["level"].name == "INFO"]
    assert [(record["message"], record["extra"]) for record in warnings] == [
        ("Skipping orphan cleanup: entity no longer exists", {"file_path": "notes/missing.md"})
    ]
    # The changed path logs its own info before the final summary.
    assert (infos[0]["message"], infos[0]["extra"]) == (
        "Skipping orphan cleanup: entity path changed",
        {"entity_id": 11, "file_path": "notes/changed.md"},
    )
    assert (infos[-1]["message"], infos[-1]["extra"]) == (
        "Deleted orphan entities during project reindex",
        {"orphan_paths": 3, "deleted_files": 1},
    )
