"""Portable directory-delete result and cleanup enqueue orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.indexing.project_index_maintenance import delete_project_index_vector_rows
from basic_memory.models import Entity, NoteContent, Project, Relation
from basic_memory.runtime.cleanup import (
    RuntimeDeleteStatus,
    RuntimeDirectoryFileSnapshot,
    RuntimeFileDeleteResult,
    RuntimeNoteFileDeleteJobRequest,
    plan_directory_file_snapshot,
    plan_note_file_delete_job_request,
)
from basic_memory.runtime.storage import ProjectExternalId, ProjectId, RuntimeFilePath
from basic_memory.utils import valid_project_path_value

type DirectoryDeleteFileStatus = Literal["complete", "pending", "failed"]


class DirectoryDeleteRejectKind(StrEnum):
    """Portable directory-delete rejection categories."""

    bad_request = "bad_request"
    not_found = "not_found"

    @property
    def http_status_code(self) -> int:
        """Return the route status that matches this rejection behavior."""
        match self:
            case DirectoryDeleteRejectKind.bad_request:
                return 400
            case DirectoryDeleteRejectKind.not_found:
                return 404


@dataclass(frozen=True, slots=True)
class DirectoryDeleteRejection:
    """Typed rejection from directory-delete acceptance."""

    kind: DirectoryDeleteRejectKind
    detail: str


class DirectoryDeleteRejected(Exception):
    """Exception wrapper for a typed directory-delete rejection."""

    def __init__(self, rejection: DirectoryDeleteRejection) -> None:
        super().__init__(rejection.detail)
        self.rejection = rejection


class DirectoryFileDeleteEnqueueError(Exception):
    """Raised by adapters for expected cleanup queue submission failures."""


@dataclass(frozen=True, slots=True)
class DirectoryDeleteAcceptanceRequest:
    """Input for accepting a directory delete before cleanup jobs run."""

    project_external_id: ProjectExternalId
    directory: RuntimeFilePath


@dataclass(frozen=True, slots=True)
class DirectoryDeleteAcceptance:
    """Accepted DB-side directory delete before cleanup jobs run."""

    project_id: ProjectId
    files: tuple[RuntimeDirectoryFileSnapshot, ...]
    # Surviving entities OUTSIDE the deleted directory that linked into it: their
    # search_index relation rows go stale once the deleted rows disappear, so the
    # caller reindexes these sources to drop the dangling relation rows.
    relation_cleanup_entity_ids: frozenset[int] = frozenset()

    @property
    def deleted_files(self) -> tuple[RuntimeFilePath, ...]:
        return tuple(file_snapshot.file_path for file_snapshot in self.files)


class DirectoryDeleteAcceptanceStore(Protocol):
    """Repository capability for accepting directory deletes into DB state."""

    async def load_project_id(
        self,
        session: AsyncSession,
        project_external_id: ProjectExternalId,
    ) -> ProjectId | None:
        """Return the database id for a project external id."""

    async def load_directory_file_snapshots(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        directory: RuntimeFilePath,
    ) -> Sequence[RuntimeDirectoryFileSnapshot]:
        """Return file snapshots owned by the accepted directory delete."""

    async def delete_directory_entities(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        entity_ids: Sequence[int],
    ) -> frozenset[int]:
        """Delete the accepted entity rows and return surviving relation sources.

        The returned ids are entities OUTSIDE the deleted set whose relations pointed
        into it; their search rows need reindexing to drop now-dangling relations.
        """


@dataclass(frozen=True, slots=True)
class DirectoryDeleteRuntime:
    """Dependencies required by directory-delete acceptance orchestration."""

    store: DirectoryDeleteAcceptanceStore
    file_delete_enqueuer: DirectoryFileDeleteEnqueuer


@dataclass(frozen=True, slots=True)
class RepositoryDirectoryDeleteAcceptanceStore:
    """Repository-backed directory-delete acceptance store."""

    async def load_project_id(
        self,
        session: AsyncSession,
        project_external_id: ProjectExternalId,
    ) -> ProjectId | None:
        result = await session.execute(
            select(Project.id).where(Project.external_id == project_external_id).limit(1)
        )
        project_id = result.scalars().one_or_none()
        return int(project_id) if project_id is not None else None

    async def load_directory_file_snapshots(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        directory: RuntimeFilePath,
    ) -> Sequence[RuntimeDirectoryFileSnapshot]:
        query = (
            select(
                Entity.id,
                Entity.file_path,
                Entity.checksum,
                Entity.mtime,
                Entity.size,
                Entity.updated_at,
                NoteContent.file_checksum.label("note_file_checksum"),
                NoteContent.file_updated_at.label("note_file_updated_at"),
            )
            .outerjoin(NoteContent, NoteContent.entity_id == Entity.id)
            .where(Entity.project_id == project_id)
        )
        if directory not in {"", "/"}:
            escaped_directory = directory_delete_like_prefix(directory)
            query = query.where(Entity.file_path.like(f"{escaped_directory}/%", escape="\\"))

        result = await session.execute(
            query.order_by(Entity.file_path.asc()).with_for_update(of=Entity)
        )
        return [
            plan_directory_file_snapshot(
                entity_id=int(row.id),
                file_path=str(row.file_path),
                entity_checksum=str(row.checksum) if row.checksum is not None else None,
                entity_mtime=float(row.mtime) if row.mtime is not None else None,
                entity_size=int(row.size) if row.size is not None else None,
                note_file_checksum=(
                    str(row.note_file_checksum) if row.note_file_checksum is not None else None
                ),
                note_file_updated_at=row.note_file_updated_at,
            )
            for row in result.all()
        ]

    async def delete_directory_entities(
        self,
        session: AsyncSession,
        *,
        project_id: ProjectId,
        entity_ids: Sequence[int],
    ) -> frozenset[int]:
        if not entity_ids:
            return frozenset()
        deleted_entity_ids = tuple(entity_ids)

        # Capture surviving sources before the delete: Relation.to_id CASCADE will drop
        # the relation table rows for incoming links from entities outside the directory,
        # but those sources own the matching search_index relation rows and are never in
        # the deleted set, so the caller must reindex them to clear the stale rows.
        surviving_relation_sources = await session.execute(
            select(Relation.from_id)
            .where(
                Relation.project_id == project_id,
                Relation.to_id.in_(deleted_entity_ids),
                Relation.from_id.not_in(deleted_entity_ids),
            )
            .distinct()
        )
        relation_cleanup_entity_ids = frozenset(
            int(source_id) for source_id in surviving_relation_sources.scalars()
        )

        await session.execute(
            text(
                """
                DELETE FROM search_index
                WHERE project_id = :project_id AND entity_id IN :entity_ids
                """
            ).bindparams(bindparam("entity_ids", expanding=True)),
            {"project_id": project_id, "entity_ids": deleted_entity_ids},
        )
        await delete_project_index_vector_rows(
            session,
            project_id=project_id,
            entity_ids=deleted_entity_ids,
        )
        await session.execute(delete(Entity).where(Entity.id.in_(deleted_entity_ids)))
        return relation_cleanup_entity_ids


def normalize_directory_delete_path(directory: str) -> RuntimeFilePath:
    """Normalize a project-relative directory delete path or reject traversal."""
    normalized_directory = directory.strip().strip("/")
    if not valid_project_path_value(normalized_directory):
        raise ValueError("Invalid directory path")
    return normalized_directory


def directory_delete_like_prefix(directory: RuntimeFilePath) -> RuntimeFilePath:
    """Escape one normalized directory path for SQL LIKE prefix matching."""
    return directory.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


@dataclass(frozen=True, slots=True)
class DirectoryDeleteFileFailure:
    """One accepted file left on disk after cleanup, with its caller-visible reason."""

    file_path: RuntimeFilePath
    reason: str


@dataclass(frozen=True, slots=True)
class DirectoryDeleteAcceptedResult:
    """Existing directory-delete response shape before route serialization."""

    deleted_files: tuple[RuntimeFilePath, ...]
    file_delete_status: DirectoryDeleteFileStatus
    error: str | None = None
    # Files whose guarded cleanup was skipped (checksum changed / no accepted
    # checksum) or whose cleanup enqueue failed; they remain on disk despite the
    # DB row being deleted. "missing" results count as successful (already gone).
    failed_files: tuple[DirectoryDeleteFileFailure, ...] = ()
    # Surviving source entities whose relations pointed into the deleted directory;
    # the caller reindexes these to clear now-stale relation rows from search.
    relation_cleanup_entity_ids: frozenset[int] = frozenset()

    @classmethod
    def complete(cls) -> "DirectoryDeleteAcceptedResult":
        """Return the response for a directory delete with no matching files."""
        return cls(deleted_files=(), file_delete_status="complete")

    @classmethod
    def pending(
        cls,
        *,
        deleted_files: Sequence[RuntimeFilePath],
        failed_files: Sequence[DirectoryDeleteFileFailure] = (),
        relation_cleanup_entity_ids: frozenset[int] = frozenset(),
    ) -> "DirectoryDeleteAcceptedResult":
        """Return the response after DB rows were deleted and cleanup was queued."""
        return cls(
            deleted_files=tuple(deleted_files),
            file_delete_status="pending",
            failed_files=tuple(failed_files),
            relation_cleanup_entity_ids=relation_cleanup_entity_ids,
        )

    @classmethod
    def failed(
        cls,
        *,
        deleted_files: Sequence[RuntimeFilePath],
        error: str,
    ) -> "DirectoryDeleteAcceptedResult":
        """Return the response after DB rows were deleted but cleanup queueing failed."""
        return cls(
            deleted_files=tuple(deleted_files),
            file_delete_status="failed",
            error=error,
        )

    def to_response_payload(self) -> dict[str, object]:
        """Serialize to the current Basic Memory directory-delete response contract.

        A guarded cleanup that left files on disk (failed_files) is reported as
        failed_deletes with reasons, not folded into successful_deletes — otherwise
        callers see a clean success while stale files remain and later reappear.
        """
        failed = len(self.failed_files)
        payload: dict[str, object] = {
            "total_files": len(self.deleted_files),
            "successful_deletes": len(self.deleted_files) - failed,
            "failed_deletes": failed,
            "deleted_files": list(self.deleted_files),
            # The MCP/CLI client validates errors as DirectoryDeleteError objects
            # ({path, error}); plain strings fail Pydantic validation client-side
            # on every partial failure, so serialize structured entries.
            "errors": [
                {"path": failure.file_path, "error": failure.reason}
                for failure in self.failed_files
            ],
            "file_delete_status": self.file_delete_status,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


class DirectoryFileDeleteEnqueuer(Protocol):
    """Capability that queues cleanup for one accepted directory-delete file.

    Returns the guarded delete result when cleanup runs inline (local runtime),
    or None when the work is queued for later (cloud runtime, outcome deferred).
    """

    async def enqueue_directory_file_delete(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> RuntimeFileDeleteResult | None: ...


@dataclass(frozen=True, slots=True)
class DirectoryFileDeleteEnqueueFailure:
    """One accepted file whose cleanup could not be queued or run inline."""

    file_path: RuntimeFilePath
    reason: str


@dataclass(frozen=True, slots=True)
class DirectoryFileDeleteEnqueueOutcome:
    """Aggregate of one directory delete's per-file cleanup enqueue attempts."""

    results: tuple[RuntimeFileDeleteResult, ...] = ()
    failures: tuple[DirectoryFileDeleteEnqueueFailure, ...] = ()


