"""Tests for portable batch note_content reconciliation after indexing."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, AsyncIterator, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import basic_memory.indexing.note_content_batch_reconciliation as batch_reconciliation_module
from basic_memory import db, file_utils
from basic_memory.indexing.models import IndexedEntity
from basic_memory.indexing.note_content_batch_reconciliation import (
    DefaultIndexedNoteContentTimestampProvider,
    indexed_note_content_observed_at,
    reconcile_indexed_note_content_batch,
    run_indexing_tasks_with_retries,
)
from basic_memory.indexing.note_content_reconciler import NoteContentReconciler
from basic_memory.models import Entity, Project
from basic_memory.repository import EntityRepository, NoteContentRepository


@dataclass(frozen=True, slots=True)
class StubReconcileFile:
    """Canonical file re-read at reconcile time in tests."""

    content: bytes | None
    last_modified: datetime | None


@dataclass(frozen=True, slots=True)
class StubReconcileFileReader:
    """Return a fixed re-read file for the entity under test."""

    file: StubReconcileFile

    async def get_file(self, path: str) -> StubReconcileFile:
        assert path is not None
        return self.file


@dataclass(frozen=True, slots=True)
class FakeEntity:
    id: int


@dataclass(frozen=True, slots=True)
class FakeFileInfo:
    observed_at: datetime
    checksum: str = "checksum-ok"

    @property
    def last_modified(self) -> datetime:
        return self.observed_at


@dataclass(slots=True)
class FlakyIndexingTask:
    attempts: int = 0

    async def run(self) -> str:
        self.attempts += 1
        if self.attempts == 1:
            raise SQLAlchemyTimeoutError("pool timeout")
        return "ok"


@dataclass(slots=True)
class RecordingIndexedNoteContentTimestampProvider:
    calls: list[tuple[str, FakeFileInfo | None]]

    def observed_at(
        self,
        indexed: IndexedEntity,
        file_info: FakeFileInfo | None,
    ) -> datetime | None:
        self.calls.append((indexed.path, file_info))
        return file_info.observed_at if file_info is not None else None


class FakeEntityRepository:
    def __init__(self, entities: list[FakeEntity]) -> None:
        self.entities = entities
        self.loaded_ids: list[int] | None = None

    async def find_by_ids(
        self,
        session: AsyncSession,
        ids: list[int],
    ) -> list[FakeEntity]:
        assert session is not None
        self.loaded_ids = ids
        return self.entities


@pytest.mark.asyncio
async def test_reconcile_indexed_note_content_batch_reports_per_file_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch reconciliation should keep indexing results while surfacing follow-up errors."""
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = cast(AsyncSession, object())
    repository = FakeEntityRepository([FakeEntity(id=42), FakeEntity(id=43)])
    reconcile = AsyncMock()
    observed_at = datetime(2026, 6, 19, 14, 0, tzinfo=UTC)
    timestamp_provider = RecordingIndexedNoteContentTimestampProvider(calls=[])

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert scoped_session_maker is session_maker
        yield session

    async def reconcile_note_content(
        *,
        entity: FakeEntity,
        markdown_content: str,
        observed_at: datetime | None,
        source: str,
    ) -> None:
        await reconcile(
            entity=entity,
            markdown_content=markdown_content,
            observed_at=observed_at,
            source=source,
        )
        if entity.id == 43:
            raise RuntimeError("note_content failed")

    monkeypatch.setattr(
        batch_reconciliation_module.db,
        "scoped_session",
        fake_scoped_session,
    )

    errors = await reconcile_indexed_note_content_batch(
        [
            IndexedEntity(
                path="ok.md",
                entity_id=42,
                permalink="ok",
                checksum="checksum-ok",
                markdown_content="# OK\n",
            ),
            IndexedEntity(
                path="missing.md",
                entity_id=404,
                permalink="missing",
                checksum="checksum-missing",
                markdown_content="# Missing\n",
            ),
            IndexedEntity(
                path="bad.md",
                entity_id=43,
                permalink="bad",
                checksum="checksum-bad",
                markdown_content="# Bad\n",
            ),
            IndexedEntity(
                path="binary.png",
                entity_id=44,
                permalink=None,
                checksum="checksum-binary",
                markdown_content=None,
            ),
        ],
        file_infos={"ok.md": FakeFileInfo(observed_at=observed_at)},
        entity_repository=repository,
        session_maker=session_maker,
        note_content_reconciler=cast(Any, SimpleNamespace(reconcile=reconcile_note_content)),
        timestamp_provider=timestamp_provider,
        max_concurrent=2,
        source="index",
    )

    assert repository.loaded_ids == [42, 404, 43]
    assert timestamp_provider.calls == [
        ("ok.md", FakeFileInfo(observed_at=observed_at)),
        ("bad.md", None),
    ]
    assert [error.as_tuple() for error in errors] == [
        ("missing.md", "Entity 404 not found after indexing"),
        ("bad.md", "note_content failed"),
    ]
    assert reconcile.await_args_list[0].kwargs == {
        "entity": FakeEntity(id=42),
        "markdown_content": "# OK\n",
        "observed_at": observed_at,
        "source": "index",
    }
    assert reconcile.await_args_list[1].kwargs == {
        "entity": FakeEntity(id=43),
        "markdown_content": "# Bad\n",
        "observed_at": None,
        "source": "index",
    }


