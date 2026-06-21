"""Local filesystem runtime adapters for event-based indexing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from loguru import logger

from basic_memory.index.inline_operations import (
    InlineStorageEventIndexRuntime,
    InlineStorageEventOperationProcessor,
)
from basic_memory.index.local_dependencies import (
    DefaultLocalIndexProjectDependencyProvider,
    LocalIndexProjectDependencyProvider,
)
from basic_memory.index.storage_events import (
    StorageEventIndexRuntime,
    StorageEventOperationProcessorFactory,
    StorageEventProjectResolver,
)
from basic_memory.indexing import (
    ExternalFileDeleteResult,
    FileIndexChecker,
    IndexFileJobResult,
    IndexFileObjectMetadata,
    OrphanSearchIndex,
    RepositoryCurrentMaterializedNoteSource,
    RepositoryIndexedFileChecksumSource,
    StorageCurrentFileChecksumSource,
)
from basic_memory.indexing.external_file_delete_runner import (
    RepositoryExternalFileDeleteEntities,
)
from basic_memory.models import Entity, Project
from basic_memory.runtime import (
    ProjectPath,
    ProjectRuntimeReference,
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeStorageEventOperation,
    TenantId,
)
from basic_memory.services import FileService

LOCAL_EVENT_INDEX_TENANT_ID: TenantId = UUID("00000000-0000-0000-0000-000000000000")


@dataclass(frozen=True, slots=True)
class LocalStorageEventProjectResolver(StorageEventProjectResolver):
    """Resolve the watcher project prefix to the current local project."""

    project: ProjectRuntimeReference
    project_prefix: ProjectPath

    async def resolve_project(self, project_path: ProjectPath) -> ProjectRuntimeReference | None:
        if project_path != self.project_prefix:
            return None
        return self.project


@dataclass(frozen=True, slots=True)
class LocalStorageFileMetadataSource:
    """Load local file metadata for event-index freshness checks."""

    file_service: FileService

    async def load_current_file_metadata(
        self,
        file_path: RuntimeFilePath,
    ) -> IndexFileObjectMetadata | None:
        if not await self.file_service.exists(file_path):
            return None
        return IndexFileObjectMetadata(
            checksum=await self.file_service.compute_checksum(file_path),
            metadata={},
        )

    async def load_current_file_checksum(
        self,
        file_path: RuntimeFilePath,
    ) -> RuntimeFileChecksum | None:
        current_metadata = await self.load_current_file_metadata(file_path)
        return current_metadata.checksum if current_metadata is not None else None


@dataclass(frozen=True, slots=True)
class LocalExternalFileDeleteObjects:
    """Adapt local filesystem state to stale-delete checks."""

    file_service: FileService

    async def file_exists(self, file_path: RuntimeFilePath) -> bool:
        return await self.file_service.exists(file_path)


@dataclass(frozen=True, slots=True)
class LocalInlineStorageEventResultRecorder:
    """Log inline local event-index results and clean search state after deletes."""

    search_service: OrphanSearchIndex[Entity]

    async def index_file_completed(
        self,
        operation: RuntimeStorageEventOperation,
        result: IndexFileJobResult,
    ) -> None:
        logger.info(
            "Local event-index file result",
            file_path=operation.relative_path,
            status=result.status,
            reason=result.reason,
            entity_id=result.entity_id,
        )

    async def delete_file_completed(
        self,
        operation: RuntimeStorageEventOperation,
        result: ExternalFileDeleteResult,
    ) -> None:
        logger.info(
            "Local event-index delete result",
            file_path=operation.relative_path,
            action=result.plan.action,
            reason=result.plan.reason,
            entity_deleted=result.entity_deleted,
        )
        if not result.entity_deleted:
            return
        if not isinstance(result.deleted_entity, Entity):
            raise RuntimeError("Local external file delete returned an incomplete entity result")
        await self.search_service.handle_delete(result.deleted_entity)

    async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
        logger.debug(
            "Skipping local event-index storage event",
            file_path=operation.relative_path,
            reason=operation.skip_reason,
        )

    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        logger.warning(
            "Local event-index storage event failed",
            file_path=operation.relative_path,
            error=str(exc),
        )


@dataclass(frozen=True, slots=True)
class LocalStorageEventOperationProcessorFactory(StorageEventOperationProcessorFactory):
    """Create inline local operation processors for resolved watcher projects."""

    runtime: InlineStorageEventIndexRuntime

    def processor_for_project(
        self,
        project: ProjectRuntimeReference,
    ) -> InlineStorageEventOperationProcessor:
        if project.project_id != self.runtime.project.project_id:
            raise RuntimeError(
                "Local event-index processor received a project different from its runtime"
            )
        return InlineStorageEventOperationProcessor(self.runtime)


@dataclass(frozen=True, slots=True)
class LocalWatchEventIndexRuntimeFactory:
    """Build local event-index runtime dependencies for a watched project."""

    dependency_provider: LocalIndexProjectDependencyProvider = (
        DefaultLocalIndexProjectDependencyProvider()
    )
    tenant_id: TenantId = LOCAL_EVENT_INDEX_TENANT_ID
    index_embeddings: bool = True

    async def runtime_for_project(self, project: Project) -> StorageEventIndexRuntime:
        dependencies = await self.dependency_provider.dependencies_for_project(project)
        project_ref = ProjectRuntimeReference.from_project(project)
        project_prefix = local_project_prefix(project)
        metadata_source = LocalStorageFileMetadataSource(dependencies.file_service)
        checker = FileIndexChecker(
            indexed_checksum_source=RepositoryIndexedFileChecksumSource(
                session_maker=dependencies.session_maker,
                entity_repository=dependencies.entity_repository,
            ),
            current_checksum_source=StorageCurrentFileChecksumSource(
                metadata_source=metadata_source,
            ),
        )
        inline_runtime = InlineStorageEventIndexRuntime(
            tenant_id=self.tenant_id,
            project=project_ref,
            checker=checker,
            metadata_source=metadata_source,
            materialized_note_source=RepositoryCurrentMaterializedNoteSource(
                session_maker=dependencies.session_maker,
                entity_repository=dependencies.entity_repository,
            ),
            file_indexer=dependencies.file_indexer,
            delete_entities=RepositoryExternalFileDeleteEntities(
                session_maker=dependencies.session_maker,
                entity_repository=dependencies.entity_repository,
            ),
            delete_objects=LocalExternalFileDeleteObjects(dependencies.file_service),
            result_recorder=LocalInlineStorageEventResultRecorder(
                dependencies.search_service,
            ),
            index_embeddings=self.index_embeddings,
        )
        return StorageEventIndexRuntime(
            project_resolver=LocalStorageEventProjectResolver(
                project=project_ref,
                project_prefix=project_prefix,
            ),
            operation_processor_factory=LocalStorageEventOperationProcessorFactory(
                runtime=inline_runtime,
            ),
        )


def local_project_prefix(project: Project) -> ProjectPath:
    """Return the cloud-compatible project prefix for local watcher events."""
    return Path(project.path).expanduser().resolve().name