async def enqueue_directory_file_delete_jobs(
    *,
    project_id: ProjectId,
    files: Sequence[RuntimeDirectoryFileSnapshot],
    enqueuer: DirectoryFileDeleteEnqueuer,
) -> DirectoryFileDeleteEnqueueOutcome:
    """Queue guarded cleanup jobs for files accepted by a directory delete.

    Returns the inline cleanup results (local runtime); deferred/queued cleanups
    (cloud runtime) contribute nothing here. Per-file enqueue failures are collected
    rather than raised so one unqueueable file does not abort cleanup of the rest.
    """
    results: list[RuntimeFileDeleteResult] = []
    failures: list[DirectoryFileDeleteEnqueueFailure] = []
    for file_snapshot in files:
        # Trigger: one file's guarded delete/enqueue raises (unreadable file, queue down).
        # Why: the entity row is already deleted, so aborting the batch strands every
        #   remaining file on disk with its DB row gone; sync then resurrects them.
        # Outcome: record the failure, keep going, and report it as a failed delete.
        try:
            result = await enqueuer.enqueue_directory_file_delete(
                plan_note_file_delete_job_request(
                    file_snapshot.to_pending_note_file_delete(project_id=project_id)
                )
            )
        except DirectoryFileDeleteEnqueueError as exc:
            failures.append(
                DirectoryFileDeleteEnqueueFailure(
                    file_path=file_snapshot.file_path,
                    reason=str(exc),
                )
            )
            continue
        if result is not None:
            results.append(result)
    return DirectoryFileDeleteEnqueueOutcome(
        results=tuple(results),
        failures=tuple(failures),
    )


