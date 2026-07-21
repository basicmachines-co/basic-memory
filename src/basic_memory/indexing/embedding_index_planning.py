"""Portable semantic embedding planning and execution."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing.progress import (
    VectorSyncBatchSummary,
    VectorSyncProgress,
    apply_vector_sync_batch_result,
    initialize_vector_sync_progress,
)
from basic_memory.runtime.storage import ProjectId

if TYPE_CHECKING:  # pragma: no cover
    from loguru import Logger

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


@dataclass(frozen=True, slots=True)
class EmbeddingIndexTarget:
    """One entity version that may need embedding indexing."""

    entity_id: int
    entity_checksum: str


@dataclass(frozen=True, slots=True)
class EmbeddingIndexJobRequest:
    """Queue-neutral request shape for indexing embeddings for one entity."""

    project_id: int
    entity_id: int
    entity_checksum: str | None = None

    def dedupe_key(self) -> str:
        """Return the logical single-entity embedding queue identity."""
        checksum_key = self.entity_checksum or "latest"
        return f"index-embeddings:{self.project_id}:{self.entity_id}:{checksum_key}"

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the single-entity embedding job."""
        routing_headers = dict(headers or {})
        routing_headers["project_id"] = str(self.project_id)
        return routing_headers


@dataclass(frozen=True, slots=True)
class EmbeddingIndexBatchJobRequest:
    """Queue-neutral request shape for indexing embeddings for entity versions."""

    project_id: int
    project_path: str
    entities: tuple[EmbeddingIndexTarget, ...] = ()

    def dedupe_key(self) -> str:
        """Return the logical batch embedding queue identity."""
        fingerprint = EmbeddingIndexPlanner().fingerprint(self.entities)
        return f"index-embeddings-batch:{self.project_id}:{fingerprint}"

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for the batch embedding job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "project_id": str(self.project_id),
                "project_path": self.project_path,
            }
        )
        return routing_headers


@dataclass(frozen=True, slots=True)
class EmbeddingIndexBatchJobContext:
    """Indexed entity versions that may need batched embedding jobs."""

    project_id: int
    project_path: str
    index_embeddings: bool
    targets: tuple[EmbeddingIndexTarget, ...]
    batch_size: int


class EmbeddingIndexStatus(StrEnum):
    """Normal outcomes for one semantic-embedding indexing job."""

    processed = "processed"
    noop = "noop"


@dataclass(frozen=True, slots=True)
class EmbeddingIndexResult:
    """Summary of one embedding indexing job."""

    entity_id: int
    status: EmbeddingIndexStatus
    reason: str


@dataclass(frozen=True, slots=True)
class EmbeddingIndexPlan:
    """The entity set handed to vector sync code."""

    total_targets: int
    entity_ids: tuple[int, ...]
    fingerprint: str

    @property
    def unique_entities(self) -> int:
        """Number of unique entities in this plan."""
        return len(self.entity_ids)


class EmbeddingIndexBatchSummary(Protocol):
    """Vector sync counts produced by the concrete search backend."""

    entities_synced: int
    entities_skipped: int
    entities_failed: int
    entities_deferred: int


class EmbeddingVectorSync(Protocol):
    """Capability that refreshes vectors for one entity."""

    async def sync_entity_vectors(self, entity_id: int) -> object: ...


class EmbeddingBatchVectorSync(Protocol):
    """Capability that refreshes vectors for a batch of entities."""

    async def sync_entity_vectors_batch(
        self,
        entity_ids: list[int],
    ) -> EmbeddingIndexBatchSummary: ...


@dataclass(frozen=True, slots=True)
class EmbeddingIndexBatchResult:
    """Summary of a batch embedding index operation."""

    total_entities: int
    unique_entities: int
    synced_entities: int
    skipped_entities: int
    failed_entities: int
    deferred_entities: int
    reason: str

    @classmethod
    def no_entities(cls) -> "EmbeddingIndexBatchResult":
        """Return the result for an empty batch that does no backend work."""
        return cls(
            total_entities=0,
            unique_entities=0,
            synced_entities=0,
            skipped_entities=0,
            failed_entities=0,
            deferred_entities=0,
            reason="no entities",
        )


