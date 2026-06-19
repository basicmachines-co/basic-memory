"""Portable project-index workflow request values."""

from dataclasses import dataclass
from typing import Protocol, Self

from basic_memory.runtime import (
    ProjectExternalId,
    ProjectId,
    ProjectName,
    ProjectPath,
    ProjectPermalink,
    ProjectRuntimeReference,
    TenantId,
    WorkflowId,
)


class ProjectIndexWorkflowSource(Protocol):
    """Minimal source shape for project-index workflow requests."""

    tenant_id: TenantId
    project_id: ProjectId
    project_external_id: ProjectExternalId
    project_name: ProjectName | None
    project_permalink: ProjectPermalink | None
    project_path: ProjectPath
    workflow_id: WorkflowId
    force_full: bool
    search: bool
    embeddings: bool


@dataclass(frozen=True, slots=True)
class ProjectIndexWorkflowRequest:
    """Project-index workflow identity and mode flags."""

    tenant_id: TenantId
    workflow_id: WorkflowId
    project: ProjectRuntimeReference
    force_full: bool
    search: bool
    embeddings: bool

    @classmethod
    def from_source(cls, source: ProjectIndexWorkflowSource) -> Self:
        """Build a workflow request from queue payloads or boundary models."""
        project_external_id = str(source.project_external_id).strip()
        if not project_external_id:
            raise ValueError(f"Project {source.project_id} is missing external_id")

        project_path = str(source.project_path).strip()
        if not project_path:
            raise ValueError(f"Project {source.project_id} is missing path")

        project_name = str(source.project_name).strip() if source.project_name else None
        project_permalink = (
            str(source.project_permalink).strip() if source.project_permalink else None
        )
        return cls(
            tenant_id=source.tenant_id,
            workflow_id=source.workflow_id,
            project=ProjectRuntimeReference(
                project_id=source.project_id,
                project_external_id=project_external_id,
                project_name=project_name,
                project_permalink=project_permalink,
                project_path=project_path,
            ),
            force_full=source.force_full,
            search=source.search,
            embeddings=source.embeddings,
        )

    def workflow_payload_metadata(self) -> dict[str, object]:
        """Serialize to the existing workflow metadata payload shape."""
        return {
            "tenant_id": str(self.tenant_id),
            **self.project.workflow_metadata(),
            "force_full": self.force_full,
            "search": self.search,
            "embeddings": self.embeddings,
        }
