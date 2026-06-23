"""Local filesystem runtime adapters for event-based indexing."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from loguru import logger

from basic_memory.index.inline_operations import (
    InlineStorageEventIndexRuntime,
    InlineStorageEventOperationProcessor,
)
from basic_memory.index.local_dependencies import (
    DefaultLocalIndexProjectDependencyProvider,
    LocalIndexProjectDependencyProvider,
    LocalIndexSearchService,
)
from basic_memory.index.local_moves import (
    LocalProjectIndexMoveContentUpdater,
    LocalWatchMoveProcessor,
)
from basic_memory.index.local_watch import LocalWatchStorageEventIndexRuntime
from basic_memory.index.local_watch import local_project_prefix
from basic_memory.index.storage_events import (
    StorageEventIndexRuntime,
    StorageEventOperationProcessorFactory,
    StorageEventProjectResolver,
)
from basic_memory.indexing import (
    ExternalFileDeleteResult,
    FileIndexChecker,
    IndexFileJobResult,
    IndexFileJobStatus,
    IndexFileRelationResolutionContext,
    IndexFileObjectMetadata,
    ProjectIndexMovedEntitySearchRefresher,
    RepositoryCurrentMaterializedNoteSource,
    RepositoryIndexedFileChecksumSource,
    RepositoryProjectIndexMaintenanceStore,
    RepositoryProjectIndexMovedEntitySearchRefresher,
    RepositoryRelationResolutionRuntime,
    RelationResolutionRuntime,
    StorageCurrentFileChecksumSource,
    StoreProjectIndexMaintenanceRunner,
    plan_index_file_relation_resolution,
    resolve_project_relations,
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
    """Log inline local event-index results and run note follow-ups.

    Runs the same post-index follow-ups the project-index path runs: forward
    reference resolution and, when enabled, semantic vector embedding. Without
    the embedding step, notes that arrive through the watcher (Obsidian, git
    pull, cloud sync) are full-text searchable but silently absent from semantic
    search until a manual reindex (#1016).
    """

    tenant_id: TenantId
    project: ProjectRuntimeReference
    search_service: LocalIndexSearchService
    relation_cleanup_search_refresher: ProjectIndexMovedEntitySearchRefresher
    relation_runtime: RelationResolutionRuntime
    index_embeddings: bool

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

        # --- Relation repair ---
        # Back-resolve forward references now that this file is indexed.
        relation_request = plan_index_file_relation_resolution(
            IndexFileRelationResolutionContext(
                tenant_id=self.tenant_id,
                project_id=self.project.project_id,
                project_path=self.project.project_path,
                workflow_id=None,
                status=result.status,
            )
        )
        if relation_request is not None:
            relation_result = await resolve_project_relations(self.relation_runtime)
            logger.info(
                "Local event-index relation repair completed",
                project_id=relation_request.project_id,
                project_path=relation_request.project_path,
                resolved=relation_result.resolved,
                remaining=relation_result.remaining,
                passes=relation_result.passes,
            )

        # --- Semantic embedding ---
        # Trigger: a file was (re)indexed and semantic embeddings are enabled.
        # Why: the watcher path indexes FTS + relations but, unlike the API
        #   write path and reindex, never embeds — so externally edited notes
        #   silently miss semantic search until a manual reindex (#1016).
        # Outcome: refresh this entity's vector chunks inline.
        if (
            self.index_embeddings
            and result.status == IndexFileJobStatus.processed
            and result.entity_id is not None
        ):
            await self.search_service.sync_entity_vectors_batch([result.entity_id])
            logger.info(
                "Local event-index embedding completed",
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
        await self.relation_cleanup_search_refresher.refresh_moved_entities(
            tuple(sorted(result.relation_cleanup_entity_ids)),
        )

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
    # Embedding requires semantic search to be configured, so default it off and
    # let runtime construction opt in via semantic_search_enabled (#1016).
    index_embeddings: bool = False
    move_batch_size: int = 100

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
        maintenance_store = RepositoryProjectIndexMaintenanceStore(
            session_maker=dependencies.session_maker,
            project_id=dependencies.project_id,
            move_content_updater=LocalProjectIndexMoveContentUpdater(
                entity_service=dependencies.entity_service,
                file_service=dependencies.file_service,
            ),
        )
        maintenance_runner = StoreProjectIndexMaintenanceRunner(
            move_store=maintenance_store,
            delete_store=maintenance_store,
        )
        moved_entity_search_refresher = RepositoryProjectIndexMovedEntitySearchRefresher(
            session_maker=dependencies.session_maker,
            entity_repository=dependencies.entity_repository,
            entity_indexer=dependencies.search_service,
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
                tenant_id=self.tenant_id,
                project=project_ref,
                search_service=dependencies.search_service,
                relation_cleanup_search_refresher=moved_entity_search_refresher,
                relation_runtime=RepositoryRelationResolutionRuntime(
                    session_maker=dependencies.session_maker,
                    relation_repository=dependencies.relation_repository,
                    entity_repository=dependencies.entity_repository,
                    link_resolver=dependencies.link_resolver,
                    entity_indexer=dependencies.search_service,
                ),
                index_embeddings=self.index_embeddings,
            ),
            index_embeddings=self.index_embeddings,
        )
        return LocalWatchStorageEventIndexRuntime(
            project_resolver=LocalStorageEventProjectResolver(
                project=project_ref,
                project_prefix=project_prefix,
            ),
            operation_processor_factory=LocalStorageEventOperationProcessorFactory(
                runtime=inline_runtime,
            ),
            move_processor=LocalWatchMoveProcessor(
                session_maker=dependencies.session_maker,
                file_service=dependencies.file_service,
                entity_repository=dependencies.entity_repository,
                maintenance_runner=maintenance_runner,
                moved_entity_search_refresher=moved_entity_search_refresher,
                batch_size=self.move_batch_size,
            ),
        )
