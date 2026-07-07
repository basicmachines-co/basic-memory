"""Portable vector-sync planning helpers."""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.progress import (
    VectorSyncBatchSummary,
    VectorSyncProgress,
    apply_vector_sync_batch_result,
    initialize_vector_sync_progress,
)
from basic_memory.runtime.storage import ProjectId

type CheckpointPhase = str | None
type EntityId = int

VECTOR_RESUME_PHASES = frozenset({"forward_refs_complete", "syncing_vectors"})
VECTOR_SYNC_CHUNK_SIZE = 100


class VectorSyncBatchProgressCallback(Protocol):
    """Low-level vector backend progress callback shape."""

    def __call__(self, entity_id: EntityId, index: int, total_count: int) -> None:
        """Report progress for one vector-sync entity inside a backend batch."""


def vector_sync_perf_counter() -> float:
    """Return a monotonic timestamp in seconds for vector-sync progress timing."""
    return time.perf_counter()


class VectorSyncExecutor(Protocol):
    """Capability for refreshing semantic vector chunks for entity batches."""

    async def sync_entity_vectors_batch(
        self,
        entity_ids: list[EntityId],
        progress_callback: VectorSyncBatchProgressCallback | None = None,
    ) -> VectorSyncBatchSummary:
        """Refresh vector chunks for one batch of entities."""


class VectorSyncLogger(Protocol):
    """Logging surface needed by the portable vector-sync runner."""

    def info(self, message: str, **kwargs: object) -> None:
        """Record vector-sync progress."""

    def error(self, message: str, **kwargs: object) -> None:
        """Record vector-sync failures."""


class VectorSyncEntitySource(Protocol):
    """Capability that selects project entities for vector sync work."""

    async def list_project_entity_ids(self) -> list[EntityId]: ...

    async def list_markdown_entity_ids(self) -> list[EntityId]: ...

    async def filter_markdown_entity_ids(self, entity_ids: set[EntityId]) -> set[EntityId]: ...


@dataclass(frozen=True, slots=True)
class RepositoryVectorSyncEntitySource:
    """Load vector-sync entity candidates from the Basic Memory entity table."""

    session_maker: async_sessionmaker[AsyncSession]
    project_id: ProjectId

    async def list_project_entity_ids(self) -> list[EntityId]:
        """Return all entity ids for this project in stable order."""
        async with self.session_maker() as session:
            result = await session.execute(
                text("""
                    SELECT id
                    FROM entity
                    WHERE project_id = :project_id
                    ORDER BY id
                """),
                {"project_id": self.project_id},
            )
            return [int(row[0]) for row in result.all()]

    async def list_markdown_entity_ids(self) -> list[EntityId]:
        """Return markdown entity ids for this project in stable order."""
        async with self.session_maker() as session:
            result = await session.execute(
                text("""
                    SELECT id
                    FROM entity
                    WHERE project_id = :project_id
                      AND content_type = 'text/markdown'
                    ORDER BY id
                """),
                {"project_id": self.project_id},
            )
            return [int(row[0]) for row in result.all()]

    async def filter_markdown_entity_ids(self, entity_ids: set[EntityId]) -> set[EntityId]:
        """Keep only markdown entity ids from a planned vector-sync candidate set."""
        if not entity_ids:
            return set()

        params: dict[str, int] = {"project_id": self.project_id}
        id_placeholders: list[str] = []
        for index, entity_id in enumerate(sorted(entity_ids)):
            key = f"entity_id_{index}"
            params[key] = entity_id
            id_placeholders.append(f":{key}")

        async with self.session_maker() as session:
            result = await session.execute(
                text(f"""
                    SELECT id
                    FROM entity
                    WHERE project_id = :project_id
                      AND content_type = 'text/markdown'
                      AND id IN ({", ".join(id_placeholders)})
                    ORDER BY id
                """),
                params,
            )
            return {int(row[0]) for row in result.all()}


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


async def run_vector_sync(
    entity_ids: Sequence[EntityId],
    *,
    vector_sync: VectorSyncExecutor,
    logger: VectorSyncLogger,
    resume_progress: VectorSyncProgress | None = None,
    chunk_size: int = VECTOR_SYNC_CHUNK_SIZE,
    project_id: int | None = None,
) -> VectorSyncProgress:
    """Sync semantic vectors for entity ids with durable chunk boundaries."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")

    progress_state = initialize_vector_sync_progress(
        entity_ids=entity_ids,
        resume_progress=resume_progress,
    )
    effective_entity_ids = progress_state.entity_ids
    total = len(effective_entity_ids)
    if total == 0:
        return progress_state

    sync_start = vector_sync_perf_counter() - progress_state.elapsed_seconds
    last_callback_at = vector_sync_perf_counter()

    if progress_state.next_index > 0:
        logger.info(f"♻️ [VECTOR] Resuming at entity {progress_state.next_index}/{total}")

    for chunk_start in range(progress_state.next_index, total, chunk_size):
        chunk_entity_ids = effective_entity_ids[chunk_start : chunk_start + chunk_size]

        def on_progress(
            entity_id: EntityId,
            index: int,
            total_count: int,
            *,
            chunk_start_index: int = chunk_start,
        ) -> None:
            nonlocal last_callback_at

            del entity_id, total_count
            now = vector_sync_perf_counter()
            previous_entity_seconds = now - last_callback_at
            completed = chunk_start_index + index

            if completed > 0 and (completed % 10 == 0 or previous_entity_seconds > 5.0):
                total_elapsed = now - sync_start
                rate = completed / total_elapsed if total_elapsed > 0 else 0.0
                logger.info(
                    f"🧠 [VECTOR] Progress: {completed}/{total} entities "
                    f"({previous_entity_seconds:.1f}s previous entity, "
                    f"{total_elapsed:.1f}s total, {rate:.1f} entities/s)"
                )
            last_callback_at = now

        batch_result = await vector_sync.sync_entity_vectors_batch(
            chunk_entity_ids,
            progress_callback=on_progress,
        )

        new_failed_entity_ids = apply_vector_sync_batch_result(
            progress_state,
            batch_result,
            next_index=chunk_start + len(chunk_entity_ids),
            elapsed_seconds=vector_sync_perf_counter() - sync_start,
        )
        for failed_entity_id in new_failed_entity_ids:
            logger.error(f"❌ [VECTOR] Failed to sync entity {failed_entity_id}")

    completion_message = (
        f"✅ [VECTOR] Completed: {progress_state.entities_synced}/{total} synced, "
        f"{progress_state.entities_failed} errors, "
        f"{progress_state.elapsed_seconds:.1f}s total, "
        f"{progress_state.embedding_jobs_total} embedding jobs, "
        f"{progress_state.embed_seconds_total:.1f}s embed, "
        f"{progress_state.write_seconds_total:.1f}s write"
    )
    if project_id is None:
        logger.info(completion_message)
    else:
        logger.info(completion_message, project_id=project_id)

    return progress_state
