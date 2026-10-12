"""Best-effort invalidation shared by mutation and indexing runtimes.

Writers that know what they changed record a ``ReadCacheChange`` and retire only the
reads that depend on it. Writers that do not, and every failure path, retire the
whole project: a partial commit we cannot describe must not stay hidden behind an
older cached read.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from loguru import logger

import logfire
from basic_memory.read_cache.contract import (
    ReadCacheChange,
    ReadCacheInvalidator,
    ReadCacheInvalidationStatus,
    ReadCacheUnavailable,
)


def _invalidation_scope(change: ReadCacheChange) -> str:
    """Bounded label for the widest group a change retires."""
    if change.project:
        return "project"
    if change.notes:
        return "notes"
    if change.content:
        return "content"
    return "none"


def _record_invalidation_event(
    change: ReadCacheChange,
    event: ReadCacheInvalidationStatus,
) -> None:
    logfire.metric_counter("basic_memory_read_cache_events_total").add(
        1,
        attributes={
            "operation": "invalidate",
            "scope": _invalidation_scope(change),
            "event": event.value,
        },
    )


async def invalidate_read_cache(
    cache: ReadCacheInvalidator,
    project_id: str,
    change: ReadCacheChange,
) -> ReadCacheInvalidationStatus:
    """Retire the reads ``change`` made stale without failing a committed mutation."""
    if change.is_empty:
        return ReadCacheInvalidationStatus.invalidated
    try:
        status = await cache.invalidate(project_id, change)
    except ReadCacheUnavailable as error:
        # Trigger: an authoritative mutation committed while Redis was unavailable.
        # Why: failing the request cannot roll the mutation back and would invite
        # duplicate retries; the configured response TTL bounds stale exposure.
        # Outcome: surface prominent telemetry and let the committed write succeed.
        status = ReadCacheInvalidationStatus.unavailable
        logger.error(
            "Read cache invalidation unavailable; cached values may remain "
            "reachable until TTL expiry",
            error=str(error),
        )

    # Every write invalidates, so a span here fired per mutation; the events counter
    # carries the outcome and unavailability still logs an error above.
    _record_invalidation_event(change, status)
    return status


async def invalidate_project_read_cache(
    cache: ReadCacheInvalidator,
    project_id: str,
) -> ReadCacheInvalidationStatus:
    """Retire every cached read in one project."""
    return await invalidate_read_cache(cache, project_id, ReadCacheChange.whole_project())


async def finish_read_cache_invalidation(
    cache: ReadCacheInvalidator,
    project_id: str,
    change: ReadCacheChange,
) -> ReadCacheInvalidationStatus:
    """Finish invalidation before propagating caller cancellation."""
    invalidation = asyncio.create_task(invalidate_read_cache(cache, project_id, change))
    try:
        return await asyncio.shield(invalidation)
    except asyncio.CancelledError as cancellation:
        # Trigger: cancellation landed after authoritative state may have committed.
        # Why: abandoning this Redis write can leave that state hidden behind the
        #   previous version until its TTL expires.
        # Outcome: finish invalidation, then preserve the caller's cancellation.
        cleanup_error: BaseException | None = None
        while not invalidation.done():
            try:
                await asyncio.shield(invalidation)
            except asyncio.CancelledError:
                continue
            except BaseException as error:
                cleanup_error = error
        if cleanup_error is None:
            if invalidation.cancelled():
                cleanup_error = asyncio.CancelledError("read-cache invalidation task was cancelled")
            else:
                cleanup_error = invalidation.exception()
        if cleanup_error is not None:
            cancellation.add_note(
                f"Read-cache invalidation failed during cancellation: {cleanup_error!r}"
            )
        raise


async def finish_project_read_cache_invalidation(
    cache: ReadCacheInvalidator,
    project_id: str,
) -> ReadCacheInvalidationStatus:
    """Retire every cached read in one project, surviving caller cancellation."""
    return await finish_read_cache_invalidation(
        cache,
        project_id,
        ReadCacheChange.whole_project(),
    )


@asynccontextmanager
async def invalidate_cache(
    cache: ReadCacheInvalidator,
    project_id: str,
) -> AsyncIterator[None]:
    """Retire one project's whole read cache when the enclosed operation exits."""
    try:
        yield
    finally:
        await finish_project_read_cache_invalidation(cache, project_id)


@dataclass(slots=True)
class ReadCacheChangeRecorder:
    """Collects what an enclosed operation changed, as it learns it."""

    change: ReadCacheChange = field(default_factory=ReadCacheChange)

    def record(self, change: ReadCacheChange) -> None:
        self.change = self.change | change


@asynccontextmanager
async def invalidate_recorded_change(
    cache: ReadCacheInvalidator,
    project_id: str,
) -> AsyncIterator[ReadCacheChangeRecorder]:
    """Retire what the enclosed operation recorded, or the whole project if it raised.

    A raise can follow a commit (transaction exit, a later follow-up step), and the
    recorder may not yet describe that commit, so failure retires everything.
    """
    recorder = ReadCacheChangeRecorder()
    try:
        yield recorder
    except BaseException:
        await finish_project_read_cache_invalidation(cache, project_id)
        raise
    await finish_read_cache_invalidation(cache, project_id, recorder.change)
