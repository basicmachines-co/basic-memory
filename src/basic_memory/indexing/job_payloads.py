"""Pydantic boundary models for portable indexing worker payloads."""

from collections.abc import Mapping
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field

from basic_memory.indexing.embedding_index_planning import (
    EmbeddingIndexBatchJobRequest,
    EmbeddingIndexJobRequest,
    EmbeddingIndexTarget,
)
from basic_memory.indexing.index_file_runtime import IndexFileRuntimeRequest
from basic_memory.indexing.models import (
    IndexFileEmbeddingJobContext,
    IndexFileJobResult,
    IndexFileJobStatus,
    IndexFileNoteLiveUpdateContext,
)
from basic_memory.indexing.project_index_progress import ObservedObjectIndexCompletionContext
from basic_memory.indexing.relation_resolution import IndexFileRelationResolutionContext
from basic_memory.indexing.relation_resolution import ResolveRelationsJobRequest
from basic_memory.runtime import (
    JobEntrypoint,
    ProjectRuntimeReference,
    RuntimeIndexFileBatchJobRequest,
    RuntimeJobRequest,
    RuntimeObservedIndexFile,
    RuntimeProjectDeleteJobRequest,
    RuntimeProjectIndexJobRequest,
    RuntimeStorageFileIndexContext,
    RuntimeStorageFileIndexJobIdentity,
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectObservation,
    runtime_job_request_from_source,
)

INDEX_FILE_ENTRYPOINT: JobEntrypoint = "index_file"
INDEX_FILE_BATCH_ENTRYPOINT: JobEntrypoint = "index_file_batch"
INDEX_PROJECT_ENTRYPOINT: JobEntrypoint = "index_project"
DELETE_PROJECT_ENTRYPOINT: JobEntrypoint = "delete_project"
INDEX_EMBEDDINGS_ENTRYPOINT: JobEntrypoint = "index_embeddings"
INDEX_EMBEDDINGS_BATCH_ENTRYPOINT: JobEntrypoint = "index_embeddings_batch"
RESOLVE_RELATIONS_ENTRYPOINT: JobEntrypoint = "resolve_relations"


class IndexFileObjectMetadataPayload(BaseModel):
    """Observed storage object metadata captured by a queue producer."""

    etag: str = Field(description="Storage ETag observed for the object.")
    size: int | None = Field(default=None, description="Object size observed for the object.")

    @classmethod
    def from_runtime_observation(cls, observation: RuntimeStorageObjectObservation) -> Self:
        """Validate a storage-neutral observation at a worker payload boundary."""
        return cls(etag=observation.etag, size=observation.size)

    def to_runtime_observation(self) -> RuntimeStorageObjectObservation:
        """Map validated queue metadata into the storage-neutral runtime value."""
        return RuntimeStorageObjectObservation(etag=self.etag, size=self.size)


