"""Service dependency injection for basic-memory.

This module is the FastAPI composition root for the service layer: every
provider constructs services from repositories, config, and the local runtime
implementations that live in ``basic_memory.index`` — it defines no runtime
behavior of its own:
- EntityParser, MarkdownProcessor
- FileService, EntityService
- SearchService, LinkResolver, ContextService
- ProjectService, DirectoryService
- local note-content, indexing, and background-scheduler runtimes
"""

from pathlib import Path
from typing import Annotated

from fastapi import Depends
from loguru import logger

from basic_memory.deps.config import AppConfigDep
from basic_memory.deps.db import SessionMakerDep
from basic_memory.deps.projects import (
    ProjectConfigDep,
    ProjectConfigV2Dep,
    ProjectConfigV2ExternalDep,
    ProjectRepositoryDep,
)
from basic_memory.deps.repositories import (
    EntityRepositoryDep,
    EntityRepositoryV2Dep,
    EntityRepositoryV2ExternalDep,
    ObservationRepositoryDep,
    ObservationRepositoryV2Dep,
    ObservationRepositoryV2ExternalDep,
    RelationRepositoryDep,
    RelationRepositoryV2Dep,
    RelationRepositoryV2ExternalDep,
    SearchRepositoryDep,
    SearchRepositoryV2Dep,
    SearchRepositoryV2ExternalDep,
)
from basic_memory.indexing.relation_resolution import RepositoryRelationResolutionRuntime
from basic_memory.cloud import (
    DirectoryDeleteService,
    LocalNoteContentMaterializationProvider,
    NoteContentMutationService,
    NoteContentQueryService,
)
from basic_memory.index.local_dependencies import build_local_markdown_file_indexer
from basic_memory.index.local_notes import (
    LocalAcceptedNotePreparerFactory,
    LocalAcceptedNoteRepositories,
    LocalCurrentNoteContentFreshener,
    LocalDirectoryDeleteRelationCleanupRefresher,
    LocalDirectoryFileDeleteEnqueuer,
)
from basic_memory.index.local_project import (
    LocalProjectIndexCommand,
    LocalProjectIndexRunner,
    ProjectIndexCommand,
    ProjectIndexObserver,
    ProjectIndexRunner,
    ProjectIndexScheduler,
)

# Re-exported lifecycle hook: the API, MCP, and one-shot CLI lifespans drain
# scheduled background work through the composition root before shutting down
# the database.
from basic_memory.index.local_schedulers import drain_background_tasks as drain_background_tasks
from basic_memory.index.local_schedulers import (
    EntityVectorSyncScheduler,
    LocalEntityVectorSyncScheduler,
    LocalProjectIndexScheduler,
    LocalRelationResolutionScheduler,
    LocalSearchReindexScheduler,
    RelationResolutionScheduler,
    SearchReindexScheduler,
)
from basic_memory.indexing.accepted_note_mutation_runner import (
    AcceptedNoteMutationDependencies,
    AcceptedNoteMutationMovePolicy,
)
from basic_memory.indexing.batch_indexer import BatchIndexer
from basic_memory.indexing.directory_delete_runner import (
    DirectoryDeleteRuntime,
    RepositoryDirectoryDeleteAcceptanceStore,
)
from basic_memory.indexing.index_file_runner import IndexFileExecutor
from basic_memory.indexing.models import StorageIndexFileWriter
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.services import EntityService, ProjectService
from basic_memory.services.context_service import ContextService
from basic_memory.services.directory_service import DirectoryService
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService

# --- Entity Parser ---


async def get_entity_parser(project_config: ProjectConfigDep) -> EntityParser:
    return EntityParser(project_config.home)


EntityParserDep = Annotated["EntityParser", Depends(get_entity_parser)]


async def get_entity_parser_v2(
    project_config: ProjectConfigV2Dep,
) -> EntityParser:  # pragma: no cover
    return EntityParser(project_config.home)


