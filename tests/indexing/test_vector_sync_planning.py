"""Tests for portable vector-sync plan construction."""

from basic_memory.indexing.progress import VectorSyncProgress
from basic_memory.indexing.vector_sync_planning import plan_vector_sync_progress


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