async def accept_directory_delete(
    session: AsyncSession,
    *,
    request: DirectoryDeleteAcceptanceRequest,
    store: DirectoryDeleteAcceptanceStore,
) -> DirectoryDeleteAcceptance:
    """Accept a directory delete into DB state before post-commit cleanup jobs."""
    try:
        directory = normalize_directory_delete_path(request.directory)
    except ValueError as exc:
        raise DirectoryDeleteRejected(
            DirectoryDeleteRejection(
                kind=DirectoryDeleteRejectKind.bad_request,
                detail="Invalid directory path",
            )
        ) from exc

    project_id = await store.load_project_id(session, request.project_external_id)
    if project_id is None:
        raise DirectoryDeleteRejected(
            DirectoryDeleteRejection(
                kind=DirectoryDeleteRejectKind.not_found,
                detail=f"Project '{request.project_external_id}' not found",
            )
        )

    file_snapshots = tuple(
        await store.load_directory_file_snapshots(
            session,
            project_id=project_id,
            directory=directory,
        )
    )
    if not file_snapshots:
        return DirectoryDeleteAcceptance(project_id=project_id, files=())

    relation_cleanup_entity_ids = await store.delete_directory_entities(
        session,
        project_id=project_id,
        entity_ids=[snapshot.entity_id for snapshot in file_snapshots],
    )
    return DirectoryDeleteAcceptance(
        project_id=project_id,
        files=file_snapshots,
        relation_cleanup_entity_ids=relation_cleanup_entity_ids,
    )