class IndexFileJobPayload(BaseModel):
    """Serialized worker payload for indexing one project file."""

    tenant_id: UUID
    project_id: int
    project_external_id: str | None = None
    project_name: str | None = None
    project_path: str
    file_path: str
    mode: RuntimeStorageFileIndexMode = RuntimeStorageFileIndexMode.observed_object
    object_metadata: IndexFileObjectMetadataPayload | None = None
    index_embeddings: bool = True
    workflow_id: UUID | None = None

    @classmethod
    def from_runtime_request(cls, request: IndexFileRuntimeRequest) -> Self:
        """Validate a storage-neutral index-file request at a worker payload boundary."""
        object_metadata = (
            IndexFileObjectMetadataPayload.from_runtime_observation(request.object_observation)
            if request.object_observation is not None
            else None
        )
        return cls(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            project_external_id=request.project_external_id,
            project_name=request.project_name,
            project_path=request.project_path,
            file_path=request.file_path,
            mode=request.mode,
            object_metadata=object_metadata,
            index_embeddings=request.index_embeddings,
            workflow_id=request.workflow_id,
        )

    def to_runtime_request(self) -> IndexFileRuntimeRequest:
        """Map the validated worker payload into the storage-neutral index request."""
        return IndexFileRuntimeRequest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            project_external_id=self.project_external_id,
            project_name=self.project_name,
            project_path=self.project_path,
            file_path=self.file_path,
            mode=self.mode,
            object_observation=(
                self.object_metadata.to_runtime_observation()
                if self.object_metadata is not None
                else None
            ),
            index_embeddings=self.index_embeddings,
            workflow_id=self.workflow_id,
        )

    def runtime_job_identity(self) -> RuntimeStorageFileIndexJobIdentity:
        return self.to_runtime_request().storage_job_identity()

    def runtime_index_context(self) -> RuntimeStorageFileIndexContext:
        return self.to_runtime_request().storage_index_context()

    def note_live_update_context(self) -> IndexFileNoteLiveUpdateContext:
        return self.to_runtime_request().note_live_update_context()

    def observed_object_completion_context(self) -> ObservedObjectIndexCompletionContext:
        return self.to_runtime_request().observed_object_completion_context()

    def relation_resolution_context(
        self,
        status: IndexFileJobStatus,
    ) -> IndexFileRelationResolutionContext:
        return self.to_runtime_request().relation_resolution_context(status)

    def embedding_job_context(
        self,
        result: IndexFileJobResult,
    ) -> IndexFileEmbeddingJobContext:
        return self.to_runtime_request().embedding_job_context(result)

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest:
        """Build the concrete runtime queue request for file indexing."""
        runtime_request = self.to_runtime_request()
        runtime_request.storage_index_context().require_enqueue_context()
        return runtime_job_request_from_source(
            runtime_request,
            entrypoint=INDEX_FILE_ENTRYPOINT,
            payload=self.model_dump_json().encode("utf-8"),
            headers=headers,
        )


class ObservedIndexFilePayload(BaseModel):
    """Serialized storage metadata for one file-index batch target."""

    path: str
    checksum: str | None = None
    size: int | None = None

    @classmethod
    def from_runtime_observed_file(cls, observed_file: RuntimeObservedIndexFile) -> Self:
        """Validate runtime observed metadata at a worker boundary."""
        return cls(
            path=observed_file.path,
            checksum=observed_file.checksum,
            size=observed_file.size,
        )

    def to_runtime_observed_file(self) -> RuntimeObservedIndexFile:
        """Map queued observed metadata back to the runtime value."""
        return RuntimeObservedIndexFile(
            path=self.path,
            checksum=self.checksum,
            size=self.size,
        )


class IndexFileBatchJobPayload(BaseModel):
    """Serialized worker payload for indexing a project file batch."""

    tenant_id: UUID
    project_id: int
    project_external_id: str
    project_path: str
    file_paths: list[str] = Field(default_factory=list)
    observed_files: list[ObservedIndexFilePayload] = Field(default_factory=list)
    batch_index: int
    batch_count: int
    workflow_id: UUID
    index_embeddings: bool = True
    force_full: bool = False

    def targets(self) -> list[ObservedIndexFilePayload]:
        """Return the file targets this batch should evaluate at runtime."""
        if self.observed_files:
            return list(self.observed_files)
        return [ObservedIndexFilePayload(path=file_path) for file_path in self.file_paths]

    def target_paths(self) -> list[str]:
        """Return target paths in their batch order."""
        return [target.path for target in self.targets()]

    @classmethod
    def from_runtime_request(cls, request: RuntimeIndexFileBatchJobRequest) -> Self:
        """Validate the runtime file-batch request at a worker boundary."""
        return cls(
            tenant_id=request.tenant_id,
            project_id=request.project.project_id,
            project_external_id=request.project.project_external_id,
            project_path=request.project.project_path,
            file_paths=list(request.file_paths),
            observed_files=[
                ObservedIndexFilePayload.from_runtime_observed_file(observed_file)
                for observed_file in request.observed_files
            ],
            batch_index=request.batch_index,
            batch_count=request.batch_count,
            workflow_id=request.workflow_id,
            index_embeddings=request.index_embeddings,
            force_full=request.force_full,
        )

    def to_runtime_request(self) -> RuntimeIndexFileBatchJobRequest:
        """Map the validated worker payload back to the runtime request."""
        return RuntimeIndexFileBatchJobRequest(
            tenant_id=self.tenant_id,
            project=ProjectRuntimeReference(
                project_id=self.project_id,
                project_external_id=self.project_external_id,
                project_path=self.project_path,
            ),
            workflow_id=self.workflow_id,
            batch_index=self.batch_index,
            batch_count=self.batch_count,
            file_paths=tuple(self.file_paths),
            observed_files=tuple(
                observed_file.to_runtime_observed_file() for observed_file in self.observed_files
            ),
            index_embeddings=self.index_embeddings,
            force_full=self.force_full,
        )

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest:
        """Build the concrete runtime queue request for file-batch indexing."""
        return runtime_job_request_from_source(
            self.to_runtime_request(),
            entrypoint=INDEX_FILE_BATCH_ENTRYPOINT,
            payload=self.model_dump_json().encode("utf-8"),
            headers=headers,
        )


