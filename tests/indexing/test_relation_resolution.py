"""Tests for portable relation resolution orchestration."""

import pytest

from basic_memory.indexing.relation_resolution import (
    ResolveRelationsResult,
    resolve_relations_until_stable,
)


class StubUnresolvedRelationCounter:
    """Returns scripted unresolved relation counts, in call order."""

    def __init__(self, counts: list[int]) -> None:
        self._counts = counts
        self.calls = 0

    async def count_unresolved_relations(self) -> int:
        index = min(self.calls, len(self._counts) - 1)
        self.calls += 1
        return self._counts[index]


class StubRelationResolutionPass:
    """Returns scripted affected entity sets, in pass order."""

    def __init__(self, affected_per_pass: list[set[int]]) -> None:
        self._affected_per_pass = affected_per_pass
        self.calls = 0

    async def resolve_relations(self) -> set[int]:
        index = min(self.calls, len(self._affected_per_pass) - 1)
        self.calls += 1
        return self._affected_per_pass[index]


@pytest.mark.asyncio
async def test_resolves_until_a_stable_pass_changes_nothing() -> None:
    counter = StubUnresolvedRelationCounter([3, 1])
    resolver = StubRelationResolutionPass([{10, 11}, set()])

    result = await resolve_relations_until_stable(
        resolver=resolver,
        unresolved_counter=counter,
    )

    assert result == ResolveRelationsResult(
        unresolved_before=3,
        remaining=1,
        passes=2,
        affected_entities=2,
    )
    assert result.resolved == 2
    assert resolver.calls == 2
    assert counter.calls == 2


@pytest.mark.asyncio
async def test_stops_immediately_when_no_relations_resolve() -> None:
    counter = StubUnresolvedRelationCounter([1, 1])
    resolver = StubRelationResolutionPass([set()])

    result = await resolve_relations_until_stable(
        resolver=resolver,
        unresolved_counter=counter,
    )

    assert result.passes == 1
    assert result.resolved == 0
    assert result.remaining == 1
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_resolution_loop_is_bounded_by_max_passes() -> None:
    counter = StubUnresolvedRelationCounter([2, 0])
    resolver = StubRelationResolutionPass([{1}])

    result = await resolve_relations_until_stable(
        resolver=resolver,
        unresolved_counter=counter,
        max_passes=3,
    )

    assert result.passes == 3
    assert result.remaining == 0
    assert resolver.calls == 3
