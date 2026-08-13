"""Best-effort project invalidation shared by mutation and indexing runtimes."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger

import logfire
from basic_memory.read_cache.contract import (
    ReadCacheInvalidator,
    ReadCacheInvalidationStatus,
    ReadCacheUnavailable,
)


def _record_invalidation_event(event: ReadCacheInvalidationStatus) -> None:
    logfire.metric_counter("basic_memory_read_cache_events_total").add(
        1,
        attributes={
            "operation": "project",
            "event": event.value,
        },
    )


async def invalidate_project_read_cache(
    cache: ReadCacheInvalidator,
    project_id: str,
) -> ReadCacheInvalidationStatus:
    """Invalidate one project without failing an already-committed mutation."""
    with logfire.span("read_cache.invalidate_project") as span:
        try:
            status = await cache.invalidate_project(project_id)
        except ReadCacheUnavailable as error:
            # Trigger: an authoritative mutation committed while Redis was unavailable.
            # Why: failing the request cannot roll the mutation back and would invite
            # duplicate retries; the configured response TTL bounds stale exposure.
            # Outcome: surface prominent telemetry and let the committed write succeed.
            status = ReadCacheInvalidationStatus.unavailable
            logger.error(
                "Read cache project invalidation unavailable; cached values may remain "
                "reachable until TTL expiry",
                error=str(error),
            )

        _record_invalidation_event(status)
        span.set_attribute("cache.outcome", status.value)
        return status


async def finish_project_read_cache_invalidation(
    cache: ReadCacheInvalidator,
    project_id: str,
) -> ReadCacheInvalidationStatus:
    """Finish invalidation before propagating caller cancellation."""
    invalidation = asyncio.create_task(invalidate_project_read_cache(cache, project_id))
    try:
        return await asyncio.shield(invalidation)
    except asyncio.CancelledError as cancellation:
        # Trigger: cancellation landed after authoritative state may have committed.
        # Why: abandoning this Redis write can leave that state hidden behind the
        #   previous generation until its TTL expires.
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


@asynccontextmanager
async def invalidate_cache(
    cache: ReadCacheInvalidator,
    project_id: str,
) -> AsyncIterator[None]:
    """Invalidate one project's read cache when the enclosed operation exits."""
    try:
        yield
    finally:
        await finish_project_read_cache_invalidation(cache, project_id)