class ProjectIndexJobPayload(BaseModel):
    """Serialized worker payload for coordinating a project-wide index."""

    tenant_id: UUID
    project_id: int
    project_external_id: str
    project_name: str | None = None
    project_permalink: str | None = None
    project_path: str
    workflow_id: UUID
    force_full: bool = False
    search: bool = True
    embeddings: bool = True

    @classmethod
    def from_runtime_request(cls, request: RuntimeProjectIndexJobRequest) -> Self:
        """Validate the runtime project-index request at a worker boundary."""
        return cls(
            tenant_id=request.tenant_id,
            project_id=request.project.project_id,
            project_external_id=request.project.project_external_id,
            project_name=request.project.project_name,
            project_permalink=request.project.project_permalink,
            project_path=request.project.project_path,
            workflow_id=request.workflow_id,
            force_full=request.force_full,
            search=request.search,
            embeddings=request.embeddings,
        )

    def project_reference(self) -> ProjectRuntimeReference:
        """Return the typed project identity carried by this queued payload."""
        return ProjectRuntimeReference(
            project_id=self.project_id,
            project_external_id=self.project_external_id,
            project_name=self.project_name,
            project_permalink=self.project_permalink,
            project_path=self.project_path,
        )

    def to_runtime_request(self) -> RuntimeProjectIndexJobRequest:
        """Map the validated worker payload back to the runtime request."""
        return RuntimeProjectIndexJobRequest(
            tenant_id=self.tenant_id,
            project=self.project_reference(),
            workflow_id=self.workflow_id,
            force_full=self.force_full,
            search=self.search,
            embeddings=self.embeddings,
        )

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest:
        """Build the concrete runtime queue request for project-index coordination."""
        return runtime_job_request_from_source(
            self.to_runtime_request(),
            entrypoint=INDEX_PROJECT_ENTRYPOINT,
            payload=self.model_dump_json().encode("utf-8"),
            headers=headers,
        )


class ProjectDeleteJobPayload(BaseModel):
    """Serialized worker payload for hard-deleting a soft-deleted project."""

    tenant_id: UUID
    project_id: int
    project_external_id: str
    project_name: str
    project_path: str
    delete_notes: bool = True

    @classmethod
    def from_runtime_request(cls, request: RuntimeProjectDeleteJobRequest) -> Self:
        """Validate the runtime project-delete request at a worker boundary."""
        return cls(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            project_external_id=request.project_external_id,
            project_name=request.project_name,
            project_path=request.project_path,
            delete_notes=request.delete_notes,
        )

    def to_runtime_request(self) -> RuntimeProjectDeleteJobRequest:
        """Map the validated worker payload back to the runtime request."""
        return RuntimeProjectDeleteJobRequest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            project_external_id=self.project_external_id,
            project_name=self.project_name,
            project_path=self.project_path,
            delete_notes=self.delete_notes,
        )

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest:
        """Build the concrete runtime queue request for project deletion."""
        return runtime_job_request_from_source(
            self.to_runtime_request(),
            entrypoint=DELETE_PROJECT_ENTRYPOINT,
            payload=self.model_dump_json().encode("utf-8"),
            headers=headers,
        )


