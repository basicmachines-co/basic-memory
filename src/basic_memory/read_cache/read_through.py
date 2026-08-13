"""Typed read-through behavior shared by cacheable API boundaries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256

import logfire
from pydantic import BaseModel, ValidationError

from basic_memory.read_cache.contract import (
    ReadCache,
    ReadCacheDataError,
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


def _generation_digest(generation: str) -> str:
    """Hash the opaque generation token before attaching it to diagnostics."""
    return sha256(generation.encode("utf-8")).hexdigest()


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
            span.set_attributes(
                {
                    "cache.operation": key.operation.value,
                    "cache.configured_ttl_seconds": self.ttl_seconds,
                    "cache.lookup.outcome": "pending",
                    "cache.store.outcome": "not_attempted",
                }
            )
            try:
                lookup = await self.backend.lookup(key)
            except ReadCacheUnavailable:
                # Trigger: Redis is unreachable or timed out.
                # Why: the database or storage path remains authoritative.
                # Outcome: return fresh data without attempting another cache operation.
                _record_event(key, "bypass")
                span.set_attribute("cache.lookup.outcome", "unavailable")
                result = ReadCacheScope[ModelT]()
                yield result
                result.require_value()
                return
            except ReadCacheDataError:
                # Trigger: Redis returned a structurally invalid cache value.
                # Why: corruption must remain fail-fast, but the span still needs a bounded
                # terminal outcome instead of retaining its initialization sentinel.
                # Outcome: report corruption and preserve the original exception for the caller.
                _record_event(key, "corrupt")
                span.set_attribute("cache.lookup.outcome", "corrupt")
                raise

            lookup_attributes: dict[str, str | int | float] = {
                "cache.lookup.outcome": "hit" if lookup.is_hit else "miss",
                "cache.generation_digest": _generation_digest(lookup.generation),
            }
            if lookup.payload is not None:
                lookup_attributes["cache.payload_bytes"] = len(lookup.payload)
                if lookup.remaining_ttl_seconds is not None:
                    lookup_attributes["cache.remaining_ttl_seconds"] = lookup.remaining_ttl_seconds
                try:
                    cached_value = self.model_type.model_validate_json(lookup.payload)
                except ValidationError:
                    # Trigger: the cache envelope is valid but its typed response payload is not.
                    # Why: treating invalid data as a hit hides corruption and leaves misleading
                    # telemetry, while falling back would weaken the fail-fast contract.
                    # Outcome: mark the lookup corrupt and re-raise the validation error unchanged.
                    lookup_attributes["cache.lookup.outcome"] = "corrupt"
                    _record_event(key, "corrupt")
                    span.set_attributes(lookup_attributes)
                    raise

                _record_event(key, "hit")
                span.set_attributes(lookup_attributes)
                yield ReadCacheScope(
                    value=cached_value,
                    cacheable=False,
                )
                return

            _record_event(key, "miss")
            span.set_attributes(lookup_attributes)
            result = ReadCacheScope[ModelT]()
            yield result
            value = result.require_value()
            if not result.cacheable:
                _record_event(key, "ineligible")
                span.set_attribute("cache.store.outcome", "ineligible")
                return

            payload = value.model_dump_json().encode("utf-8")
            if len(payload) > self.max_payload_bytes:
                _record_event(key, "oversize")
                span.set_attributes(
                    {
                        "cache.store.outcome": "oversize",
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
                span.set_attribute("cache.store.outcome", "unavailable")
                return
            except ReadCacheDataError:
                # Trigger: Redis returned an invalid result from its guarded store script.
                # Why: a Lua contract violation is corruption, not an availability failure;
                # swallowing it would conceal a broken cache implementation contract.
                # Outcome: preserve the miss, terminate the store outcome, and fail fast.
                _record_event(key, "store_corrupt")
                span.set_attributes(
                    {
                        "cache.store.outcome": "corrupt",
                        "cache.payload_bytes": len(payload),
                    }
                )
                raise

            _record_event(key, store_status.value)
            span.set_attributes(
                {
                    "cache.store.outcome": store_status.value,
                    "cache.payload_bytes": len(payload),
                }
            )
