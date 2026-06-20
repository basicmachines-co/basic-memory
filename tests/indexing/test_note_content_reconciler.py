"""Tests for note-content reconciliation service behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from basic_memory import file_utils
from basic_memory.indexing.note_content_reconciliation import (
    NoteContentMaterializationStatusUpdate,
    NoteContentMaterializedCurrent,
)
from basic_memory.indexing.note_content_reconciler import (
    NoteContentReconciler,
    RepositoryNoteMaterializationFailureMarker,
    apply_note_content_update_plan,
    mark_note_materialization_enqueue_failed,
    reconcile_note_content_for_entity,
)
from basic_memory.models import Entity


class FakeSession:
    """Minimal async session surface used by the reconciler conflict path."""

    def __init__(self) -> None:
        self.rollback_count = 0

    async def rollback(self) -> None:
        self.rollback_count += 1


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeRepositorySession(FakeSession):
    async def __aenter__(self) -> FakeRepositorySession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> FakeTransaction:
        return FakeTransaction()


@pytest.mark.asyncio
async def test_reconciler_converges_after_concurrent_create_conflict() -> None:
    """A concurrent repair winner should not make the losing worker fail permanently."""
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    markdown_content = "# Repaired\n"
    observed_checksum = await file_utils.compute_checksum(markdown_content)
    existing_note_content = SimpleNamespace(
        db_version=1,
        db_checksum="previous-db-checksum",
        file_version=1,
        file_checksum="previous-file-checksum",
    )
    repository = SimpleNamespace(
        get_by_entity_id=AsyncMock(side_effect=[None, existing_note_content]),
        create=AsyncMock(
            side_effect=IntegrityError(
                "INSERT INTO note_content",
                {},
                Exception("duplicate key"),
            )
        ),
        update_state_fields=AsyncMock(),
    )
    entity = cast(Entity, SimpleNamespace(id=42))
    session = FakeSession()

    @asynccontextmanager
    async def fake_scoped_session(_session_maker: object):
        yield session

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "basic_memory.indexing.note_content_reconciler.db.scoped_session",
            fake_scoped_session,
        )
        await NoteContentReconciler(
            note_content_repository=cast(Any, repository),
            session_maker=cast(Any, object()),
        ).reconcile(
            entity=entity,
            markdown_content=markdown_content,
            observed_at=observed_at,
            source="read_repair",
        )

    repository.create.assert_awaited_once()
    assert session.rollback_count == 1
    assert repository.get_by_entity_id.await_count == 2
    repository.update_state_fields.assert_awaited_once_with(
        session,
        42,
        markdown_content=markdown_content,
        db_version=2,
        db_checksum=observed_checksum,
        file_version=2,
        file_checksum=observed_checksum,
        file_write_status="synced",
        last_source="read_repair",
        updated_at=observed_at,
        file_updated_at=observed_at,
        last_materialization_error=None,
        last_materialization_attempt_at=None,
    )


@pytest.mark.asyncio
async def test_apply_note_content_update_plan_publishes_materialized_current_file() -> None:
    """Materialization publish plans should apply through the note_content repository."""
    file_updated_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    attempted_at = datetime(2026, 4, 13, 14, 59, tzinfo=UTC)
    repository = SimpleNamespace(update_state_fields=AsyncMock())
    session = FakeSession()

    await apply_note_content_update_plan(
        cast(Any, repository),
        cast(Any, session),
        42,
        NoteContentMaterializedCurrent(
            file_version=4,
            file_checksum="written-file-checksum",
            file_write_status="synced",
            file_updated_at=file_updated_at,
            last_materialization_error=None,
            last_materialization_attempt_at=attempted_at,
        ),
    )

    repository.update_state_fields.assert_awaited_once_with(
        session,
        42,
        file_version=4,
        file_checksum="written-file-checksum",
        file_write_status="synced",
        file_updated_at=file_updated_at,
        last_materialization_error=None,
        last_materialization_attempt_at=attempted_at,
    )


@pytest.mark.asyncio
async def test_apply_note_content_update_plan_marks_materialization_status() -> None:
    """Materialization status plans should apply without overwriting file checksum."""
    attempted_at = datetime(2026, 4, 13, 14, 59, tzinfo=UTC)
    repository = SimpleNamespace(update_state_fields=AsyncMock())
    session = FakeSession()

    await apply_note_content_update_plan(
        cast(Any, repository),
        cast(Any, session),
        42,
        NoteContentMaterializationStatusUpdate(
            file_write_status="failed",
            file_checksum=None,
            last_materialization_error="write failed",
            last_materialization_attempt_at=attempted_at,
        ),
    )

    repository.update_state_fields.assert_awaited_once_with(
        session,
        42,
        file_write_status="failed",
        last_materialization_error="write failed",
        last_materialization_attempt_at=attempted_at,
    )


@pytest.mark.asyncio
async def test_mark_note_materialization_enqueue_failed_uses_repository_factory() -> None:
    """Queue failure bookkeeping should stay in core repository adapter code."""
    attempted_at = datetime(2026, 4, 13, 14, 59, tzinfo=UTC)
    session = FakeRepositorySession()
    repository_calls: list[tuple[int, FakeRepositorySession, int, dict[str, object]]] = []

    def session_maker() -> FakeRepositorySession:
        return session

    class FakeNoteContentRepository:
        def __init__(self, project_id: int) -> None:
            self.project_id = project_id

        async def update_state_fields(
            self,
            session: Any,
            entity_id: int,
            **updates: object,
        ) -> None:
            repository_calls.append((self.project_id, session, entity_id, updates))

    await mark_note_materialization_enqueue_failed(
        session_maker=cast(Any, session_maker),
        project_id=7,
        entity_id=42,
        error_message="pgq unavailable",
        note_content_repository_factory=FakeNoteContentRepository,
        attempted_at=attempted_at,
    )

    assert repository_calls == [
        (
            7,
            session,
            42,
            {
                "file_write_status": "failed",
                "last_materialization_error": "pgq unavailable",
                "last_materialization_attempt_at": attempted_at,
            },
        )
    ]


@pytest.mark.asyncio
async def test_repository_failure_marker_records_materialization_enqueue_failure() -> None:
    """The failure-marker protocol adapter should be usable by enqueue runners."""
    session = FakeRepositorySession()
    repository_calls: list[dict[str, object]] = []

    def session_maker() -> FakeRepositorySession:
        return session

    class FakeNoteContentRepository:
        def __init__(self, project_id: int) -> None:
            self.project_id = project_id

        async def update_state_fields(
            self,
            session: Any,
            entity_id: int,
            **updates: object,
        ) -> None:
            assert session is not None
            assert entity_id == 42
            repository_calls.append(updates)

    marker = RepositoryNoteMaterializationFailureMarker(
        session_maker=cast(Any, session_maker),
        note_content_repository_factory=FakeNoteContentRepository,
    )

    await marker.mark_note_materialization_failed(
        project_id=7,
        entity_id=42,
        error_message="pgq unavailable",
    )

    assert len(repository_calls) == 1
    assert repository_calls[0]["file_write_status"] == "failed"
    assert repository_calls[0]["last_materialization_error"] == "pgq unavailable"
    assert isinstance(repository_calls[0]["last_materialization_attempt_at"], datetime)


@pytest.mark.asyncio
async def test_reconcile_note_content_for_entity_reads_canonical_file() -> None:
    """Entity-id reconciliation should read canonical markdown and delegate to reconciler."""
    observed_at = datetime(2026, 4, 13, 15, 0, tzinfo=UTC)
    session = FakeRepositorySession()
    entity = SimpleNamespace(id=42, file_path="notes/indexed.md", is_markdown=True)
    repository = SimpleNamespace(find_by_id=AsyncMock(return_value=entity))
    file_reader = SimpleNamespace(
        get_file=AsyncMock(
            return_value=SimpleNamespace(
                content=b"# Indexed\n",
                last_modified=observed_at,
            )
        )
    )
    reconciler = SimpleNamespace(reconcile=AsyncMock())

    def session_maker() -> FakeRepositorySession:
        return session

    reconciled = await reconcile_note_content_for_entity(
        session_maker=cast(Any, session_maker),
        entity_repository=cast(Any, repository),
        file_reader=cast(Any, file_reader),
        reconciler=cast(Any, reconciler),
        entity_id=42,
        source="index",
    )

    assert reconciled is True
    repository.find_by_id.assert_awaited_once_with(session, 42)
    file_reader.get_file.assert_awaited_once_with("notes/indexed.md")
    reconciler.reconcile.assert_awaited_once_with(
        entity=entity,
        markdown_content="# Indexed\n",
        observed_at=observed_at,
        source="index",
    )


@pytest.mark.asyncio
async def test_reconcile_note_content_for_entity_skips_non_markdown_entities() -> None:
    """Non-markdown entities should not trigger a storage content read."""
    session = FakeRepositorySession()
    entity = SimpleNamespace(id=42, file_path="assets/image.png", is_markdown=False)
    repository = SimpleNamespace(find_by_id=AsyncMock(return_value=entity))
    file_reader = SimpleNamespace(get_file=AsyncMock())
    reconciler = SimpleNamespace(reconcile=AsyncMock())

    def session_maker() -> FakeRepositorySession:
        return session

    reconciled = await reconcile_note_content_for_entity(
        session_maker=cast(Any, session_maker),
        entity_repository=cast(Any, repository),
        file_reader=cast(Any, file_reader),
        reconciler=cast(Any, reconciler),
        entity_id=42,
        source="index",
    )

    assert reconciled is False
    repository.find_by_id.assert_awaited_once_with(session, 42)
    file_reader.get_file.assert_not_awaited()
    reconciler.reconcile.assert_not_awaited()