EntityParserV2Dep = Annotated["EntityParser", Depends(get_entity_parser_v2)]


async def get_entity_parser_v2_external(project_config: ProjectConfigV2ExternalDep) -> EntityParser:
    return EntityParser(project_config.home)


EntityParserV2ExternalDep = Annotated["EntityParser", Depends(get_entity_parser_v2_external)]


# --- Markdown Processor ---


async def get_markdown_processor(
    entity_parser: EntityParserDep, app_config: AppConfigDep
) -> MarkdownProcessor:
    return MarkdownProcessor(entity_parser, app_config=app_config)


MarkdownProcessorDep = Annotated[MarkdownProcessor, Depends(get_markdown_processor)]


async def get_markdown_processor_v2(  # pragma: no cover
    entity_parser: EntityParserV2Dep, app_config: AppConfigDep
) -> MarkdownProcessor:
    return MarkdownProcessor(entity_parser, app_config=app_config)


MarkdownProcessorV2Dep = Annotated[MarkdownProcessor, Depends(get_markdown_processor_v2)]


async def get_markdown_processor_v2_external(
    entity_parser: EntityParserV2ExternalDep, app_config: AppConfigDep
) -> MarkdownProcessor:
    return MarkdownProcessor(entity_parser, app_config=app_config)


MarkdownProcessorV2ExternalDep = Annotated[
    MarkdownProcessor, Depends(get_markdown_processor_v2_external)
]


# --- File Service ---


async def get_file_service(
    project_config: ProjectConfigDep,
    markdown_processor: MarkdownProcessorDep,
    app_config: AppConfigDep,
) -> FileService:
    file_service = FileService(project_config.home, markdown_processor, app_config=app_config)
    logger.debug(
        f"Created FileService for project: {project_config.name}, base_path: {project_config.home} "
    )
    return file_service


FileServiceDep = Annotated[FileService, Depends(get_file_service)]


async def get_file_service_v2(  # pragma: no cover
    project_config: ProjectConfigV2Dep,
    markdown_processor: MarkdownProcessorV2Dep,
    app_config: AppConfigDep,
) -> FileService:
    file_service = FileService(project_config.home, markdown_processor, app_config=app_config)
    logger.debug(
        f"Created FileService for project: {project_config.name}, base_path: {project_config.home}"
    )
    return file_service


FileServiceV2Dep = Annotated[FileService, Depends(get_file_service_v2)]


async def get_file_service_v2_external(
    project_config: ProjectConfigV2ExternalDep,
    markdown_processor: MarkdownProcessorV2ExternalDep,
    app_config: AppConfigDep,
) -> FileService:
    file_service = FileService(project_config.home, markdown_processor, app_config=app_config)
    logger.debug(
        f"Created FileService for project: {project_config.name}, base_path: {project_config.home}"
    )
    return file_service


FileServiceV2ExternalDep = Annotated[FileService, Depends(get_file_service_v2_external)]


# --- Search Service ---


async def get_search_service(
    search_repository: SearchRepositoryDep,
    entity_repository: EntityRepositoryDep,
    file_service: FileServiceDep,
    session_maker: SessionMakerDep,
) -> SearchService:
    """Create SearchService with dependencies."""
    return SearchService(search_repository, entity_repository, file_service, session_maker)


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]


async def get_search_service_v2(  # pragma: no cover
    search_repository: SearchRepositoryV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    file_service: FileServiceV2Dep,
    session_maker: SessionMakerDep,
) -> SearchService:
    """Create SearchService for v2 API."""
    return SearchService(search_repository, entity_repository, file_service, session_maker)


SearchServiceV2Dep = Annotated[SearchService, Depends(get_search_service_v2)]


async def get_search_service_v2_external(
    search_repository: SearchRepositoryV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    session_maker: SessionMakerDep,
) -> SearchService:
    """Create SearchService for v2 API (uses external_id)."""
    return SearchService(search_repository, entity_repository, file_service, session_maker)


