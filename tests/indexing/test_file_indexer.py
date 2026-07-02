"""Tests for the portable per-file index service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from unittest.mock import ANY, AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.file_indexer import (
    FileIndexer,
    IndexCurrentMarkdownFileIndexer,
    build_default_file_indexer,
)
from basic_memory.indexing.models import FileIndexOperation, FileIndexResult, SyncedMarkdownFile
from basic_memory.indexing.note_content_reconciler import NoteContentReconciler

CHECKSUM = "abc123"
CANONICAL_MARKDOWN = "---\ntitle: Note\npermalink: notes/note\n---\n\n# Note\n"
OBSERVED_AT = datetime(2026, 4, 12, 12, 0, tzinfo=UTC)


class _FakeSession:
    def get_bind(self) -> Mock:
        return Mock()

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _entity(*, entity_id: int = 42, checksum: str = "old-checksum") -> Mock:
    """Create the tiny entity shape FileIndexer needs."""
    entity = Mock()
    entity.id = entity_id
    entity.checksum = checksum
    entity.external_id = "note-42"
    entity.title = "Note"
    entity.permalink = "notes/note"
    entity.observations = []
    entity.outgoing_relations = []
    entity.incoming_relations = []
    entity.relations = []
    return entity


def _synced_file(*, entity: Mock | None = None) -> SyncedMarkdownFile:
    """Create the canonical markdown index result consumed by FileIndexer."""
    return SyncedMarkdownFile(
        entity=entity or _entity(),
        checksum=CHECKSUM,
        markdown_content=CANONICAL_MARKDOWN,
        file_path="notes/note.md",
        content_type="text/markdown",
        updated_at=OBSERVED_AT,
        size=len(CANONICAL_MARKDOWN.encode("utf-8")),
    )


def _file_indexer(
    *,
    existing_entity: Mock | None = None,
    synced_file: SyncedMarkdownFile | None = None,
):
    """Create FileIndexer with explicit async collaborators."""
    index_result = synced_file or _synced_file()
    entity_repository = Mock()
    entity_repository.get_by_file_path = AsyncMock(return_value=existing_entity)

    markdown_indexer = Mock()
    markdown_indexer.session_maker = _FakeSession
    markdown_indexer.entity_repository = entity_repository
    markdown_indexer.index_current_markdown_file = AsyncMock(return_value=index_result)
    markdown_indexer.index_file = AsyncMock(
        return_value=FileIndexResult(
            file_path="notes/note.md",
            entity_id=42,
            external_id="note-42",
            title="Note",
            permalink="notes/note",
            checksum=CHECKSUM,
            operation=FileIndexOperation.updated,
        )
    )

    note_content_reconciler = Mock()
    note_content_reconciler.reconcile = AsyncMock()

    return (
        FileIndexer(
            markdown_indexer=markdown_indexer,
            note_content_reconciler=note_content_reconciler,
        ),
        markdown_indexer,
        note_content_reconciler,
    )


def test_build_default_file_indexer_composes_note_content_reconciler() -> None:
    markdown_indexer = Mock()
    markdown_indexer.session_maker = cast(async_sessionmaker[AsyncSession], _FakeSession)
    markdown_indexer.entity_repository = Mock()
    markdown_indexer.index_current_markdown_file = AsyncMock()
    markdown_indexer.index_file = AsyncMock()

    file_indexer = build_default_file_indexer(
        project_id=42,
        markdown_indexer=cast(IndexCurrentMarkdownFileIndexer, markdown_indexer),
    )

    assert isinstance(file_indexer, FileIndexer)
    assert file_indexer.markdown_indexer is markdown_indexer
    assert isinstance(file_indexer.note_content_reconciler, NoteContentReconciler)


@pytest.mark.asyncio
async def test_file_indexer_delegates_generic_file_indexing_to_project_adapter() -> None:
    file_indexer, markdown_indexer, note_content_reconciler = _file_indexer()

    result = await file_indexer.index_file("assets/file.pdf", source="s3_webhook")

    markdown_indexer.index_file.assert_awaited_once_with(
        "assets/file.pdf",
        source="s3_webhook",
    )
    note_content_reconciler.reconcile.assert_not_awaited()
    assert result.file_path == "notes/note.md"


@pytest.mark.asyncio
async def test_file_indexer_indexes_new_markdown_file() -> None:
    """
    Given a markdown file with no existing entity
    When the per-file indexer processes that file
    Then it asks the markdown indexer to persist it as new and caches the canonical result.
    """
    synced_file = _synced_file()
    file_indexer, markdown_indexer, note_content_reconciler = _file_indexer(
        synced_file=synced_file,
    )

    result = await file_indexer.index_markdown_file("notes/note.md", source="s3_webhook")

    markdown_indexer.entity_repository.get_by_file_path.assert_awaited_once_with(
        ANY,
        "notes/note.md",
        load_relations=False,
    )
    markdown_indexer.index_current_markdown_file.assert_awaited_once_with(
        "notes/note.md",
        new=True,
        index_search=True,
        resolve_relations=False,
        refresh_unchanged_derived_state=False,
    )
    note_content_reconciler.reconcile.assert_awaited_once_with(
        entity=synced_file.entity,
        markdown_content=CANONICAL_MARKDOWN,
        observed_at=OBSERVED_AT,
        source="s3_webhook",
    )
    assert result.file_path == "notes/note.md"
    assert result.entity_id == 42
    assert result.checksum == CHECKSUM
    assert result.operation == FileIndexOperation.created


@pytest.mark.asyncio
async def test_file_indexer_indexes_existing_markdown_file() -> None:
    """
    Given a markdown file already has an entity
    When the per-file indexer processes that file
    Then it asks the markdown indexer to persist it as an update.
    """
    file_indexer, markdown_indexer, _note_content_reconciler = _file_indexer(
        existing_entity=_entity(entity_id=7),
    )

    result = await file_indexer.index_markdown_file("notes/note.md")

    markdown_indexer.index_current_markdown_file.assert_awaited_once_with(
        "notes/note.md",
        new=False,
        index_search=True,
        resolve_relations=False,
        refresh_unchanged_derived_state=True,
    )
    assert result.operation == FileIndexOperation.updated


@pytest.mark.asyncio
async def test_file_indexer_repairs_derived_rows_for_unchanged_markdown_result() -> None:
    """
    Given a cloud-written note already has the same checksum as its materialized file
    When the per-file indexer delegates to the markdown indexer
    Then it asks the indexer to refresh derived observations, relations, and search.
    """
    existing_entity = _entity(checksum=CHECKSUM)
    synced_file = _synced_file(entity=existing_entity)
    file_indexer, markdown_indexer, _note_content_reconciler = _file_indexer(
        existing_entity=existing_entity,
        synced_file=synced_file,
    )
    markdown_indexer.batch_indexer = Mock()
    markdown_indexer.batch_indexer.index_markdown_file = AsyncMock(
        side_effect=AssertionError("FileIndexer should delegate unchanged repair")
    )

    result = await file_indexer.index_markdown_file("notes/note.md")

    markdown_indexer.index_current_markdown_file.assert_awaited_once_with(
        "notes/note.md",
        new=False,
        index_search=True,
        resolve_relations=False,
        refresh_unchanged_derived_state=True,
    )
    markdown_indexer.batch_indexer.index_markdown_file.assert_not_awaited()
    assert result.checksum == CHECKSUM
    assert result.operation == FileIndexOperation.updated


@pytest.mark.asyncio
async def test_file_indexer_reports_refreshed_derived_counts_after_unchanged_repair() -> None:
    """The final indexing log context should come from the repaired entity graph."""
    existing_entity = _entity(checksum=CHECKSUM)
    refreshed_entity = _entity(checksum=CHECKSUM)
    refreshed_entity.observations = [Mock(), Mock()]
    refreshed_entity.relations = [Mock()]
    synced_file = _synced_file(entity=refreshed_entity)
    file_indexer, _markdown_indexer, _note_content_reconciler = _file_indexer(
        existing_entity=existing_entity,
        synced_file=synced_file,
    )
    bound_logger = Mock()

    result = await file_indexer.index_markdown_file(
        "notes/note.md",
        bound_logger=bound_logger,
    )

    assert result.entity_id == refreshed_entity.id
    final_log = bound_logger.info.call_args_list[-1]
    assert final_log.args == ("Indexed markdown file: notes/note.md",)
    assert final_log.kwargs["observation_count"] == 2
    assert final_log.kwargs["relation_count"] == 1


@pytest.mark.asyncio
async def test_file_indexer_propagates_markdown_index_errors() -> None:
    """
    Given the markdown indexer cannot persist the file
    When the per-file indexer processes that file
    Then the error propagates and note_content is not reconciled.
    """
    file_indexer, markdown_indexer, note_content_reconciler = _file_indexer()
    markdown_indexer.index_current_markdown_file.side_effect = RuntimeError("index failed")

    with pytest.raises(RuntimeError, match="index failed"):
        await file_indexer.index_markdown_file("notes/note.md")

    note_content_reconciler.reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_file_indexer_propagates_note_content_reconcile_errors() -> None:
    """
    Given Basic Memory successfully syncs the file but note_content reconciliation fails
    When the per-file indexer processes that file
    Then the error propagates so callers can retry the job.
    """
    file_indexer, _markdown_indexer, note_content_reconciler = _file_indexer()
    note_content_reconciler.reconcile.side_effect = RuntimeError("cache failed")

    with pytest.raises(RuntimeError, match="cache failed"):
        await file_indexer.index_markdown_file("notes/note.md")
