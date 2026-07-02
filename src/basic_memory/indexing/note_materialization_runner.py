"""Portable orchestration for note file materialization jobs."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, Self

from loguru import logger

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.indexing.note_content_reconciler import (
    NoteContentRepositories,
    apply_note_content_update_plan,
    build_default_note_content_repositories,
    note_content_state_from_model,
)
from basic_memory.indexing.note_content_reconciliation import (
    AcceptedNoteContentVersion,
    MaterializedNoteContentFile,
    NoteContentMaterializedCurrent,
    NoteContentMaterializedStale,
    NoteContentState,
    NoteContentWriteStatus,
    plan_note_content_materialization_publish,
    plan_note_content_materialization_status,
)
from basic_memory.runtime import (
    RuntimeNoteContentStore,
    RuntimeFileChecksum,
    RuntimeFileConflictError,
    RuntimeFilePath,
    RuntimeEntityId,
    RuntimeNoteFileDeleteJobRequest,
    RuntimeNoteContentVersionSource,
    RuntimeNoteMaterializationJobRequest,
    RuntimeNoteMaterializationResult,
    RuntimeNoteMaterializationStatus,
    RuntimePendingNoteFileDelete,
    RuntimePreparedNoteWrite,
    RuntimeWrittenFileState,
    ProjectId,
    TenantId,
    note_content_matches_materialization_request,
    plan_note_file_delete_job_request,
    plan_note_materialization_cleanup_file_delete,
    plan_prepared_note_write,
    write_prepared_note_to_content_store,
)
from basic_memory.file_utils import FileError
from basic_memory.models import Entity, NoteContent
from basic_memory.services.exceptions import FileOperationError

type NoteMaterializationPreflightOutcome = (
    RuntimePreparedNoteWrite | RuntimeNoteMaterializationResult
)
type NoteMaterializationPublishUpdate = (
    NoteContentMaterializedCurrent | NoteContentMaterializedStale
)


class NoteMaterializationClock(Protocol):
    """Clock used to timestamp note materialization attempts."""

    def now(self) -> datetime: ...


class NoteMaterializationSessionProvider(Protocol):
    """Session provider for note materialization persistence."""

    def open_session(
        self,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AbstractAsyncContextManager[AsyncSession]: ...


@dataclass(frozen=True, slots=True)
class SystemNoteMaterializationClock:
    """System UTC clock for note materialization attempts."""

    def now(self) -> datetime:
        return note_materialization_utc_now()


@dataclass(frozen=True, slots=True)
class DefaultNoteMaterializationSessionProvider:
    """Default session provider for local note materialization persistence."""

    def open_session(
        self,
        session_maker: async_sessionmaker[AsyncSession],
    ) -> AbstractAsyncContextManager[AsyncSession]:
        return db.scoped_session(session_maker)


class NoteMaterializationPublishAction(StrEnum):
    """Post-write DB work selected for a materialized note file."""

    missing_note_content = "missing_note_content"
    stale_file_path = "stale_file_path"
    stale_db_version = "stale_db_version"
    current = "current"


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


@dataclass(frozen=True, slots=True)
class NoteMaterializationPublishPlan:
    """Pure post-write outcome before an adapter persists materialization state."""

    action: NoteMaterializationPublishAction
    result: RuntimeNoteMaterializationResult
    note_content_update: NoteMaterializationPublishUpdate | None = None

    @property
    def should_update_entity(self) -> bool:
        """Return whether the adapter must update the owning entity row too."""
        return self.action is NoteMaterializationPublishAction.current

    def require_note_content_update(self) -> NoteMaterializationPublishUpdate:
        """Return the note_content update required by this publish plan."""
        if self.note_content_update is None:
            raise RuntimeError(f"publish plan has no note_content update: {self.action}")
        return self.note_content_update


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


class NoteMaterializationSessionLock(Protocol):
    """Capability that serializes DB-mediated writes for one project note."""

    async def lock_note_materialization(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        entity_id: RuntimeEntityId,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class NoopNoteMaterializationSessionLock:
    """Session lock for runtimes that do not need an extra DB advisory lock."""

    async def lock_note_materialization(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        entity_id: RuntimeEntityId,
    ) -> None:
        return None


@dataclass(frozen=True, slots=True)
class ContentStoreNoteMaterializationFileWriter:
    """Content-store adapter for writing one prepared accepted note."""

    content_store: RuntimeNoteContentStore

    async def write_prepared_note(
        self,
        prepared_write: RuntimePreparedNoteWrite,
    ) -> RuntimeWrittenFileState:
        return await write_prepared_note_to_content_store(self.content_store, prepared_write)


def note_materialization_utc_now() -> datetime:
    """Return the default UTC timestamp for note materialization persistence."""
    return datetime.now(tz=UTC)


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


def plan_written_note_materialization_publish(
    *,
    request: RuntimeNoteMaterializationJobRequest,
    prepared_write: RuntimePreparedNoteWrite,
    written_file: RuntimeWrittenFileState,
    current_note_content: NoteContentState | None,
    current_file_path: RuntimeFilePath | None,
) -> NoteMaterializationPublishPlan:
    """Plan DB publication after storage accepts a materialized note file."""

    def _result(
        status: RuntimeNoteMaterializationStatus,
        reason: str,
        *,
        written_file_orphaned: bool = False,
    ) -> RuntimeNoteMaterializationResult:
        return RuntimeNoteMaterializationResult(
            entity_id=request.entity_id,
            status=status,
            reason=reason,
            file_path=written_file.file_path,
            file_checksum=written_file.file_checksum,
            written_file_orphaned=written_file_orphaned,
        )

    if current_note_content is None:
        return NoteMaterializationPublishPlan(
            action=NoteMaterializationPublishAction.missing_note_content,
            result=_result(
                RuntimeNoteMaterializationStatus.missing,
                f"note state disappeared after file write: {request.entity_id}",
                written_file_orphaned=True,
            ),
        )

    if current_file_path is None:
        raise ValueError("current file path is required with note_content state")

    if current_file_path != prepared_write.file_path:
        return NoteMaterializationPublishPlan(
            action=NoteMaterializationPublishAction.stale_file_path,
            result=_result(
                RuntimeNoteMaterializationStatus.stale,
                f"note path changed before file publish: {request.entity_id}",
                written_file_orphaned=True,
            ),
        )

    materialized_file = MaterializedNoteContentFile(
        db_version=request.db_version,
        db_checksum=request.db_checksum,
        file_checksum=written_file.file_checksum,
        file_updated_at=written_file.file_updated_at,
        attempted_at=prepared_write.attempted_at,
    )
    note_content_update = plan_note_content_materialization_publish(
        current=current_note_content,
        written=materialized_file,
    )

    if not note_content_matches_materialization_request(current_note_content, request):
        return NoteMaterializationPublishPlan(
            action=NoteMaterializationPublishAction.stale_db_version,
            result=_result(
                RuntimeNoteMaterializationStatus.stale,
                f"file written but newer accepted note remains pending: {request.entity_id}",
            ),
            note_content_update=note_content_update,
        )

    return NoteMaterializationPublishPlan(
        action=NoteMaterializationPublishAction.current,
        result=_result(
            RuntimeNoteMaterializationStatus.written,
            f"note file written: {written_file.file_path}",
        ),
        note_content_update=note_content_update,
    )


@dataclass(frozen=True, slots=True)
class RepositoryNoteMaterializationPreflight:
    """Repository-backed preflight for one accepted note materialization attempt."""

    session_maker: async_sessionmaker[AsyncSession]
    session_lock: NoteMaterializationSessionLock = field(
        default_factory=NoopNoteMaterializationSessionLock
    )
    session_provider: NoteMaterializationSessionProvider = (
        DefaultNoteMaterializationSessionProvider()
    )
    clock: NoteMaterializationClock = SystemNoteMaterializationClock()

    async def prepare_note_materialization(
        self,
        request: RuntimeNoteMaterializationJobRequest,
    ) -> NoteMaterializationPreflightResult:
        async with self.session_provider.open_session(self.session_maker) as session:
            await self.session_lock.lock_note_materialization(
                session,
                project_id=request.project_id,
                entity_id=request.entity_id,
            )

            entity = await session.get(Entity, request.entity_id)
            note_content = await session.get(NoteContent, request.entity_id)
            attempted_at = self.clock.now()
            preflight_result = plan_note_materialization_preflight(
                request,
                entity=entity,
                note_content=note_content,
                attempted_at=attempted_at,
            )
            if preflight_result.terminal_result is not None:
                return preflight_result

            if note_content is None:
                raise RuntimeError("prepared note materialization requires note_content")

            note_content.file_write_status = "writing"
            note_content.last_materialization_attempt_at = attempted_at
            await session.flush()
            return preflight_result


@dataclass(frozen=True, slots=True)
class RepositoryNoteMaterializationPublisher:
    """Repository-backed publisher for successful materialized file writes."""

    session_maker: async_sessionmaker[AsyncSession]
    session_lock: NoteMaterializationSessionLock = field(
        default_factory=NoopNoteMaterializationSessionLock
    )
    session_provider: NoteMaterializationSessionProvider = (
        DefaultNoteMaterializationSessionProvider()
    )
    repositories: NoteContentRepositories = field(
        default_factory=build_default_note_content_repositories
    )

    async def publish_written_file_state(
        self,
        request: RuntimeNoteMaterializationJobRequest,
        prepared_write: RuntimePreparedNoteWrite,
        written_file: RuntimeWrittenFileState,
    ) -> RuntimeNoteMaterializationResult:
        async with self.session_provider.open_session(self.session_maker) as session:
            await self.session_lock.lock_note_materialization(
                session,
                project_id=request.project_id,
                entity_id=request.entity_id,
            )

            note_content = await session.get(NoteContent, request.entity_id)
            publish_plan = plan_written_note_materialization_publish(
                request=request,
                prepared_write=prepared_write,
                written_file=written_file,
                current_note_content=(
                    note_content_state_from_model(note_content)
                    if note_content is not None
                    else None
                ),
                current_file_path=note_content.file_path if note_content is not None else None,
            )
            if note_content is None:
                return publish_plan.result

            if publish_plan.action is NoteMaterializationPublishAction.stale_file_path:
                return publish_plan.result

            if publish_plan.action is NoteMaterializationPublishAction.stale_db_version:
                await apply_note_content_update_plan(
                    self.repositories.note_content_repository(request.project_id),
                    session,
                    request.entity_id,
                    publish_plan.require_note_content_update(),
                )
                return publish_plan.result

            if publish_plan.action is not NoteMaterializationPublishAction.current:
                raise RuntimeError(
                    f"Unhandled note materialization publish action: {publish_plan.action}"
                )

            entity = await session.get(Entity, request.entity_id)
            if entity is None:
                return RuntimeNoteMaterializationResult(
                    entity_id=request.entity_id,
                    status=RuntimeNoteMaterializationStatus.missing,
                    reason=f"entity disappeared after file write: {request.entity_id}",
                    file_path=written_file.file_path,
                    file_checksum=written_file.file_checksum,
                )

            await apply_note_content_update_plan(
                self.repositories.note_content_repository(request.project_id),
                session,
                request.entity_id,
                publish_plan.require_note_content_update(),
            )
            entity.updated_at = written_file.file_updated_at
            entity.mtime = written_file.file_updated_at.timestamp()
            entity.size = len(prepared_write.markdown_content.encode("utf-8"))
            await session.flush()
            return publish_plan.result


@dataclass(frozen=True, slots=True)
class RepositoryNoteMaterializationStatusPublisher:
    """Repository-backed publisher for materialization conflict or failure state."""

    session_maker: async_sessionmaker[AsyncSession]
    session_lock: NoteMaterializationSessionLock = field(
        default_factory=NoopNoteMaterializationSessionLock
    )
    session_provider: NoteMaterializationSessionProvider = (
        DefaultNoteMaterializationSessionProvider()
    )
    repositories: NoteContentRepositories = field(
        default_factory=build_default_note_content_repositories
    )

    async def publish_note_materialization_status(
        self,
        request: RuntimeNoteMaterializationJobRequest,
        publication: NoteMaterializationStatusPublication,
    ) -> None:
        async with self.session_provider.open_session(self.session_maker) as session:
            await self.session_lock.lock_note_materialization(
                session,
                project_id=request.project_id,
                entity_id=request.entity_id,
            )

            note_content = await session.get(NoteContent, request.entity_id)
            if note_content is None:
                return

            plan = plan_note_content_materialization_status(
                current=note_content_state_from_model(note_content),
                accepted=AcceptedNoteContentVersion(
                    db_version=request.db_version,
                    db_checksum=request.db_checksum,
                ),
                file_write_status=publication.file_write_status,
                actual_file_checksum=publication.actual_file_checksum,
                error_message=publication.error_message,
                attempted_at=publication.attempted_at,
            )
            if plan is None:
                return

            await apply_note_content_update_plan(
                self.repositories.note_content_repository(request.project_id),
                session,
                request.entity_id,
                plan,
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
        cleanup_enqueued = await enqueue_cleanup_file(
            cleanup_enqueuer,
            tenant_id=request.tenant_id,
            cleanup_file=preflight_result.cleanup_file,
        )
        return replace(
            preflight_result.terminal_result,
            cleanup_enqueue_failed=not cleanup_enqueued,
        )

    prepared_write = preflight_result.require_prepared_write()
    try:
        written_file = await writer.write_prepared_note(prepared_write)
        result = await publisher.publish_written_file_state(
            request,
            prepared_write,
            written_file,
        )
        if result.status == RuntimeNoteMaterializationStatus.written:
            cleanup_enqueued = await enqueue_cleanup_file(
                cleanup_enqueuer,
                tenant_id=request.tenant_id,
                cleanup_file=cleanup_file_from_prepared_write(request, prepared_write),
            )
            result = replace(result, cleanup_enqueue_failed=not cleanup_enqueued)
        elif result.written_file_orphaned:
            # Written to disk but the note moved/disappeared before publish, so the
            # DB no longer owns this path; clean up the just-written file or the
            # watcher/project index re-imports it as a duplicate note.
            cleanup_enqueued = await enqueue_cleanup_file(
                cleanup_enqueuer,
                tenant_id=request.tenant_id,
                cleanup_file=cleanup_file_for_orphaned_write(request, written_file),
            )
            result = replace(result, cleanup_enqueue_failed=not cleanup_enqueued)
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
    # Any other storage failure (atomic write -> FileWriteError, checksum ->
    # FileError, post-write stat -> OSError) must also flip the note out of the
    # "writing" preflight state, or reads/status report an in-progress write that
    # never completes. FileError/OSError do not subclass FileOperationError.
    except (FileOperationError, FileError, OSError) as exc:
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


def cleanup_file_for_orphaned_write(
    request: RuntimeNoteMaterializationJobRequest,
    written_file: RuntimeWrittenFileState,
) -> RuntimePendingNoteFileDelete:
    """Cleanup target for a file written to disk that the DB no longer owns.

    The note moved or disappeared before publish, so the just-written file is
    orphaned. The delete is checksum-guarded against the bytes we just wrote, so it
    no-ops if anything else has since changed the file.
    """
    return RuntimePendingNoteFileDelete(
        project_id=request.project_id,
        entity_id=request.entity_id,
        file_path=written_file.file_path,
        file_checksum=written_file.file_checksum,
    )


async def enqueue_cleanup_file(
    cleanup_enqueuer: NoteFileDeleteEnqueuer,
    *,
    tenant_id: TenantId,
    cleanup_file: RuntimePendingNoteFileDelete | None,
) -> bool:
    """Enqueue old-file cleanup when materialization produced one.

    Returns False when the enqueue failed. Cleanup runs after the materialized
    write and its DB state are already durable, so a transient queue error must
    not fail the job — that would report a completed materialization as failed
    and re-run finished work. The caller surfaces the failure on the result
    instead; the orphaned old-path file shows up as a duplicate note on the next
    project index until it is cleaned up.
    """
    if cleanup_file is None:
        return True
    try:
        await cleanup_enqueuer.enqueue_note_file_delete(
            plan_note_file_delete_job_request(
                tenant_id=tenant_id,
                file_delete=cleanup_file,
            )
        )
    except Exception:
        logger.exception(
            "Failed to enqueue note file cleanup after materialization",
            entity_id=cleanup_file.entity_id,
            file_path=cleanup_file.file_path,
        )
        return False
    return True
