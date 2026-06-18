"""Tests for portable embedding index planning."""

from basic_memory.indexing.embedding_index_planning import (
    EmbeddingIndexPlanner,
    EmbeddingIndexTarget,
)


def test_embedding_index_planner_dedupes_entities_and_fingerprints_versions() -> None:
    planner = EmbeddingIndexPlanner()
    targets = [
        EmbeddingIndexTarget(entity_id=43, entity_checksum="checksum-43"),
        EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-42"),
        EmbeddingIndexTarget(entity_id=42, entity_checksum="newer-checksum-42"),
    ]

    plan = planner.plan(targets)
    same_plan = planner.plan(list(reversed(targets)))

    assert plan.total_targets == 3
    assert plan.entity_ids == (42, 43)
    assert plan.unique_entities == 2
    assert plan.fingerprint == "ac0bc9102835b829086fa453"
    assert plan.fingerprint == same_plan.fingerprint