class EmbeddingIndexPlanner:
    """Plan semantic embedding work across single, batch, and resumable paths."""

    def plan(self, targets: Sequence[EmbeddingIndexTarget]) -> EmbeddingIndexPlan:
        """Dedupe entity ids and fingerprint the queued entity versions."""
        entity_ids = tuple(sorted({target.entity_id for target in targets}))
        return EmbeddingIndexPlan(
            total_targets=len(targets),
            entity_ids=entity_ids,
            fingerprint=self.fingerprint(targets),
        )

    def fingerprint(self, targets: Sequence[EmbeddingIndexTarget]) -> str:
        """Return a stable key for one batch of queued entity versions."""
        material = "|".join(
            f"{target.entity_id}:{target.entity_checksum}"
            for target in sorted(targets, key=lambda item: (item.entity_id, item.entity_checksum))
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def plan_batch_jobs(
        self,
        context: EmbeddingIndexBatchJobContext,
    ) -> tuple[EmbeddingIndexBatchJobRequest, ...]:
        """Plan queue-neutral batch embedding jobs after a file-index batch."""
        if not context.index_embeddings:
            return ()
        if not context.targets:
            return ()
        if context.batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")

        return tuple(
            EmbeddingIndexBatchJobRequest(
                project_id=context.project_id,
                project_path=context.project_path,
                entities=context.targets[index : index + context.batch_size],
            )
            for index in range(0, len(context.targets), context.batch_size)
        )

    def plan_progress(
        self,
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


def plan_embedding_index_batch_jobs(
    context: EmbeddingIndexBatchJobContext,
) -> tuple[EmbeddingIndexBatchJobRequest, ...]:
    """Plan queue-neutral batch embedding jobs after a file-index batch."""
    return EmbeddingIndexPlanner().plan_batch_jobs(context)


async def run_embedding_index(
    request: EmbeddingIndexJobRequest,
    *,
    vector_sync: EmbeddingVectorSync,
) -> EmbeddingIndexResult:
    """Run one embedding index request through a concrete vector sync backend."""
    await vector_sync.sync_entity_vectors(request.entity_id)
    return EmbeddingIndexResult(
        entity_id=request.entity_id,
        status=EmbeddingIndexStatus.processed,
        reason=f"entity embeddings indexed: {request.entity_id}",
    )


async def run_embedding_index_batch(
    request: EmbeddingIndexBatchJobRequest,
    *,
    vector_sync: EmbeddingBatchVectorSync,
    planner: EmbeddingIndexPlanner | None = None,
) -> EmbeddingIndexBatchResult:
    """Run one batch embedding request through a concrete vector sync backend."""
    if not request.entities:
        return EmbeddingIndexBatchResult.no_entities()

    index_plan = (planner or EmbeddingIndexPlanner()).plan(request.entities)
    batch_result = await vector_sync.sync_entity_vectors_batch(list(index_plan.entity_ids))
    return summarize_embedding_index_batch_result(index_plan, batch_result)


def summarize_embedding_index_batch_result(
    plan: EmbeddingIndexPlan,
    batch_result: EmbeddingIndexBatchSummary,
) -> EmbeddingIndexBatchResult:
    """Combine a deduped embedding plan with backend vector sync counts."""
    return EmbeddingIndexBatchResult(
        total_entities=plan.total_targets,
        unique_entities=plan.unique_entities,
        synced_entities=batch_result.entities_synced,
        skipped_entities=batch_result.entities_skipped,
        failed_entities=batch_result.entities_failed,
        deferred_entities=batch_result.entities_deferred,
        reason=f"entity embedding batch indexed: {plan.unique_entities} entities",
    )


async def run_vector_sync(
    entity_ids: Sequence[EntityId],
    *,
    vector_sync: VectorSyncExecutor,
    logger: Logger,
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
        # Attach project_id as structured context so the completion log stays
        # queryable; passing it as a format arg would silently drop it.
        logger.bind(project_id=project_id).info(completion_message)

    return progress_state
