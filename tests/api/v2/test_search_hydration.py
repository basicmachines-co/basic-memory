"""Tests for search result hydration in to_search_results().

Proves that the batch fetch eliminates N+1 queries and that
entity ID lookups are correct across all result types.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from basic_memory.api.v2.utils import to_search_results
from basic_memory.repository.search_index_row import SearchIndexRow


# --- Helpers ---


def _make_entity(id: int, permalink: str) -> SimpleNamespace:
    return SimpleNamespace(id=id, permalink=permalink)


def _make_row(*, type: str, id: int, **kwargs) -> SearchIndexRow:
    now = datetime.now(timezone.utc)
    defaults = dict(
        project_id=1,
        file_path=f"notes/{id}.md",
        created_at=now,
        updated_at=now,
        score=1.0,
        title=f"Item {id}",
        permalink=f"notes/{id}",
    )
    defaults.update(kwargs)
    return SearchIndexRow(type=type, id=id, **defaults)


class SpyEntityService:
    """Tracks calls to get_entities_by_id and returns from a preset lookup."""

    def __init__(self, entities_by_id: dict[int, SimpleNamespace]):
        self.entities_by_id = entities_by_id
        self.calls: list[list[int]] = []

    async def get_entities_by_id(self, ids: list[int]):
        self.calls.append(ids)
        return [self.entities_by_id[i] for i in ids if i in self.entities_by_id]


# --- Single batch fetch (N+1 elimination) ---


@pytest.mark.asyncio
async def test_single_db_call_for_multiple_results():
    """Multiple search results must trigger exactly one get_entities_by_id call."""
    service = SpyEntityService(
        {
            1: _make_entity(1, "notes/a"),
            2: _make_entity(2, "notes/b"),
            3: _make_entity(3, "notes/c"),
        }
    )
    results = [
        _make_row(type="entity", id=1, entity_id=1),
        _make_row(type="entity", id=2, entity_id=2),
        _make_row(type="entity", id=3, entity_id=3),
    ]

    await to_search_results(service, results)

    assert len(service.calls) == 1, f"Expected 1 DB call, got {len(service.calls)}"


@pytest.mark.asyncio
async def test_no_db_call_for_empty_results():
    """Empty result list should not make any DB call."""
    service = SpyEntityService({})

    search_results = await to_search_results(service, [])

    assert len(service.calls) == 0
    assert search_results == []


# --- ID deduplication ---


@pytest.mark.asyncio
async def test_deduplicates_entity_ids():
    """Shared entity IDs across results should be fetched once, not per-result."""
    # entity_id=1 appears in all three results, from_id=1 overlaps with entity_id
    service = SpyEntityService(
        {
            1: _make_entity(1, "notes/shared"),
            2: _make_entity(2, "notes/target-a"),
            3: _make_entity(3, "notes/target-b"),
        }
    )
    results = [
        _make_row(type="relation", id=10, entity_id=1, from_id=1, to_id=2, relation_type="links"),
        _make_row(type="relation", id=11, entity_id=1, from_id=1, to_id=3, relation_type="links"),
    ]

    await to_search_results(service, results)

    # Single call with deduplicated IDs: {1, 2, 3}
    assert len(service.calls) == 1
    fetched_ids = set(service.calls[0])
    assert fetched_ids == {1, 2, 3}


# --- Correct entity-to-field mapping ---


@pytest.mark.asyncio
async def test_entity_result_maps_permalink():
    """Entity results should populate the 'entity' field with the entity's permalink."""
    service = SpyEntityService({5: _make_entity(5, "notes/my-entity")})
    results = [_make_row(type="entity", id=5, entity_id=5)]

    search_results = await to_search_results(service, results)

    assert len(search_results) == 1
    r = search_results[0]
    assert r.entity == "notes/my-entity"
    assert r.entity_id == 5
    assert r.from_entity is None
    assert r.to_entity is None


@pytest.mark.asyncio
async def test_observation_result_maps_parent_entity():
    """Observation results should populate 'entity' with the parent entity's permalink."""
    service = SpyEntityService({10: _make_entity(10, "notes/parent")})
    results = [_make_row(type="observation", id=20, entity_id=10)]

    search_results = await to_search_results(service, results)

    r = search_results[0]
    assert r.entity == "notes/parent"
    assert r.entity_id == 10
    assert r.observation_id == 20
    assert r.from_entity is None
    assert r.to_entity is None


