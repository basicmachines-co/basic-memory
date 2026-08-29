"""Tests for the local per-file markdown indexer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock

from loguru import logger
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.index.local_dependencies import (
    LocalIndexEntityRepository,
    LocalMarkdownFileIndexer,
)
from basic_memory.indexing.batch_indexer import BatchIndexer
from basic_memory.indexing.file_indexer import IndexMarkdownNoteContentReconciler
from basic_memory.indexing.models import (
    FileIndexOperation,
    FileIndexResult,
    IndexEntitySearchWriter,
    SyncedMarkdownFile,
)
from basic_memory.indexing.note_content_reconciliation import NoteContentReconciliationResult
from basic_memory.services import FileService


class _FakeSession:
    def get_bind(self) -> Mock:
        return Mock()

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_local_file_indexer_logs_brace_path_through_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local retry loop must render customer braces as literal path and title text."""
    file_path = "notes/{AG} Plan.md"
    entity = Mock()
    entity.id = 42
    entity.external_id = "note-42"
    entity.title = "{AG} Plan"
    entity.permalink = "notes/ag-plan"
    entity.observations = []
    entity.relations = []
    synced_file = SyncedMarkdownFile(
        entity=entity,
        checksum="abc123",
        markdown_content="# {AG} Plan\n",
        file_path=file_path,
        content_type="text/markdown",
        updated_at=datetime(2026, 8, 9, tzinfo=UTC),
        size=12,
    )

    entity_repository = Mock()
    entity_repository.get_by_file_path = AsyncMock(return_value=entity)
    note_content_reconciler = Mock()
    note_content_reconciler.capture_anchor = AsyncMock(return_value=None)
    note_content_reconciler.reconcile = AsyncMock(
        side_effect=[
            NoteContentReconciliationResult.stale(),
            NoteContentReconciliationResult.current(3),
        ]
    )
    batch_indexer = Mock()
    batch_indexer.publish_relation_generation = AsyncMock(return_value=True)
    batch_indexer.resolve_relation_targets = AsyncMock()
    batch_indexer.refresh_indexed_entity_search = AsyncMock()
    indexer = LocalMarkdownFileIndexer(
        file_service=cast(FileService, Mock()),
        session_maker=cast(async_sessionmaker[AsyncSession], _FakeSession),
        entity_repository=cast(LocalIndexEntityRepository, entity_repository),
        batch_indexer=cast(BatchIndexer, batch_indexer),
        search_service=cast(IndexEntitySearchWriter, Mock()),
        note_content_reconciler=cast(
            IndexMarkdownNoteContentReconciler,
            note_content_reconciler,
        ),
    )
    monkeypatch.setattr(
        LocalMarkdownFileIndexer,
        "index_current_markdown_file",
        AsyncMock(return_value=synced_file),
    )

    rendered_messages: list[str] = []
    sink_id = logger.add(
        lambda message: rendered_messages.append(str(message).strip()),
        format="{message}",
        level="INFO",
    )
    try:
        result = await indexer.index_markdown_file(file_path, source="index")
    finally:
        logger.remove(sink_id)

    assert note_content_reconciler.reconcile.await_count == 2
    assert f"Retrying markdown index without a current relation generation: {file_path}" in (
        rendered_messages
    )
    assert f"Indexed markdown file: {file_path}" in rendered_messages
    assert result.file_path == file_path
    assert result.title == "{AG} Plan"


@pytest.mark.asyncio
async def test_local_file_indexer_treats_empty_markdown_basename_as_regular_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A MIME-marked ``.md`` object is a resource, not a poison markdown job."""
    file_service = Mock()
    file_service.is_markdown.return_value = True
    indexer = LocalMarkdownFileIndexer(
        file_service=cast(FileService, file_service),
        session_maker=cast(async_sessionmaker[AsyncSession], _FakeSession),
        entity_repository=cast(LocalIndexEntityRepository, Mock()),
        batch_indexer=cast(BatchIndexer, Mock()),
        search_service=cast(IndexEntitySearchWriter, Mock()),
        note_content_reconciler=cast(IndexMarkdownNoteContentReconciler, Mock()),
    )
    regular_result = FileIndexResult(
        file_path="_phase7_import/.md",
        entity_id=42,
        external_id="resource-42",
        title=".md",
        permalink=None,
        checksum="abc123",
        operation=FileIndexOperation.created,
    )
    markdown_index = AsyncMock()
    regular_index = AsyncMock(return_value=regular_result)
    monkeypatch.setattr(LocalMarkdownFileIndexer, "index_markdown_file", markdown_index)
    monkeypatch.setattr(LocalMarkdownFileIndexer, "index_regular_file", regular_index)

    result = await indexer.index_file("_phase7_import/.md", source="s3_webhook")

    markdown_index.assert_not_awaited()
    regular_index.assert_awaited_once_with(
        "_phase7_import/.md",
        source="s3_webhook",
    )
    assert result == regular_result
