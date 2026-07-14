"""Background work scheduling for the local runtime.

Note mutations schedule derived work — semantic vector sync, search reindex,
project indexing, and forward-reference resolution — off the request path.
This module owns the in-process task machinery and the local scheduler
implementations; the FastAPI composition root in ``basic_memory.deps.services``
wires them into route dependencies. Cloud composes queue-backed equivalents
behind the same protocols.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Coroutine, Protocol

from loguru import logger

from basic_memory.index.local_project import ProjectIndexRunner
from basic_memory.indexing.relation_resolution import (
    RelationResolutionRuntime,
    resolve_project_relations,
)

# --- Scheduler Capabilities ---


class EntityVectorSyncScheduler(Protocol):
    """Schedule out-of-band semantic vector refreshes for note mutations."""

    def schedule_entity_vector_sync(self, *, entity_id: int, project_id: int) -> None: ...


class SearchReindexScheduler(Protocol):
    """Schedule a search-index rebuild for the active project."""

    def schedule_search_reindex(self, *, project_id: int) -> None: ...


class RelationResolutionScheduler(Protocol):
    """Schedule background forward-reference resolution after note mutations."""

    def schedule_relation_resolution(self, *, project_id: int) -> None: ...


class EntityVectorSyncSearchService(Protocol):
    async def sync_entity_vectors(self, entity_id: int) -> object: ...


class SearchReindexService(Protocol):
    async def reindex_all(self) -> object: ...


# --- Background Task Machinery ---


def _log_task_failure(completed: asyncio.Task) -> None:
    if completed.cancelled():
        return
    try:
        completed.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:  # pragma: no cover
        logger.exception("Background task failed", error=str(exc))


# The event loop holds only weak references to tasks; without a strong reference
# a suspended background task can be garbage-collected mid-flight and silently
# never finish (asyncio.create_task docs: "Save a reference to the result").
_background_tasks: set[asyncio.Task[object]] = set()


def _schedule_background_coroutine(
    coroutine: Coroutine[Any, Any, object],
    *,
    test_mode: bool,
) -> None:
    # Background tasks outlive pytest fixture cleanup and can race engine disposal.
    # Focused tests call the scheduler classes directly with test_mode=False.
    if test_mode:
        coroutine.close()
        return

    task = asyncio.create_task(coroutine)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task.add_done_callback(_log_task_failure)


async def drain_background_tasks() -> None:
    """Await scheduled background work until none remains.

    One-shot CLI clients close the event loop right after the command coroutine
    returns, which would cancel in-flight vector sync and relation resolution
    scheduled by the write path — leaving semantic search stale until an
    unrelated reindex. A task can schedule a follow-up task (the
    relation-resolution dirty re-run), so drain in waves until no running task
    remains. Failures are already logged by the done callback; the drain itself
    never raises.
    """
    while True:
        # Filter on task state, not set membership: completed tasks are pruned
        # by a call_soon done-callback that may not have run yet, and awaiting
        # only already-done tasks never suspends — checking membership alone
        # would busy-spin without ever letting that callback fire.
        running = [task for task in _background_tasks if not task.done()]
        if not running:
            break
        await asyncio.wait(running)


# --- Local Schedulers ---


@dataclass(frozen=True, slots=True)
class LocalEntityVectorSyncScheduler:
    search_service: EntityVectorSyncSearchService
    test_mode: bool

    def schedule_entity_vector_sync(self, *, entity_id: int, project_id: int) -> None:
        _ = project_id
        _schedule_background_coroutine(
            self.search_service.sync_entity_vectors(entity_id),
            test_mode=self.test_mode,
        )


# Process-lifetime single-flight state: project ids with an index run already
# scheduled or in flight. Every POST .../index and the startup scan previously
# spawned an independent full coordinator run over the same rows; overlapping
# runs are also the trigger for move/delete races, so at most one run per
# project may be in flight.
_pending_project_index: set[int] = set()
# Projects whose index request arrived while a run was already in flight, with
# the strongest force_full seen. The in-flight run scanned a snapshot that may
# predate the new request, so exactly one trailing rerun starts when it
# finishes — mirroring the relation-resolution dirty bit above.
_dirty_project_index: dict[int, bool] = {}


@dataclass(frozen=True, slots=True)
class LocalProjectIndexScheduler:
    """Run background project indexing with per-project single-flight coalescing."""

    project_index_runner: ProjectIndexRunner
    test_mode: bool

    def schedule_project_index(self, *, project_id: int, force_full: bool = False) -> None:
        # Early-return in test mode BEFORE touching the pending set: the
        # background coroutine (which clears the set) never runs under test mode,
        # so adding here would leak the project id forever.
        if self.test_mode:
            return
        # Coalesce: a run is already pending/in flight for this project. Mark it
        # dirty (keeping the strongest force_full) so one follow-up run covers
        # this request once the current run finishes, instead of racing it.
        if project_id in _pending_project_index:
            _dirty_project_index[project_id] = (
                _dirty_project_index.get(project_id, False) or force_full
            )
            return
        _pending_project_index.add(project_id)
        _schedule_background_coroutine(
            self._run_project_index(project_id, force_full=force_full),
            test_mode=self.test_mode,
        )

    async def _run_project_index(self, project_id: int, *, force_full: bool) -> None:
        try:
            await self.project_index_runner.index_project(project_id, force_full=force_full)
        finally:
            rerun_force_full = _dirty_project_index.pop(project_id, None)
            _pending_project_index.discard(project_id)
            # Re-arm inside finally, outside the in-flight window (pending now
            # cleared), so a request that raced the run gets its own pass even
            # when this run raised — a failed run is exactly when the coalesced
            # request most needs its retry. Bounded to one extra run per burst.
            if rerun_force_full is not None:
                self.schedule_project_index(project_id=project_id, force_full=rerun_force_full)


@dataclass(frozen=True, slots=True)
class LocalSearchReindexScheduler:
    search_service: SearchReindexService
    test_mode: bool

    def schedule_search_reindex(self, *, project_id: int) -> None:
        _ = project_id
        _schedule_background_coroutine(
            self.search_service.reindex_all(),
            test_mode=self.test_mode,
        )


# Process-lifetime coalescing state: project ids with a relation-resolution
# pass already pending or in flight. A burst of writes collapses to a single
# offline pass instead of one whole-project relation scan per write — running a
# scan per write made the write path heavier and piled up under concurrency
# (see benchmarks/docs/write-load-benchmark.md).
_pending_relation_resolution: set[int] = set()
# Project ids whose forward references arrived while a pass was already scanning.
# The scan resolves whatever is unresolved when it reads the table, so a write that
# commits during the scan (after that read) would otherwise be missed until an
# unrelated later trigger. This dirty bit forces exactly one follow-up pass.
_dirty_relation_resolution: set[int] = set()


@dataclass(frozen=True, slots=True)
class LocalRelationResolutionScheduler:
    """Back-resolve dangling forward references off the request path, coalesced.

    The MCP/API write path inline-indexes the materialized note but never
    back-resolves inbound `[[wikilinks]]` whose target the new note now
    satisfies (#1015). Resolution is a whole-project scan, so running it per
    write is both wasteful and a real write-load cost. Instead each write only
    enqueues: the first write of a burst schedules one debounced background pass
    and every other write coalesces onto it (at most one pending pass per
    project). The accept path stays light; reconciliation runs offline. No-op in
    test mode, consistent with the other local schedulers.
    """

    relation_runtime: RelationResolutionRuntime
    test_mode: bool
    debounce_seconds: float = 0.5

    def schedule_relation_resolution(self, *, project_id: int) -> None:
        # Early-return in test mode BEFORE touching the pending set: the
        # background coroutine (which clears the set) never runs under test mode,
        # so adding here would leak the project id forever.
        if self.test_mode:
            return
        # Coalesce: a pass is already pending/running for this project. Mark it
        # dirty so a scan that has already read the table re-runs once more and
        # picks up this write's rows, instead of dropping it (#1002 review).
        if project_id in _pending_relation_resolution:
            _dirty_relation_resolution.add(project_id)
            return
        _pending_relation_resolution.add(project_id)
        _schedule_background_coroutine(
            self._resolve_after_debounce(project_id),
            test_mode=self.test_mode,
        )

    async def _resolve_after_debounce(self, project_id: int) -> None:
        try:
            # Debounce: let the burst settle so one pass covers all of it.
            await asyncio.sleep(self.debounce_seconds)
            # Writes up to here are covered by the scan we are about to run, so only
            # writes that land DURING the scan should force a re-run.
            _dirty_relation_resolution.discard(project_id)
            await resolve_project_relations(self.relation_runtime)
        finally:
            rerun = project_id in _dirty_relation_resolution
            _dirty_relation_resolution.discard(project_id)
            _pending_relation_resolution.discard(project_id)
        # Re-arm outside the in-flight window (pending now cleared) so a write that
        # raced the scan gets its own pass. Bounded to one extra pass per burst.
        if rerun:
            self.schedule_relation_resolution(project_id=project_id)
