"""Typed scheduler tests for derived async work."""

import asyncio
from typing import cast

import pytest

from basic_memory.indexing.project_index_coordinator import ProjectIndexCoordinatorResult
from basic_memory.deps.services import (
    LocalEntityVectorSyncScheduler,
    LocalProjectIndexScheduler,
    LocalRelationResolutionScheduler,
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


class StubRelationResolutionRuntime:
    def __init__(self) -> None:
        self.resolve_calls = 0

    async def count_unresolved_relations(self) -> int:
        return 0

    async def resolve_relations(self, entity_id: int | None = None) -> set[int]:
        self.resolve_calls += 1
        return set()


@pytest.mark.asyncio
async def test_relation_resolution_scheduler_runs_project_resolution():
    """A single write schedules one debounced project resolution pass."""
    from basic_memory.deps.services import _pending_relation_resolution

    _pending_relation_resolution.clear()
    runtime = StubRelationResolutionRuntime()

    scheduler = LocalRelationResolutionScheduler(
        relation_runtime=runtime,
        test_mode=False,
        debounce_seconds=0.0,
    )
    scheduler.schedule_relation_resolution(project_id=13)
    await asyncio.sleep(0.05)

    assert runtime.resolve_calls == 1
    # The pending marker is cleared after the pass so later writes can schedule.
    assert 13 not in _pending_relation_resolution


@pytest.mark.asyncio
async def test_relation_resolution_scheduler_coalesces_a_burst():
    """A burst of writes collapses to a single project resolution pass."""
    from basic_memory.deps.services import _pending_relation_resolution

    _pending_relation_resolution.clear()
    runtime = StubRelationResolutionRuntime()

    scheduler = LocalRelationResolutionScheduler(
        relation_runtime=runtime,
        test_mode=False,
        debounce_seconds=0.02,
    )
    for _ in range(10):
        scheduler.schedule_relation_resolution(project_id=7)
    await asyncio.sleep(0.1)

    # Ten writes, one offline pass — not one whole-project scan per write.
    assert runtime.resolve_calls == 1


@pytest.mark.asyncio
async def test_relation_resolution_scheduler_reruns_for_write_during_pass():
    """A write that commits while a pass is scanning must trigger a follow-up pass,
    not be dropped by coalescing (the scan already read the unresolved rows)."""
    from basic_memory.deps.services import (
        _dirty_relation_resolution,
        _pending_relation_resolution,
    )

    _pending_relation_resolution.clear()
    _dirty_relation_resolution.clear()

    class WriteDuringScanRuntime:
        def __init__(self) -> None:
            self.resolve_calls = 0
            self.scheduler: LocalRelationResolutionScheduler | None = None

        async def count_unresolved_relations(self) -> int:
            return 0

        async def resolve_relations(self, entity_id: int | None = None) -> set[int]:
            self.resolve_calls += 1
            if self.resolve_calls == 1:
                # A new write lands while the first scan is running.
                assert self.scheduler is not None
                self.scheduler.schedule_relation_resolution(project_id=21)
            return set()

    runtime = WriteDuringScanRuntime()
    scheduler = LocalRelationResolutionScheduler(
        relation_runtime=runtime,
        test_mode=False,
        debounce_seconds=0.0,
    )
    runtime.scheduler = scheduler

    scheduler.schedule_relation_resolution(project_id=21)
    await asyncio.sleep(0.05)

    assert runtime.resolve_calls == 2
    assert 21 not in _pending_relation_resolution
    assert 21 not in _dirty_relation_resolution


@pytest.mark.asyncio
async def test_relation_resolution_scheduler_is_noop_in_test_mode():
    """Test mode should suppress the background resolution pass entirely."""
    from basic_memory.deps.services import _pending_relation_resolution

    _pending_relation_resolution.clear()
    runtime = StubRelationResolutionRuntime()

    scheduler = LocalRelationResolutionScheduler(
        relation_runtime=runtime,
        test_mode=True,
    )
    scheduler.schedule_relation_resolution(project_id=13)
    await asyncio.sleep(0.05)

    assert runtime.resolve_calls == 0
    # Test mode must not leak a pending marker (it never runs the clearer).
    assert 13 not in _pending_relation_resolution
