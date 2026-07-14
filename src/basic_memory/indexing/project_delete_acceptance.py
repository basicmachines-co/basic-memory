"""Portable accepted-response values for project delete requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Self

from basic_memory.runtime.jobs import RuntimeJobId, RuntimeProjectDeleteJobRequest
from basic_memory.schemas.project_info import ProjectItem

type ProjectDeleteAcceptedStatus = Literal["success"]
type ProjectDeleteAcceptedDeletionStatus = Literal["pending"]
type ProjectDeleteAcceptedFileStatus = Literal["pending", "skipped"]


@dataclass(frozen=True, slots=True)
class ProjectDeleteAcceptedResult:
    """Accepted response for a project delete queued for background cleanup."""

    project_name: str
    job_id: RuntimeJobId
    file_delete_status: ProjectDeleteAcceptedFileStatus
    old_project: ProjectItem
    status: ProjectDeleteAcceptedStatus = "success"
    deletion_status: ProjectDeleteAcceptedDeletionStatus = "pending"
    background: bool = True

    @classmethod
    def queued(
        cls,
        *,
        request: RuntimeProjectDeleteJobRequest,
        job_id: RuntimeJobId,
        old_project: ProjectItem,
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
            # ProjectItem also carries cloud-hosting metadata (display_name,
            # is_private) that the accepted-delete response has never included;
            # serialize only the persisted project fields so bytes stay stable.
            "old_project": self.old_project.model_dump(
                include={"id", "external_id", "name", "path", "is_default"}
            ),
            "new_project": None,
        }
