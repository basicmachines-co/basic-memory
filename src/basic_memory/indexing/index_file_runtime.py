"""Typed runtime request for one file-index worker handoff."""

from __future__ import annotations

from dataclasses import dataclass

from basic_memory.indexing.models import (
    IndexFileEmbeddingJobContext,
    IndexFileJobResult,
    IndexFileJobStatus,
    IndexFileNoteLiveUpdateContext,
)
from basic_memory.indexing.project_index_progress import ObservedObjectIndexCompletionContext
from basic_memory.indexing.relation_resolution import IndexFileRelationResolutionContext
from basic_memory.runtime import (
    ProjectExternalId,
    ProjectName,
    ProjectPath,
    RuntimeFilePath,
    RuntimeStorageFileIndexContext,
    RuntimeStorageFileIndexJobIdentity,
    RuntimeStorageFileIndexMode,
    RuntimeStorageObjectObservation,
    TenantId,
    WorkflowId,
)


@dataclass(frozen=True, slots=True)
class IndexFileRuntimeRequest:
    """Storage-neutral request shape for indexing one project file."""

    tenant_id: TenantId
    project_id: int
    project_external_id: ProjectExternalId | None
    project_name: ProjectName | None
    project_path: ProjectPath
    file_path: RuntimeFilePath
    mode: RuntimeStorageFileIndexMode = RuntimeStorageFileIndexMode.observed_object
    object_observation: RuntimeStorageObjectObservation | None = None
    index_embeddings: bool = True
    workflow_id: WorkflowId | None = None

    def storage_job_identity(self) -> RuntimeStorageFileIndexJobIdentity:
        if (
            self.mode == RuntimeStorageFileIndexMode.observed_object
            and self.object_observation is not None
        ):
            return self.object_observation.to_file_index_job_identity(
                tenant_id=self.tenant_id,
                project_id=self.project_id,
                file_path=self.file_path,
                workflow_id=self.workflow_id,
            )

        return RuntimeStorageFileIndexJobIdentity(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            file_path=self.file_path,
            mode=self.mode,
            workflow_id=self.workflow_id,
        )

    def storage_index_context(self) -> RuntimeStorageFileIndexContext:
        return RuntimeStorageFileIndexContext(
            mode=self.mode,
            project_external_id=self.project_external_id,
            project_name=self.project_name,
            workflow_id=self.workflow_id,
        )

    def note_live_update_context(self) -> IndexFileNoteLiveUpdateContext:
        object_etag = self.object_observation.etag if self.object_observation is not None else None
        object_size = self.object_observation.size if self.object_observation is not None else None
        return IndexFileNoteLiveUpdateContext(
            tenant_id=self.tenant_id,
            project_external_id=self.project_external_id,
            project_name=self.project_name,
            file_path=self.file_path,
            mode=self.mode,
            workflow_id=self.workflow_id,
            object_etag=object_etag,
            object_size=object_size,
        )

    def observed_object_completion_context(self) -> ObservedObjectIndexCompletionContext:
        return ObservedObjectIndexCompletionContext(
            tenant_id=self.tenant_id,
            project_external_id=self.project_external_id,
            project_name=self.project_name,
            project_path=self.project_path,
            mode=self.mode,
            workflow_id=self.workflow_id,
        )

    def relation_resolution_context(
        self,
        status: IndexFileJobStatus,
    ) -> IndexFileRelationResolutionContext:
        return IndexFileRelationResolutionContext(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            project_path=self.project_path,
            workflow_id=self.workflow_id,
            status=status,
        )

    def embedding_job_context(
        self,
        result: IndexFileJobResult,
    ) -> IndexFileEmbeddingJobContext:
        return IndexFileEmbeddingJobContext(
            tenant_id=self.tenant_id,
            project_id=self.project_id,
            index_embeddings=self.index_embeddings,
            result=result,
        )
