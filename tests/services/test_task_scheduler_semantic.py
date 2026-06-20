"""Task scheduler tests for derived async work."""

import asyncio
from typing import Any, cast

import pytest

from basic_memory.config import BasicMemoryConfig
from basic_memory.deps.services import get_task_scheduler


class StubProjectIndexRunner:
    def __init__(self) -> None:
        self.indexed: list[tuple[int, bool]] = []

    async def index_project(self, project_id: int, *, force_full: bool = False) -> None:
        self.indexed.append((project_id, force_full))


class StubSearchService:
    def __init__(self) -> None:
        self.vector_synced: list[int] = []
        self.reindexed_project = False

    async def sync_entity_vectors(self, entity_id: int) -> None:
        self.vector_synced.append(entity_id)

    async def reindex_all(self) -> None:
        self.reindexed_project = True


@pytest.mark.asyncio
async def test_sync_entity_vectors_task_maps_to_search_service(tmp_path):
    """Explicit sync_entity_vectors task should call SearchService sync method."""
    project_index_runner = StubProjectIndexRunner()
    search_service = StubSearchService()
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": str(tmp_path)},
        default_project="test-project",
        semantic_search_enabled=True,
    )

    scheduler = await get_task_scheduler(
        project_index_runner=cast(Any, project_index_runner),
        search_service=cast(Any, search_service),
        app_config=app_config,
    )
    # Enable background tasks for this test — uses stubs, no real DB race risk
    cast(Any, scheduler)._test_mode = False
    scheduler.schedule("sync_entity_vectors", entity_id=7)
    await asyncio.sleep(0.05)

    assert search_service.vector_synced == [7]


@pytest.mark.asyncio
async def test_sync_project_task_maps_to_project_index_runner(tmp_path):
    """Explicit sync_project task should call the event-index project runner."""
    project_index_runner = StubProjectIndexRunner()
    search_service = StubSearchService()
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": str(tmp_path)},
        default_project="test-project",
        semantic_search_enabled=True,
    )

    scheduler = await get_task_scheduler(
        project_index_runner=cast(Any, project_index_runner),
        search_service=cast(Any, search_service),
        app_config=app_config,
    )
    cast(Any, scheduler)._test_mode = False
    scheduler.schedule("sync_project", project_id=13, force_full=True)
    await asyncio.sleep(0.05)

    assert project_index_runner.indexed == [(13, True)]