@pytest.mark.asyncio
async def test_run_indexing_tasks_with_retries_uses_fresh_task_factory() -> None:
    """Retrying task factories should not reuse an already-awaited coroutine."""
    task = FlakyIndexingTask()

    results = await run_indexing_tasks_with_retries(
        [task],
        max_concurrent=1,
        retry_wait_seconds=0,
    )

    assert results == ("ok",)
    assert task.attempts == 2


def test_indexed_note_content_observed_at_uses_original_timestamp_when_unchanged() -> None:
    observed_at = datetime(2026, 6, 19, 14, 0, tzinfo=UTC)

    result = indexed_note_content_observed_at(
        IndexedEntity(
            path="ok.md",
            entity_id=42,
            permalink="ok",
            checksum="checksum-ok",
            markdown_content="# OK\n",
        ),
        FakeFileInfo(checksum="checksum-ok", observed_at=observed_at),
    )

    assert result == observed_at


def test_indexed_note_content_observed_at_uses_now_when_frontmatter_rewrites_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_at = datetime(2026, 6, 19, 14, 0, tzinfo=UTC)
    rewritten_at = datetime(2026, 6, 19, 14, 5, tzinfo=UTC)
    monkeypatch.setattr(
        "basic_memory.indexing.note_content_batch_reconciliation.indexed_note_content_utc_now",
        lambda: rewritten_at,
    )

    result = indexed_note_content_observed_at(
        IndexedEntity(
            path="ok.md",
            entity_id=42,
            permalink="ok",
            checksum="rewritten-checksum",
            markdown_content="# OK\n",
        ),
        FakeFileInfo(checksum="checksum-ok", observed_at=observed_at),
    )

    assert result == rewritten_at