SearchServiceV2ExternalDep = Annotated[SearchService, Depends(get_search_service_v2_external)]


# --- Note Content Reads ---


async def get_note_content_query_service(
    session_maker: SessionMakerDep,
) -> NoteContentQueryService:
    """Create the runtime note-content read facade for API routes."""
    return NoteContentQueryService(session_maker=session_maker)


NoteContentQueryServiceDep = Annotated[
    NoteContentQueryService, Depends(get_note_content_query_service)
]


# --- Directory Delete Runtime ---


async def get_directory_delete_service(
    session_maker: SessionMakerDep,
    file_service: FileServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
) -> DirectoryDeleteService:
    """Create the route-level directory-delete service for the local runtime."""
    return DirectoryDeleteService(
        session_maker=session_maker,
        runtime=DirectoryDeleteRuntime(
            store=RepositoryDirectoryDeleteAcceptanceStore(),
            file_delete_enqueuer=LocalDirectoryFileDeleteEnqueuer(file_service=file_service),
            relation_cleanup_refresher=LocalDirectoryDeleteRelationCleanupRefresher(
                session_maker=session_maker,
                entity_repository=entity_repository,
                search_service=search_service,
            ),
        ),
    )


DirectoryDeleteServiceDep = Annotated[DirectoryDeleteService, Depends(get_directory_delete_service)]


# --- Link Resolver ---


async def get_link_resolver(
    entity_repository: EntityRepositoryDep,
    search_service: SearchServiceDep,
    session_maker: SessionMakerDep,
) -> LinkResolver:
    return LinkResolver(
        entity_repository=entity_repository,
        search_service=search_service,
        session_maker=session_maker,
    )


LinkResolverDep = Annotated[LinkResolver, Depends(get_link_resolver)]


async def get_link_resolver_v2(  # pragma: no cover
    entity_repository: EntityRepositoryV2Dep,
    search_service: SearchServiceV2Dep,
    session_maker: SessionMakerDep,
) -> LinkResolver:
    return LinkResolver(
        entity_repository=entity_repository,
        search_service=search_service,
        session_maker=session_maker,
    )


LinkResolverV2Dep = Annotated[LinkResolver, Depends(get_link_resolver_v2)]


async def get_link_resolver_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    session_maker: SessionMakerDep,
) -> LinkResolver:
    return LinkResolver(
        entity_repository=entity_repository,
        search_service=search_service,
        session_maker=session_maker,
    )


LinkResolverV2ExternalDep = Annotated[LinkResolver, Depends(get_link_resolver_v2_external)]


# --- Entity Service ---


async def get_entity_service(
    entity_repository: EntityRepositoryDep,
    observation_repository: ObservationRepositoryDep,
    relation_repository: RelationRepositoryDep,
    entity_parser: EntityParserDep,
    file_service: FileServiceDep,
    link_resolver: LinkResolverDep,
    search_service: SearchServiceDep,
    session_maker: SessionMakerDep,
    app_config: AppConfigDep,
) -> EntityService:
    """Create EntityService with repository."""
    return EntityService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        entity_parser=entity_parser,
        file_service=file_service,
        link_resolver=link_resolver,
        session_maker=session_maker,
        search_service=search_service,
        app_config=app_config,
    )


EntityServiceDep = Annotated[EntityService, Depends(get_entity_service)]


async def get_entity_service_v2(  # pragma: no cover
    entity_repository: EntityRepositoryV2Dep,
    observation_repository: ObservationRepositoryV2Dep,
    relation_repository: RelationRepositoryV2Dep,
    entity_parser: EntityParserV2Dep,
    file_service: FileServiceV2Dep,
    link_resolver: LinkResolverV2Dep,
    search_service: SearchServiceV2Dep,
    session_maker: SessionMakerDep,
    app_config: AppConfigDep,
) -> EntityService:
    """Create EntityService for v2 API."""
    return EntityService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        entity_parser=entity_parser,
        file_service=file_service,
        link_resolver=link_resolver,
        session_maker=session_maker,
        search_service=search_service,
        app_config=app_config,
    )


