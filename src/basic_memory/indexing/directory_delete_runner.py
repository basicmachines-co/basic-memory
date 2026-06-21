"""Portable directory-delete result and cleanup enqueue orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models import Entity, NoteContent, Project
from basic_memory.runtime import (
    RuntimeDirectoryFileSnapshot,
    RuntimeFilePath,
    RuntimeNoteFileDeleteJobRequest,
    TenantId,
    ProjectId,
    ProjectExternalId,
    plan_directory_file_snapshot,
    plan_note_file_delete_job_request,
)
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

    tenant_id: TenantId
    project_external_id: ProjectExternalId
    directory: RuntimeFilePath


@dataclass(frozen=True, slots=True)
class DirectoryDeleteAcceptance:
    """Accepted DB-side directory delete before cleanup jobs run."""

    project_id: ProjectId
    files: tuple[RuntimeDirectoryFileSnapshot, ...]

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
        entity_ids: Sequence[int],
    ) -> None:
        """Delete the accepted entity rows."""


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
        entity_ids: Sequence[int],
    ) -> None:
        if not entity_ids:
            return
        await session.execute(delete(Entity).where(Entity.id.in_(entity_ids)))


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
class DirectoryDeleteAcceptedResult:
    """Existing directory-delete response shape before route serialization."""

    deleted_files: tuple[RuntimeFilePath, ...]
    file_delete_status: DirectoryDeleteFileStatus
    error: str | None = None

    @classmethod
    def complete(cls) -> "DirectoryDeleteAcceptedResult":
        """Return the response for a directory delete with no matching files."""
        return cls(deleted_files=(), file_delete_status="complete")

    @classmethod
    def pending(
        cls,
        *,
        deleted_files: Sequence[RuntimeFilePath],
    ) -> "DirectoryDeleteAcceptedResult":
        """Return the response after DB rows were deleted and cleanup was queued."""
        return cls(deleted_files=tuple(deleted_files), file_delete_status="pending")

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
        """Serialize to the current Basic Memory directory-delete response contract."""
        payload: dict[str, object] = {
            "total_files": len(self.deleted_files),
            "successful_deletes": len(self.deleted_files),
            "failed_deletes": 0,
            "deleted_files": list(self.deleted_files),
            "errors": [],
            "file_delete_status": self.file_delete_status,
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


class DirectoryFileDeleteEnqueuer(Protocol):
    """Capability that queues cleanup for one accepted directory-delete file."""

    async def enqueue_directory_file_delete(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> None: ...


async def enqueue_directory_file_delete_jobs(
    *,
    tenant_id: TenantId,
    project_id: ProjectId,
    files: Sequence[RuntimeDirectoryFileSnapshot],
    enqueuer: DirectoryFileDeleteEnqueuer,
) -> None:
    """Queue guarded cleanup jobs for files accepted by a directory delete."""
    for file_snapshot in files:
        await enqueuer.enqueue_directory_file_delete(
            plan_note_file_delete_job_request(
                tenant_id=tenant_id,
                file_delete=file_snapshot.to_pending_note_file_delete(
                    project_id=project_id,
                ),
            )
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

    await store.delete_directory_entities(
        session,
        entity_ids=[snapshot.entity_id for snapshot in file_snapshots],
    )
    return DirectoryDeleteAcceptance(project_id=project_id, files=file_snapshots)


async def finish_directory_delete_acceptance(
    *,
    request: DirectoryDeleteAcceptanceRequest,
    accepted: DirectoryDeleteAcceptance,
    enqueuer: DirectoryFileDeleteEnqueuer,
) -> DirectoryDeleteAcceptedResult:
    """Queue cleanup for an accepted directory delete after the DB commit."""
    if not accepted.files:
        return DirectoryDeleteAcceptedResult.complete()

    try:
        await enqueue_directory_file_delete_jobs(
            tenant_id=request.tenant_id,
            project_id=accepted.project_id,
            files=accepted.files,
            enqueuer=enqueuer,
        )
    except DirectoryFileDeleteEnqueueError as exc:
        return DirectoryDeleteAcceptedResult.failed(
            deleted_files=accepted.deleted_files,
            error=str(exc),
        )

    return DirectoryDeleteAcceptedResult.pending(deleted_files=accepted.deleted_files)


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
