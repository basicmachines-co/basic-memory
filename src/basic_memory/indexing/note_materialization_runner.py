"""Portable orchestration for note file materialization jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Self

from basic_memory.indexing.note_content_reconciliation import NoteContentWriteStatus
from basic_memory.runtime import (
    RuntimeFileChecksum,
    RuntimeFileConflictError,
    RuntimeFilePath,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteContentVersionSource,
    RuntimeNoteMaterializationJobRequest,
    RuntimeNoteMaterializationResult,
    RuntimeNoteMaterializationStatus,
    RuntimePendingNoteFileDelete,
    RuntimePreparedNoteWrite,
    RuntimeWrittenFileState,
    TenantId,
    note_content_matches_materialization_request,
    plan_note_file_delete_job_request,
    plan_note_materialization_cleanup_file_delete,
    plan_prepared_note_write,
)
from basic_memory.services.exceptions import FileOperationError

type NoteMaterializationPreflightOutcome = (
    RuntimePreparedNoteWrite | RuntimeNoteMaterializationResult
)


@dataclass(frozen=True, slots=True)
class NoteMaterializationPreflightResult:
    """DB preflight outcome before storage materialization starts."""

    prepared_write: RuntimePreparedNoteWrite | None = None
    terminal_result: RuntimeNoteMaterializationResult | None = None
    cleanup_file: RuntimePendingNoteFileDelete | None = None

    def __post_init__(self) -> None:
        has_prepared_write = self.prepared_write is not None
        has_terminal_result = self.terminal_result is not None
        if has_prepared_write == has_terminal_result:
            raise ValueError("note materialization preflight requires one outcome")
        if has_prepared_write and self.cleanup_file is not None:
            raise ValueError("prepared note materialization cannot carry terminal cleanup")

    @classmethod
    def prepared(cls, prepared_write: RuntimePreparedNoteWrite) -> Self:
        """Return a preflight result that may proceed to storage I/O."""
        return cls(prepared_write=prepared_write)

    @classmethod
    def terminal(
        cls,
        terminal_result: RuntimeNoteMaterializationResult,
        *,
        cleanup_file: RuntimePendingNoteFileDelete | None = None,
    ) -> Self:
        """Return a preflight result that should finish without storage I/O."""
        return cls(terminal_result=terminal_result, cleanup_file=cleanup_file)

    def require_prepared_write(self) -> RuntimePreparedNoteWrite:
        """Return the prepared write after validating this is not terminal."""
        if self.prepared_write is None:
            raise RuntimeError("terminal note materialization preflight has no prepared write")
        return self.prepared_write


@dataclass(frozen=True, slots=True)
class NoteMaterializationStatusPublication:
    """Failure or conflict status that a persistence adapter should publish."""

    file_write_status: NoteContentWriteStatus
    attempted_at: datetime
    actual_file_checksum: RuntimeFileChecksum | None = None
    error_message: str | None = None


class NoteMaterializationPreflightProvider(Protocol):
    """Capability that prepares one current accepted note for materialization."""

    async def prepare_note_materialization(
        self,
        request: RuntimeNoteMaterializationJobRequest,
    ) -> NoteMaterializationPreflightResult: ...


class NoteMaterializationEntitySource(Protocol):
    """Entity fields needed to plan one note materialization preflight."""

    @property
    def file_path(self) -> RuntimeFilePath: ...


class NoteMaterializationContentSource(RuntimeNoteContentVersionSource, Protocol):
    """note_content fields needed to plan one note materialization preflight."""

    @property
    def markdown_content(self) -> str: ...

    @property
    def file_checksum(self) -> object | None: ...


class NoteMaterializationFileWriter(Protocol):
    """Capability that writes one prepared note to storage."""

    async def write_prepared_note(
        self,
        prepared_write: RuntimePreparedNoteWrite,
    ) -> RuntimeWrittenFileState: ...


class NoteMaterializationPublisher(Protocol):
    """Capability that publishes a successfully written file state."""

    async def publish_written_file_state(
        self,
        request: RuntimeNoteMaterializationJobRequest,
        prepared_write: RuntimePreparedNoteWrite,
        written_file: RuntimeWrittenFileState,
    ) -> RuntimeNoteMaterializationResult: ...


class NoteMaterializationStatusPublisher(Protocol):
    """Capability that publishes conflict or failure materialization status."""

    async def publish_note_materialization_status(
        self,
        request: RuntimeNoteMaterializationJobRequest,
        publication: NoteMaterializationStatusPublication,
    ) -> None: ...


class NoteFileDeleteEnqueuer(Protocol):
    """Capability that enqueues cleanup for old materialized note files."""

    async def enqueue_note_file_delete(self, request: RuntimeNoteFileDeleteJobRequest) -> None: ...


def plan_note_materialization_preflight(
    request: RuntimeNoteMaterializationJobRequest,
    *,
    entity: NoteMaterializationEntitySource | None,
    note_content: NoteMaterializationContentSource | None,
    attempted_at: datetime,
) -> NoteMaterializationPreflightResult:
    """Plan the DB preflight outcome for one queued note materialization request."""
    if entity is None or note_content is None:
        return NoteMaterializationPreflightResult.terminal(
            RuntimeNoteMaterializationResult(
                entity_id=request.entity_id,
                status=RuntimeNoteMaterializationStatus.missing,
                reason=f"note state no longer exists: {request.entity_id}",
            ),
            cleanup_file=plan_note_materialization_cleanup_file_delete(request),
        )

    if not note_content_matches_materialization_request(note_content, request):
        return NoteMaterializationPreflightResult.terminal(
            RuntimeNoteMaterializationResult(
                entity_id=request.entity_id,
                status=RuntimeNoteMaterializationStatus.stale,
                reason=f"accepted note changed before file write: {request.entity_id}",
                file_path=entity.file_path,
            )
        )

    return NoteMaterializationPreflightResult.prepared(
        plan_prepared_note_write(
            request=request,
            file_path=entity.file_path,
            markdown_content=str(note_content.markdown_content),
            previous_file_checksum=(
                str(note_content.file_checksum) if note_content.file_checksum is not None else None
            ),
            attempted_at=attempted_at,
        )
    )


async def run_note_materialization(
    request: RuntimeNoteMaterializationJobRequest,
    *,
    preflight: NoteMaterializationPreflightProvider,
    writer: NoteMaterializationFileWriter,
    publisher: NoteMaterializationPublisher,
    status_publisher: NoteMaterializationStatusPublisher,
    cleanup_enqueuer: NoteFileDeleteEnqueuer,
) -> RuntimeNoteMaterializationResult:
    """Run one queue-neutral materialized-note write."""
    preflight_result = await preflight.prepare_note_materialization(request)
    if preflight_result.terminal_result is not None:
        await enqueue_cleanup_file(
            cleanup_enqueuer,
            tenant_id=request.tenant_id,
            cleanup_file=preflight_result.cleanup_file,
        )
        return preflight_result.terminal_result

    prepared_write = preflight_result.require_prepared_write()
    try:
        written_file = await writer.write_prepared_note(prepared_write)
        result = await publisher.publish_written_file_state(
            request,
            prepared_write,
            written_file,
        )
        if result.status == RuntimeNoteMaterializationStatus.written:
            await enqueue_cleanup_file(
                cleanup_enqueuer,
                tenant_id=request.tenant_id,
                cleanup_file=cleanup_file_from_prepared_write(request, prepared_write),
            )
        return result
    except RuntimeFileConflictError as exc:
        await status_publisher.publish_note_materialization_status(
            request,
            NoteMaterializationStatusPublication(
                file_write_status="external_change_detected",
                attempted_at=prepared_write.attempted_at,
                actual_file_checksum=exc.actual_checksum,
                error_message=str(exc),
            ),
        )
        return RuntimeNoteMaterializationResult(
            entity_id=request.entity_id,
            status=RuntimeNoteMaterializationStatus.conflict,
            reason=str(exc),
            file_path=exc.file_path,
            file_checksum=exc.actual_checksum,
        )
    except FileOperationError as exc:
        await status_publisher.publish_note_materialization_status(
            request,
            NoteMaterializationStatusPublication(
                file_write_status="failed",
                attempted_at=prepared_write.attempted_at,
                error_message=str(exc),
            ),
        )
        raise


def cleanup_file_from_prepared_write(
    request: RuntimeNoteMaterializationJobRequest,
    prepared_write: RuntimePreparedNoteWrite,
) -> RuntimePendingNoteFileDelete | None:
    """Return old-file cleanup captured by a prepared note write."""
    if prepared_write.cleanup_file_path is None:
        return None
    return RuntimePendingNoteFileDelete(
        project_id=request.project_id,
        entity_id=request.entity_id,
        file_path=prepared_write.cleanup_file_path,
        file_checksum=prepared_write.cleanup_file_checksum,
    )


async def enqueue_cleanup_file(
    cleanup_enqueuer: NoteFileDeleteEnqueuer,
    *,
    tenant_id: TenantId,
    cleanup_file: RuntimePendingNoteFileDelete | None,
) -> None:
    """Enqueue old-file cleanup when materialization produced one."""
    if cleanup_file is None:
        return
    await cleanup_enqueuer.enqueue_note_file_delete(
        plan_note_file_delete_job_request(
            tenant_id=tenant_id,
            file_delete=cleanup_file,
        )
    )
