"""Portable vector-sync planning helpers."""

from collections.abc import Sequence

from basic_memory.indexing.progress import VectorSyncProgress

type CheckpointPhase = str | None
type EntityId = int

VECTOR_RESUME_PHASES = frozenset({"forward_refs_complete", "syncing_vectors"})


def plan_vector_sync_progress(
    *,
    checkpoint_phase: CheckpointPhase,
    candidate_entity_ids: Sequence[EntityId],
    resume_progress: VectorSyncProgress,
) -> VectorSyncProgress:
    """Build the durable vector-sync candidate plan for an indexing run."""
    vector_sync_entity_ids = list(resume_progress.entity_ids)
    known_vector_entity_ids = set(vector_sync_entity_ids)
    for entity_id in candidate_entity_ids:
        if entity_id in known_vector_entity_ids:
            continue
        vector_sync_entity_ids.append(entity_id)
        known_vector_entity_ids.add(entity_id)

    planned_vector_progress = (
        resume_progress
        if checkpoint_phase in VECTOR_RESUME_PHASES
        else VectorSyncProgress(entity_ids=list(vector_sync_entity_ids))
    )
    planned_vector_progress.entity_ids = list(vector_sync_entity_ids)
    return planned_vector_progress