class EmbeddingIndexJobPayload(BaseModel):
    """Serialized worker payload for indexing one entity's embeddings."""

    tenant_id: UUID
    project_id: int
    entity_id: int
    entity_checksum: str | None = None

    @classmethod
    def from_runtime_request(cls, request: EmbeddingIndexJobRequest) -> Self:
        """Validate the runtime embedding request at a worker boundary."""
        return cls(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            entity_id=request.entity_id,
            entity_checksum=request.entity_checksum,
        )

    def to_runtime_request(self) -> EmbeddingIndexJobRequest:
        """Map the validated worker payload back to the runtime request."""
        return EmbeddingIndexJobRequest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            entity_id=self.entity_id,
            entity_checksum=self.entity_checksum,
        )

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest:
        """Build the concrete runtime queue request for embedding indexing."""
        return runtime_job_request_from_source(
            self.to_runtime_request(),
            entrypoint=INDEX_EMBEDDINGS_ENTRYPOINT,
            payload=self.model_dump_json().encode("utf-8"),
            headers=headers,
        )


class EmbeddingIndexTargetPayload(BaseModel):
    """Serialized entity version that may need semantic embeddings indexed."""

    entity_id: int
    entity_checksum: str

    @classmethod
    def from_embedding_target(cls, target: EmbeddingIndexTarget) -> Self:
        """Validate a runtime embedding target at a worker boundary."""
        return cls(
            entity_id=target.entity_id,
            entity_checksum=target.entity_checksum,
        )

    def to_embedding_target(self) -> EmbeddingIndexTarget:
        """Map queued entity metadata back to the runtime planner value."""
        return EmbeddingIndexTarget(
            entity_id=self.entity_id,
            entity_checksum=self.entity_checksum,
        )


class EmbeddingIndexBatchJobPayload(BaseModel):
    """Serialized worker payload for indexing embeddings for many entities."""

    tenant_id: UUID
    project_id: int
    project_path: str
    entities: list[EmbeddingIndexTargetPayload] = Field(default_factory=list)
    workflow_id: UUID | None = None

    @classmethod
    def from_runtime_request(cls, request: EmbeddingIndexBatchJobRequest) -> Self:
        """Validate the runtime embedding batch request at a worker boundary."""
        return cls(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            project_path=request.project_path,
            entities=[
                EmbeddingIndexTargetPayload.from_embedding_target(target)
                for target in request.entities
            ],
            workflow_id=request.workflow_id,
        )

    def targets(self) -> list[EmbeddingIndexTarget]:
        """Return planner targets in payload order."""
        return [entity.to_embedding_target() for entity in self.entities]

    def to_runtime_request(self) -> EmbeddingIndexBatchJobRequest:
        """Map the validated worker payload back to the runtime request."""
        return EmbeddingIndexBatchJobRequest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            project_path=self.project_path,
            entities=tuple(self.targets()),
            workflow_id=self.workflow_id,
        )

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest:
        """Build the concrete runtime queue request for embedding-batch indexing."""
        return runtime_job_request_from_source(
            self.to_runtime_request(),
            entrypoint=INDEX_EMBEDDINGS_BATCH_ENTRYPOINT,
            payload=self.model_dump_json().encode("utf-8"),
            headers=headers,
        )


class ResolveRelationsJobPayload(BaseModel):
    """Serialized worker payload for resolving one project's relations."""

    tenant_id: UUID
    project_id: int
    project_path: str

    @classmethod
    def from_runtime_request(cls, request: ResolveRelationsJobRequest) -> Self:
        """Validate the runtime relation-resolution request at a worker boundary."""
        return cls(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            project_path=request.project_path,
        )

    def to_runtime_request(self) -> ResolveRelationsJobRequest:
        """Map the validated worker payload back to the runtime request."""
        return ResolveRelationsJobRequest(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            project_path=self.project_path,
        )

    def runtime_job_request(
        self,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> RuntimeJobRequest:
        """Build the concrete runtime queue request for relation resolution."""
        runtime_request = self.to_runtime_request()
        return runtime_job_request_from_source(
            runtime_request,
            entrypoint=RESOLVE_RELATIONS_ENTRYPOINT,
            payload=self.model_dump_json().encode("utf-8"),
            headers=headers,
            execute_after=runtime_request.execute_after,
        )