EntityServiceV2Dep = Annotated[EntityService, Depends(get_entity_service_v2)]


async def get_entity_service_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep,
    observation_repository: ObservationRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    entity_parser: EntityParserV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    link_resolver: LinkResolverV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    session_maker: SessionMakerDep,
    app_config: AppConfigDep,
) -> EntityService:
    """Create EntityService for v2 API (uses external_id)."""
    return EntityService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        entity_parser=entity_parser,
        file_service=file_service,
        link_resolver=link_resolver,
        session_maker=session_maker,
        search_service=search_service,
        app_config=app_config,
    )


EntityServiceV2ExternalDep = Annotated[EntityService, Depends(get_entity_service_v2_external)]


# --- Context Service ---


async def get_context_service(
    search_repository: SearchRepositoryDep,
    entity_repository: EntityRepositoryDep,
    observation_repository: ObservationRepositoryDep,
    link_resolver: LinkResolverDep,
    session_maker: SessionMakerDep,
) -> ContextService:
    return ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        link_resolver=link_resolver,
        session_maker=session_maker,
    )


ContextServiceDep = Annotated[ContextService, Depends(get_context_service)]


async def get_context_service_v2(  # pragma: no cover
    search_repository: SearchRepositoryV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    observation_repository: ObservationRepositoryV2Dep,
    link_resolver: LinkResolverV2Dep,
    session_maker: SessionMakerDep,
) -> ContextService:
    """Create ContextService for v2 API."""
    return ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        link_resolver=link_resolver,
        session_maker=session_maker,
    )


ContextServiceV2Dep = Annotated[ContextService, Depends(get_context_service_v2)]


async def get_context_service_v2_external(
    search_repository: SearchRepositoryV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    observation_repository: ObservationRepositoryV2ExternalDep,
    link_resolver: LinkResolverV2ExternalDep,
    session_maker: SessionMakerDep,
) -> ContextService:
    """Create ContextService for v2 API (uses external_id)."""
    return ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        link_resolver=link_resolver,
        session_maker=session_maker,
    )


ContextServiceV2ExternalDep = Annotated[ContextService, Depends(get_context_service_v2_external)]


# --- File Indexing ---


async def get_index_file_executor_v2_external(
    app_config: AppConfigDep,
    entity_service: EntityServiceV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    session_maker: SessionMakerDep,
) -> IndexFileExecutor:
    """Create the event-indexing single-file executor for v2 API routes."""
    project_id = entity_repository.project_id
    if project_id is None:  # pragma: no cover
        raise RuntimeError("Index file executor requires a project-scoped entity repository")

    batch_indexer = BatchIndexer(
        app_config=app_config,
        entity_service=entity_service,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        file_writer=StorageIndexFileWriter(storage=file_service),
        session_maker=session_maker,
    )
    return build_local_markdown_file_indexer(
        project_id=project_id,
        file_service=file_service,
        session_maker=session_maker,
        entity_repository=entity_repository,
        batch_indexer=batch_indexer,
        search_service=search_service,
    )


IndexFileExecutorV2ExternalDep = Annotated[
    IndexFileExecutor, Depends(get_index_file_executor_v2_external)
]


# --- Note Content Writes ---


