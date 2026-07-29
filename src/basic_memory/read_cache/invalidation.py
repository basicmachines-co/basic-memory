"""Best-effort project invalidation shared by mutation and indexing runtimes."""

from loguru import logger

import logfire
from basic_memory.read_cache.contract import (
    ReadCache,
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
    cache: ReadCache,
    project_id: str,
) -> ReadCacheInvalidationStatus:
    """Invalidate one project without failing an already-committed mutation."""
    with logfire.span("read_cache.invalidate_project") as span:
        try:
            status = await cache.invalidate_project(project_id)
        except ReadCacheUnavailable as error:
            # Trigger: an authoritative mutation committed while Redis was unavailable.
            # Why: failing the request cannot roll the mutation back and would invite
            # duplicate retries; the 60-second TTL already bounds stale exposure.
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
