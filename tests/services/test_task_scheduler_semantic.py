"""Typed scheduler tests for derived async work."""

import asyncio
from typing import cast

import pytest

from basic_memory.index import ProjectIndexCoordinatorResult
from basic_memory.deps.services import (
    LocalEntityVectorSyncScheduler,
    LocalProjectIndexScheduler,
    LocalSearchReindexScheduler,
)


class StubProjectIndexRunner:
    def __init__(self) -> None:
        self.indexed: list[tuple[int, bool]] = []

    async def index_project(
        self,
        project_id: int,
        *,
        force_full: bool = False,
    ) -> ProjectIndexCoordinatorResult:
        self.indexed.append((project_id, force_full))
        return cast(ProjectIndexCoordinatorResult, object())


class StubSearchService:
    def __init__(self) -> None:
        self.vector_synced: list[int] = []
        self.reindexed_project = False

    async def sync_entity_vectors(self, entity_id: int) -> None:
        self.vector_synced.append(entity_id)

    async def reindex_all(self) -> None:
        self.reindexed_project = True


@pytest.mark.asyncio
async def test_entity_vector_scheduler_maps_to_search_service():
    """Entity vector scheduling should call the semantic vector sync method."""
    search_service = StubSearchService()

    scheduler = LocalEntityVectorSyncScheduler(
        search_service=search_service,
        test_mode=False,
    )
    scheduler.schedule_entity_vector_sync(entity_id=7, project_id=13)
    await asyncio.sleep(0.05)

    assert search_service.vector_synced == [7]


@pytest.mark.asyncio
async def test_project_index_scheduler_maps_to_project_index_runner():
    """Project index scheduling should call the event-index project runner."""
    project_index_runner = StubProjectIndexRunner()

    scheduler = LocalProjectIndexScheduler(
        project_index_runner=project_index_runner,
        test_mode=False,
    )
    scheduler.schedule_project_index(project_id=13, force_full=True)
    await asyncio.sleep(0.05)

    assert project_index_runner.indexed == [(13, True)]


@pytest.mark.asyncio
async def test_search_reindex_scheduler_maps_to_search_service():
    """Search reindex scheduling should rebuild the search index."""
    search_service = StubSearchService()

    scheduler = LocalSearchReindexScheduler(
        search_service=search_service,
        test_mode=False,
    )
    scheduler.schedule_search_reindex(project_id=13)
    await asyncio.sleep(0.05)

    assert search_service.reindexed_project is True
