"""Pydantic worker payloads for portable import runtime boundaries."""

from collections.abc import Mapping
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel

from basic_memory.runtime import (
    ProjectExternalId,
    ProjectId,
    ProjectName,
    ProjectPath,
    ProjectPermalink,
    ProjectRuntimeReference,
    ProjectRuntimeSource,
    StorageKey,
    TenantId,
    WorkflowId,
)

IMPORT_DATA_ENTRYPOINT = "import_data"
type ImportKind = Literal["claude", "chatgpt", "memory-json", "project-zip"]


class ImportDataPayload(BaseModel):
    """Serialized worker payload for importing one upload into one project."""

    tenant_id: TenantId
    import_type: ImportKind
    s3_input_key: StorageKey
    destination_folder: str
    project_id: ProjectId
    project_name: ProjectName | None = None
    project_permalink: ProjectPermalink | None = None
    project_external_id: ProjectExternalId
    project_path: ProjectPath
    workflow_id: WorkflowId

    @classmethod
    def from_project(
        cls,
        *,
        tenant_id: UUID,
        import_type: ImportKind,
        s3_input_key: StorageKey,
        destination_folder: str,
        project: ProjectRuntimeSource,
        workflow_id: UUID,
    ) -> Self:
        """Build an import payload from the minimal runtime project shape."""
        return cls.from_project_reference(
            tenant_id=tenant_id,
            import_type=import_type,
            s3_input_key=s3_input_key,
            destination_folder=destination_folder,
            project=ProjectRuntimeReference.from_project(project),
            workflow_id=workflow_id,
        )

    @classmethod
    def from_project_reference(
        cls,
        *,
        tenant_id: UUID,
        import_type: ImportKind,
        s3_input_key: StorageKey,
        destination_folder: str,
        project: ProjectRuntimeReference,
        workflow_id: UUID,
    ) -> Self:
        """Build an import payload from a stable runtime project reference."""
        return cls(
            tenant_id=tenant_id,
            import_type=import_type,
            s3_input_key=s3_input_key,
            destination_folder=destination_folder,
            project_id=project.project_id,
            project_name=project.project_name,
            project_permalink=project.project_permalink,
            project_external_id=project.project_external_id,
            project_path=project.project_path,
            workflow_id=workflow_id,
        )

    def project_reference(self) -> ProjectRuntimeReference:
        """Return the project identity carried by this queued import payload."""
        return ProjectRuntimeReference(
            project_id=self.project_id,
            project_external_id=self.project_external_id,
            project_name=self.project_name,
            project_permalink=self.project_permalink,
            project_path=self.project_path,
        )

    def workflow_payload_metadata(self) -> dict[str, object]:
        """Serialize the existing workflow payload metadata shape."""
        return {
            "tenant_id": str(self.tenant_id),
            "import_type": self.import_type,
            "s3_input_key": self.s3_input_key,
            "destination_folder": self.destination_folder,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_permalink": self.project_permalink,
            "project_external_id": self.project_external_id,
            "project_path": self.project_path,
        }

    def routing_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        """Return queue routing headers for this import job."""
        routing_headers = dict(headers or {})
        routing_headers.update(
            {
                "tenant_id": str(self.tenant_id),
                "project_id": str(self.project_id),
                "project_path": self.project_path,
                "workflow_id": str(self.workflow_id),
            }
        )
        return routing_headers