async def finish_directory_delete_acceptance(
    *,
    request: DirectoryDeleteAcceptanceRequest,
    accepted: DirectoryDeleteAcceptance,
    enqueuer: DirectoryFileDeleteEnqueuer,
) -> DirectoryDeleteAcceptedResult:
    """Queue cleanup for an accepted directory delete after the DB commit."""
    if not accepted.files:
        return DirectoryDeleteAcceptedResult.complete()

    outcome = await enqueue_directory_file_delete_jobs(
        project_id=accepted.project_id,
        files=accepted.files,
        enqueuer=enqueuer,
    )

    # Both a guarded skip (checksum changed) and an enqueue failure leave the file on
    # disk while its DB row is gone; surface both as failed deletes instead of reporting
    # a clean success that later reappears when sync re-indexes the stranded file.
    skipped = [r for r in outcome.results if r.status == RuntimeDeleteStatus.skipped]
    failed_files = tuple(
        DirectoryDeleteFileFailure(file_path=result.file_path, reason=result.reason)
        for result in skipped
    ) + tuple(
        DirectoryDeleteFileFailure(file_path=failure.file_path, reason=failure.reason)
        for failure in outcome.failures
    )
    return DirectoryDeleteAcceptedResult.pending(
        deleted_files=accepted.deleted_files,
        failed_files=failed_files,
        relation_cleanup_entity_ids=accepted.relation_cleanup_entity_ids,
    )


async def run_directory_delete(
    session: AsyncSession,
    *,
    request: DirectoryDeleteAcceptanceRequest,
    runtime: DirectoryDeleteRuntime,
) -> DirectoryDeleteAcceptedResult:
    """Accept a directory delete into DB state and queue guarded file cleanup."""
    accepted = await accept_directory_delete(
        session,
        request=request,
        store=runtime.store,
    )
    return await finish_directory_delete_acceptance(
        request=request,
        accepted=accepted,
        enqueuer=runtime.file_delete_enqueuer,
    )
