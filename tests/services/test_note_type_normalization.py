"""Search-service coverage for canonical note-type indexing and query preparation."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from basic_memory.models import Entity
from basic_memory.repository.search_repository import SearchRepository
from basic_memory.schemas.search import SearchQuery
from basic_memory.services.search_service import SearchService


def _search_service(repository: SearchRepository) -> SearchService:
    return SearchService(
        search_repository=repository,
        entity_repository=MagicMock(),
        file_service=MagicMock(),
        session_maker=MagicMock(),
    )


def test_prepare_query_canonicalizes_directly_assigned_note_types():
    """Service callers cannot bypass canonicalization by mutating SearchQuery."""
    repository = cast(SearchRepository, MagicMock())
    service = _search_service(repository)
    query = SearchQuery.model_construct(note_types=["TaskItem"])

    prepared = service._prepare_query(query)

    assert prepared is not None
    assert prepared.note_types == ["task_item"]


@pytest.mark.asyncio
async def test_reindex_canonicalizes_legacy_entity_note_type():
    """Reindexing gives legacy ORM rows the canonical search-filter identity."""
    repository_mock = MagicMock()
    repository_mock.index_item = AsyncMock()
    repository = cast(SearchRepository, repository_mock)
    service = _search_service(repository)
    entity = cast(
        Entity,
        SimpleNamespace(
            id=1,
            title="Legacy Task",
            permalink="tasks/legacy-task",
            file_path="tasks/legacy-task.pdf",
            note_type="TaskItem",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            project_id=1,
        ),
    )

    await service.index_entity_file(entity)

    repository_mock.index_item.assert_awaited_once()
    index_call = repository_mock.index_item.await_args
    assert index_call is not None
    indexed_row = index_call.args[0]
    assert indexed_row.metadata["note_type"] == "task_item"
