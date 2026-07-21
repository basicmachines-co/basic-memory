"""Shared semantic vector synchronization for search repositories."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import logfire
from loguru import logger
from sqlalchemy import text

from basic_memory import db
from basic_memory.indexing.embedding_index_planning import EntityVectorShardPlan
from basic_memory.repository.semantic_chunking import VectorChunkRecord
from basic_memory.schemas.search import SearchItemType

if TYPE_CHECKING:  # pragma: no cover - import cycle exists only for static analysis
    from sqlalchemy.ext.asyncio import AsyncSession

    from basic_memory.repository.search_repository_base import SearchRepositoryBase

OVERSIZED_ENTITY_VECTOR_SHARD_SIZE = 256
SQLITE_MAX_PREPARE_WINDOW = 8


@dataclass
class VectorSyncBatchResult:
    """Aggregate result for batched semantic vector sync runs."""

    entities_total: int
    entities_synced: int
    entities_failed: int
    entities_deferred: int = 0
    entities_skipped: int = 0
    failed_entity_ids: list[int] = field(default_factory=list)
    chunks_total: int = 0
    chunks_skipped: int = 0
    embedding_jobs_total: int = 0
    prepare_seconds_total: float = 0.0
    queue_wait_seconds_total: float = 0.0
    embed_seconds_total: float = 0.0
    write_seconds_total: float = 0.0


@dataclass
class PreparedEntityVectorSync:
    """Prepared chunk mutations and embedding jobs for one entity."""

    entity_id: int
    sync_start: float
    source_rows_count: int
    embedding_jobs: list[tuple[int, str]]
    chunks_total: int = 0
    chunks_skipped: int = 0
    entity_skipped: bool = False
    entity_complete: bool = True
    oversized_entity: bool = False
    pending_jobs_total: int = 0
    shard_index: int = 1
    shard_count: int = 1
    remaining_jobs_after_shard: int = 0
    prepare_seconds: float = 0.0
    queue_start: float | None = None


@dataclass
class PendingEmbeddingJob:
    """Pending embedding write entry with entity ownership metadata."""

    entity_id: int
    chunk_row_id: int
    chunk_text: str


@dataclass
class EntitySyncRuntime:
    """Per-entity runtime counters used while flushes are in flight."""

    sync_start: float
    queue_start: float
    source_rows_count: int
    embedding_jobs_count: int
    remaining_jobs: int
    chunks_total: int = 0
    chunks_skipped: int = 0
    entity_skipped: bool = False
    entity_complete: bool = True
    oversized_entity: bool = False
    pending_jobs_total: int = 0
    shard_index: int = 1
    shard_count: int = 1
    remaining_jobs_after_shard: int = 0
    prepare_seconds: float = 0.0
    embed_seconds: float = 0.0
    write_seconds: float = 0.0


@dataclass(frozen=True)
class VectorChunkState:
    """Existing vector chunk state fetched for one prepare window."""

    id: int
    chunk_key: str
    source_hash: str
    entity_fingerprint: str
    embedding_model: str
    has_embedding: bool


def log_vector_shard_plan(
    repository: SearchRepositoryBase,
    *,
    entity_id: int,
    shard_plan: EntityVectorShardPlan,
    shard_size: int = OVERSIZED_ENTITY_VECTOR_SHARD_SIZE,
) -> None:
    """Emit shard planning logs once the pending work is known."""
    if shard_plan.pending_jobs_total == 0:
        return

    if shard_plan.oversized_entity:
        logger.warning(
            "Vector sync oversized entity detected: project_id={project_id} "
            "entity_id={entity_id} pending_jobs_total={pending_jobs_total} "
            "shard_size={shard_size} shard_count={shard_count}",
            project_id=repository.project_id,
            entity_id=entity_id,
            pending_jobs_total=shard_plan.pending_jobs_total,
            shard_size=shard_size,
            shard_count=shard_plan.shard_count,
        )


async def sync_entity_vectors_internal(
    repository: SearchRepositoryBase,
    entity_ids: list[int],
    progress_callback: Callable[[int, int, int], Any] | None,
    continue_on_error: bool,
) -> VectorSyncBatchResult:
    """Run shared vector sync orchestration for one or many entities."""
    repository._assert_semantic_available()
    await repository._ensure_vector_tables()
    assert repository._embedding_provider is not None

    total_entities = len(entity_ids)
    result = VectorSyncBatchResult(
        entities_total=total_entities,
        entities_synced=0,
        entities_failed=0,
    )
    if total_entities == 0:
        return result
    batch_start = time.perf_counter()
    backend_name = type(repository).__name__.removesuffix("SearchRepository").lower()

    repository._log_vector_sync_runtime_settings(
        backend_name=backend_name, entities_total=total_entities
    )
    logger.info(
        "Vector batch sync start: project_id={project_id} entities_total={entities_total} "
        "sync_batch_size={sync_batch_size} prepare_window_size={prepare_window_size}",
        project_id=repository.project_id,
        entities_total=total_entities,
        sync_batch_size=repository._semantic_embedding_sync_batch_size,
        prepare_window_size=repository._vector_prepare_window_size(),
    )

    pending_jobs: list[PendingEmbeddingJob] = []
    entity_runtime: dict[int, EntitySyncRuntime] = {}
    failed_entity_ids: set[int] = set()
    deferred_entity_ids: set[int] = set()
    synced_entity_ids: set[int] = set()
    completed_entities = 0

    def emit_progress(entity_id: int) -> None:
        """Report terminal entity progress to callers such as the CLI.

        Trigger: an entity reaches a terminal state in this sync run.
        Why: operators need progress based on completed work, not the moment
        an entity merely enters prepare.
        Outcome: the progress bar advances when an entity is done for this
        run, whether it synced, skipped, deferred, or failed.
        """
        nonlocal completed_entities
        if progress_callback is None:
            return
        completed_entities += 1
        progress_callback(entity_id, completed_entities, total_entities)

    prepare_window_size = repository._vector_prepare_window_size()
    with logfire.span(
        "basic_memory.vector_sync.batch",
        project_id=repository.project_id,
        backend=backend_name,
        entities_total=total_entities,
        window_size=prepare_window_size,
    ) as batch_span:
        for window_start in range(0, total_entities, prepare_window_size):
            window_entity_ids = entity_ids[window_start : window_start + prepare_window_size]

            prepared_window = await repository._prepare_entity_vector_jobs_window(window_entity_ids)

            for entity_id, prepared in zip(window_entity_ids, prepared_window, strict=True):
                if isinstance(prepared, BaseException):
                    if not continue_on_error:
                        raise prepared
                    failed_entity_ids.add(entity_id)
                    logger.warning(
                        "Vector batch sync entity prepare failed: project_id={project_id} "
                        "entity_id={entity_id} error={error}",
                        project_id=repository.project_id,
                        entity_id=entity_id,
                        error=str(prepared),
                    )
                    emit_progress(entity_id)
                    continue

                embedding_jobs_count = len(prepared.embedding_jobs)
                result.chunks_total += prepared.chunks_total
                result.chunks_skipped += prepared.chunks_skipped
                if prepared.entity_skipped:
                    result.entities_skipped += 1
                result.embedding_jobs_total += embedding_jobs_count
                result.prepare_seconds_total += prepared.prepare_seconds

                if embedding_jobs_count == 0:
                    if prepared.entity_complete:
                        synced_entity_ids.add(entity_id)
                    else:
                        deferred_entity_ids.add(entity_id)
                    total_seconds = time.perf_counter() - prepared.sync_start
                    # Trigger: this entity never entered the shared embedding queue.
                    # Why: queue wait should track real flush contention only.
                    # Outcome: skip-only and delete-only entities report queue_wait ~= 0.
                    queue_wait_seconds = 0.0
                    repository._log_vector_sync_complete(
                        entity_id=entity_id,
                        total_seconds=total_seconds,
                        prepare_seconds=prepared.prepare_seconds,
                        queue_wait_seconds=queue_wait_seconds,
                        embed_seconds=0.0,
                        write_seconds=0.0,
                        source_rows_count=prepared.source_rows_count,
                        chunks_total=prepared.chunks_total,
                        chunks_skipped=prepared.chunks_skipped,
                        embedding_jobs_count=0,
                        entity_skipped=prepared.entity_skipped,
                        entity_complete=prepared.entity_complete,
                        oversized_entity=prepared.oversized_entity,
                        pending_jobs_total=prepared.pending_jobs_total,
                        shard_index=prepared.shard_index,
                        shard_count=prepared.shard_count,
                        remaining_jobs_after_shard=prepared.remaining_jobs_after_shard,
                    )
                    emit_progress(entity_id)
                    continue

                entity_runtime[entity_id] = EntitySyncRuntime(
                    sync_start=prepared.sync_start,
                    queue_start=(
                        prepared.queue_start
                        if prepared.queue_start is not None
                        else prepared.sync_start + prepared.prepare_seconds
                    ),
                    source_rows_count=prepared.source_rows_count,
                    embedding_jobs_count=embedding_jobs_count,
                    remaining_jobs=embedding_jobs_count,
                    chunks_total=prepared.chunks_total,
                    chunks_skipped=prepared.chunks_skipped,
                    entity_skipped=prepared.entity_skipped,
                    entity_complete=prepared.entity_complete,
                    oversized_entity=prepared.oversized_entity,
                    pending_jobs_total=prepared.pending_jobs_total,
                    shard_index=prepared.shard_index,
                    shard_count=prepared.shard_count,
                    remaining_jobs_after_shard=prepared.remaining_jobs_after_shard,
                    prepare_seconds=prepared.prepare_seconds,
                )
                pending_jobs.extend(
                    PendingEmbeddingJob(
                        entity_id=entity_id,
                        chunk_row_id=row_id,
                        chunk_text=chunk_text,
                    )
                    for row_id, chunk_text in prepared.embedding_jobs
                )

                while len(pending_jobs) >= repository._semantic_embedding_sync_batch_size:
                    flush_jobs = pending_jobs[: repository._semantic_embedding_sync_batch_size]
                    pending_jobs = pending_jobs[repository._semantic_embedding_sync_batch_size :]
                    try:
                        embed_seconds, write_seconds = await repository._flush_embedding_jobs(
                            flush_jobs=flush_jobs,
                            entity_runtime=entity_runtime,
                            synced_entity_ids=synced_entity_ids,
                        )
                        result.embed_seconds_total += embed_seconds
                        result.write_seconds_total += write_seconds
                        result.queue_wait_seconds_total += (
                            repository._finalize_completed_entity_syncs(
                                entity_runtime=entity_runtime,
                                synced_entity_ids=synced_entity_ids,
                                deferred_entity_ids=deferred_entity_ids,
                                progress_callback=emit_progress,
                            )
                        )
                    except Exception as exc:
                        if not continue_on_error:
                            raise
                        affected_entity_ids = sorted({job.entity_id for job in flush_jobs})
                        failed_entity_ids.update(affected_entity_ids)
                        synced_entity_ids.difference_update(affected_entity_ids)
                        deferred_entity_ids.difference_update(affected_entity_ids)
                        for failed_entity_id in affected_entity_ids:
                            entity_runtime.pop(failed_entity_id, None)
                        logger.warning(
                            "Vector batch sync flush failed: project_id={project_id} "
                            "affected_entities={affected_entities} "
                            "chunk_count={chunk_count} error={error}",
                            project_id=repository.project_id,
                            affected_entities=affected_entity_ids,
                            chunk_count=len(flush_jobs),
                            error=str(exc),
                        )
                        for failed_entity_id in affected_entity_ids:
                            emit_progress(failed_entity_id)

        if pending_jobs:
            flush_jobs = list(pending_jobs)
            pending_jobs = []
            try:
                embed_seconds, write_seconds = await repository._flush_embedding_jobs(
                    flush_jobs=flush_jobs,
                    entity_runtime=entity_runtime,
                    synced_entity_ids=synced_entity_ids,
                )
                result.embed_seconds_total += embed_seconds
                result.write_seconds_total += write_seconds
                result.queue_wait_seconds_total += repository._finalize_completed_entity_syncs(
                    entity_runtime=entity_runtime,
                    synced_entity_ids=synced_entity_ids,
                    deferred_entity_ids=deferred_entity_ids,
                    progress_callback=emit_progress,
                )
            except Exception as exc:
                if not continue_on_error:
                    raise
                affected_entity_ids = sorted({job.entity_id for job in flush_jobs})
                failed_entity_ids.update(affected_entity_ids)
                synced_entity_ids.difference_update(affected_entity_ids)
                deferred_entity_ids.difference_update(affected_entity_ids)
                for failed_entity_id in affected_entity_ids:
                    entity_runtime.pop(failed_entity_id, None)
                logger.warning(
                    "Vector batch sync final flush failed: project_id={project_id} "
                    "affected_entities={affected_entities} chunk_count={chunk_count} "
                    "error={error}",
                    project_id=repository.project_id,
                    affected_entities=affected_entity_ids,
                    chunk_count=len(flush_jobs),
                    error=str(exc),
                )
                for failed_entity_id in affected_entity_ids:
                    emit_progress(failed_entity_id)

        # Trigger: this should never happen after all flushes succeed.
        # Why: remaining jobs mean runtime tracking drifted from queued jobs.
        # Outcome: fail-safe marks these entities as failed to avoid false positives.
        if entity_runtime:
            orphan_runtime_entities = sorted(entity_runtime.keys())
            failed_entity_ids.update(orphan_runtime_entities)
            synced_entity_ids.difference_update(orphan_runtime_entities)
            deferred_entity_ids.difference_update(orphan_runtime_entities)
            logger.warning(
                "Vector batch sync left unfinished entities after flushes: "
                "project_id={project_id} unfinished_entities={unfinished_entities}",
                project_id=repository.project_id,
                unfinished_entities=orphan_runtime_entities,
            )
            for failed_entity_id in orphan_runtime_entities:
                emit_progress(failed_entity_id)

        synced_entity_ids.difference_update(failed_entity_ids)
        deferred_entity_ids.difference_update(failed_entity_ids)
        deferred_entity_ids.difference_update(synced_entity_ids)
        result.failed_entity_ids = sorted(failed_entity_ids)
        result.entities_failed = len(result.failed_entity_ids)
        result.entities_deferred = len(deferred_entity_ids)
        result.entities_synced = len(synced_entity_ids)

        logger.info(
            "Vector batch sync complete: project_id={project_id} entities_total={entities_total} "
            "entities_synced={entities_synced} entities_failed={entities_failed} "
            "entities_deferred={entities_deferred} "
            "entities_skipped={entities_skipped} chunks_total={chunks_total} "
            "chunks_skipped={chunks_skipped} embedding_jobs_total={embedding_jobs_total} "
            "prepare_seconds_total={prepare_seconds_total:.3f} "
            "queue_wait_seconds_total={queue_wait_seconds_total:.3f} "
            "embed_seconds_total={embed_seconds_total:.3f} "
            "write_seconds_total={write_seconds_total:.3f}",
            project_id=repository.project_id,
            entities_total=result.entities_total,
            entities_synced=result.entities_synced,
            entities_failed=result.entities_failed,
            entities_deferred=result.entities_deferred,
            entities_skipped=result.entities_skipped,
            chunks_total=result.chunks_total,
            chunks_skipped=result.chunks_skipped,
            embedding_jobs_total=result.embedding_jobs_total,
            prepare_seconds_total=result.prepare_seconds_total,
            queue_wait_seconds_total=result.queue_wait_seconds_total,
            embed_seconds_total=result.embed_seconds_total,
            write_seconds_total=result.write_seconds_total,
        )
        batch_total_seconds = time.perf_counter() - batch_start
        batch_attrs = {
            "backend": backend_name,
            "skip_only_batch": result.embedding_jobs_total == 0,
        }
        logfire.metric_histogram("vector_sync_batch_total_seconds", unit="s").record(
            batch_total_seconds, attributes=batch_attrs
        )
        logfire.metric_histogram("vector_sync_prepare_seconds", unit="s").record(
            result.prepare_seconds_total, attributes=batch_attrs
        )
        logfire.metric_histogram("vector_sync_queue_wait_seconds", unit="s").record(
            result.queue_wait_seconds_total, attributes=batch_attrs
        )
        logfire.metric_histogram("vector_sync_embed_seconds", unit="s").record(
            result.embed_seconds_total, attributes=batch_attrs
        )
        logfire.metric_histogram("vector_sync_write_seconds", unit="s").record(
            result.write_seconds_total, attributes=batch_attrs
        )
        logfire.metric_counter("vector_sync_entities_total").add(
            result.entities_total, attributes=batch_attrs
        )
        logfire.metric_counter("vector_sync_entities_skipped").add(
            result.entities_skipped, attributes=batch_attrs
        )
        logfire.metric_counter("vector_sync_entities_deferred").add(
            result.entities_deferred, attributes=batch_attrs
        )
        logfire.metric_counter("vector_sync_embedding_jobs_total").add(
            result.embedding_jobs_total, attributes=batch_attrs
        )
        logfire.metric_counter("vector_sync_chunks_total").add(
            result.chunks_total, attributes=batch_attrs
        )
        logfire.metric_counter("vector_sync_chunks_skipped").add(
            result.chunks_skipped, attributes=batch_attrs
        )
        if batch_span is not None:
            batch_span.set_attributes(
                {
                    "backend": backend_name,
                    "entities_synced": result.entities_synced,
                    "entities_failed": result.entities_failed,
                    "entities_deferred": result.entities_deferred,
                    "entities_skipped": result.entities_skipped,
                    "embedding_jobs_total": result.embedding_jobs_total,
                    "chunks_total": result.chunks_total,
                    "chunks_skipped": result.chunks_skipped,
                    "batch_total_seconds": batch_total_seconds,
                }
            )

    return result


def vector_prepare_window_size(
    repository: SearchRepositoryBase,
    *,
    max_window_size: int = SQLITE_MAX_PREPARE_WINDOW,
) -> int:
    """Return the number of entities to prepare in one orchestration window."""
    # Trigger: the shared window path batches reads and then fans back out
    # into per-entity prepare work.
    # Why: SQLite benefits from concurrency too, but letting the default path
    # explode to the full embed batch size creates unnecessary write contention.
    # Outcome: local backends get a small bounded window, while Postgres keeps
    # its explicit higher concurrency override.
    return max(
        1,
        min(repository._semantic_embedding_sync_batch_size, max_window_size),
    )


def prepare_window_entity_params(
    repository: SearchRepositoryBase,
    entity_ids: list[int],
) -> tuple[str, dict[str, object]]:
    """Build deterministic bind params for one prepare window."""
    placeholders = ", ".join(f":entity_id_{index}" for index in range(len(entity_ids)))
    params: dict[str, object] = {"project_id": repository.project_id}
    params.update({f"entity_id_{index}": entity_id for index, entity_id in enumerate(entity_ids)})
    return placeholders, params


async def fetch_prepare_window_source_rows(
    repository: SearchRepositoryBase,
    session: AsyncSession,
    entity_ids: list[int],
) -> dict[int, list[Any]]:
    """Fetch all search_index rows needed for one prepare window."""
    grouped_rows: dict[int, list[Any]] = {entity_id: [] for entity_id in entity_ids}
    if not entity_ids:
        return grouped_rows

    placeholders, params = repository._prepare_window_entity_params(entity_ids)
    params.update(
        {
            "entity_type": SearchItemType.ENTITY.value,
            "observation_type": SearchItemType.OBSERVATION.value,
            "relation_type_type": SearchItemType.RELATION.value,
        }
    )
    result = await session.execute(
        text(
            "SELECT entity_id, id, type, title, permalink, content_stems, content_snippet, "
            "category, relation_type "
            "FROM search_index "
            f"WHERE project_id = :project_id AND entity_id IN ({placeholders}) "
            "ORDER BY entity_id ASC, "
            "CASE type "
            "WHEN :entity_type THEN 0 "
            "WHEN :observation_type THEN 1 "
            "WHEN :relation_type_type THEN 2 "
            "ELSE 3 END, id ASC"
        ),
        params,
    )
    for row in result.fetchall():
        grouped_rows.setdefault(int(row.entity_id), []).append(row)
    return grouped_rows


def prepare_window_existing_rows_sql(placeholders: str) -> str:
    """Build SQL for existing chunk and embedding rows in one prepare window."""
    return (
        "SELECT c.entity_id, c.id, c.chunk_key, c.source_hash, c.entity_fingerprint, "
        "c.embedding_model, (e.chunk_id IS NOT NULL) AS has_embedding "
        "FROM search_vector_chunks c "
        "LEFT JOIN search_vector_embeddings e ON e.chunk_id = c.id "
        f"WHERE c.project_id = :project_id AND c.entity_id IN ({placeholders}) "
        "ORDER BY c.entity_id ASC, c.chunk_key ASC"
    )


async def fetch_prepare_window_existing_rows(
    repository: SearchRepositoryBase,
    session: AsyncSession,
    entity_ids: list[int],
) -> dict[int, list[VectorChunkState]]:
    """Fetch all persisted chunk state needed for one prepare window."""
    grouped_rows: dict[int, list[VectorChunkState]] = {entity_id: [] for entity_id in entity_ids}
    if not entity_ids:
        return grouped_rows

    placeholders, params = repository._prepare_window_entity_params(entity_ids)
    result = await session.execute(
        text(repository._prepare_window_existing_rows_sql(placeholders)), params
    )
    for row in result.mappings().all():
        grouped_rows.setdefault(int(row["entity_id"]), []).append(
            VectorChunkState(
                id=int(row["id"]),
                chunk_key=str(row["chunk_key"]),
                source_hash=str(row["source_hash"]),
                entity_fingerprint=str(row["entity_fingerprint"]),
                embedding_model=str(row["embedding_model"]),
                has_embedding=bool(row["has_embedding"]),
            )
        )
    return grouped_rows


async def prepare_entity_vector_jobs_window(
    repository: SearchRepositoryBase,
    entity_ids: list[int],
) -> list[PreparedEntityVectorSync | BaseException]:
    """Prepare one window of entity vector jobs with shared read-side batching."""
    if not entity_ids:
        return []

    try:
        async with db.scoped_session(repository.session_maker) as session:
            await repository._prepare_vector_session(session)
            source_rows_by_entity = await repository._fetch_prepare_window_source_rows(
                session, entity_ids
            )
            existing_rows_by_entity = await repository._fetch_prepare_window_existing_rows(
                session, entity_ids
            )
    except Exception as exc:
        # Trigger: the shared read pass failed before we had entity-level diffs.
        # Why: once the window-level read session breaks, we cannot safely
        # distinguish one entity from another inside that window.
        # Outcome: every entity in the window gets the same failure object.
        return [exc for _ in entity_ids]

    # Trigger: prepare now does one shared read pass per window instead of
    # paying the same select/join round-trips per entity.
    # Why: both SQLite and Postgres were still burning wall clock in read-side
    # fingerprint/orphan checks even when every entity ended up skipped.
    # Outcome: batch the reads once, close that shared read session, and
    # then fan back out over entities while preserving input order.
    prepared_window = await asyncio.gather(
        *(
            repository._prepare_entity_vector_jobs_prefetched(
                entity_id=entity_id,
                source_rows=source_rows_by_entity.get(entity_id, []),
                existing_rows=existing_rows_by_entity.get(entity_id, []),
            )
            for entity_id in entity_ids
        ),
        return_exceptions=True,
    )
    return list(prepared_window)


async def prepare_entity_vector_jobs(
    repository: SearchRepositoryBase,
    entity_id: int,
) -> PreparedEntityVectorSync:
    """Prepare chunk mutations and embedding jobs for one entity."""
    prepared_window = await repository._prepare_entity_vector_jobs_window([entity_id])
    prepared = prepared_window[0]
    if isinstance(prepared, BaseException):
        raise prepared
    return prepared


async def prepare_entity_vector_jobs_prefetched(
    repository: SearchRepositoryBase,
    *,
    entity_id: int,
    source_rows: list[Any],
    existing_rows: list[VectorChunkState],
) -> PreparedEntityVectorSync:
    """Prepare one entity using prefetched window rows."""
    sync_start = time.perf_counter()
    prepare_start = sync_start
    source_rows_count = len(source_rows)

    async def delete_entity_chunks_and_finish() -> PreparedEntityVectorSync:
        """Delete derived rows and return the empty prepare result."""
        async with repository._prepare_entity_write_scope():
            async with db.scoped_session(repository.session_maker) as session:
                await repository._prepare_vector_session(session)
                await repository._delete_entity_chunks(session, entity_id)
                await session.commit()
        prepare_seconds = time.perf_counter() - prepare_start
        return PreparedEntityVectorSync(
            entity_id=entity_id,
            sync_start=sync_start,
            source_rows_count=source_rows_count,
            embedding_jobs=[],
            prepare_seconds=prepare_seconds,
        )

    if not source_rows:
        return await delete_entity_chunks_and_finish()

    chunk_records = repository._build_chunk_records(source_rows)
    built_chunk_records_count = len(chunk_records)
    if not chunk_records:
        return await delete_entity_chunks_and_finish()

    current_entity_fingerprint = repository._build_entity_fingerprint(chunk_records)
    current_embedding_model = repository._embedding_model_key()
    existing_by_key = {row.chunk_key: row for row in existing_rows}
    incoming_chunk_keys = {record["chunk_key"] for record in chunk_records}
    stale_ids = [
        row.id for chunk_key, row in existing_by_key.items() if chunk_key not in incoming_chunk_keys
    ]
    orphan_ids = {row.id for row in existing_rows if not row.has_embedding}

    # Trigger: all persisted chunk metadata already matches this entity's
    # current fingerprint/model and every chunk still has an embedding.
    # Why: unchanged entities should stop in prepare instead of paying write
    # or queue accounting they never actually used.
    # Outcome: skip-only entities return immediately with zero embedding jobs.
    skip_unchanged_entity = (
        len(existing_rows) == built_chunk_records_count
        and not stale_ids
        and not orphan_ids
        and bool(existing_rows)
        and all(
            row.entity_fingerprint == current_entity_fingerprint
            and row.embedding_model == current_embedding_model
            for row in existing_rows
        )
    )
    if skip_unchanged_entity:
        prepare_seconds = time.perf_counter() - prepare_start
        return PreparedEntityVectorSync(
            entity_id=entity_id,
            sync_start=sync_start,
            source_rows_count=source_rows_count,
            embedding_jobs=[],
            chunks_total=built_chunk_records_count,
            chunks_skipped=built_chunk_records_count,
            entity_skipped=True,
            prepare_seconds=prepare_seconds,
        )

    timestamp_expr = repository._timestamp_now_expr()
    metadata_update_ids: list[int] = []
    pending_records: list[VectorChunkRecord] = []
    skipped_chunks_count = 0
    for record in chunk_records:
        current = existing_by_key.get(record["chunk_key"])
        if current is None:
            pending_records.append(record)
            continue

        same_source_hash = current.source_hash == record["source_hash"]
        same_entity_fingerprint = current.entity_fingerprint == current_entity_fingerprint
        same_embedding_model = current.embedding_model == current_embedding_model

        if same_source_hash and current.id not in orphan_ids and same_embedding_model:
            if not same_entity_fingerprint:
                metadata_update_ids.append(current.id)
            skipped_chunks_count += 1
            continue

        pending_records.append(record)

    shard_plan = repository._plan_entity_vector_shard(pending_records)
    repository._log_vector_shard_plan(entity_id=entity_id, shard_plan=shard_plan)

    # Trigger: oversized entities can still produce many changed chunks even
    # after the read side is batched.
    # Why: retain the existing shard cap so one entity cannot monopolize a sync run.
    # Outcome: batching removes read overhead without changing deferred semantics.
    scheduled_records = [
        record
        for record in sorted(pending_records, key=lambda record: record["chunk_key"])
        if record["chunk_key"] in shard_plan.scheduled_chunk_keys
    ]

    embedding_jobs: list[tuple[int, str]] = []
    if stale_ids or metadata_update_ids or scheduled_records:
        # Trigger: prepare needs to mutate chunk rows for this entity.
        # Why: Postgres can keep these write-side steps concurrent, while
        # SQLite should funnel them through one writer even after shared reads.
        # Outcome: backends share the batched read path without forcing
        # SQLite into unnecessary concurrent write transactions.
        async with repository._prepare_entity_write_scope():
            async with db.scoped_session(repository.session_maker) as session:
                await repository._prepare_vector_session(session)
                if stale_ids:
                    await repository._delete_stale_chunks(session, stale_ids, entity_id)
                for row_id in metadata_update_ids:
                    await session.execute(
                        text(
                            "UPDATE search_vector_chunks "
                            "SET entity_fingerprint = :entity_fingerprint, "
                            "embedding_model = :embedding_model, "
                            f"updated_at = {timestamp_expr} "
                            "WHERE id = :id"
                        ),
                        {
                            "id": row_id,
                            "entity_fingerprint": current_entity_fingerprint,
                            "embedding_model": current_embedding_model,
                        },
                    )
                if scheduled_records:
                    embedding_jobs = await repository._upsert_scheduled_chunk_records(
                        session,
                        entity_id=entity_id,
                        scheduled_records=scheduled_records,
                        existing_by_key=existing_by_key,
                        entity_fingerprint=current_entity_fingerprint,
                        embedding_model=current_embedding_model,
                    )
                await session.commit()

    prepare_seconds = time.perf_counter() - prepare_start
    return PreparedEntityVectorSync(
        entity_id=entity_id,
        sync_start=sync_start,
        source_rows_count=source_rows_count,
        embedding_jobs=embedding_jobs,
        chunks_total=built_chunk_records_count,
        chunks_skipped=skipped_chunks_count,
        entity_complete=shard_plan.entity_complete,
        oversized_entity=shard_plan.oversized_entity,
        pending_jobs_total=shard_plan.pending_jobs_total,
        shard_index=shard_plan.shard_index,
        shard_count=shard_plan.shard_count,
        remaining_jobs_after_shard=shard_plan.remaining_jobs_after_shard,
        prepare_seconds=prepare_seconds,
        queue_start=time.perf_counter(),
    )


async def upsert_scheduled_chunk_records(
    repository: SearchRepositoryBase,
    session: AsyncSession,
    *,
    entity_id: int,
    scheduled_records: list[VectorChunkRecord],
    existing_by_key: dict[str, VectorChunkState],
    entity_fingerprint: str,
    embedding_model: str,
) -> list[tuple[int, str]]:
    """Upsert scheduled chunk rows and return embedding jobs."""
    timestamp_expr = repository._timestamp_now_expr()
    embedding_jobs: list[tuple[int, str]] = []
    for record in scheduled_records:
        current = existing_by_key.get(record["chunk_key"])
        if current:
            if (
                current.source_hash != record["source_hash"]
                or current.entity_fingerprint != entity_fingerprint
                or current.embedding_model != embedding_model
            ):
                await session.execute(
                    text(
                        "UPDATE search_vector_chunks "
                        "SET chunk_text = :chunk_text, source_hash = :source_hash, "
                        "entity_fingerprint = :entity_fingerprint, "
                        "embedding_model = :embedding_model, "
                        f"updated_at = {timestamp_expr} "
                        "WHERE id = :id"
                    ),
                    {
                        "id": current.id,
                        "chunk_text": record["chunk_text"],
                        "source_hash": record["source_hash"],
                        "entity_fingerprint": entity_fingerprint,
                        "embedding_model": embedding_model,
                    },
                )
            embedding_jobs.append((current.id, record["chunk_text"]))
            continue

        inserted = await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "entity_id, project_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, updated_at"
                ") VALUES ("
                ":entity_id, :project_id, :chunk_key, :chunk_text, :source_hash, "
                ":entity_fingerprint, :embedding_model, "
                f"{timestamp_expr}"
                ") RETURNING id"
            ),
            {
                "entity_id": entity_id,
                "project_id": repository.project_id,
                "chunk_key": record["chunk_key"],
                "chunk_text": record["chunk_text"],
                "source_hash": record["source_hash"],
                "entity_fingerprint": entity_fingerprint,
                "embedding_model": embedding_model,
            },
        )
        embedding_jobs.append((int(inserted.scalar_one()), record["chunk_text"]))
    return embedding_jobs


async def flush_embedding_jobs(
    repository: SearchRepositoryBase,
    flush_jobs: list[PendingEmbeddingJob],
    entity_runtime: dict[int, EntitySyncRuntime],
    synced_entity_ids: set[int],
) -> tuple[float, float]:
    """Embed and persist one queued flush chunk."""
    if not flush_jobs:
        return 0.0, 0.0
    assert repository._embedding_provider is not None

    embed_start = time.perf_counter()
    texts = [job.chunk_text for job in flush_jobs]
    embeddings = await repository._embedding_provider.embed_documents(texts)
    embed_seconds = time.perf_counter() - embed_start
    if len(embeddings) != len(flush_jobs):
        raise RuntimeError("Embedding provider returned an unexpected number of vectors.")

    write_start = time.perf_counter()
    async with db.scoped_session(repository.session_maker) as session:
        await repository._prepare_vector_session(session)
        write_jobs = [(job.chunk_row_id, job.chunk_text) for job in flush_jobs]
        await repository._write_embeddings(session, write_jobs, embeddings)
        await session.commit()
    write_seconds = time.perf_counter() - write_start

    flush_size = len(flush_jobs)
    entity_job_counts: dict[int, int] = {}
    for job in flush_jobs:
        entity_job_counts[job.entity_id] = entity_job_counts.get(job.entity_id, 0) + 1

    for entity_id, entity_job_count in entity_job_counts.items():
        runtime = entity_runtime.get(entity_id)
        if runtime is None:
            continue
        runtime.remaining_jobs -= entity_job_count

        # Attribute flush wall-clock to entities in proportion to rows written.
        flush_share = entity_job_count / flush_size
        runtime.embed_seconds += embed_seconds * flush_share
        runtime.write_seconds += write_seconds * flush_share

        if runtime.remaining_jobs <= 0 and runtime.entity_complete:
            synced_entity_ids.add(entity_id)

    return embed_seconds, write_seconds


def finalize_completed_entity_syncs(
    repository: SearchRepositoryBase,
    *,
    entity_runtime: dict[int, EntitySyncRuntime],
    synced_entity_ids: set[int],
    deferred_entity_ids: set[int],
    progress_callback: Callable[[int], None] | None = None,
) -> float:
    """Finalize completed entities and return cumulative queue wait seconds."""
    queue_wait_seconds_total = 0.0
    for entity_id, runtime in list(entity_runtime.items()):
        if runtime.remaining_jobs > 0:
            continue

        if runtime.entity_complete:
            synced_entity_ids.add(entity_id)
        else:
            deferred_entity_ids.add(entity_id)
        completed_at = time.perf_counter()
        total_seconds = completed_at - runtime.sync_start
        # Trigger: queue wait should represent time spent behind shared flush
        # work after prepare finished.
        # Why: skip-only entities never entered that queue, and mixed batches
        # should only charge queue time to entities that actually waited.
        # Outcome: skip-only batches stay near zero while real contention remains visible.
        queue_wait_seconds = max(
            0.0,
            completed_at - runtime.queue_start - runtime.embed_seconds - runtime.write_seconds,
        )
        queue_wait_seconds_total += queue_wait_seconds
        repository._log_vector_sync_complete(
            entity_id=entity_id,
            total_seconds=total_seconds,
            prepare_seconds=runtime.prepare_seconds,
            queue_wait_seconds=queue_wait_seconds,
            embed_seconds=runtime.embed_seconds,
            write_seconds=runtime.write_seconds,
            source_rows_count=runtime.source_rows_count,
            chunks_total=runtime.chunks_total,
            chunks_skipped=runtime.chunks_skipped,
            embedding_jobs_count=runtime.embedding_jobs_count,
            entity_skipped=runtime.entity_skipped,
            entity_complete=runtime.entity_complete,
            oversized_entity=runtime.oversized_entity,
            pending_jobs_total=runtime.pending_jobs_total,
            shard_index=runtime.shard_index,
            shard_count=runtime.shard_count,
            remaining_jobs_after_shard=runtime.remaining_jobs_after_shard,
        )
        entity_runtime.pop(entity_id, None)
        if progress_callback is not None:
            progress_callback(entity_id)

    return queue_wait_seconds_total


def log_vector_sync_runtime_settings(
    repository: SearchRepositoryBase,
    *,
    backend_name: str,
    entities_total: int,
) -> None:
    """Log the resolved embedding runtime knobs before the first prepare window."""
    assert repository._embedding_provider is not None

    provider = repository._embedding_provider
    runtime_attrs = provider.runtime_log_attrs() if hasattr(provider, "runtime_log_attrs") else {}
    if runtime_attrs:
        logger.info(
            "Vector batch runtime settings: project_id={project_id} backend={backend} "
            "entities_total={entities_total} provider={provider} model_name={model_name} "
            "dimensions={dimensions} sync_batch_size={sync_batch_size} "
            "{runtime_attrs}",
            project_id=repository.project_id,
            backend=backend_name,
            entities_total=entities_total,
            provider=type(provider).__name__,
            model_name=provider.model_name,
            dimensions=provider.dimensions,
            sync_batch_size=repository._semantic_embedding_sync_batch_size,
            runtime_attrs=" ".join(f"{key}={value}" for key, value in runtime_attrs.items()),
            **runtime_attrs,
        )
        return

    logger.info(
        "Vector batch runtime settings: project_id={project_id} backend={backend} "
        "entities_total={entities_total} provider={provider} sync_batch_size={sync_batch_size}",
        project_id=repository.project_id,
        backend=backend_name,
        entities_total=entities_total,
        provider=type(provider).__name__,
        sync_batch_size=repository._semantic_embedding_sync_batch_size,
    )


def log_vector_sync_complete(
    repository: SearchRepositoryBase,
    *,
    entity_id: int,
    total_seconds: float,
    prepare_seconds: float,
    queue_wait_seconds: float,
    embed_seconds: float,
    write_seconds: float,
    source_rows_count: int,
    chunks_total: int,
    chunks_skipped: int,
    embedding_jobs_count: int,
    entity_skipped: bool,
    entity_complete: bool,
    oversized_entity: bool,
    pending_jobs_total: int,
    shard_index: int,
    shard_count: int,
    remaining_jobs_after_shard: int,
) -> None:
    """Log completion and slow-entity warnings with a consistent format."""
    if total_seconds > 10:
        logger.warning(
            "Vector sync slow entity: project_id={project_id} entity_id={entity_id} "
            "total_seconds={total_seconds:.3f} prepare_seconds={prepare_seconds:.3f} "
            "queue_wait_seconds={queue_wait_seconds:.3f} embed_seconds={embed_seconds:.3f} "
            "write_seconds={write_seconds:.3f} source_rows_count={source_rows_count} "
            "chunks_total={chunks_total} chunks_skipped={chunks_skipped} "
            "embedding_jobs_count={embedding_jobs_count} entity_skipped={entity_skipped} "
            "entity_complete={entity_complete} oversized_entity={oversized_entity} "
            "pending_jobs_total={pending_jobs_total} shard_index={shard_index} "
            "shard_count={shard_count} "
            "remaining_jobs_after_shard={remaining_jobs_after_shard}",
            project_id=repository.project_id,
            entity_id=entity_id,
            total_seconds=total_seconds,
            prepare_seconds=prepare_seconds,
            queue_wait_seconds=queue_wait_seconds,
            embed_seconds=embed_seconds,
            write_seconds=write_seconds,
            source_rows_count=source_rows_count,
            chunks_total=chunks_total,
            chunks_skipped=chunks_skipped,
            embedding_jobs_count=embedding_jobs_count,
            entity_skipped=entity_skipped,
            entity_complete=entity_complete,
            oversized_entity=oversized_entity,
            pending_jobs_total=pending_jobs_total,
            shard_index=shard_index,
            shard_count=shard_count,
            remaining_jobs_after_shard=remaining_jobs_after_shard,
        )