async def get_note_content_mutation_service(
    project_repository: ProjectRepositoryDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    file_indexer: IndexFileExecutorV2ExternalDep,
    session_maker: SessionMakerDep,
    app_config: AppConfigDep,
) -> NoteContentMutationService:
    """Create the local accepted-note mutation facade for API routes."""
    accepted_note_repositories = LocalAcceptedNoteRepositories()
    return NoteContentMutationService(
        session_maker=session_maker,
        mutation_dependencies=AcceptedNoteMutationDependencies(
            project_repository=project_repository,
            lookup_repositories=accepted_note_repositories,
            preparer_factory=LocalAcceptedNotePreparerFactory(
                session_maker=session_maker,
                app_config=app_config,
            ),
            write_repositories=accepted_note_repositories,
            move_policy=AcceptedNoteMutationMovePolicy(
                disable_permalinks=app_config.disable_permalinks,
                update_permalinks_on_move=app_config.update_permalinks_on_move,
            ),
            # Local filesystem is the source of truth: reject a create when the
            # target file already exists on disk but is not yet indexed (#1002
            # review), rather than diverging DB/search from the file.
            verify_storage_absent_on_create=True,
        ),
        content_freshener=LocalCurrentNoteContentFreshener(
            entity_repository=entity_repository,
            file_service=file_service,
            file_indexer=file_indexer,
            session_maker=session_maker,
        ),
    )


NoteContentMutationServiceDep = Annotated[
    NoteContentMutationService, Depends(get_note_content_mutation_service)
]


# --- Project Indexing ---


async def get_project_index_runner(
    project_repository: ProjectRepositoryDep,
    session_maker: SessionMakerDep,
) -> LocalProjectIndexRunner:
    """Create the local project-index runner used by API routes and tasks."""
    return LocalProjectIndexRunner(
        project_repository=project_repository,
        session_maker=session_maker,
    )


async def get_project_index_observer(
    project_repository: ProjectRepositoryDep,
    session_maker: SessionMakerDep,
) -> LocalProjectIndexRunner:
    """Create the local project-index observer used by status routes."""
    return LocalProjectIndexRunner(
        project_repository=project_repository,
        session_maker=session_maker,
    )


ProjectIndexRunnerDep = Annotated[ProjectIndexRunner, Depends(get_project_index_runner)]
ProjectIndexObserverDep = Annotated[
    ProjectIndexObserver,
    Depends(get_project_index_observer),
]


# --- Background Work Schedulers ---


async def get_entity_vector_sync_scheduler(
    search_service: SearchServiceV2ExternalDep,
    app_config: AppConfigDep,
) -> EntityVectorSyncScheduler:
    return LocalEntityVectorSyncScheduler(
        search_service=search_service,
        test_mode=app_config.is_test_env,
    )


async def get_project_index_scheduler(
    project_index_runner: ProjectIndexRunnerDep,
    app_config: AppConfigDep,
) -> ProjectIndexScheduler:
    return LocalProjectIndexScheduler(
        project_index_runner=project_index_runner,
        test_mode=app_config.is_test_env,
    )


async def get_search_reindex_scheduler(
    search_service: SearchServiceV2ExternalDep,
    app_config: AppConfigDep,
) -> SearchReindexScheduler:
    return LocalSearchReindexScheduler(
        search_service=search_service,
        test_mode=app_config.is_test_env,
    )


async def get_relation_resolution_scheduler(
    session_maker: SessionMakerDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    link_resolver: LinkResolverV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
    app_config: AppConfigDep,
) -> RelationResolutionScheduler:
    # Build the project-scoped resolution runtime. It owns its own sessions via
    # session_maker, so it is safe to run from a detached background task.
    runtime = RepositoryRelationResolutionRuntime(
        session_maker=session_maker,
        relation_repository=relation_repository,
        entity_repository=entity_repository,
        link_resolver=link_resolver,
        entity_indexer=search_service,
    )
    return LocalRelationResolutionScheduler(
        relation_runtime=runtime,
        test_mode=app_config.is_test_env,
    )


EntityVectorSyncSchedulerDep = Annotated[
    EntityVectorSyncScheduler,
    Depends(get_entity_vector_sync_scheduler),
]
ProjectIndexSchedulerDep = Annotated[
    ProjectIndexScheduler,
    Depends(get_project_index_scheduler),
]
SearchReindexSchedulerDep = Annotated[
    SearchReindexScheduler,
    Depends(get_search_reindex_scheduler),
]
RelationResolutionSchedulerDep = Annotated[
    RelationResolutionScheduler,
    Depends(get_relation_resolution_scheduler),
]