def test_indexed_note_content_observed_at_handles_missing_file_info() -> None:
    result = indexed_note_content_observed_at(
        IndexedEntity(
            path="ok.md",
            entity_id=42,
            permalink="ok",
            checksum="checksum-ok",
            markdown_content="# OK\n",
        ),
        None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_batch_reader_skips_reconcile_when_file_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file deleted between scan and reconcile yields no content and is skipped."""
    session_maker = cast(async_sessionmaker[AsyncSession], object())
    session = cast(AsyncSession, object())
    repository = FakeEntityRepository([FakeEntity(id=42)])
    reconcile = AsyncMock()

    @asynccontextmanager
    async def fake_scoped_session(
        scoped_session_maker: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert scoped_session_maker is session_maker
        yield session

    monkeypatch.setattr(batch_reconciliation_module.db, "scoped_session", fake_scoped_session)

    errors = await reconcile_indexed_note_content_batch(
        [
            IndexedEntity(
                path="gone.md",
                entity_id=42,
                permalink="gone",
                checksum="checksum-scan",
                markdown_content="# Scan snapshot\n",
            ),
        ],
        file_infos={},
        entity_repository=repository,
        session_maker=session_maker,
        note_content_reconciler=cast(Any, SimpleNamespace(reconcile=reconcile)),
        timestamp_provider=RecordingIndexedNoteContentTimestampProvider(calls=[]),
        max_concurrent=1,
        source="index",
        file_reader=StubReconcileFileReader(StubReconcileFile(content=None, last_modified=None)),
    )

    assert errors == ()
    reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_reader_reconciles_fresh_content_not_scan_snapshot(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    """Re-reading at reconcile time must not revert a newer accepted note_content version.

    A note materialization can rewrite the file to a newer accepted db_version after
    the scan captured an older snapshot. Re-reading the current file (which now holds
    the accepted content) keeps note_content at that accepted version instead of
    promoting the stale snapshot over it.
    """
    stale_snapshot = "# Old scan snapshot\n"
    accepted_content = "# New accepted content\n"
    accepted_checksum = await file_utils.compute_checksum(accepted_content)
    stale_checksum = await file_utils.compute_checksum(stale_snapshot)
    now = datetime.now(tz=UTC)

    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await repository.create(
            session,
            {
                "entity_id": sample_entity.id,
                "project_id": sample_entity.project_id,
                "external_id": sample_entity.external_id,
                "file_path": sample_entity.file_path,
                "markdown_content": accepted_content,
                "db_version": 5,
                "db_checksum": accepted_checksum,
                "file_version": 5,
                "file_checksum": accepted_checksum,
                "file_write_status": "synced",
                "last_source": "api",
                "updated_at": now,
                "file_updated_at": now,
                "last_materialization_error": None,
                "last_materialization_attempt_at": None,
            },
        )

    reconciler = NoteContentReconciler(
        note_content_repository=repository,
        session_maker=session_maker,
    )
    # The scan captured the OLD snapshot, but the current file now holds the accepted
    # content; the reader returns what is really on disk at reconcile time.
    reader = StubReconcileFileReader(
        StubReconcileFile(content=accepted_content.encode("utf-8"), last_modified=now)
    )

    errors = await reconcile_indexed_note_content_batch(
        [
            IndexedEntity(
                path=sample_entity.file_path,
                entity_id=sample_entity.id,
                permalink=sample_entity.permalink,
                checksum=stale_checksum,
                markdown_content=stale_snapshot,
            ),
        ],
        file_infos={},
        entity_repository=EntityRepository(project_id=test_project.id),
        session_maker=session_maker,
        note_content_reconciler=reconciler,
        timestamp_provider=DefaultIndexedNoteContentTimestampProvider(),
        max_concurrent=1,
        source="index",
        file_reader=reader,
    )

    assert errors == ()
    async with db.scoped_session(session_maker) as session:
        row = await repository.get_by_entity_id(session, sample_entity.id)
    assert row is not None
    # The accepted version is preserved: no revert to the stale snapshot.
    assert row.db_version == 5
    assert row.db_checksum == accepted_checksum
    assert row.markdown_content == accepted_content


@pytest.mark.asyncio
async def test_batch_without_reader_reverts_to_scan_snapshot(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
    sample_entity: Entity,
) -> None:
    """Without re-reading, the stale scan snapshot promotes over newer accepted content.

    This documents the concurrency defect the reader fixes: the db_version
    compare-and-set guard cannot catch it because the stale snapshot promotes
    cleanly to a fresh version.
    """
    stale_snapshot = "# Old scan snapshot\n"
    accepted_content = "# New accepted content\n"
    accepted_checksum = await file_utils.compute_checksum(accepted_content)
    stale_checksum = await file_utils.compute_checksum(stale_snapshot)
    now = datetime.now(tz=UTC)

    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await repository.create(
            session,
            {
                "entity_id": sample_entity.id,
                "project_id": sample_entity.project_id,
                "external_id": sample_entity.external_id,
                "file_path": sample_entity.file_path,
                "markdown_content": accepted_content,
                "db_version": 5,
                "db_checksum": accepted_checksum,
                "file_version": 5,
                "file_checksum": accepted_checksum,
                "file_write_status": "synced",
                "last_source": "api",
                "updated_at": now,
                "file_updated_at": now,
                "last_materialization_error": None,
                "last_materialization_attempt_at": None,
            },
        )

    reconciler = NoteContentReconciler(
        note_content_repository=repository,
        session_maker=session_maker,
    )

    errors = await reconcile_indexed_note_content_batch(
        [
            IndexedEntity(
                path=sample_entity.file_path,
                entity_id=sample_entity.id,
                permalink=sample_entity.permalink,
                checksum=stale_checksum,
                markdown_content=stale_snapshot,
            ),
        ],
        file_infos={},
        entity_repository=EntityRepository(project_id=test_project.id),
        session_maker=session_maker,
        note_content_reconciler=reconciler,
        timestamp_provider=DefaultIndexedNoteContentTimestampProvider(),
        max_concurrent=1,
        source="index",
    )

    assert errors == ()
    async with db.scoped_session(session_maker) as session:
        row = await repository.get_by_entity_id(session, sample_entity.id)
    assert row is not None
    # Without the reader the stale snapshot is promoted, reverting accepted content.
    assert row.db_version == 6
    assert row.db_checksum == stale_checksum
    assert row.markdown_content == stale_snapshot
