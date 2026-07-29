"""Typed read-through behavior shared by cacheable API boundaries."""

from collections.abc import Awaitable, Callable

import logfire
from pydantic import BaseModel

from basic_memory.read_cache.contract import (
    ReadCache,
    ReadCacheKey,
    ReadCacheUnavailable,
)


def _record_event(key: ReadCacheKey, event: str) -> None:
    logfire.metric_counter("basic_memory_read_cache_events_total").add(
        1,
        attributes={
            "operation": key.operation.value,
            "event": event,
        },
    )


async def read_through_model[ModelT: BaseModel](
    *,
    cache: ReadCache,
    key: ReadCacheKey,
    model_type: type[ModelT],
    load: Callable[[], Awaitable[ModelT]],
    ttl_seconds: int,
    max_payload_bytes: int,
    should_store: Callable[[ModelT], bool] | None = None,
) -> ModelT:
    """Return a validated cached model or load and best-effort cache it."""
    if ttl_seconds <= 0:
        raise ValueError("read-cache ttl_seconds must be positive")
    if max_payload_bytes <= 0:
        raise ValueError("read-cache max_payload_bytes must be positive")

    with logfire.span(
        "read_cache.read_through",
        operation=key.operation.value,
    ) as span:
        try:
            lookup = await cache.lookup(key)
        except ReadCacheUnavailable:
            # Trigger: Redis is unreachable or timed out.
            # Why: the database or storage path remains authoritative.
            # Outcome: return fresh data without attempting another cache operation.
            _record_event(key, "bypass")
            span.set_attribute("cache.outcome", "bypass")
            return await load()

        if lookup.generation is None:
            # Trigger: the host selected the no-op cache implementation.
            # Why: an optional cache must not serialize every response merely to
            # discover that storage is disabled.
            # Outcome: execute only the authoritative read path.
            _record_event(key, "disabled")
            span.set_attribute("cache.outcome", "disabled")
            return await load()

        if lookup.payload is not None:
            _record_event(key, "hit")
            span.set_attributes(
                {
                    "cache.outcome": "hit",
                    "cache.payload_bytes": len(lookup.payload),
                }
            )
            return model_type.model_validate_json(lookup.payload)

        _record_event(key, "miss")
        value = await load()
        if should_store is not None and not should_store(value):
            _record_event(key, "ineligible")
            span.set_attribute("cache.outcome", "ineligible")
            return value

        payload = value.model_dump_json().encode("utf-8")
        if len(payload) > max_payload_bytes:
            _record_event(key, "oversize")
            span.set_attributes(
                {
                    "cache.outcome": "oversize",
                    "cache.payload_bytes": len(payload),
                }
            )
            return value

        try:
            store_status = await cache.store(
                key,
                lookup,
                payload,
                ttl_seconds=ttl_seconds,
            )
        except ReadCacheUnavailable:
            _record_event(key, "store_unavailable")
            span.set_attribute("cache.outcome", "store_unavailable")
            return value

        _record_event(key, store_status.value)
        span.set_attributes(
            {
                "cache.outcome": store_status.value,
                "cache.payload_bytes": len(payload),
            }
        )
        return value
