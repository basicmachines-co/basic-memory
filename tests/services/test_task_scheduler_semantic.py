"""Task scheduler semantic indexing tests."""

import asyncio
from pathlib import Path

import pytest

from basic_memory.config import BasicMemoryConfig, ProjectConfig
from basic_memory.deps.services import get_task_scheduler


class StubEntityService:
    def __init__(self) -> None:
        self.reindexed: list[int] = []

    async def reindex_entity(self, entity_id: int) -> None:
        self.reindexed.append(entity_id)


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
async def test_reindex_entity_task_chains_vector_sync_when_semantic_enabled(tmp_path):
    """Reindex task should enqueue vector sync when semantic mode is enabled."""
    entity_service = StubEntityService()
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
        entity_service=entity_service,
        sync_service=sync_service,
        search_service=search_service,
        project_config=project_config,
        app_config=app_config,
    )
    scheduler.schedule("reindex_entity", entity_id=42)
    await asyncio.sleep(0.05)

    assert entity_service.reindexed == [42]
    assert search_service.vector_synced == [42]


@pytest.mark.asyncio
async def test_reindex_entity_task_skips_vector_sync_when_semantic_disabled(tmp_path):
    """Reindex task should not enqueue vector sync when semantic mode is disabled."""
    entity_service = StubEntityService()
    sync_service = StubSyncService()
    search_service = StubSearchService()
    app_config = BasicMemoryConfig(
        env="test",
        projects={"test-project": str(tmp_path)},
        default_project="test-project",
        semantic_search_enabled=False,
    )
    project_config = ProjectConfig(name="test-project", home=tmp_path)

    scheduler = await get_task_scheduler(
        entity_service=entity_service,
        sync_service=sync_service,
        search_service=search_service,
        project_config=project_config,
        app_config=app_config,
    )
    scheduler.schedule("reindex_entity", entity_id=42)
    await asyncio.sleep(0.05)

    assert entity_service.reindexed == [42]
    assert search_service.vector_synced == []


@pytest.mark.asyncio
async def test_sync_entity_vectors_task_maps_to_search_service(tmp_path):
    """Explicit sync_entity_vectors task should call SearchService sync method."""
    entity_service = StubEntityService()
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
        entity_service=entity_service,
        sync_service=sync_service,
        search_service=search_service,
        project_config=project_config,
        app_config=app_config,
    )
    scheduler.schedule("sync_entity_vectors", entity_id=7)
    await asyncio.sleep(0.05)

    assert search_service.vector_synced == [7]
