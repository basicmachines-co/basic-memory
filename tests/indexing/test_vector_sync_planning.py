"""Tests for portable vector-sync plan construction."""

from dataclasses import dataclass, field

import pytest
from basic_memory.indexing.progress import VectorSyncProgress
from basic_memory.indexing.vector_sync_planning import (
    VectorSyncBatchProgressCallback,
    plan_vector_sync_progress,
    run_vector_sync,
)


@dataclass(slots=True)
class BatchSummary:
    """Minimal batch result for exercising portable vector-sync execution."""

    entities_synced: int
    entities_failed: int = 0
    failed_entity_ids: list[int] = field(default_factory=list)
    embedding_jobs_total: int = 0
    embed_seconds_total: float = 0.0
    write_seconds_total: float = 0.0


@dataclass(slots=True)
class RecordingVectorSync:
    """Fake vector sync adapter that records chunk calls."""

    results: list[BatchSummary]
    progress_events: list[tuple[int, int, int]] = field(default_factory=list)
    calls: list[list[int]] = field(default_factory=list)

    async def sync_entity_vectors_batch(
        self,
        entity_ids: list[int],
        progress_callback: VectorSyncBatchProgressCallback | None = None,
    ) -> BatchSummary:
        self.calls.append(list(entity_ids))
        if progress_callback is not None:
            for entity_id, index, total_count in self.progress_events:
                progress_callback(entity_id, index, total_count)
        return self.results.pop(0)


@dataclass(slots=True)
class RecordingLogger:
    """Collect vector-sync log calls without depending on a concrete logger."""

    infos: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    errors: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def info(self, message: str, **kwargs: object) -> None:
        self.infos.append((message, kwargs))

    def error(self, message: str, **kwargs: object) -> None:
        self.errors.append((message, kwargs))


def test_vector_sync_plan_starts_new_progress_for_non_resume_phase() -> None:
    resume_progress = VectorSyncProgress(
        entity_ids=[11, 22],
        next_index=1,
        entities_synced=1,
        embedding_jobs_total=20,
    )

    planned = plan_vector_sync_progress(
        checkpoint_phase="relations_complete",
        candidate_entity_ids=[22, 33, 11, 44, 33],
        resume_progress=resume_progress,
    )

    assert planned is not resume_progress
    assert planned.entity_ids == [11, 22, 33, 44]
    assert planned.next_index == 0
    assert planned.entities_synced == 0
    assert planned.embedding_jobs_total == 0


def test_vector_sync_plan_reuses_resume_state_for_vector_resume_phase() -> None:
    resume_progress = VectorSyncProgress(
        entity_ids=[10, 20],
        next_index=1,
        entities_synced=1,
        entities_failed=0,
        embed_seconds_total=2.5,
        write_seconds_total=0.5,
        elapsed_seconds=3.0,
    )

    planned = plan_vector_sync_progress(
        checkpoint_phase="syncing_vectors",
        candidate_entity_ids=[20, 30],
        resume_progress=resume_progress,
    )

    assert planned is resume_progress
    assert planned.entity_ids == [10, 20, 30]
    assert planned.next_index == 1
    assert planned.entities_synced == 1
    assert planned.embed_seconds_total == 2.5


def test_vector_sync_plan_uses_resume_state_after_forward_refs_complete() -> None:
    resume_progress = VectorSyncProgress(entity_ids=[1])

    planned = plan_vector_sync_progress(
        checkpoint_phase="forward_refs_complete",
        candidate_entity_ids=[2, 1, 3],
        resume_progress=resume_progress,
    )

    assert planned is resume_progress
    assert planned.entity_ids == [1, 2, 3]


def test_vector_sync_plan_dedupes_candidates_for_empty_resume() -> None:
    planned = plan_vector_sync_progress(
        checkpoint_phase=None,
        candidate_entity_ids=[5, 5, 6],
        resume_progress=VectorSyncProgress(),
    )

    assert planned.entity_ids == [5, 6]
    assert planned.entities_total == 2


@pytest.mark.asyncio
async def test_run_vector_sync_resumes_from_chunk_boundary_and_reports_progress() -> None:
    vector_sync = RecordingVectorSync(
        results=[
            BatchSummary(
                entities_synced=25,
                entities_failed=1,
                failed_entity_ids=[125],
                embedding_jobs_total=50,
                embed_seconds_total=3.0,
                write_seconds_total=1.0,
            )
        ]
    )
    logger = RecordingLogger()
    progress_updates: list[tuple[str, VectorSyncProgress]] = []

    async def record_progress(progress: str, vector_progress: VectorSyncProgress) -> None:
        progress_updates.append(
            (
                progress,
                VectorSyncProgress.from_checkpoint_state(vector_progress.to_checkpoint_state()),
            )
        )

    clock_values = [20.0, 20.0, 23.0]

    def clock() -> float:
        return clock_values.pop(0)

    resumed = await run_vector_sync(
        list(range(1, 126)),
        vector_sync=vector_sync,
        logger=logger,
        report_progress=record_progress,
        resume_progress=VectorSyncProgress(
            entity_ids=list(range(1, 126)),
            next_index=100,
            entities_synced=100,
            entities_failed=0,
            embedding_jobs_total=200,
            embed_seconds_total=10.0,
            write_seconds_total=2.0,
            elapsed_seconds=15.0,
        ),
        project_id=7,
        clock=clock,
    )

    assert vector_sync.calls == [list(range(101, 126))]
    assert resumed.next_index == 125
    assert resumed.entities_synced == 125
    assert resumed.entities_failed == 1
    assert resumed.failed_entity_ids == [125]
    assert resumed.embedding_jobs_total == 250
    assert resumed.embed_seconds_total == 13.0
    assert resumed.write_seconds_total == 3.0
    assert resumed.elapsed_seconds == 18.0
    assert progress_updates[-1][0] == "Syncing vectors: 125/125 entities (100.0%)..."
    assert progress_updates[-1][1].next_index == 125
    assert logger.errors == [("❌ [VECTOR] Failed to sync entity 125", {})]
    assert logger.infos[-1][1] == {"project_id": 7}


@pytest.mark.asyncio
async def test_run_vector_sync_returns_empty_progress_without_executor_call() -> None:
    vector_sync = RecordingVectorSync(results=[])
    logger = RecordingLogger()

    progress = await run_vector_sync([], vector_sync=vector_sync, logger=logger)

    assert progress == VectorSyncProgress()
    assert vector_sync.calls == []
    assert logger.infos == []


@pytest.mark.asyncio
async def test_run_vector_sync_logs_periodic_batch_progress() -> None:
    vector_sync = RecordingVectorSync(
        results=[BatchSummary(entities_synced=3)],
        progress_events=[(1, 1, 3)],
    )
    logger = RecordingLogger()
    clock_values = [0.0, 0.0, 6.5, 8.0]

    def clock() -> float:
        return clock_values.pop(0)

    await run_vector_sync(
        [1, 2, 3],
        vector_sync=vector_sync,
        logger=logger,
        clock=clock,
    )

    assert logger.infos[0][0] == (
        "🧠 [VECTOR] Progress: 1/3 entities (6.5s previous entity, 6.5s total, 0.2 entities/s)"
    )


@pytest.mark.asyncio
async def test_run_vector_sync_rejects_invalid_chunk_size() -> None:
    with pytest.raises(ValueError, match="chunk_size"):
        await run_vector_sync(
            [1],
            vector_sync=RecordingVectorSync(results=[]),
            logger=RecordingLogger(),
            chunk_size=0,
        )
