"""Typed read-through behavior shared by cacheable API boundaries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import logfire
from pydantic import BaseModel

from basic_memory.read_cache.contract import (
    ReadCache,
    ReadCacheInvalidationStatus,
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


@dataclass(slots=True)
class ReadCacheScope[ModelT: BaseModel]:
    """Mutable state exchanged with one configured read-cache scope."""

    value: ModelT | None = None
    cacheable: bool = True

    def require_value(self) -> ModelT:
        """Return the authoritative value supplied by the route."""
        if self.value is None:
            raise RuntimeError("read-through cache scope exited without a result")
        return self.value


@dataclass(frozen=True, slots=True)
class ModelReadCache[ModelT: BaseModel]:
    """Typed facade that binds one cache backend to a response model and policy."""

    backend: ReadCache
    model_type: type[ModelT]
    ttl_seconds: int
    max_payload_bytes: int

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("read-cache ttl_seconds must be positive")
        if self.max_payload_bytes <= 0:
            raise ValueError("read-cache max_payload_bytes must be positive")

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        """Delegate invalidation without exposing the backend to API routes."""
        return await self.backend.invalidate_project(project_id)

    @asynccontextmanager
    async def read(
        self,
        *,
        key: ReadCacheKey,
    ) -> AsyncIterator[ReadCacheScope[ModelT]]:
        """Yield a cached model or store the authoritative value supplied by the route."""
        with logfire.span(
            "read_cache.read_through",
            operation=key.operation.value,
        ) as span:
            try:
                lookup = await self.backend.lookup(key)
            except ReadCacheUnavailable:
                # Trigger: Redis is unreachable or timed out.
                # Why: the database or storage path remains authoritative.
                # Outcome: return fresh data without attempting another cache operation.
                _record_event(key, "bypass")
                span.set_attribute("cache.outcome", "bypass")
                result = ReadCacheScope[ModelT]()
                yield result
                result.require_value()
                return

            if lookup.payload is not None:
                _record_event(key, "hit")
                span.set_attributes(
                    {
                        "cache.outcome": "hit",
                        "cache.payload_bytes": len(lookup.payload),
                    }
                )
                yield ReadCacheScope(
                    value=self.model_type.model_validate_json(lookup.payload),
                    cacheable=False,
                )
                return

            _record_event(key, "miss")
            result = ReadCacheScope[ModelT]()
            yield result
            value = result.require_value()
            if not result.cacheable:
                _record_event(key, "ineligible")
                span.set_attribute("cache.outcome", "ineligible")
                return

            payload = value.model_dump_json().encode("utf-8")
            if len(payload) > self.max_payload_bytes:
                _record_event(key, "oversize")
                span.set_attributes(
                    {
                        "cache.outcome": "oversize",
                        "cache.payload_bytes": len(payload),
                    }
                )
                return

            try:
                store_status = await self.backend.store(
                    key,
                    lookup,
                    payload,
                    ttl_seconds=self.ttl_seconds,
                )
            except ReadCacheUnavailable:
                _record_event(key, "store_unavailable")
                span.set_attribute("cache.outcome", "store_unavailable")
                return

            _record_event(key, store_status.value)
            span.set_attributes(
                {
                    "cache.outcome": store_status.value,
                    "cache.payload_bytes": len(payload),
                }
            )
