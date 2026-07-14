"""Local note-content materialization adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Any, Protocol

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db, file_utils
from basic_memory.indexing.index_file_runner import IndexFileExecutor
from basic_memory.indexing.note_file_delete_runner import run_note_file_delete
from basic_memory.indexing.note_materialization_runner import (
    ContentStoreNoteMaterializationFileWriter,
    RepositoryNoteMaterializationPreflight,
    RepositoryNoteMaterializationPublisher,
    RepositoryNoteMaterializationStatusPublisher,
    run_note_materialization,
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
    plan_accepted_note_response,
    plan_note_materialization_job_request,
)
from basic_memory.runtime.note_materialization import RuntimeFileMetadataSource
from basic_memory.runtime.storage import RuntimeFileChecksum, RuntimeFilePath
from basic_memory.models import Entity
from basic_memory.repository import EntityRepository, NoteContentRepository
from basic_memory.schemas.response import ObservationResponse, RelationResponse
from basic_memory.services.file_service import FileService


class _MaterializationWorkerPool:
    """Bounded in-process worker pool that drains queued note materializations.

    Mirrors the cloud's queue worker model locally: the accept enqueues a
    materialization and returns; a fixed number of workers pull from the queue
    and run them. Bounding concurrency to `workers` is the point — fire-and-forget
    `create_task` let every deferred file write + index run at once, and at high
    write load they contended en masse for the single SQLite writer and the event
    loop, collapsing the tail (p99) and throughput
    (benchmarks/docs/write-load-benchmark.md). With N workers only N
    materializations are in flight; the rest wait in the queue and drain over
    time, so the accept path stays light AND the writer isn't thrashed.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Coroutine[Any, Any, object]] | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def submit(self, work: Coroutine[Any, Any, object], *, workers: int) -> None:
        self._ensure_workers(workers)
        assert self._queue is not None
        self._queue.put_nowait(work)

    def _ensure_workers(self, workers: int) -> None:
        # Trigger: first submit, or submit on a different event loop than the one
        # the workers were bound to (e.g. a fresh per-test loop).
        # Why: workers are long-lived tasks bound to one loop; reusing a queue
        # whose workers live on a dead loop would hang. Outcome: (re)create the
        # queue + `workers` worker tasks on the current running loop. Orphaned
        # workers on a closed loop are already dead, so dropping them is safe.
        loop = asyncio.get_running_loop()
        if self._queue is not None and self._loop is loop:
            return
        self._loop = loop
        self._queue = asyncio.Queue()
        self._workers = [asyncio.create_task(self._run()) for _ in range(max(1, workers))]

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            work = await self._queue.get()
            try:
                await work
            except Exception:  # pragma: no cover - defensive worker guard
                logger.exception("Local note materialization failed")
            finally:
                self._queue.task_done()

    async def join(self) -> None:
        """Block until every queued materialization has completed (tests)."""
        if self._queue is not None:
            await self._queue.join()

    async def aclose(self) -> None:
        """Cancel workers and reset the pool (clean test teardown / shutdown)."""
        workers = self._workers
        self._workers = []
        self._queue = None
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
        cleanup_enqueuer=InlineNoteFileDeleteEnqueuer(storage),
    )


async def recover_stuck_materializations(
    *,
    session_maker: async_sessionmaker[AsyncSession],
    file_service: FileService,
    project_id: int,
) -> int:
    """Re-drive every note materialization stuck in writing/pending/failed for a project.

    Meant to run once per project at startup, before serving. Non-fatal per row:
    a single row that raises is logged and skipped so one poisoned note cannot
    block startup recovery for the rest of the project. Returns the number of rows
    that reached a written file state.
    """
    async with db.scoped_session(session_maker) as session:
        stuck_rows = await NoteContentRepository(
            project_id=project_id
        ).find_stuck_materializations(session)

    if not stuck_rows:
        return 0

    logger.info(
        "Recovering stuck note materializations",
        project_id=project_id,
        stuck_count=len(stuck_rows),
    )
    recovered = 0
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
        return await self.file_service.compute_checksum(path)

    async def delete_file(self, path: RuntimeFilePath) -> None:
        await self.file_service.delete_file(path)


@dataclass(frozen=True, slots=True)
class InlineNoteFileDeleteEnqueuer:
    """Execute note-file cleanup immediately in the local runtime."""

    storage: LocalNoteContentStorage

    async def enqueue_note_file_delete(self, request: RuntimeNoteFileDeleteJobRequest) -> None:
        # Trigger: a move scheduled old-path cleanup whose old and new paths differ
        # only by case (or otherwise alias the same inode) on a case-insensitive
        # filesystem, so the old path now points at the just-written new file.
        # Why: the checksum guard cannot tell "old file still present" from "old path
        # aliases the new file" — both read the same bytes — so deleting the old path
        # would destroy the note's only copy (then scan reconciliation removes the row).
        # Outcome: skip the delete entirely; the paths are the same physical file.
        if request.live_file_path is not None and self.storage.file_service.paths_share_storage_target(
            request.file_path, request.live_file_path
        ):
            logger.info(
                "Skipping note-file cleanup that aliases the live file (case-only rename)",
                entity_id=request.entity_id,
                file_path=request.file_path,
                live_file_path=request.live_file_path,
            )
            return
        await run_note_file_delete(request, storage=self.storage)


class RelationResolutionScheduling(Protocol):
    """Capability to back-resolve forward references after a write is indexed."""

    def schedule_relation_resolution(self, *, project_id: int) -> None: ...


@dataclass(frozen=True, slots=True)
class LocalNoteContentMaterializationProvider:
    """Run accepted-note materialization inline for the local runtime."""

    session_maker: async_sessionmaker[AsyncSession]
    file_service: FileService
    file_indexer: IndexFileExecutor | None = None
    test_mode: bool = False
    materialization_workers: int = 4
    relation_resolution_scheduler: RelationResolutionScheduling | None = None

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
        if accepted.materialization is None:
            return accepted
        if self.test_mode:
            return await self._materialize_write_now(accepted)
        self._schedule_materialization(accepted)
        return accepted

    def _schedule_materialization(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> None:
        # Hand the materialization to the bounded worker pool instead of spawning
        # an unbounded task per write — see _MaterializationWorkerPool for why.
        _materialization_pool.submit(
            self._materialize_write_now(accepted),
            workers=self.materialization_workers,
        )

    async def _materialize_write_now(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]:
        if accepted.materialization is None:  # pragma: no cover - guarded by caller
            return accepted
        storage = LocalNoteContentStorage(self.file_service)
        cleanup_enqueuer = InlineNoteFileDeleteEnqueuer(storage)
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
        await InlineNoteFileDeleteEnqueuer(storage).enqueue_note_file_delete(
            plan_note_file_delete_job_request(accepted.file_delete)
        )
        return accepted