# --- Note Content Materialization ---
# Defined after the relation-resolution scheduler so the local materializer can
# trigger a relation pass once the deferred index finishes: the router schedules an
# eager pass right after enqueue, but that pass can run before the queued
# index_file inserts the new entity/relation rows, leaving inbound forward
# references unresolved until an unrelated later write.


async def get_note_content_materialization_provider(
    file_service: FileServiceV2ExternalDep,
    file_indexer: IndexFileExecutorV2ExternalDep,
    session_maker: SessionMakerDep,
    app_config: AppConfigDep,
    relation_resolution_scheduler: RelationResolutionSchedulerDep,
) -> LocalNoteContentMaterializationProvider:
    """Create the local materializer for accepted-note route writes.

    test_mode keeps materialization inline so tests can assert file/search state
    synchronously; production defers the file write + index off the accept path
    for cloud parity (see LocalNoteContentMaterializationProvider).
    """
    return LocalNoteContentMaterializationProvider(
        session_maker=session_maker,
        file_service=file_service,
        file_indexer=file_indexer,
        test_mode=app_config.is_test_env,
        materialization_workers=app_config.materialization_workers,
        relation_resolution_scheduler=relation_resolution_scheduler,
    )


NoteContentMaterializationProviderDep = Annotated[
    LocalNoteContentMaterializationProvider,
    Depends(get_note_content_materialization_provider),
]


async def get_project_index_command(
    project_index_runner: ProjectIndexRunnerDep,
    project_index_scheduler: ProjectIndexSchedulerDep,
) -> ProjectIndexCommand:
    return LocalProjectIndexCommand(
        project_index_runner=project_index_runner,
        project_index_scheduler=project_index_scheduler,
    )


ProjectIndexCommandDep = Annotated[
    ProjectIndexCommand,
    Depends(get_project_index_command),
]


# --- Project Service ---


async def get_project_service(
    project_repository: ProjectRepositoryDep,
    session_maker: SessionMakerDep,
    app_config: AppConfigDep,
) -> ProjectService:
    """Create ProjectService with repository and a system-level FileService for directory operations."""
    # A system-level FileService for project directory creation (no project-specific base_path needed).
    # ensure_directory() accepts absolute paths and ignores base_path for those, so Path.home() is safe.
    entity_parser = EntityParser(Path.home())
    markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
    file_service = FileService(Path.home(), markdown_processor, app_config=app_config)
    return ProjectService(
        repository=project_repository, session_maker=session_maker, file_service=file_service
    )


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


# --- Directory Service ---


async def get_directory_service(
    entity_repository: EntityRepositoryDep,
    session_maker: SessionMakerDep,
) -> DirectoryService:
    """Create DirectoryService with dependencies."""
    return DirectoryService(
        entity_repository=entity_repository,
        session_maker=session_maker,
    )


DirectoryServiceDep = Annotated[DirectoryService, Depends(get_directory_service)]


async def get_directory_service_v2(  # pragma: no cover
    entity_repository: EntityRepositoryV2Dep,
    session_maker: SessionMakerDep,
) -> DirectoryService:
    """Create DirectoryService for v2 API (uses integer project_id from path)."""
    return DirectoryService(
        entity_repository=entity_repository,
        session_maker=session_maker,
    )


DirectoryServiceV2Dep = Annotated[DirectoryService, Depends(get_directory_service_v2)]


async def get_directory_service_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep,
    session_maker: SessionMakerDep,
) -> DirectoryService:
    """Create DirectoryService for v2 API (uses external_id from path)."""
    return DirectoryService(
        entity_repository=entity_repository,
        session_maker=session_maker,
    )


DirectoryServiceV2ExternalDep = Annotated[
    DirectoryService, Depends(get_directory_service_v2_external)
]
