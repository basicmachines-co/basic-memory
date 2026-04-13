"""Tests for graph context hydration in to_graph_context().

Proves that recent-activity/build-context hydration batches entity lookups
for entities, observations, and relations in a single repository call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from basic_memory.api.v2.utils import to_graph_context
from basic_memory.schemas.memory import EntitySummary, ObservationSummary, RelationSummary
from basic_memory.schemas.search import SearchItemType
from basic_memory.services.context_service import (
    ContextMetadata,
    ContextResult as ServiceContextResult,
    ContextResultItem,
    ContextResultRow,
)


# --- Helpers ---


def _make_entity(id: int, title: str, external_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=id, title=title, external_id=external_id)


def _make_row(*, type: str, id: int, root_id: int, **kwargs: Any) -> ContextResultRow:
    now = kwargs.pop("created_at", datetime.now(timezone.utc))
    defaults: dict[str, Any] = dict(
        title=f"Item {id}",
        permalink=f"notes/{id}",
        file_path=f"notes/{id}.md",
        depth=0,
        root_id=root_id,
        created_at=now,
    )
    defaults.update(kwargs)
    return ContextResultRow(type=type, id=id, **defaults)


class SpyEntityRepository:
    """Tracks batched ID lookups and returns entities from a preset map."""

    def __init__(self, entities_by_id: dict[int, SimpleNamespace]):
        self.entities_by_id = entities_by_id
        self.calls: list[list[int]] = []

    async def find_by_ids(self, ids: list[int]):
        self.calls.append(ids)
        return [self.entities_by_id[i] for i in ids if i in self.entities_by_id]


# --- Single batch fetch (N+1 elimination) ---


@pytest.mark.asyncio
async def test_to_graph_context_batches_entity_hydration_for_recent_activity():
    """Mixed entity, observation, and relation items must hydrate in one lookup."""
    repo = SpyEntityRepository(
        {
            1: _make_entity(1, "Root", "ext-root"),
            2: _make_entity(2, "Child", "ext-child"),
            3: _make_entity(3, "Peer", "ext-peer"),
        }
    )
    now = datetime.now(timezone.utc)

    root_entity = _make_row(
        type="entity",
        id=1,
        root_id=1,
        title="Root",
        permalink="notes/root",
        file_path="notes/root.md",
        created_at=now,
    )
    root_observation = _make_row(
        type="observation",
        id=10,
        root_id=1,
        title="fact: observed",
        permalink="notes/root/observations/fact/observed",
        file_path="notes/root.md",
        category="fact",
        content="observed",
        entity_id=1,
        created_at=now,
    )
    root_relation = _make_row(
        type="relation",
        id=20,
        root_id=1,
        title="links_to: Child",
        permalink="notes/root",
        file_path="notes/root.md",
        relation_type="links_to",
        from_id=1,
        to_id=2,
        depth=1,
        created_at=now,
    )
    child_observation = _make_row(
        type="observation",
        id=11,
        root_id=11,
        title="note: child update",
        permalink="notes/child/observations/note/update",
        file_path="notes/child.md",
        category="note",
        content="child update",
        entity_id=2,
        created_at=now,
    )
    peer_entity = _make_row(
        type="entity",
        id=3,
        root_id=11,
        title="Peer",
        permalink="notes/peer",
        file_path="notes/peer.md",
        depth=1,
        created_at=now,
    )

    context = ServiceContextResult(
        results=[
            ContextResultItem(
                primary_result=root_entity,
                observations=[root_observation],
                related_results=[root_relation],
            ),
            ContextResultItem(
                primary_result=child_observation,
                observations=[],
                related_results=[peer_entity],
            ),
        ],
        metadata=ContextMetadata(
            types=[
                SearchItemType.ENTITY,
                SearchItemType.OBSERVATION,
                SearchItemType.RELATION,
            ],
            depth=1,
            primary_count=2,
            related_count=2,
            total_relations=1,
            total_observations=1,
        ),
    )

    graph = await to_graph_context(context, entity_repository=repo, page=1, page_size=10)

    assert len(repo.calls) == 1, f"Expected 1 entity lookup, got {len(repo.calls)}"
    assert set(repo.calls[0]) == {1, 2, 3}

    first_result = graph.results[0]
    first_primary = first_result.primary_result
    assert isinstance(first_primary, EntitySummary)
    assert first_primary.external_id == "ext-root"

    first_observation = first_result.observations[0]
    assert isinstance(first_observation, ObservationSummary)
    assert first_observation.entity_external_id == "ext-root"
    assert first_observation.title == "Root"

    relation = first_result.related_results[0]
    assert isinstance(relation, RelationSummary)
    assert relation.from_entity == "Root"
    assert relation.from_entity_external_id == "ext-root"
    assert relation.to_entity == "Child"
    assert relation.to_entity_external_id == "ext-child"

    second_result = graph.results[1]
    second_primary = second_result.primary_result
    assert isinstance(second_primary, ObservationSummary)
    assert second_primary.entity_external_id == "ext-child"
    assert second_primary.title == "Child"

    peer_result = second_result.related_results[0]
    assert isinstance(peer_result, EntitySummary)
    assert peer_result.external_id == "ext-peer"


@pytest.mark.asyncio
async def test_to_graph_context_empty_results_skip_entity_lookup():
    """An empty context result should not perform any entity hydration lookup."""
    repo = SpyEntityRepository({})
    context = ServiceContextResult(results=[], metadata=ContextMetadata(depth=1))

    graph = await to_graph_context(context, entity_repository=repo)

    assert repo.calls == []
    assert list(graph.results) == []
