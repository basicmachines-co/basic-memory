"""Telemetry and policy contracts for read-cache invalidation."""

from dataclasses import dataclass, field

import pytest

from basic_memory.read_cache import (
    ReadCacheChange,
    ReadCacheInvalidationStatus,
    ReadCacheUnavailable,
    invalidate_read_cache,
)
from basic_memory.read_cache import invalidation
from basic_memory.read_cache.policy import (
    READ_CACHE_TTL_SECONDS,
    RECENT_ACTIVITY_READ_CACHE_TTL_SECONDS,
    SEARCH_READ_CACHE_TTL_SECONDS,
)

PROJECT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NOTE_ID = "11111111-1111-4111-8111-111111111111"


def test_ttls_bound_memory_not_freshness() -> None:
    """Invalidation is explicit, so entries live for days; recent activity is time-relative."""
    assert READ_CACHE_TTL_SECONDS == 7 * 24 * 3600
    assert SEARCH_READ_CACHE_TTL_SECONDS == 24 * 3600
    assert RECENT_ACTIVITY_READ_CACHE_TTL_SECONDS == 15 * 60


@dataclass(slots=True)
class RecordingInvalidator:
    """Records invalidations; ``unavailable`` simulates Redis being down."""

    unavailable: bool = False
    changes: list[ReadCacheChange] = field(default_factory=list)

    async def invalidate(
        self,
        project_id: str,
        change: ReadCacheChange,
    ) -> ReadCacheInvalidationStatus:
        del project_id
        if self.unavailable:
            raise ReadCacheUnavailable("redis down")
        self.changes.append(change)
        return ReadCacheInvalidationStatus.invalidated

    async def invalidate_project(self, project_id: str) -> ReadCacheInvalidationStatus:
        return await self.invalidate(project_id, ReadCacheChange.whole_project())


@pytest.fixture
def recorded_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Capture the invalidation counter's attributes (the metric is the observable)."""
    events: list[dict[str, object]] = []

    class Counter:
        def add(self, amount: int, attributes: dict[str, object]) -> None:
            assert amount == 1
            events.append(attributes)

    monkeypatch.setattr(invalidation.logfire, "metric_counter", lambda name: Counter())
    return events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "scope"),
    [
        (ReadCacheChange.whole_project(), "project"),
        (ReadCacheChange.note_content({NOTE_ID}), "notes"),
        (ReadCacheChange(content=True), "content"),
    ],
)
async def test_invalidation_reports_its_widest_scope(
    recorded_events: list[dict[str, object]],
    change: ReadCacheChange,
    scope: str,
) -> None:
    cache = RecordingInvalidator()

    status = await invalidate_read_cache(cache, PROJECT_ID, change)

    assert status is ReadCacheInvalidationStatus.invalidated
    assert cache.changes == [change]
    assert recorded_events == [{"operation": "invalidate", "scope": scope, "event": "invalidated"}]


@pytest.mark.asyncio
async def test_unavailable_invalidation_is_reported_and_does_not_raise(
    recorded_events: list[dict[str, object]],
) -> None:
    status = await invalidate_read_cache(
        RecordingInvalidator(unavailable=True),
        PROJECT_ID,
        ReadCacheChange.note_content({NOTE_ID}),
    )

    assert status is ReadCacheInvalidationStatus.unavailable
    assert recorded_events == [
        {"operation": "invalidate", "scope": "notes", "event": "unavailable"}
    ]


@pytest.mark.asyncio
async def test_an_empty_change_touches_neither_redis_nor_telemetry(
    recorded_events: list[dict[str, object]],
) -> None:
    cache = RecordingInvalidator()

    status = await invalidate_read_cache(cache, PROJECT_ID, ReadCacheChange())

    assert status is ReadCacheInvalidationStatus.invalidated
    assert cache.changes == []
    assert recorded_events == []
