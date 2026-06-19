"""Pydantic boundary models for portable indexing worker payloads."""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field

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
    RuntimeStorageFileIndexContext,
    RuntimeStorageFileIndexJobIdentity,
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectObservation,
)


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
