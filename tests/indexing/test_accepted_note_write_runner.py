"""Tests for accepted note write persistence handoffs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.accepted_note_search import AcceptedNoteSearchRow
from basic_memory.indexing.accepted_note_write_runner import (
    accept_note_content_write,
    accepted_note_content_write_from_markdown,
    accepted_note_search_row_from_entity,
    accepted_pending_entity_write_from_prepared,
    create_accepted_pending_entity,
    refresh_accepted_note_search_index,
)
from basic_memory.models import Entity, NoteContent
from basic_memory.repository import AcceptedNoteContentWrite
from basic_memory.repository.entity_repository import AcceptedPendingEntityWrite


@dataclass(frozen=True, slots=True)
class _PreparedFields:
    title: str
    note_type: str
    entity_metadata: dict[str, object] | None
    content_type: str
    permalink: str | None
    file_path: str


@dataclass(frozen=True, slots=True)
class _PreparedWrite:
    entity_fields: _PreparedFields


class _PendingEntityRepository:
    def __init__(self, entity: Entity) -> None:
        self.entity = entity
        self.calls: list[tuple[AsyncSession, AcceptedPendingEntityWrite]] = []

    async def create_pending_accepted_entity(
        self,
        session: AsyncSession,
        write: AcceptedPendingEntityWrite,
    ) -> Entity:
        self.calls.append((session, write))
        return self.entity


class _NoteContentRepository:
    def __init__(self, result: NoteContent) -> None:
        self.result = result
        self.calls: list[tuple[AsyncSession, AcceptedNoteContentWrite]] = []

    async def accept_write(
        self,
        session: AsyncSession,
        write: AcceptedNoteContentWrite,
    ) -> NoteContent:
        self.calls.append((session, write))
        return self.result


class _SearchRepository:
    def __init__(self) -> None:
        self.calls: list[AcceptedNoteSearchRow] = []

    async def refresh_entity(
        self,
        session: AsyncSession,
        row: AcceptedNoteSearchRow,
    ) -> None:
        self.calls.append(row)


def _prepared() -> _PreparedWrite:
    return _PreparedWrite(
        entity_fields=_PreparedFields(
            title="Accepted",
            note_type="note",
            entity_metadata={"status": "draft"},
            content_type="text/markdown",
            permalink="accepted",
            file_path="notes/accepted.md",
        )
    )


def _entity() -> Entity:
    return Entity(
        id=42,
        project_id=7,
        title="Accepted",
        note_type="note",
        entity_metadata={"tags": ["core"]},
        content_type="text/markdown",
        permalink="accepted",
        file_path="notes/accepted.md",
        checksum=None,
        created_at=datetime(2026, 6, 19, 12, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 19, 12, 5, tzinfo=UTC),
    )


def _note_content() -> NoteContent:
    return NoteContent(
        entity_id=42,
        project_id=7,
        external_id="note-1",
        file_path="notes/accepted.md",
        markdown_content="# Accepted\n",
        db_version=3,
        db_checksum="db-checksum",
        file_write_status="pending",
        last_source="api",
    )


def test_accepted_pending_entity_write_from_prepared_maps_core_fields() -> None:
    now = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)

    write = accepted_pending_entity_write_from_prepared(
        _prepared(),
        now=now,
        user_profile_value="user-1",
        external_id="note-1",
    )

    assert write == AcceptedPendingEntityWrite(
        title="Accepted",
        note_type="note",
        entity_metadata={"status": "draft"},
        content_type="text/markdown",
        permalink="accepted",
        file_path="notes/accepted.md",
        created_at=now,
        updated_at=now,
        created_by="user-1",
        last_updated_by="user-1",
        external_id="note-1",
    )


@pytest.mark.asyncio
async def test_create_accepted_pending_entity_uses_repository_protocol() -> None:
    session = cast(AsyncSession, object())
    entity = _entity()
    repository = _PendingEntityRepository(entity)
    now = datetime(2026, 6, 19, 12, 0, tzinfo=UTC)

    result = await create_accepted_pending_entity(
        session,
        prepared=_prepared(),
        project_id=7,
        now=now,
        user_profile_value=None,
        repository_factory=lambda project_id: repository,
    )

    assert result is entity
    assert len(repository.calls) == 1
    repository_session, write = repository.calls[0]
    assert repository_session is session
    assert write.file_path == "notes/accepted.md"
    assert write.created_by is None


def test_accepted_note_content_write_from_markdown_maps_versioned_snapshot() -> None:
    updated_at = datetime(2026, 6, 19, 12, 5, tzinfo=UTC)

    write = accepted_note_content_write_from_markdown(
        entity_id=42,
        markdown_content="# Accepted\n",
        db_version=3,
        db_checksum="db-checksum",
        last_source="mcp",
        updated_at=updated_at,
    )

    assert write == AcceptedNoteContentWrite(
        entity_id=42,
        markdown_content="# Accepted\n",
        db_version=3,
        db_checksum="db-checksum",
        last_source="mcp",
        updated_at=updated_at,
    )


@pytest.mark.asyncio
async def test_accept_note_content_write_uses_repository_protocol() -> None:
    session = cast(AsyncSession, object())
    entity = _entity()
    note_content = _note_content()
    repository = _NoteContentRepository(note_content)
    updated_at = datetime(2026, 6, 19, 12, 5, tzinfo=UTC)

    result = await accept_note_content_write(
        session,
        entity=entity,
        markdown_content="# Accepted\n",
        db_version=3,
        db_checksum="db-checksum",
        last_source="api",
        updated_at=updated_at,
        repository_factory=lambda project_id: repository,
    )

    assert result is note_content
    assert repository.calls == [
        (
            session,
            AcceptedNoteContentWrite(
                entity_id=42,
                markdown_content="# Accepted\n",
                db_version=3,
                db_checksum="db-checksum",
                last_source="api",
                updated_at=updated_at,
            ),
        )
    ]


def test_accepted_note_search_row_from_entity_builds_hot_search_row() -> None:
    entity = _entity()

    row = accepted_note_search_row_from_entity(entity, search_content="Accepted body")

    assert row.entity_id == 42
    assert row.project_id == 7
    assert row.title == "Accepted"
    assert row.file_path == "notes/accepted.md"
    assert row.content_snippet == "Accepted body"
    assert "core" in row.content_stems


@pytest.mark.asyncio
async def test_refresh_accepted_note_search_index_uses_repository_protocol() -> None:
    session = cast(AsyncSession, object())
    entity = _entity()
    repository = _SearchRepository()

    await refresh_accepted_note_search_index(
        session,
        entity=entity,
        search_content="Accepted body",
        repository_factory=lambda project_id: repository,
    )

    assert len(repository.calls) == 1
    row = repository.calls[0]
    assert row.entity_id == 42
    assert row.project_id == 7
