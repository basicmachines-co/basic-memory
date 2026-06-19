"""Tests for portable embedding index planning."""

from basic_memory.indexing.embedding_index_planning import (
    EmbeddingIndexBatchResult,
    EmbeddingIndexResult,
    EmbeddingIndexStatus,
    EmbeddingIndexPlanner,
    EmbeddingIndexTarget,
    summarize_embedding_index_batch_result,
)


class BatchResult:
    entities_synced = 2
    entities_skipped = 1
    entities_failed = 0
    entities_deferred = 1


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


def test_embedding_index_batch_result_summarizes_plan_and_sync_counts() -> None:
    planner = EmbeddingIndexPlanner()
    plan = planner.plan(
        [
            EmbeddingIndexTarget(entity_id=43, entity_checksum="checksum-43"),
            EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-42"),
            EmbeddingIndexTarget(entity_id=42, entity_checksum="newer-checksum-42"),
        ]
    )

    assert summarize_embedding_index_batch_result(plan, BatchResult()) == (
        EmbeddingIndexBatchResult(
            total_entities=3,
            unique_entities=2,
            synced_entities=2,
            skipped_entities=1,
            failed_entities=0,
            deferred_entities=1,
            reason="entity embedding batch indexed: 2 entities",
        )
    )


def test_embedding_index_batch_result_handles_empty_batches() -> None:
    assert EmbeddingIndexBatchResult.no_entities() == EmbeddingIndexBatchResult(
        total_entities=0,
        unique_entities=0,
        synced_entities=0,
        skipped_entities=0,
        failed_entities=0,
        deferred_entities=0,
        reason="no entities",
    )


def test_embedding_index_result_describes_one_entity_outcome() -> None:
    assert EmbeddingIndexResult(
        entity_id=42,
        status=EmbeddingIndexStatus.processed,
        reason="entity embeddings indexed: 42",
    ) == EmbeddingIndexResult(
        entity_id=42,
        status=EmbeddingIndexStatus.processed,
        reason="entity embeddings indexed: 42",
    )
