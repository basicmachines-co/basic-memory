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
from basic_memory.indexing.note_content_reconciliation import NoteContentMaterializedCurrent
from basic_memory.indexing.note_content_reconciler import (
    NoteContentReconciler,
    apply_note_content_update_plan,
)
from basic_memory.models import Entity


class FakeSession:
    """Minimal async session surface used by the reconciler conflict path."""

    def __init__(self) -> None:
        self.rollback_count = 0

    async def rollback(self) -> None:
        self.rollback_count += 1


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
