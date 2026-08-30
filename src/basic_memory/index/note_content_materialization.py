"""Local note-content materialization adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from contextlib import nullcontext, suppress
from dataclasses import dataclass, replace
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db, file_utils
from basic_memory.index.schedulers import RelationResolutionScheduler
from basic_memory.indexing.index_file_runner import IndexFileExecutor
from basic_memory.indexing.note_file_delete_runner import (
    MoveVacateClearer,
    RepositoryMoveVacateClearer,
    run_note_file_delete,
)
from basic_memory.indexing.note_materialization_runner import (
    ContentStoreNoteMaterializationFileWriter,
    RepositoryNoteMaterializationPreflight,
    RepositoryNoteMaterializationPublisher,
    RepositoryNoteMaterializationStatusPublisher,
    run_note_materialization,
)
from basic_memory.indexing.project_index_maintenance import (
    ProjectIndexMovedEntitySearchRefresher,
)
from basic_memory.runtime.cleanup import (
    RuntimeNoteFileDeleteJobRequest,
    plan_note_file_delete_job_request,
)
from basic_memory.runtime.note_content import (
    NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR,
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteResponse,
    RuntimeNoteContentResponsePayload,
    RuntimeNoteMaterializationJobRequest,
    RuntimeNoteMaterializationResult,
    RuntimeNoteMaterializationStatus,
    RuntimePendingNoteMaterialization,
    plan_accepted_note_response,
    plan_note_materialization_job_request,
    read_runtime_file_checksum,
)
from basic_memory.runtime.note_materialization import RuntimeFileMetadataSource
from basic_memory.runtime.storage import RuntimeFileChecksum, RuntimeFilePath
from basic_memory.models import Entity
from basic_memory.repository import EntityRepository, NoteContentRepository
from basic_memory.repository.note_file_vacate_repository import (
    NoteFileVacateRepository,
    RecoverableVacate,
)
from basic_memory.read_cache import ReadCacheInvalidator, invalidate_cache
from basic_memory.schemas.response import ObservationResponse, RelationResponse
from basic_memory.services.file_service import FileService


# The (project_id, entity_id) identity that pins all of one note's queued
# materializations to a single worker.
type _NoteRoutingKey = tuple[int, int]


class _MaterializationWorkerPool:
    """Bounded in-process worker pool that drains queued note materializations.

    Mirrors the cloud's queue worker model locally: the accept enqueues a
    materialization and returns; a fixed number of workers pull from per-worker
    queues and run them. Bounding concurrency to `workers` is the point —
    fire-and-forget `create_task` let every deferred file write + index run at
    once, and at high write load they contended en masse for the single SQLite
    writer and the event loop, collapsing the tail (p99) and throughput
    (benchmarks/docs/write-load-benchmark.md). With N workers only N
    materializations are in flight; the rest wait in the queues and drain over
    time, so the accept path stays light AND the writer isn't thrashed.

    Jobs are routed to a worker by their note identity (project_id, entity_id),
    so all jobs for one note run on the same worker FIFO in submission order.
    Materializations for the same note must never run concurrently: the older
    job's file write changes the on-disk checksum, so the newer job's writer
    guard reads unexpected content and publishes a false
    external_change_detected on the LATEST accepted row — the note is never
    materialized and is falsely flagged as conflicted.
    """

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[Coroutine[Any, Any, object]]] = []
        self._workers: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def submit(
        self,
        work: Coroutine[Any, Any, object],
        *,
        workers: int,
        key: _NoteRoutingKey,
    ) -> None:
        self._ensure_workers(workers)
        self._queues[self._worker_index(key)].put_nowait(work)

    def _worker_index(self, key: _NoteRoutingKey) -> int:
        # hash() of an int tuple is stable within one process, which is all
        # routing needs: a job only has to serialize against other jobs queued
        # in the same process lifetime.
        return hash(key) % len(self._queues)

    def _ensure_workers(self, workers: int) -> None:
        # Trigger: first submit, or submit on a different event loop than the one
        # the workers were bound to (e.g. a fresh per-test loop).
        # Why: workers are long-lived tasks bound to one loop; reusing queues
        # whose workers live on a dead loop would hang. Outcome: (re)create one
        # queue + worker task pair per worker on the current running loop.
        # Orphaned workers on a closed loop are already dead, so dropping them
        # is safe.
        loop = asyncio.get_running_loop()
        if self._queues and self._loop is loop:
            return
        self._loop = loop
        self._queues = [asyncio.Queue() for _ in range(max(1, workers))]
        self._workers = [asyncio.create_task(self._run(queue)) for queue in self._queues]

    async def _run(self, queue: asyncio.Queue[Coroutine[Any, Any, object]]) -> None:
        while True:
            work = await queue.get()
            try:
                await work
            except Exception:  # pragma: no cover - defensive worker guard
                logger.exception("Local note materialization failed")
            finally:
                queue.task_done()

    async def join(self) -> None:
        """Block until every queued materialization has completed."""
        for queue in self._queues:
            await queue.join()

    async def aclose(self) -> None:
        """Cancel workers and reset the pool (clean test teardown / shutdown)."""
        workers = self._workers
        self._workers = []
        self._queues = []
        self._loop = None
        for worker in workers:
            worker.cancel()
        for worker in workers:
            with suppress(asyncio.CancelledError):
                await worker


_materialization_pool = _MaterializationWorkerPool()


async def drain_pending_materializations() -> None:
    """Block until queued local materializations finish writing + indexing.

    One-shot clients (``bm tool write-note``, importers) return right after the
    accept enqueues the markdown write/index; without this drain the event loop can
    close before the worker writes the source-of-truth file, silently losing the
    write even though the API already reported it accepted. Long-lived servers keep
    the loop alive and don't need it.
    """
    await _materialization_pool.join()


# --- Startup Recovery ---
# accept_write marks note_content "pending", then the materialization preflight
# flips it to "writing" before the file is written and the publisher records
# "synced". If the process dies anywhere between those points the row is stuck
# forever: the crash may land before the file write (nothing on disk) or after it
# but before publish (the correct accepted file is already on disk, row still
# "writing"). A transient write error (ENOSPC, permissions) publishes "failed"
# instead — equally terminal, since nothing else ever retries it. On the next
# startup we re-drive every stuck row. The write path short-circuits when the
# accepted content is already on disk, so the crash-after-write case publishes to
# "synced" instead of tripping the external-change guard. The db_version
# compare-and-set guard in the preflight and publisher makes recovery
# unconditionally safe: an older recovery attempt can never overwrite a newer
# accepted write or its file.

# Synthetic provenance stamped on recovered writes so operators can tell a
# crash-recovery materialization apart from a normal accept-path write in logs
# and object metadata.
RECOVERY_NOTE_CHANGE_SOURCE = "note-content-materialization-recovery"
RECOVERY_NOTE_ACTOR_NAME = "startup-recovery"


@dataclass(frozen=True, slots=True)
class MaterializationRecoverySummary:
    """State-publication summary for one startup recovery sweep."""

    attempted: int = 0
    written: int = 0


async def run_recovery_materialization(
    request: RuntimeNoteMaterializationJobRequest,
    *,
    session_maker: async_sessionmaker[AsyncSession],
    file_service: FileService,
) -> RuntimeNoteMaterializationResult:
    """Re-drive one stuck materialization through the standard guarded write path.

    Uses the same preflight/writer/publisher/status-publisher as an accept-path
    write, so the db_version and file-conflict guards apply unchanged. No cleanup
    paths: a recovery request carries no old-file move, so nothing is deleted.
    """
    storage = LocalNoteContentStorage(file_service)
    return await run_note_materialization(
        request,
        preflight=RepositoryNoteMaterializationPreflight(session_maker=session_maker),
        writer=ContentStoreNoteMaterializationFileWriter(storage),
        publisher=RepositoryNoteMaterializationPublisher(session_maker=session_maker),
        status_publisher=RepositoryNoteMaterializationStatusPublisher(session_maker=session_maker),
        cleanup_enqueuer=InlineNoteFileDeleteEnqueuer(
            storage,
            vacate_clearer=RepositoryMoveVacateClearer(session_maker=session_maker),
        ),
    )


async def recover_stuck_materializations(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    file_service: FileService,
    project_id: int,
) -> MaterializationRecoverySummary:
    """Re-drive every note materialization stuck in writing/pending/failed for a project.

    Meant to run once per project at startup, before serving. Non-fatal per row:
    a single row that raises is logged and skipped so one poisoned note cannot
    block startup recovery for the rest of the project. Every attempt can publish
    terminal status, so the summary distinguishes attempted rows from writes.
    """
    async with db.scoped_session(session_maker) as session:
        stuck_rows = await NoteContentRepository(project_id=project_id).find_stuck_materializations(
            session
        )

    if not stuck_rows:
        return MaterializationRecoverySummary()

    logger.info(
        "Recovering stuck note materializations",
        project_id=project_id,
        stuck_count=len(stuck_rows),
    )
    written = 0
    for row in stuck_rows:
        # Rebuild the queue request from the row's own accepted db_version/db_checksum
        # so the preflight guard matches the current accepted state; if a newer write
        # has since advanced the row, the guard trips and this attempt no-ops.
        request = RuntimeNoteMaterializationJobRequest(
            project_id=project_id,
            entity_id=row.entity_id,
            db_version=int(row.db_version),
            db_checksum=str(row.db_checksum),
            actor_name=RECOVERY_NOTE_ACTOR_NAME,
            source=RECOVERY_NOTE_CHANGE_SOURCE,
        )
        try:
            result = await run_recovery_materialization(
                request,
                session_maker=session_maker,
                file_service=file_service,
            )
        except Exception:
            # Trigger: one row's materialization raised (storage/DB error).
            # Why: recovery is best-effort startup cleanup; the version guard makes
            # a later retry safe, so one bad row must not abort the whole sweep.
            # Outcome: log and continue to the next stuck row.
            logger.exception(
                "Failed to recover stuck note materialization",
                project_id=project_id,
                entity_id=row.entity_id,
            )
            continue
        if result.status is RuntimeNoteMaterializationStatus.written:
            written += 1
    return MaterializationRecoverySummary(
        attempted=len(stuck_rows),
        written=written,
    )


async def _recover_deleted_destination_vacate(
    vacate: RecoverableVacate,
    *,
    project_id: int,
    storage: "LocalNoteContentStorage",
    vacate_clearer: MoveVacateClearer,
) -> None:
    """Resolve one guarded vacate after its destination entity was deleted."""
    actual_checksum = await read_runtime_file_checksum(storage, vacate.file_path)
    if actual_checksum == vacate.file_checksum:
        await storage.delete_file(vacate.file_path)
    await vacate_clearer.clear_move_vacate(
        project_id=project_id,
        file_path=vacate.file_path,
        file_checksum=vacate.file_checksum,
    )


@dataclass(frozen=True, slots=True)
class SessionMoveVacateClearer:
    """Retire a recovery marker in the transaction that locked its current state."""

    session: AsyncSession

    async def clear_move_vacate(
        self,
        *,
        project_id: int,
        file_path: RuntimeFilePath,
        file_checksum: RuntimeFileChecksum | None,
    ) -> None:
        await NoteFileVacateRepository(project_id).clear_vacate(
            self.session,
            file_path=str(file_path),
            file_checksum=file_checksum,
        )


async def recover_move_vacates(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    file_service: FileService,
    project_id: int,
) -> int:
    """Retry durable move-source cleanup work that an in-process enqueue lost.

    Only checksum-guarded markers with a synchronized or deleted destination are
    eligible. This keeps the old source intact while destination publication is
    still pending or failed, then converges it on a later startup after recovery
    reaches ``synced``.
    """
    async with db.scoped_session(session_maker) as session:
        vacates = await NoteFileVacateRepository(project_id).list_recoverable_vacates(session)

    if not vacates:
        return 0

    logger.info(
        "Recovering move-vacate source cleanup",
        project_id=project_id,
        vacate_count=len(vacates),
    )
    storage = LocalNoteContentStorage(file_service)
    vacate_repository = NoteFileVacateRepository(project_id)
    recovered = 0
    for candidate in vacates:
        try:
            async with db.scoped_session(session_maker) as session:
                vacate = await vacate_repository.lock_recoverable_vacate(
                    session,
                    file_path=candidate.file_path,
                    file_checksum=candidate.file_checksum,
                )
                if vacate is None:
                    continue

                vacate_clearer = SessionMoveVacateClearer(session)
                if vacate.entity_id is None:
                    # The destination entity no longer exists, so the queue-neutral
                    # cleanup request has no valid entity identity. The durable
                    # marker still carries the checksum needed for the same guarded
                    # storage delete and marker retirement.
                    await _recover_deleted_destination_vacate(
                        vacate,
                        project_id=project_id,
                        storage=storage,
                        vacate_clearer=vacate_clearer,
                    )
                else:
                    await InlineNoteFileDeleteEnqueuer(
                        storage,
                        vacate_clearer=vacate_clearer,
                    ).enqueue_note_file_delete(
                        RuntimeNoteFileDeleteJobRequest(
                            project_id=project_id,
                            entity_id=vacate.entity_id,
                            file_path=vacate.file_path,
                            file_checksum=vacate.file_checksum,
                            live_file_path=vacate.live_file_path,
                        )
                    )
        except Exception:  # pragma: no cover - defensive startup guard
            # A failed marker stays durable and can be retried on the next startup;
            # one unavailable path must not block cleanup for the rest of the project.
            logger.exception(
                "Failed to recover move-vacate source cleanup",
                project_id=project_id,
                file_path=candidate.file_path,
            )
            continue
        recovered += 1
    return recovered


def note_content_payload_file_path(
    payload: RuntimeNoteContentResponsePayload,
) -> RuntimeFilePath | None:
    """Return the materialized file path carried by an accepted-note payload."""
    if isinstance(payload, RuntimeAcceptedNoteResponse):
        return payload.file_path
    if isinstance(payload, Mapping):
        file_path = payload.get("file_path")
        if isinstance(file_path, str) and file_path:
            return file_path
    return None


def file_write_status_from_materialization_result(
    result: RuntimeNoteMaterializationResult,
) -> str:
    """Return the response write marker for a terminal local materialization result."""
    if result.status is RuntimeNoteMaterializationStatus.conflict:
        return "external_change_detected"
    return "failed"


def note_content_payload_with_materialization_result(
    payload: RuntimeNoteContentResponsePayload,
    result: RuntimeNoteMaterializationResult,
) -> RuntimeNoteContentResponsePayload:
    """Expose a failed local materialization result in the accepted-note response payload."""
    file_write_status = file_write_status_from_materialization_result(result)

    if isinstance(payload, RuntimeAcceptedNoteResponse):
        return replace(
            payload,
            file_write_status=file_write_status,
            file_checksum=result.file_checksum
            if result.file_checksum is not None
            else payload.file_checksum,
            last_materialization_error=result.reason,
        )

    updated_payload = dict(payload)
    updated_payload["file_write_status"] = file_write_status
    updated_payload["last_materialization_error"] = result.reason
    if result.file_checksum is not None:
        updated_payload["file_checksum"] = result.file_checksum
    if file_write_status == "external_change_detected":
        updated_payload["sync_error"] = NOTE_CONTENT_EXTERNAL_CHANGE_SYNC_ERROR
    return updated_payload


def indexed_observation_payloads(entity: Entity) -> tuple[dict[str, object], ...]:
    """Serialize loaded observation rows into the v2 response shape."""
    return tuple(
        ObservationResponse.model_validate(observation).model_dump(mode="json")
        for observation in entity.observations
    )


def indexed_relation_payloads(entity: Entity) -> tuple[dict[str, object], ...]:
    """Serialize loaded relation rows into the v2 response shape."""
    return tuple(
        RelationResponse.model_validate(relation).model_dump(mode="json")
        for relation in entity.relations
    )


async def load_indexed_note_content_response_payload(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
    entity_id: int,
    fallback_source: str,
) -> RuntimeAcceptedNoteResponse:
    """Reload the local indexed entity graph after inline materialization/indexing."""
    async with db.scoped_session(session_maker) as session:
        entity = await EntityRepository(project_id=project_id).get_by_id(
            session,
            entity_id,
            load_relations=True,
        )
        if entity is None:
            raise RuntimeError(f"Indexed entity {entity_id} was not found after materialization")

        note_content = await NoteContentRepository(project_id=project_id).get_by_entity_id(
            session,
            entity_id,
        )
        if note_content is None:
            raise RuntimeError(
                f"Indexed note_content for entity {entity_id} was not found after materialization"
            )

        return replace(
            plan_accepted_note_response(
                entity=entity,
                note_content=note_content,
                fallback_source=fallback_source,
            ),
            observations=indexed_observation_payloads(entity),
            relations=indexed_relation_payloads(entity),
        )


@dataclass(frozen=True, slots=True)
class LocalNoteContentStorage:
    """Adapt the local FileService to note-content runtime storage protocols."""

    file_service: FileService

    async def write_file(
        self,
        path: RuntimeFilePath,
        content: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> RuntimeFileChecksum:
        _ = metadata
        path_obj = self.file_service.base_path / path if isinstance(path, str) else path
        full_path = path_obj if path_obj.is_absolute() else self.file_service.base_path / path_obj

        # Accepted-note materialization persists an already-accepted DB snapshot.
        # Writing bytes keeps the materialized file checksum identical to the
        # note_content checksum on Windows, where text mode would translate LF to CRLF.
        await self.file_service.ensure_directory(full_path.parent)
        await file_utils.write_file_atomic_bytes(full_path, content.encode("utf-8"))
        return await self.file_service.compute_checksum(full_path)

    async def get_file_metadata(self, path: RuntimeFilePath) -> RuntimeFileMetadataSource:
        return await self.file_service.get_file_metadata(path)

    async def exists(self, path: RuntimeFilePath) -> bool:
        return await self.file_service.exists(path)

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum:
        try:
            return await self.file_service.compute_checksum(path)
        except file_utils.FileError as exc:
            if isinstance(exc.__context__, FileNotFoundError):
                # The general FileService contract reports I/O failures as FileError,
                # while runtime guards need concurrent deletion as an absent file.
                raise FileNotFoundError(path) from exc
            raise

    async def delete_file(self, path: RuntimeFilePath) -> None:
        await self.file_service.delete_file(path)

    async def delete_file_if_unchanged(
        self,
        path: RuntimeFilePath,
        *,
        expected_checksum: RuntimeFileChecksum,
    ) -> bool:
        # Re-verify the checksum immediately before deleting so a replacement written into the
        # freshness-read → delete gap is never removed (basic-memory-cloud#1618). Local has no
        # atomic precondition; the race is negligible on a filesystem.
        if not await self.file_service.exists(path):
            return False
        try:
            actual_checksum = await self.compute_checksum(path)
        except FileNotFoundError:
            # Disappearance after the final existence probe is another safe no-delete outcome.
            return False
        if actual_checksum != expected_checksum:
            return False
        await self.file_service.delete_file(path)
        return True


@dataclass(frozen=True, slots=True)
class InlineNoteFileDeleteEnqueuer:
    """Execute note-file cleanup immediately in the local runtime."""

    storage: LocalNoteContentStorage
    # Clears the move-vacate marker once a moved note's source object is actually deleted (#1601).
    vacate_clearer: MoveVacateClearer | None = None

    async def enqueue_note_file_delete(self, request: RuntimeNoteFileDeleteJobRequest) -> None:
        # Trigger: a move scheduled old-path cleanup whose old and new paths differ
        # only by case (or otherwise alias the same inode) on a case-insensitive
        # filesystem, so the old path now points at the just-written new file.
        # Why: the checksum guard cannot tell "old file still present" from "old path
        # aliases the new file" — both read the same bytes — so deleting the old path
        # would destroy the note's only copy (then scan reconciliation removes the row).
        # Outcome: skip the delete entirely; the paths are the same physical file.
        if (
            request.live_file_path is not None
            and self.storage.file_service.paths_share_storage_target(
                request.file_path, request.live_file_path
            )
        ):
            logger.info(
                "Skipping note-file cleanup that aliases the live file (case-only rename)",
                entity_id=request.entity_id,
                file_path=request.file_path,
                live_file_path=request.live_file_path,
            )
            # Trigger: the two spellings differ only by case.
            # Why: the atomic write replaced the file's bytes through the existing
            #      directory entry, so the entry still carries the old casing; the
            #      next scan would read that as a move back and undo the rename (#1281).
            # Outcome: rename the entry to the accepted casing so disk agrees with the
            #          row. On a case-sensitive filesystem the alias check above only
            #          passes for hard links, where a rename onto itself is a no-op.
            if request.file_path != request.live_file_path and (
                request.file_path.casefold() == request.live_file_path.casefold()
            ):
                await self.storage.file_service.move_file(request.file_path, request.live_file_path)
            # No separate source object exists: the old path aliases the live destination, so the
            # move is already physically complete and its suppression marker must not outlive it.
            if self.vacate_clearer is not None:
                await self.vacate_clearer.clear_move_vacate(
                    project_id=request.project_id,
                    file_path=request.file_path,
                    file_checksum=request.file_checksum,
                )
            return
        await run_note_file_delete(
            request, storage=self.storage, vacate_clearer=self.vacate_clearer
        )


@dataclass(frozen=True, slots=True)
class LocalNoteContentMaterializationProvider:
    """Run accepted-note materialization inline for the local runtime."""

    session_maker: async_sessionmaker[AsyncSession]
    file_service: FileService
    project_external_id: str
    read_cache: ReadCacheInvalidator | None
    file_indexer: IndexFileExecutor | None = None
    test_mode: bool = False
    materialization_workers: int = 4
    relation_resolution_scheduler: RelationResolutionScheduler | None = None
    # Re-indexes surviving notes whose relation search rows named a deleted
    # target (#1351). Optional so providers wired without it (cloud, focused
    # tests) keep working; cloud relies on its orphan sweeper instead.
    relation_cleanup_refresher: ProjectIndexMovedEntitySearchRefresher | None = None

    async def materialize_write_change(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]:
        """Materialize an accepted note write OFF the accept path.

        Cloud/local parity (DO NOT UNDO): cloud's materialize_write_change
        enqueues a queue job and returns immediately, letting Tigris object storage
        + indexing catch up asynchronously because S3 writes are slow. Locally we
        mirror that with an in-process background task. The accept has already
        persisted note_content (the write/read-through cache that serves reads);
        here we only schedule writing the markdown file (the source of truth) and
        indexing it. Writing + indexing the file is the heavy part of a write, so
        doing it inline reintroduces a ~3x write-load regression
        (benchmarks/docs/write-load-benchmark.md).

        PARITY INVARIANT: production must defer. Test mode runs inline ONLY so
        tests can assert file/search state synchronously — never make the
        production path synchronous to "simplify" this.
        """
        materialization = accepted.materialization
        if materialization is None:
            return accepted
        if self.test_mode:
            return await self._materialize_write_now(accepted)
        self._schedule_materialization(accepted, materialization)
        return accepted

    def _schedule_materialization(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
        materialization: RuntimePendingNoteMaterialization,
    ) -> None:
        # Hand the materialization to the bounded worker pool instead of spawning
        # an unbounded task per write — see _MaterializationWorkerPool for why.
        # Keyed on the note's identity so two quick writes to the same note run
        # sequentially on one worker instead of racing the writer guard into a
        # false external_change_detected on the newer accepted row.
        _materialization_pool.submit(
            self._materialize_write_now(accepted),
            workers=self.materialization_workers,
            key=(materialization.project_id, materialization.entity_id),
        )

    async def _materialize_write_now(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]:
        if accepted.materialization is None:  # pragma: no cover - guarded by caller
            return accepted
        # The accepted-write invalidation runs before deferred materialization.
        # The outer boundary retires reads filled during later indexing.
        final_invalidation_scope = (
            invalidate_cache(self.read_cache, self.project_external_id)
            if self.read_cache is not None
            else nullcontext()
        )
        async with final_invalidation_scope:
            storage = LocalNoteContentStorage(self.file_service)
            cleanup_enqueuer = InlineNoteFileDeleteEnqueuer(
                storage,
                vacate_clearer=RepositoryMoveVacateClearer(session_maker=self.session_maker),
            )
            # Materialization commits terminal status before indexing begins. Retire
            # pending/writing reads at that commit instead of holding them for the
            # potentially slow index operation.
            publication_invalidation_scope = (
                invalidate_cache(self.read_cache, self.project_external_id)
                if self.read_cache is not None
                else nullcontext()
            )
            async with publication_invalidation_scope:
                result = await run_note_materialization(
                    plan_note_materialization_job_request(accepted.materialization),
                    preflight=RepositoryNoteMaterializationPreflight(
                        session_maker=self.session_maker,
                    ),
                    writer=ContentStoreNoteMaterializationFileWriter(storage),
                    publisher=RepositoryNoteMaterializationPublisher(
                        session_maker=self.session_maker,
                    ),
                    status_publisher=RepositoryNoteMaterializationStatusPublisher(
                        session_maker=self.session_maker,
                    ),
                    cleanup_enqueuer=cleanup_enqueuer,
                )
            if result.status is not RuntimeNoteMaterializationStatus.written:
                return replace(
                    accepted,
                    payload=note_content_payload_with_materialization_result(
                        accepted.payload,
                        result,
                    ),
                )

            file_path = note_content_payload_file_path(accepted.payload)
            if file_path is not None and self.file_indexer is not None:
                await self.file_indexer.index_file(
                    file_path,
                    source="note-content-materialization",
                )
                # The deferred index has now inserted this note's entity/relation rows,
                # so back-resolve inbound forward references. The router schedules an
                # eager pass right after enqueue, but under load that pass can scan
                # before this index lands; scheduling here (coalesced/re-armed by the
                # resolution scheduler) guarantees a pass runs after indexing (#1002).
                if self.relation_resolution_scheduler is not None:
                    self.relation_resolution_scheduler.schedule_relation_resolution(
                        project_id=accepted.materialization.project_id,
                    )
                return replace(
                    accepted,
                    payload=await load_indexed_note_content_response_payload(
                        session_maker=self.session_maker,
                        project_id=accepted.materialization.project_id,
                        entity_id=accepted.materialization.entity_id,
                        fallback_source=accepted.materialization.source
                        or "note-content-materialization",
                    ),
                )
            return accepted

    async def materialize_delete_change(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]:
        """Delete materialized files immediately after local accepted-note deletes."""
        if accepted.file_delete is None:
            return accepted

        storage = LocalNoteContentStorage(self.file_service)
        await InlineNoteFileDeleteEnqueuer(
            storage,
            vacate_clearer=RepositoryMoveVacateClearer(session_maker=self.session_maker),
        ).enqueue_note_file_delete(plan_note_file_delete_job_request(accepted.file_delete))

        # Trigger: surviving notes still linked to the deleted target at commit time.
        # Why: their relation search rows carry the deleted entity's id and title, and
        #      nothing else re-indexes those sources on this path — unlike the watcher
        #      and directory delete paths, which run this same refresh (#1351).
        # Outcome: re-index each surviving source so its relation rows rebuild from
        #      the now-unresolved relation table state.
        if self.relation_cleanup_refresher is not None and accepted.relation_cleanup_entity_ids:
            if self.test_mode:
                # Inline only so tests can assert search state synchronously.
                await self._refresh_relation_cleanup(accepted)
            else:
                # Deleting a hub note can leave thousands of inbound links, and each
                # refresh rereads a markdown file and rewrites its search rows. The
                # DB delete has already committed, so blocking the 202 on that makes
                # latency scale with inbound degree. Hand it to the same bounded
                # worker pool that defers materialization, keyed on the deleted note.
                _materialization_pool.submit(
                    self._refresh_relation_cleanup(accepted),
                    workers=self.materialization_workers,
                    key=(accepted.file_delete.project_id, accepted.file_delete.entity_id),
                )
        return accepted

    async def _refresh_relation_cleanup(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> None:
        """Re-index the surviving relation sources; non-fatal derived-state repair."""
        if self.relation_cleanup_refresher is None:  # pragma: no cover - guarded by caller
            return
        try:
            await self.relation_cleanup_refresher.refresh_moved_entities(
                sorted(accepted.relation_cleanup_entity_ids)
            )
        except Exception:
            # Non-fatal: the delete already committed, and stale search rows are
            # derived state that converges on the source's next edit or a reindex.
            logger.exception(
                "Relation search cleanup failed after accepted note delete",
                entity_ids=sorted(accepted.relation_cleanup_entity_ids),
            )
