"""Tests for accepted note write persistence handoffs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from asyncpg.exceptions import DeadlockDetectedError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.accepted_note_write_runner import (
    AcceptedNoteWriteRepositories,
    accepted_note_content_write_from_markdown,
    accepted_note_search_row_from_entity,
    accepted_pending_entity_write_from_prepared,
    apply_accepted_prepared_entity_fields,
    refresh_accepted_note_search_index,
)
from basic_memory.models import Entity
from basic_memory.markdown.schemas import (
    EntityFrontmatter,
    EntityMarkdown,
    Observation as MarkdownObservation,
    Relation as MarkdownRelation,
)
from basic_memory.repository import (
    AcceptedNoteContentWrite,
    AcceptedObservationWrite,
    AcceptedRelationWrite,
)
from basic_memory.repository.accepted_note_search_row import AcceptedNoteSearchRow
from basic_memory.repository.entity_repository import AcceptedPendingEntityWrite
from basic_memory.services.note_preparation import (
    PreparedEntityFields,
    PreparedEntityMove,
    PreparedEntityWrite,
)


_PreparedFields = PreparedEntityFields
_PreparedWrite = PreparedEntityWrite
_PreparedMove = PreparedEntityMove

_PREPARED_CREATED_AT = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
_PREPARED_UPDATED_AT = datetime(2024, 1, 16, 11, 45, tzinfo=UTC)


def _prepared(
    *,
    markdown_content: str = "# Accepted\n",
    search_content: str = "Accepted",
    fields: PreparedEntityFields | None = None,
    observations: Sequence[AcceptedObservationWrite] = (),
    relations: Sequence[AcceptedRelationWrite] = (),
) -> PreparedEntityWrite:
    prepared_fields = fields or PreparedEntityFields(
        title="Accepted",
        note_type="note",
        entity_metadata={"status": "draft"},
        content_type="text/markdown",
        permalink="accepted",
        file_path="notes/accepted.md",
        created_at=_PREPARED_CREATED_AT,
        updated_at=_PREPARED_UPDATED_AT,
    )
    return PreparedEntityWrite(
        file_path=Path(prepared_fields.file_path),
        markdown_content=markdown_content,
        search_content=search_content,
        entity_fields=prepared_fields,
        entity_markdown=EntityMarkdown(
            frontmatter=EntityFrontmatter(
                metadata={
                    "title": prepared_fields.title,
                    "type": prepared_fields.note_type,
                    "permalink": prepared_fields.permalink,
                }
            ),
            content=search_content,
            observations=[
                MarkdownObservation(
                    content=observation.content,
                    category=observation.category,
                    context=observation.context,
                    tags=observation.tags,
                )
                for observation in observations
            ],
            relations=[
                MarkdownRelation(
                    type=relation.relation_type,
                    target=relation.target_name,
                    context=relation.context,
                )
                for relation in relations
            ],
        ),
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


def test_apply_accepted_prepared_entity_fields_updates_mutable_entity() -> None:
    entity = _entity()

    apply_accepted_prepared_entity_fields(
        entity,
        _PreparedFields(
            title="Applied",
            note_type="schema",
            entity_metadata={"type": "schema"},
            content_type="text/markdown",
            permalink="applied",
            file_path="schemas/applied.md",
            created_at=_PREPARED_CREATED_AT,
            updated_at=_PREPARED_UPDATED_AT,
        ),
        user_profile_value="user-3",
    )

    assert entity.title == "Applied"
    assert entity.note_type == "schema"
    assert entity.entity_metadata == {"type": "schema"}
    assert entity.content_type == "text/markdown"
    assert entity.permalink == "applied"
    assert entity.file_path == "schemas/applied.md"
    assert entity.created_at == _PREPARED_CREATED_AT
    assert entity.updated_at == _PREPARED_UPDATED_AT
    assert entity.last_updated_by == "user-3"


def test_accepted_pending_entity_write_from_prepared_maps_core_fields() -> None:
    write = accepted_pending_entity_write_from_prepared(
        _prepared(),
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
        created_at=_PREPARED_CREATED_AT,
        updated_at=_PREPARED_UPDATED_AT,
        created_by="user-1",
        last_updated_by="user-1",
        external_id="note-1",
    )


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


def test_accepted_note_search_row_from_entity_builds_hot_search_row() -> None:
    entity = _entity()

    row = accepted_note_search_row_from_entity(entity, search_content="Accepted body")

    assert row.entity_id == 42
    assert row.project_id == 7
    assert row.title == "Accepted"
    assert row.file_path == "notes/accepted.md"
    assert row.content_snippet == "Accepted body"
    assert "core" in row.content_stems


@dataclass
class _FailingSearchRepository:
    error: BaseException

    async def refresh_entity(self, session: AsyncSession, row: AcceptedNoteSearchRow) -> None:
        raise self.error


@dataclass
class _FailingSearchRepositories:
    error: BaseException

    def search_repository(self, project_id: int) -> _FailingSearchRepository:
        return _FailingSearchRepository(self.error)


def _driver_failure(driver_error: BaseException) -> IntegrityError:
    """A DBAPI error raised from the driver's error, as the asyncpg dialect raises it."""
    adapted = Exception(str(driver_error))
    adapted.__cause__ = driver_error
    return IntegrityError("INSERT INTO search_index ...", {}, adapted)


@pytest.mark.asyncio
async def test_accepted_search_refresh_that_lost_a_race_returns(session_maker) -> None:
    """The hot row is derived; losing to a concurrent refresh is logged (#1681)."""
    row = accepted_note_search_row_from_entity(_entity(), search_content="Accepted body")
    repositories = _FailingSearchRepositories(
        _driver_failure(DeadlockDetectedError("deadlock detected"))
    )

    await refresh_accepted_note_search_index(
        session_maker,
        row=row,
        repositories=cast(AcceptedNoteWriteRepositories, repositories),
    )


@pytest.mark.asyncio
async def test_accepted_search_refresh_raises_any_other_database_failure(session_maker) -> None:
    row = accepted_note_search_row_from_entity(_entity(), search_content="Accepted body")
    repositories = _FailingSearchRepositories(_driver_failure(ValueError("value too long")))

    with pytest.raises(IntegrityError):
        await refresh_accepted_note_search_index(
            session_maker,
            row=row,
            repositories=cast(AcceptedNoteWriteRepositories, repositories),
        )
