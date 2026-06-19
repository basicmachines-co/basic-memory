"""Portable accepted-response values for project delete requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Self

from basic_memory.runtime import RuntimeJobId, RuntimeProjectDeleteJobRequest

type ProjectDeleteAcceptedStatus = Literal["success"]
type ProjectDeleteAcceptedDeletionStatus = Literal["pending"]
type ProjectDeleteAcceptedFileStatus = Literal["pending", "skipped"]


class ProjectDeleteAcceptedProjectSource(Protocol):
    """Minimal project row shape needed for accepted-delete responses."""

    id: int
    external_id: str
    name: str
    path: str
    is_default: bool | None


@dataclass(frozen=True, slots=True)
class ProjectDeleteAcceptedProject:
    """Project snapshot returned as ``old_project`` after a soft delete."""

    id: int
    external_id: str
    name: str
    path: str
    is_default: bool

    @classmethod
    def from_source(cls, source: ProjectDeleteAcceptedProjectSource) -> Self:
        """Snapshot the Basic Memory project fields exposed by delete responses."""
        return cls(
            id=source.id,
            external_id=source.external_id,
            name=source.name,
            path=source.path,
            is_default=source.is_default or False,
        )

    def to_response_payload(self) -> dict[str, object]:
        """Serialize to the existing Basic Memory project response shape."""
        return {
            "id": self.id,
            "external_id": self.external_id,
            "name": self.name,
            "path": self.path,
            "is_default": self.is_default,
        }


@dataclass(frozen=True, slots=True)
class ProjectDeleteAcceptedResult:
    """Accepted response for a project delete queued for background cleanup."""

    project_name: str
    job_id: RuntimeJobId
    file_delete_status: ProjectDeleteAcceptedFileStatus
    old_project: ProjectDeleteAcceptedProject
    status: ProjectDeleteAcceptedStatus = "success"
    deletion_status: ProjectDeleteAcceptedDeletionStatus = "pending"
    background: bool = True

    @classmethod
    def queued(
        cls,
        *,
        request: RuntimeProjectDeleteJobRequest,
        job_id: RuntimeJobId,
        old_project: ProjectDeleteAcceptedProject,
    ) -> Self:
        """Build the accepted response for a queued project cleanup job."""
        return cls(
            project_name=request.project_name,
            job_id=job_id,
            file_delete_status="pending" if request.delete_notes else "skipped",
            old_project=old_project,
        )

    def to_response_payload(self) -> dict[str, object]:
        """Serialize to the existing HTTP response contract."""
        return {
            "message": f"Project '{self.project_name}' deletion queued",
            "status": self.status,
            "deletion_status": self.deletion_status,
            "file_delete_status": self.file_delete_status,
            "background": self.background,
            "job_id": str(self.job_id),
            "old_project": self.old_project.to_response_payload(),
            "new_project": None,
        }
