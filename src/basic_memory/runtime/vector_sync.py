"""Portable semantic-vector synchronization contracts."""

from dataclasses import dataclass
from typing import Protocol

type EntityId = int

VECTOR_SYNC_SAMPLE_ERROR_LIMIT = 3


@dataclass(frozen=True, slots=True)
class VectorSyncBatchResult:
    """Aggregate result for one batched semantic-vector sync run."""

    entities_total: int
    entities_synced: int
    entities_failed: int
    entities_deferred: int = 0
    entities_skipped: int = 0
    failed_entity_ids: tuple[EntityId, ...] = ()
    # Which entities ended in each terminal state, not just how many. The
    # repository records the deferred ones so readiness can tell a partially
    # embedded entity from a finished one (#1440 review).
    deferred_entity_ids: tuple[EntityId, ...] = ()
    synced_entity_ids: tuple[EntityId, ...] = ()
    sample_errors: tuple[str, ...] = ()
    vector_index: str = ""
    embedding_model: str = ""
    chunks_total: int = 0
    chunks_skipped: int = 0
    embedding_jobs_total: int = 0
    prepare_seconds_total: float = 0.0
    queue_wait_seconds_total: float = 0.0
    embed_seconds_total: float = 0.0
    write_seconds_total: float = 0.0


class EntityVectorSync(Protocol):
    """Capability that refreshes semantic vectors for one entity."""

    async def sync_entity_vectors(self, entity_id: EntityId) -> None: ...
