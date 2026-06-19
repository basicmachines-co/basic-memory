"""Portable directory-delete result and cleanup enqueue orchestration."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from basic_memory.runtime import (
    RuntimeDirectoryFileSnapshot,
    RuntimeFilePath,
    RuntimeNoteFileDeleteJobRequest,
    TenantId,
    ProjectId,
    plan_note_file_delete_job_request,
)

type DirectoryDeleteFileStatus = Literal["complete", "pending", "failed"]


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
