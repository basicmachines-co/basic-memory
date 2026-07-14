"""Typed scheduler tests for derived async work."""

import asyncio
from typing import cast

import pytest

from basic_memory.indexing.project_index_coordinator import ProjectIndexCoordinatorResult
from basic_memory.index.local_schedulers import (
    LocalEntityVectorSyncScheduler,
    LocalProjectIndexScheduler,
    LocalRelationResolutionScheduler,
    LocalSearchReindexScheduler,
    drain_background_tasks,
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


def _clear_project_index_scheduler_state() -> None:
    from basic_memory.index.local_schedulers import _dirty_project_index, _pending_project_index

    _pending_project_index.clear()
    _dirty_project_index.clear()


@pytest.mark.asyncio
async def test_project_index_scheduler_maps_to_project_index_runner():
    """Project index scheduling should call the event-index project runner."""
    _clear_project_index_scheduler_state()
    project_index_runner = StubProjectIndexRunner()

    scheduler = LocalProjectIndexScheduler(
        project_index_runner=project_index_runner,
        test_mode=False,
    )
    scheduler.schedule_project_index(project_id=13, force_full=True)
    await asyncio.sleep(0.05)

    assert project_index_runner.indexed == [(13, True)]


class GatedProjectIndexRunner:
    """Runner whose first run blocks until released, to hold a run in flight."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []
        self.first_run_started = asyncio.Event()
        self.release_first_run = asyncio.Event()

    async def index_project(
        self,
        project_id: int,
        *,
        force_full: bool = False,
    ) -> ProjectIndexCoordinatorResult:
        self.calls.append((project_id, force_full))
        if len(self.calls) == 1:
            self.first_run_started.set()
            await self.release_first_run.wait()
        return cast(ProjectIndexCoordinatorResult, object())


@pytest.mark.asyncio
async def test_project_index_scheduler_coalesces_requests_during_in_flight_run():
    """While a run is in flight, new requests must not start a second concurrent
    run over the same rows; they coalesce to exactly one trailing rerun that
    keeps the strongest force_full seen."""
    from basic_memory.index.local_schedulers import _dirty_project_index, _pending_project_index

    _clear_project_index_scheduler_state()
    runner = GatedProjectIndexRunner()
    scheduler = LocalProjectIndexScheduler(project_index_runner=runner, test_mode=False)

    scheduler.schedule_project_index(project_id=13)
    await asyncio.wait_for(runner.first_run_started.wait(), timeout=1)

    # A burst of requests lands while the first run is still scanning.
    scheduler.schedule_project_index(project_id=13)
    scheduler.schedule_project_index(project_id=13, force_full=True)
    scheduler.schedule_project_index(project_id=13)
    assert runner.calls == [(13, False)]

    runner.release_first_run.set()
    await drain_background_tasks()

    assert runner.calls == [(13, False), (13, True)]
    assert 13 not in _pending_project_index
    assert 13 not in _dirty_project_index


class FailingThenGatedProjectIndexRunner:
    """Runner whose first run blocks until released, then raises."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []
        self.first_run_started = asyncio.Event()
        self.release_first_run = asyncio.Event()

    async def index_project(
        self,
        project_id: int,
        *,
        force_full: bool = False,
    ) -> ProjectIndexCoordinatorResult:
        self.calls.append((project_id, force_full))
        if len(self.calls) == 1:
            self.first_run_started.set()
            await self.release_first_run.wait()
            raise RuntimeError("scan blew up mid-run")
        return cast(ProjectIndexCoordinatorResult, object())


@pytest.mark.asyncio
async def test_project_index_scheduler_reruns_coalesced_request_after_failed_run():
    """A request coalesced behind a run that raises must still get its rerun —
    a failed run is exactly when the coalesced request most needs its retry."""
    from basic_memory.index.local_schedulers import _dirty_project_index, _pending_project_index

    _clear_project_index_scheduler_state()
    runner = FailingThenGatedProjectIndexRunner()
    scheduler = LocalProjectIndexScheduler(project_index_runner=runner, test_mode=False)

    scheduler.schedule_project_index(project_id=13)
    await asyncio.wait_for(runner.first_run_started.wait(), timeout=1)
    scheduler.schedule_project_index(project_id=13, force_full=True)

    runner.release_first_run.set()
    await drain_background_tasks()

    assert runner.calls == [(13, False), (13, True)]
    assert 13 not in _pending_project_index
    assert 13 not in _dirty_project_index


@pytest.mark.asyncio
async def test_project_index_scheduler_single_flight_is_per_project():
    """One project's in-flight run must not block another project's run."""
    _clear_project_index_scheduler_state()
    runner = GatedProjectIndexRunner()
    scheduler = LocalProjectIndexScheduler(project_index_runner=runner, test_mode=False)

    scheduler.schedule_project_index(project_id=13)
    await asyncio.wait_for(runner.first_run_started.wait(), timeout=1)

    scheduler.schedule_project_index(project_id=14)
    await asyncio.sleep(0.05)
    # Project 14 ran while project 13 was still in flight.
    assert runner.calls == [(13, False), (14, False)]

    runner.release_first_run.set()
    await drain_background_tasks()

    assert runner.calls == [(13, False), (14, False)]


@pytest.mark.asyncio
async def test_project_index_scheduler_is_noop_in_test_mode():
    """Test mode must suppress the run without leaking a pending marker."""
    from basic_memory.index.local_schedulers import _pending_project_index

    _clear_project_index_scheduler_state()
    runner = StubProjectIndexRunner()

    scheduler = LocalProjectIndexScheduler(project_index_runner=runner, test_mode=True)
    scheduler.schedule_project_index(project_id=13)
    await asyncio.sleep(0.02)

    assert runner.indexed == []
    assert 13 not in _pending_project_index


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
    from basic_memory.index.local_schedulers import _pending_relation_resolution

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
    from basic_memory.index.local_schedulers import _pending_relation_resolution

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
    from basic_memory.index.local_schedulers import (
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
async def test_drain_background_tasks_awaits_scheduled_work():
    """Draining must complete in-flight scheduled work without relying on sleeps —
    one-shot CLI clients call it right before closing the event loop."""
    search_service = StubSearchService()

    scheduler = LocalEntityVectorSyncScheduler(
        search_service=search_service,
        test_mode=False,
    )
    scheduler.schedule_entity_vector_sync(entity_id=7, project_id=13)

    await drain_background_tasks()

    assert search_service.vector_synced == [7]


@pytest.mark.asyncio
async def test_drain_background_tasks_covers_follow_up_tasks():
    """A drained task can schedule a follow-up (the relation-resolution dirty
    re-run); the drain must wait for that wave too, not just the first snapshot."""
    from basic_memory.index.local_schedulers import (
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
                assert self.scheduler is not None
                self.scheduler.schedule_relation_resolution(project_id=34)
            return set()

    runtime = WriteDuringScanRuntime()
    scheduler = LocalRelationResolutionScheduler(
        relation_runtime=runtime,
        test_mode=False,
        debounce_seconds=0.0,
    )
    runtime.scheduler = scheduler

    scheduler.schedule_relation_resolution(project_id=34)

    await drain_background_tasks()

    assert runtime.resolve_calls == 2


@pytest.mark.asyncio
async def test_relation_resolution_scheduler_is_noop_in_test_mode():
    """Test mode should suppress the background resolution pass entirely."""
    from basic_memory.index.local_schedulers import _pending_relation_resolution

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
