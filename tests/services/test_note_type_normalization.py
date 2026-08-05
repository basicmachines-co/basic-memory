"""Search-service coverage for canonical note-type indexing and query preparation."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from basic_memory import db
from basic_memory.models import Entity, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.search_index_row import SearchIndexRow
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


@pytest.mark.asyncio
async def test_search_matches_legacy_note_type_projection_without_reindex(
    search_service: SearchService,
    entity_repository: EntityRepository,
    session_maker,
    test_project: Project,
) -> None:
    """Canonical filters include exact legacy spellings still present on entities."""
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.add(
            session,
            Entity(
                title="Legacy task",
                note_type="TaskItem",
                content_type="text/markdown",
                file_path="tasks/legacy-task.md",
                permalink="tasks/legacy-task",
                created_at=now,
                updated_at=now,
                project_id=test_project.id,
            ),
        )

    await search_service.repository.index_item(
        SearchIndexRow(
            id=entity.id,
            entity_id=entity.id,
            type="entity",
            title=entity.title,
            permalink=entity.permalink,
            file_path=entity.file_path,
            metadata={"note_type": "TaskItem"},
            created_at=now,
            updated_at=now,
            project_id=test_project.id,
        )
    )

    query = SearchQuery(note_types=["task-item"])

    results = await search_service.search(query)

    assert [result.entity_id for result in results] == [entity.id]
    assert await search_service.count(query) == 1
