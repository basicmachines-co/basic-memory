"""Tests for portable embedding index planning."""

from dataclasses import FrozenInstanceError

import pytest

from basic_memory.indexing.embedding_index_planning import (
    EmbeddingIndexBatchJobContext,
    EmbeddingIndexBatchJobRequest,
    EmbeddingIndexBatchResult,
    EmbeddingIndexResult,
    EmbeddingIndexJobRequest,
    EmbeddingIndexStatus,
    EmbeddingIndexPlanner,
    EmbeddingIndexTarget,
    plan_embedding_index_batch_jobs,
    run_embedding_index,
    run_embedding_index_batch,
    summarize_embedding_index_batch_result,
    vector_sync_perf_counter,
)


class BatchResult:
    entities_synced = 2
    entities_skipped = 1
    entities_failed = 0
    entities_deferred = 1


class SingleVectorSync:
    def __init__(self) -> None:
        self.synced_entity_ids: list[int] = []

    async def sync_entity_vectors(self, entity_id: int) -> None:
        self.synced_entity_ids.append(entity_id)


class BatchVectorSync:
    def __init__(self) -> None:
        self.synced_entity_ids: list[list[int]] = []

    async def sync_entity_vectors_batch(self, entity_ids: list[int]) -> BatchResult:
        self.synced_entity_ids.append(entity_ids)
        return BatchResult()


def test_embedding_index_job_request_matches_project_queue_identity() -> None:
    request = EmbeddingIndexJobRequest(
        project_id=7,
        entity_id=42,
        entity_checksum="checksum-42",
    )

    assert request.dedupe_key() == "index-embeddings:7:42:checksum-42"
    assert request.routing_headers({"source": "test"}) == {
        "source": "test",
        "project_id": "7",
    }
    assert (
        EmbeddingIndexJobRequest(
            project_id=7,
            entity_id=42,
        ).dedupe_key()
        == "index-embeddings:7:42:latest"
    )

    with pytest.raises(FrozenInstanceError):
        setattr(request, "entity_id", 43)


def test_embedding_index_batch_job_request_uses_core_fingerprint() -> None:
    request = EmbeddingIndexBatchJobRequest(
        project_id=7,
        project_path="main",
        entities=(
            EmbeddingIndexTarget(entity_id=43, entity_checksum="checksum-43"),
            EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-42"),
            EmbeddingIndexTarget(entity_id=42, entity_checksum="newer-checksum-42"),
        ),
    )

    assert request.dedupe_key() == "index-embeddings-batch:7:ac0bc9102835b829086fa453"
    assert request.routing_headers({"source": "test"}) == {
        "source": "test",
        "project_id": "7",
        "project_path": "main",
    }
    assert EmbeddingIndexBatchJobRequest(
        project_id=7,
        project_path="main",
    ).routing_headers() == {
        "project_id": "7",
        "project_path": "main",
    }


def test_plan_embedding_index_batch_jobs_chunks_enabled_targets() -> None:
    targets = (
        EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-42"),
        EmbeddingIndexTarget(entity_id=43, entity_checksum="checksum-43"),
        EmbeddingIndexTarget(entity_id=44, entity_checksum="checksum-44"),
    )

    assert plan_embedding_index_batch_jobs(
        EmbeddingIndexBatchJobContext(
            project_id=7,
            project_path="main",
            index_embeddings=True,
            targets=targets,
            batch_size=2,
        )
    ) == (
        EmbeddingIndexBatchJobRequest(
            project_id=7,
            project_path="main",
            entities=targets[:2],
        ),
        EmbeddingIndexBatchJobRequest(
            project_id=7,
            project_path="main",
            entities=targets[2:],
        ),
    )
    assert (
        plan_embedding_index_batch_jobs(
            EmbeddingIndexBatchJobContext(
                project_id=7,
                project_path="main",
                index_embeddings=False,
                targets=targets,
                batch_size=0,
            )
        )
        == ()
    )
    assert (
        plan_embedding_index_batch_jobs(
            EmbeddingIndexBatchJobContext(
                project_id=7,
                project_path="main",
                index_embeddings=True,
                targets=(),
                batch_size=0,
            )
        )
        == ()
    )

    with pytest.raises(ValueError, match="batch_size must be greater than zero"):
        plan_embedding_index_batch_jobs(
            EmbeddingIndexBatchJobContext(
                project_id=7,
                project_path="main",
                index_embeddings=True,
                targets=targets,
                batch_size=0,
            )
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


def test_embedding_index_planner_rejects_invalid_entity_shard_size() -> None:
    with pytest.raises(ValueError, match="shard_size must be greater than zero"):
        EmbeddingIndexPlanner().plan_entity_shard(["chunk-1"], shard_size=0)


def test_vector_sync_perf_counter_returns_monotonic_seconds() -> None:
    assert isinstance(vector_sync_perf_counter(), float)


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


@pytest.mark.asyncio
async def test_run_embedding_index_syncs_one_entity_and_returns_result() -> None:
    vector_sync = SingleVectorSync()
    request = EmbeddingIndexJobRequest(
        project_id=7,
        entity_id=42,
        entity_checksum="checksum-42",
    )

    result = await run_embedding_index(request, vector_sync=vector_sync)

    assert vector_sync.synced_entity_ids == [42]
    assert result == EmbeddingIndexResult(
        entity_id=42,
        status=EmbeddingIndexStatus.processed,
        reason="entity embeddings indexed: 42",
    )


@pytest.mark.asyncio
async def test_run_embedding_index_batch_dedupes_and_summarizes_vector_sync() -> None:
    vector_sync = BatchVectorSync()
    request = EmbeddingIndexBatchJobRequest(
        project_id=7,
        project_path="main",
        entities=(
            EmbeddingIndexTarget(entity_id=43, entity_checksum="checksum-43"),
            EmbeddingIndexTarget(entity_id=42, entity_checksum="checksum-42"),
            EmbeddingIndexTarget(entity_id=42, entity_checksum="newer-checksum-42"),
        ),
    )

    result = await run_embedding_index_batch(request, vector_sync=vector_sync)

    assert vector_sync.synced_entity_ids == [[42, 43]]
    assert result == EmbeddingIndexBatchResult(
        total_entities=3,
        unique_entities=2,
        synced_entities=2,
        skipped_entities=1,
        failed_entities=0,
        deferred_entities=1,
        reason="entity embedding batch indexed: 2 entities",
    )


@pytest.mark.asyncio
async def test_run_embedding_index_batch_skips_empty_request() -> None:
    vector_sync = BatchVectorSync()

    result = await run_embedding_index_batch(
        EmbeddingIndexBatchJobRequest(project_id=7, project_path="main"),
        vector_sync=vector_sync,
    )

    assert result == EmbeddingIndexBatchResult.no_entities()
    assert vector_sync.synced_entity_ids == []
