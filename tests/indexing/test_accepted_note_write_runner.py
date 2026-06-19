"""Tests for accepted note write persistence handoffs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.accepted_note_search import AcceptedNoteSearchRow
from basic_memory.indexing.accepted_note_write_runner import (
    accept_note_content_write,
    accepted_note_content_write_from_markdown,
    accepted_note_search_row_from_entity,
    accepted_pending_entity_write_from_prepared,
    apply_accepted_prepared_entity_fields,
    create_accepted_pending_entity,
    prepare_accepted_note_create,
    prepare_accepted_note_edit,
    prepare_accepted_note_replace,
    refresh_accepted_note_search_index,
)
from basic_memory.models import Entity, NoteContent
from basic_memory.repository import AcceptedNoteContentWrite
from basic_memory.repository.entity_repository import AcceptedPendingEntityWrite
from basic_memory.schemas.base import Entity as EntitySchema


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
    markdown_content: str
    search_content: str
    entity_fields: _PreparedFields


class _FlushSession:
    def __init__(self) -> None:
        self.flush_count = 0

    async def flush(self) -> None:
        self.flush_count += 1


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


class _CreatePreparer:
    def __init__(self, prepared: _PreparedWrite) -> None:
        self.prepared = prepared
        self.calls: list[tuple[EntitySchema, bool, AsyncSession | None]] = []

    async def prepare_create_entity_content(
        self,
        schema: EntitySchema,
        *,
        check_storage_exists: bool = True,
        session: AsyncSession | None = None,
    ) -> _PreparedWrite:
        self.calls.append((schema, check_storage_exists, session))
        return self.prepared


class _ReplacePreparer:
    def __init__(self, prepared: _PreparedWrite) -> None:
        self.prepared = prepared
        self.calls: list[tuple[Entity, EntitySchema, str, AsyncSession | None]] = []

    async def prepare_update_entity_content(
        self,
        entity: Entity,
        schema: EntitySchema,
        existing_content: str,
        *,
        session: AsyncSession | None = None,
    ) -> _PreparedWrite:
        self.calls.append((entity, schema, existing_content, session))
        return self.prepared


class _EditPreparer:
    def __init__(self, prepared: _PreparedWrite) -> None:
        self.prepared = prepared
        self.calls: list[
            tuple[Entity, str, str, str, str | None, str | None, int, AsyncSession | None]
        ] = []

    async def prepare_edit_entity_content(
        self,
        entity: Entity,
        current_content: str,
        *,
        operation: str,
        content: str,
        section: str | None = None,
        find_text: str | None = None,
        expected_replacements: int = 1,
        session: AsyncSession | None = None,
    ) -> _PreparedWrite:
        self.calls.append(
            (
                entity,
                current_content,
                operation,
                content,
                section,
                find_text,
                expected_replacements,
                session,
            )
        )
        return self.prepared


def _prepared(
    *,
    markdown_content: str = "# Accepted\n",
    search_content: str = "Accepted",
    fields: _PreparedFields | None = None,
) -> _PreparedWrite:
    return _PreparedWrite(
        markdown_content=markdown_content,
        search_content=search_content,
        entity_fields=fields
        or _PreparedFields(
            title="Accepted",
            note_type="note",
            entity_metadata={"status": "draft"},
            content_type="text/markdown",
            permalink="accepted",
            file_path="notes/accepted.md",
        ),
    )


def _schema() -> EntitySchema:
    return EntitySchema(
        title="Accepted",
        directory="notes",
        note_type="note",
        content_type="text/markdown",
        content="# Accepted\n",
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


@pytest.mark.asyncio
async def test_prepare_accepted_note_create_hashes_prepared_markdown() -> None:
    session = cast(AsyncSession, object())
    schema = _schema()
    prepared = _prepared(markdown_content="# Created\n")
    preparer = _CreatePreparer(prepared)

    result = await prepare_accepted_note_create(
        preparer,
        schema,
        check_storage_exists=False,
        session=session,
    )

    assert result.prepared is prepared
    assert result.db_checksum == sha256(b"# Created\n").hexdigest()
    assert preparer.calls == [(schema, False, session)]


@pytest.mark.asyncio
async def test_prepare_accepted_note_replace_applies_entity_fields() -> None:
    session = _FlushSession()
    entity = _entity()
    schema = _schema()
    now = datetime(2026, 6, 19, 12, 30, tzinfo=UTC)
    fields = _PreparedFields(
        title="Replacement",
        note_type="decision",
        entity_metadata={"status": "accepted"},
        content_type="text/markdown",
        permalink="replacement",
        file_path="notes/replacement.md",
    )
    prepared = _prepared(markdown_content="# Replacement\n", fields=fields)
    preparer = _ReplacePreparer(prepared)

    result = await prepare_accepted_note_replace(
        preparer,
        cast(AsyncSession, session),
        entity=entity,
        data=schema,
        current_note_content=_note_content(),
        now=now,
        user_profile_value="user-2",
    )

    assert result.prepared is prepared
    assert result.db_checksum == sha256(b"# Replacement\n").hexdigest()
    assert preparer.calls == [
        (entity, schema, "# Accepted\n", cast(AsyncSession, session)),
    ]
    assert entity.title == "Replacement"
    assert entity.note_type == "decision"
    assert entity.entity_metadata == {"status": "accepted"}
    assert entity.file_path == "notes/replacement.md"
    assert entity.updated_at == now
    assert entity.last_updated_by == "user-2"
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_prepare_accepted_note_edit_applies_entity_fields() -> None:
    session = _FlushSession()
    entity = _entity()
    now = datetime(2026, 6, 19, 12, 45, tzinfo=UTC)
    fields = _PreparedFields(
        title="Edited",
        note_type="note",
        entity_metadata={"status": "edited"},
        content_type="text/markdown",
        permalink="edited",
        file_path="notes/edited.md",
    )
    prepared = _prepared(markdown_content="# Edited\n", fields=fields)
    preparer = _EditPreparer(prepared)

    result = await prepare_accepted_note_edit(
        preparer,
        cast(AsyncSession, session),
        entity=entity,
        current_note_content=_note_content(),
        operation="find_replace",
        content="# Edited",
        section=None,
        find_text="# Accepted",
        expected_replacements=1,
        now=now,
        user_profile_value=None,
    )

    assert result.prepared is prepared
    assert result.db_checksum == sha256(b"# Edited\n").hexdigest()
    assert preparer.calls == [
        (
            entity,
            "# Accepted\n",
            "find_replace",
            "# Edited",
            None,
            "# Accepted",
            1,
            cast(AsyncSession, session),
        )
    ]
    assert entity.title == "Edited"
    assert entity.permalink == "edited"
    assert entity.file_path == "notes/edited.md"
    assert entity.last_updated_by is None
    assert session.flush_count == 1


def test_apply_accepted_prepared_entity_fields_updates_mutable_entity() -> None:
    entity = _entity()
    now = datetime(2026, 6, 19, 13, 0, tzinfo=UTC)

    apply_accepted_prepared_entity_fields(
        entity,
        _PreparedFields(
            title="Applied",
            note_type="schema",
            entity_metadata={"type": "schema"},
            content_type="text/markdown",
            permalink="applied",
            file_path="schemas/applied.md",
        ),
        updated_at=now,
        user_profile_value="user-3",
    )

    assert entity.title == "Applied"
    assert entity.note_type == "schema"
    assert entity.entity_metadata == {"type": "schema"}
    assert entity.content_type == "text/markdown"
    assert entity.permalink == "applied"
    assert entity.file_path == "schemas/applied.md"
    assert entity.updated_at == now
    assert entity.last_updated_by == "user-3"


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
