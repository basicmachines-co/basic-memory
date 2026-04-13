"""Task scheduler tests for derived async work."""

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from basic_memory.config import BasicMemoryConfig, ProjectConfig
from basic_memory.deps.services import get_task_scheduler


class StubSyncService:
    def __init__(self) -> None:
        self.resolved: list[int] = []
        self.synced: list[tuple[str, str, bool]] = []

    async def resolve_relations(self, entity_id: int) -> None:
        self.resolved.append(entity_id)

    async def sync(self, home: Path, name: str, force_full: bool = False) -> None:
        self.synced.append((str(home), name, force_full))


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
    sync_service = StubSyncService()
    search_service = StubSearchService()
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": str(tmp_path)},
        default_project="test-project",
        semantic_search_enabled=True,
    )
    project_config = ProjectConfig(name="test-project", home=tmp_path)

    scheduler = await get_task_scheduler(
        sync_service=cast(Any, sync_service),
        search_service=cast(Any, search_service),
        project_config=project_config,
        app_config=app_config,
    )
    # Enable background tasks for this test — uses stubs, no real DB race risk
    cast(Any, scheduler)._test_mode = False
    scheduler.schedule("sync_entity_vectors", entity_id=7)
    await asyncio.sleep(0.05)

    assert search_service.vector_synced == [7]


@pytest.mark.asyncio
async def test_sync_project_task_maps_to_sync_service(tmp_path):
    """Explicit sync_project task should call SyncService sync method."""
    sync_service = StubSyncService()
    search_service = StubSearchService()
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": str(tmp_path)},
        default_project="test-project",
        semantic_search_enabled=True,
    )
    project_config = ProjectConfig(name="test-project", home=tmp_path)

    scheduler = await get_task_scheduler(
        sync_service=cast(Any, sync_service),
        search_service=cast(Any, search_service),
        project_config=project_config,
        app_config=app_config,
    )
    cast(Any, scheduler)._test_mode = False
    scheduler.schedule("sync_project", force_full=True)
    await asyncio.sleep(0.05)

    assert sync_service.synced == [(str(tmp_path), "test-project", True)]
