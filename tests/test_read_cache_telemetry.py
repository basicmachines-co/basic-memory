"""Telemetry contracts for typed semantic read-through caching."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256

import pytest
from pydantic import BaseModel, ValidationError

from basic_memory.read_cache import (
    ModelReadCache,
    ReadCacheDataError,
    ReadCacheInvalidationStatus,
    ReadCacheKey,
    ReadCacheLookup,
    ReadCacheOperation,
    ReadCacheStoreStatus,
    ReadCacheUnavailable,
    read_cache_request_digest,
)
from basic_memory.read_cache import read_through
from basic_memory.read_cache.policy import (
    READ_CACHE_TTL_SECONDS,
    SEARCH_READ_CACHE_TTL_SECONDS,
)

PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
GENERATION = "1" * 32


class CachedValue(BaseModel):
    title: str


def test_production_ttls_keep_search_results_for_thirty_minutes() -> None:
    assert READ_CACHE_TTL_SECONDS == 300
    assert SEARCH_READ_CACHE_TTL_SECONDS == 1800


@dataclass(slots=True)
class RecordedSpan:
    name: str
    attributes: dict[str, object] = field(default_factory=dict)

    def set_attribute(self, name: str, value: object) -> None:
        self.attributes[name] = value

    def set_attributes(self, attributes: dict[str, object]) -> None:
        self.attributes.update(attributes)


@dataclass(slots=True)
class RecordingCache:
    lookup_result: ReadCacheLookup | None = None
    lookup_error: ReadCacheUnavailable | ReadCacheDataError | None = None
    store_status: ReadCacheStoreStatus = ReadCacheStoreStatus.stored
    store_error: ReadCacheUnavailable | ReadCacheDataError | None = None
    store_ttls: list[int] = field(default_factory=list)

    async def lookup(self, key: ReadCacheKey) -> ReadCacheLookup:
        del key
        if self.lookup_error is not None:
            raise self.lookup_error
        if self.lookup_result is None:
            raise AssertionError("test cache requires a lookup result or error")
        return self.lookup_result

    async def store(
        self,
        key: ReadCacheKey,
        lookup: ReadCacheLookup,
        payload: bytes,
        *,
        ttl_seconds: int,
    ) -> ReadCacheStoreStatus:
        del key, lookup, payload
        self.store_ttls.append(ttl_seconds)
        if self.store_error is not None:
            raise self.store_error
        return self.store_status

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        del project_id
        return ReadCacheInvalidationStatus.invalidated


def _key() -> ReadCacheKey:
    return ReadCacheKey(
        project_id=PROJECT_ID,
        operation=ReadCacheOperation.entity,
        request_digest=read_cache_request_digest("entity-1"),
    )


def _capture_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[RecordedSpan], list[tuple[str, dict[str, str]]]]:
    spans: list[RecordedSpan] = []
    events: list[tuple[str, dict[str, str]]] = []

    @contextmanager
    def fake_span(name: str, **attributes: object) -> Iterator[RecordedSpan]:
        span = RecordedSpan(name=name, attributes=dict(attributes))
        spans.append(span)
        yield span

    class Counter:
        def add(self, amount: int, *, attributes: dict[str, str]) -> None:
            assert amount == 1
            events.append(("basic_memory_read_cache_events_total", attributes))

    def fake_metric_counter(name: str) -> Counter:
        assert name == "basic_memory_read_cache_events_total"
        return Counter()

    monkeypatch.setattr(read_through.logfire, "span", fake_span)
    monkeypatch.setattr(read_through.logfire, "metric_counter", fake_metric_counter)
    return spans, events


@pytest.mark.asyncio
async def test_hit_telemetry_reports_lookup_details_without_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans, events = _capture_telemetry(monkeypatch)
    payload = CachedValue(title="cached").model_dump_json().encode("utf-8")
    backend = RecordingCache(
        lookup_result=ReadCacheLookup(
            generation=GENERATION,
            payload=payload,
            remaining_ttl_seconds=241.25,
        )
    )
    cache = ModelReadCache(
        backend=backend,
        model_type=CachedValue,
        ttl_seconds=300,
        max_payload_bytes=1_024,
    )

    async with cache.read(key=_key()) as cached:
        assert cached.value == CachedValue(title="cached")

    assert len(spans) == 1
    assert spans[0].attributes == {
        "operation": "entity",
        "cache.operation": "entity",
        "cache.configured_ttl_seconds": 300,
        "cache.lookup.outcome": "hit",
        "cache.store.outcome": "not_attempted",
        "cache.generation_digest": sha256(GENERATION.encode("utf-8")).hexdigest(),
        "cache.payload_bytes": len(payload),
        "cache.remaining_ttl_seconds": 241.25,
    }
    assert events == [
        ("basic_memory_read_cache_events_total", {"operation": "entity", "event": "hit"})
    ]
    assert backend.store_ttls == []


@pytest.mark.asyncio
async def test_miss_telemetry_keeps_lookup_and_store_outcomes_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans, events = _capture_telemetry(monkeypatch)
    backend = RecordingCache(lookup_result=ReadCacheLookup(generation=GENERATION))
    cache = ModelReadCache(
        backend=backend,
        model_type=CachedValue,
        ttl_seconds=300,
        max_payload_bytes=1_024,
    )

    async with cache.read(key=_key()) as cached:
        cached.value = CachedValue(title="authoritative")

    payload = CachedValue(title="authoritative").model_dump_json().encode("utf-8")
    assert len(spans) == 1
    assert spans[0].attributes == {
        "operation": "entity",
        "cache.operation": "entity",
        "cache.configured_ttl_seconds": 300,
        "cache.lookup.outcome": "miss",
        "cache.store.outcome": "stored",
        "cache.generation_digest": sha256(GENERATION.encode("utf-8")).hexdigest(),
        "cache.payload_bytes": len(payload),
    }
    assert events == [
        ("basic_memory_read_cache_events_total", {"operation": "entity", "event": "miss"}),
        ("basic_memory_read_cache_events_total", {"operation": "entity", "event": "stored"}),
    ]
    assert backend.store_ttls == [300]


@pytest.mark.asyncio
async def test_unavailable_lookup_remains_fail_open_and_reports_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans, events = _capture_telemetry(monkeypatch)
    backend = RecordingCache(lookup_error=ReadCacheUnavailable("Redis unavailable"))
    cache = ModelReadCache(
        backend=backend,
        model_type=CachedValue,
        ttl_seconds=300,
        max_payload_bytes=1_024,
    )

    async with cache.read(key=_key()) as cached:
        cached.value = CachedValue(title="authoritative")

    assert spans[0].attributes["cache.lookup.outcome"] == "unavailable"
    assert spans[0].attributes["cache.store.outcome"] == "not_attempted"
    assert spans[0].attributes["cache.configured_ttl_seconds"] == 300
    assert events == [
        ("basic_memory_read_cache_events_total", {"operation": "entity", "event": "bypass"})
    ]
    assert backend.store_ttls == []


@pytest.mark.asyncio
async def test_corrupt_lookup_error_is_terminal_and_remains_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans, events = _capture_telemetry(monkeypatch)
    backend = RecordingCache(lookup_error=ReadCacheDataError("invalid Redis envelope"))
    cache = ModelReadCache(
        backend=backend,
        model_type=CachedValue,
        ttl_seconds=300,
        max_payload_bytes=1_024,
    )

    with pytest.raises(ReadCacheDataError, match="invalid Redis envelope"):
        async with cache.read(key=_key()):
            raise AssertionError("a corrupt lookup must not yield")

    assert spans[0].attributes["cache.lookup.outcome"] == "corrupt"
    assert spans[0].attributes["cache.store.outcome"] == "not_attempted"
    assert events == [
        ("basic_memory_read_cache_events_total", {"operation": "entity", "event": "corrupt"})
    ]
    assert backend.store_ttls == []


@pytest.mark.asyncio
async def test_invalid_cached_model_is_terminal_and_remains_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans, events = _capture_telemetry(monkeypatch)
    payload = b"not-json"
    backend = RecordingCache(
        lookup_result=ReadCacheLookup(
            generation=GENERATION,
            payload=payload,
            remaining_ttl_seconds=123.5,
        )
    )
    cache = ModelReadCache(
        backend=backend,
        model_type=CachedValue,
        ttl_seconds=300,
        max_payload_bytes=1_024,
    )

    with pytest.raises(ValidationError):
        async with cache.read(key=_key()):
            raise AssertionError("an invalid cached model must not yield")

    assert spans[0].attributes == {
        "operation": "entity",
        "cache.operation": "entity",
        "cache.configured_ttl_seconds": 300,
        "cache.lookup.outcome": "corrupt",
        "cache.store.outcome": "not_attempted",
        "cache.generation_digest": sha256(GENERATION.encode("utf-8")).hexdigest(),
        "cache.payload_bytes": len(payload),
        "cache.remaining_ttl_seconds": 123.5,
    }
    assert events == [
        ("basic_memory_read_cache_events_total", {"operation": "entity", "event": "corrupt"})
    ]
    assert backend.store_ttls == []


@pytest.mark.asyncio
async def test_corrupt_store_error_is_terminal_and_remains_fail_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans, events = _capture_telemetry(monkeypatch)
    backend = RecordingCache(
        lookup_result=ReadCacheLookup(generation=GENERATION),
        store_error=ReadCacheDataError("invalid Redis store result"),
    )
    cache = ModelReadCache(
        backend=backend,
        model_type=CachedValue,
        ttl_seconds=300,
        max_payload_bytes=1_024,
    )

    with pytest.raises(ReadCacheDataError, match="invalid Redis store result"):
        async with cache.read(key=_key()) as cached:
            cached.value = CachedValue(title="authoritative")

    payload = CachedValue(title="authoritative").model_dump_json().encode("utf-8")
    assert spans[0].attributes == {
        "operation": "entity",
        "cache.operation": "entity",
        "cache.configured_ttl_seconds": 300,
        "cache.lookup.outcome": "miss",
        "cache.store.outcome": "corrupt",
        "cache.generation_digest": sha256(GENERATION.encode("utf-8")).hexdigest(),
        "cache.payload_bytes": len(payload),
    }
    assert events == [
        ("basic_memory_read_cache_events_total", {"operation": "entity", "event": "miss"}),
        (
            "basic_memory_read_cache_events_total",
            {"operation": "entity", "event": "store_corrupt"},
        ),
    ]
    assert backend.store_ttls == [300]
