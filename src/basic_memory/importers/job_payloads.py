"""Worker payloads and typed results for portable import runtime boundaries."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel

from basic_memory.runtime import (
    JobEntrypoint,
    ProjectExternalId,
    ProjectId,
    ProjectName,
    ProjectPath,
    ProjectPermalink,
    ProjectRuntimeReference,
    ProjectRuntimeSource,
    RuntimeQueuedWorkflowMetadata,
    RuntimeJobId,
    RuntimeJobRequest,
    RuntimeWorkflowAttemptTransportMetadata,
    RuntimeWorkflowBroker,
    RuntimeWorkflowMetadataPatch,
    RuntimeWorkflowPhase,
    RuntimeWorkflowProgress,
    RuntimeWorkflowTransport,
    StorageKey,
    TenantId,
    WorkflowId,
)

IMPORT_DATA_ENTRYPOINT = "import_data"
type ImportKind = Literal["claude", "chatgpt", "memory-json", "project-zip"]


@dataclass(frozen=True, slots=True)
class ImportDataWorkflowQueued:
    """Portable queued metadata for an import workflow handoff."""

    metadata: dict[str, object]
    queued_event_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class ImportDataWorkflowStart:
    """Portable running metadata for a claimed import workflow attempt."""

    phase: RuntimeWorkflowPhase
    progress: RuntimeWorkflowProgress
    metadata_patch: RuntimeWorkflowMetadataPatch


@dataclass(frozen=True, slots=True)
class ImportDataResultPayload(Mapping[str, object]):
    """Validated import result fields returned by importer/API boundaries."""

    values: Mapping[str, object]

    def __post_init__(self) -> None:
        copied: dict[str, object] = {}
        for key, value in self.values.items():
            if not isinstance(key, str):
                raise RuntimeError("Import API returned a response with a non-string key")
            copied[key] = value
        object.__setattr__(self, "values", MappingProxyType(copied))

    @classmethod
    def from_mapping(cls, values: Mapping[object, object]) -> Self:
        """Validate known mapping-like result data before internal handoff."""
        copied: dict[str, object] = {}
        for key, value in values.items():
            if not isinstance(key, str):
                raise RuntimeError("Import API returned a response with a non-string key")
            copied[key] = value
        return cls(values=copied)

    @classmethod
    def from_response_body(cls, response_body: object) -> Self:
        """Validate an untyped import API JSON response body."""
        if not isinstance(response_body, Mapping):
            raise RuntimeError("Import API returned a non-object response")
        copied: dict[str, object] = {}
        for key, value in response_body.items():
            if not isinstance(key, str):
                raise RuntimeError("Import API returned a response with a non-string key")
            copied[key] = value
        return cls(values=copied)

    def __getitem__(self, key: str) -> object:
        return self.values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    @property
    def succeeded(self) -> bool:
        """Return the import success flag with fail-fast shape checking."""
        success = self.values.get("success", True)
        if not isinstance(success, bool):
            raise RuntimeError("Import API returned a non-boolean success value")
        return success

    @property
    def error_message(self) -> str:
        """Return the import error message with fail-fast shape checking."""
        error_message = self.values.get("error_message", "Import failed")
        if not isinstance(error_message, str):
            raise RuntimeError("Import API returned a non-string error_message value")
        return error_message

    def as_dict(self) -> dict[str, object]:
        """Return a mutable workflow/admin payload copy."""
        return dict(self.values)

    def progress_metadata_patch(self) -> dict[str, object]:
        """Return the workflow metadata patch stored after import work finishes."""
        return {"result": self.as_dict()}


@dataclass(frozen=True, slots=True)
class ImportDataResult:
    """Summary of import work and downstream indexing."""

    result: ImportDataResultPayload
    index_job_id: str

    def result_payload(self) -> dict[str, object]:
        """Return a mutable workflow/admin payload copy."""
        return self.result.as_dict()

    def workflow_result(self) -> dict[str, object]:
        """Return the durable workflow result payload."""
        return self.result_payload()

    def completion_metadata_patch(self) -> dict[str, object]:
        """Return the workflow metadata patch for the downstream index workflow."""
        return {"index_job_id": self.index_job_id}


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


def build_import_data_job_request(
    *,
    payload: ImportDataPayload,
    entrypoint: JobEntrypoint,
    headers: Mapping[str, str] | None = None,
) -> RuntimeJobRequest:
    """Build the queue-neutral import job request from the validated payload."""
    return RuntimeJobRequest(
        entrypoint=entrypoint,
        payload=payload.model_dump_json().encode("utf-8"),
        headers=payload.routing_headers(headers),
    )


def build_import_data_workflow_queued(
    *,
    payload: ImportDataPayload,
    transport_broker: RuntimeWorkflowBroker,
    transport_entrypoint: JobEntrypoint,
) -> ImportDataWorkflowQueued:
    """Build queued workflow metadata before the import worker starts."""
    queued_metadata = RuntimeQueuedWorkflowMetadata(
        workflow_id=payload.workflow_id,
        progress="queued for import",
        payload=payload.workflow_payload_metadata(),
        transport=RuntimeWorkflowTransport(
            broker=transport_broker,
            entrypoint=transport_entrypoint,
        ),
    )

    return ImportDataWorkflowQueued(
        metadata=queued_metadata.workflow_metadata(),
        queued_event_data={
            "entrypoint": transport_entrypoint,
            "phase": "queued",
            "progress": "queued for import",
            "import_type": payload.import_type,
            **payload.project_reference().workflow_metadata(),
        },
    )


def build_import_data_workflow_start(
    *,
    payload: ImportDataPayload,
    pgq_job_id: RuntimeJobId | None,
    transport_entrypoint: JobEntrypoint,
) -> ImportDataWorkflowStart:
    """Build running workflow metadata for a claimed import worker job."""
    return ImportDataWorkflowStart(
        phase="running",
        progress=f"importing {payload.import_type}",
        metadata_patch=RuntimeWorkflowAttemptTransportMetadata.pgq(
            entrypoint=transport_entrypoint,
            pgq_job_id=pgq_job_id,
        ).metadata_patch(),
    )