@pytest.mark.asyncio
async def test_relation_result_maps_from_and_to():
    """Relation results should populate entity, from_entity, and to_entity correctly."""
    service = SpyEntityService(
        {
            1: _make_entity(1, "notes/parent"),
            2: _make_entity(2, "notes/source"),
            3: _make_entity(3, "notes/target"),
        }
    )
    results = [
        _make_row(
            type="relation",
            id=99,
            entity_id=1,
            from_id=2,
            to_id=3,
            relation_type="references",
        )
    ]

    search_results = await to_search_results(service, results)

    r = search_results[0]
    assert r.entity == "notes/parent"
    assert r.from_entity == "notes/source"
    assert r.to_entity == "notes/target"
    assert r.relation_id == 99
    assert r.relation_type == "references"


@pytest.mark.asyncio
async def test_relation_with_distinct_entity_and_from_ids():
    """When entity_id != from_id, from_entity must use from_id's permalink, not entity_id's.

    This was a bug in the old positional-index code: entities[0] was used for both
    'entity' and 'from_entity', which was wrong when entity_id != from_id.
    """
    service = SpyEntityService(
        {
            10: _make_entity(10, "notes/parent-entity"),
            20: _make_entity(20, "notes/actual-source"),
            30: _make_entity(30, "notes/target"),
        }
    )
    results = [
        _make_row(
            type="relation",
            id=50,
            entity_id=10,
            from_id=20,
            to_id=30,
            relation_type="derived_from",
        )
    ]

    search_results = await to_search_results(service, results)

    r = search_results[0]
    # entity should be the parent entity (entity_id=10)
    assert r.entity == "notes/parent-entity"
    # from_entity must be from_id=20, NOT entity_id=10
    assert r.from_entity == "notes/actual-source"
    assert r.to_entity == "notes/target"


# --- Mixed result types ---


@pytest.mark.asyncio
async def test_mixed_result_types_single_fetch():
    """A mix of entity, observation, and relation results should all hydrate in one fetch."""
    service = SpyEntityService(
        {
            1: _make_entity(1, "notes/entity-one"),
            2: _make_entity(2, "notes/entity-two"),
            3: _make_entity(3, "notes/entity-three"),
        }
    )
    results = [
        _make_row(type="entity", id=1, entity_id=1),
        _make_row(type="observation", id=10, entity_id=2, category="fact"),
        _make_row(type="relation", id=20, entity_id=1, from_id=1, to_id=3, relation_type="links"),
    ]

    search_results = await to_search_results(service, results)

    # Single DB call
    assert len(service.calls) == 1

    # Entity result
    assert search_results[0].entity == "notes/entity-one"
    assert search_results[0].entity_id == 1

    # Observation result
    assert search_results[1].entity == "notes/entity-two"
    assert search_results[1].observation_id == 10

    # Relation result
    assert search_results[2].from_entity == "notes/entity-one"
    assert search_results[2].to_entity == "notes/entity-three"


# --- Graceful handling of missing entities ---


@pytest.mark.asyncio
async def test_missing_entity_returns_none_permalink():
    """If an entity ID isn't found in the DB, permalink fields should be None."""
    # Only entity 1 exists; entity 99 (to_id) is missing
    service = SpyEntityService({1: _make_entity(1, "notes/source")})
    results = [
        _make_row(type="relation", id=5, entity_id=1, from_id=1, to_id=99, relation_type="links")
    ]

    search_results = await to_search_results(service, results)

    r = search_results[0]
    assert r.entity == "notes/source"
    assert r.from_entity == "notes/source"
    assert r.to_entity is None  # entity 99 not found


@pytest.mark.asyncio
async def test_null_ids_handled_gracefully():
    """Results with None entity_id/from_id/to_id should not cause errors."""
    service = SpyEntityService({})
    # Entity result: entity_id is the row id itself, from_id/to_id are None
    results = [_make_row(type="entity", id=1)]

    search_results = await to_search_results(service, results)

    # No entity_id on the row means no fetch needed, all fields None
    r = search_results[0]
    assert r.entity is None
    assert r.from_entity is None
    assert r.to_entity is None


# --- Scaling: prove O(1) DB calls ---


@pytest.mark.asyncio
async def test_single_db_call_scales_to_many_results():
    """Even with many results, only one DB call should be made."""
    n = 50
    entities = {i: _make_entity(i, f"notes/e-{i}") for i in range(1, n + 1)}
    service = SpyEntityService(entities)
    results = [_make_row(type="entity", id=i, entity_id=i) for i in range(1, n + 1)]

    search_results = await to_search_results(service, results)

    assert len(service.calls) == 1, f"Expected 1 DB call for {n} results, got {len(service.calls)}"
    assert len(search_results) == n
    # Every result got its permalink
    for i, r in enumerate(search_results, start=1):
        assert r.entity == f"notes/e-{i}"
