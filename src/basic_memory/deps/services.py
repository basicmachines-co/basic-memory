"""Service dependency injection for basic-memory.

This module provides service-layer dependencies:
- EntityParser, MarkdownProcessor
- FileService, EntityService
- SearchService, LinkResolver, ContextService
- ProjectService, DirectoryService
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Coroutine, Protocol

from fastapi import Depends
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.config import BasicMemoryConfig
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
from basic_memory.indexing.relation_resolution import (
    RelationResolutionRuntime,
    RepositoryRelationResolutionRuntime,
    resolve_project_relations,
)
from basic_memory.cloud import (
    DirectoryDeleteService,
    LocalNoteContentMaterializationProvider,
    NoteContentMutationService,
    NoteContentQueryService,
)
from basic_memory.index import (
    LOCAL_EVENT_INDEX_TENANT_ID,
    LocalProjectIndexObservation,
    LocalProjectIndexRunner,
    ProjectIndexCoordinatorResult,
    build_local_markdown_file_indexer,
)
from basic_memory.indexing import (
    AcceptedNoteMutationDependencies,
    AcceptedNoteMutationMovePolicy,
    AcceptedNoteMutationPreparer,
    BatchIndexer,
    DirectoryDeleteRuntime,
    DirectoryFileDeleteEnqueueError,
    IndexFileExecutor,
    RepositoryDirectoryDeleteAcceptanceStore,
    StorageIndexFileWriter,
    SystemAcceptedNoteMutationClock,
    build_default_accepted_note_repositories,
    run_note_file_delete,
)
from basic_memory.file_utils import FileError
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Project
from basic_memory.repository import ObservationRepository, RelationRepository
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.runtime import (
    RuntimeFileChecksum,
    RuntimeFilePath,
    RuntimeNoteFileDeleteJobRequest,
    TenantId,
    runtime_content_type_is_markdown,
)
from basic_memory.schemas import ProjectIndexRunResponse
from basic_memory.services import EntityService, ProjectService
from basic_memory.services.exceptions import FileOperationError
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


@dataclass(frozen=True, slots=True)
class LocalAcceptedNotePreparerFactory:
    """Construct prepare-only note semantics for local accepted-note mutations."""

    session_maker: async_sessionmaker[AsyncSession]
    app_config: BasicMemoryConfig

    def create_note_preparer(self, project: Project) -> AcceptedNoteMutationPreparer:
        entity_parser = EntityParser(Path(project.path))
        markdown_processor = MarkdownProcessor(entity_parser, app_config=self.app_config)
        file_service = FileService(
            Path(project.path),
            markdown_processor,
            app_config=self.app_config,
        )
        entity_repository = EntityRepository(project_id=project.id)
        search_repository = create_search_repository(
            self.session_maker,
            project_id=project.id,
            app_config=self.app_config,
        )
        search_service = SearchService(
            search_repository,
            entity_repository,
            file_service,
            self.session_maker,
        )
        link_resolver = LinkResolver(
            entity_repository=entity_repository,
            search_service=search_service,
            session_maker=self.session_maker,
        )
        return EntityService(
            entity_repository=entity_repository,
            observation_repository=ObservationRepository(project_id=project.id),
            relation_repository=RelationRepository(project_id=project.id),
            entity_parser=entity_parser,
            file_service=file_service,
            link_resolver=link_resolver,
            session_maker=self.session_maker,
            search_service=search_service,
            app_config=self.app_config,
        )


class LocalCurrentNoteEntity(Protocol):
    """Entity fields needed to refresh current markdown before route mutation."""

    file_path: str
    content_type: str


class LocalCurrentNoteEntityRepository(Protocol):
    """Entity lookup needed by the local note-content freshener."""

    async def get_by_external_id(
        self,
        session: AsyncSession,
        external_id: str,
        *,
        load_relations: bool = True,
    ) -> LocalCurrentNoteEntity | None: ...


class LocalCurrentNoteFileService(Protocol):
    """Current file-state access needed before mutating accepted note content."""

    async def exists(
        self,
        path: RuntimeFilePath,
    ) -> bool: ...


class LocalCurrentNoteFileIndexer(Protocol):
    """Single-file indexing capability used by the local note-content freshener."""

    async def index_file(
        self,
        file_path: RuntimeFilePath,
        *,
        source: str,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class LocalCurrentNoteContentFreshener:
    """Converge directly-edited local markdown before accepted-note mutations."""

    entity_repository: LocalCurrentNoteEntityRepository
    file_service: LocalCurrentNoteFileService
    file_indexer: LocalCurrentNoteFileIndexer
    session_maker: async_sessionmaker[AsyncSession]

    async def freshen_note_content(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
    ) -> None:
        del project_external_id

        async with self.session_maker() as session:
            entity = await self.entity_repository.get_by_external_id(
                session,
                entity_external_id,
                load_relations=False,
            )
            if entity is None or not runtime_content_type_is_markdown(entity):
                return
            file_path = entity.file_path

        if not await self.file_service.exists(file_path):
            return

        await self.file_indexer.index_file(
            file_path,
            source="note-content-mutation-freshen",
        )


# --- Directory Delete Runtime ---


async def get_runtime_tenant_id() -> TenantId:
    """Return the runtime tenant id for local route-owned cleanup requests."""
    return LOCAL_EVENT_INDEX_TENANT_ID


RuntimeTenantIdDep = Annotated[TenantId, Depends(get_runtime_tenant_id)]


@dataclass(frozen=True, slots=True)
class LocalNoteFileDeleteStorage:
    """Adapt local FileService to guarded materialized-note cleanup."""

    file_service: FileService

    async def exists(self, path: RuntimeFilePath) -> bool:
        return await self.file_service.exists(path)

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum:
        return await self.file_service.compute_checksum(path)

    async def delete_file(self, path: RuntimeFilePath) -> None:
        await self.file_service.delete_file(path)


@dataclass(frozen=True, slots=True)
class LocalDirectoryFileDeleteEnqueuer:
    """Run accepted directory-delete file cleanup inline for the local runtime."""

    file_service: FileService

    async def enqueue_directory_file_delete(
        self,
        request: RuntimeNoteFileDeleteJobRequest,
    ) -> None:
        try:
            await run_note_file_delete(
                request,
                storage=LocalNoteFileDeleteStorage(file_service=self.file_service),
            )
        except (FileError, FileOperationError, OSError) as exc:
            raise DirectoryFileDeleteEnqueueError(str(exc)) from exc


async def get_directory_delete_service(
    session_maker: SessionMakerDep,
    file_service: FileServiceV2ExternalDep,
) -> DirectoryDeleteService:
    """Create the route-level directory-delete service for the local runtime."""
    return DirectoryDeleteService(
        session_maker=session_maker,
        runtime=DirectoryDeleteRuntime(
            store=RepositoryDirectoryDeleteAcceptanceStore(),
            file_delete_enqueuer=LocalDirectoryFileDeleteEnqueuer(file_service=file_service),
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
    accepted_note_repositories = build_default_accepted_note_repositories()
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
            clock=SystemAcceptedNoteMutationClock(),
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


# --- Note Content Materialization ---


async def get_note_content_materialization_provider(
    file_service: FileServiceV2ExternalDep,
    file_indexer: IndexFileExecutorV2ExternalDep,
    session_maker: SessionMakerDep,
) -> LocalNoteContentMaterializationProvider:
    """Create the local inline materializer for accepted-note route writes."""
    return LocalNoteContentMaterializationProvider(
        session_maker=session_maker,
        file_service=file_service,
        file_indexer=file_indexer,
    )


NoteContentMaterializationProviderDep = Annotated[
    LocalNoteContentMaterializationProvider,
    Depends(get_note_content_materialization_provider),
]


# --- Project Indexing ---


class ProjectIndexRunner(Protocol):
    """Run project-wide indexing in the current process."""

    async def index_project(
        self,
        project_id: int,
        *,
        force_full: bool = False,
    ) -> ProjectIndexCoordinatorResult: ...


class ProjectIndexObserver(Protocol):
    """Observe project files visible to the active runtime."""

    async def observe_project(self, project_id: int) -> LocalProjectIndexObservation: ...


class ProjectIndexScheduler(Protocol):
    """Schedule background project indexing."""

    def schedule_project_index(self, *, project_id: int, force_full: bool = False) -> None: ...


@dataclass(frozen=True, slots=True)
class ProjectIndexRouteRequest:
    """Route-level project-index command input."""

    project_id: int
    project_name: str
    force_full: bool
    run_in_background: bool


type ProjectIndexRouteResult = ProjectIndexRunResponse | dict[str, str]


class ProjectIndexCommand(Protocol):
    """Handle a project-index route request."""

    async def index_project(
        self,
        request: ProjectIndexRouteRequest,
    ) -> ProjectIndexRouteResult: ...


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


class EntityVectorSyncScheduler(Protocol):
    """Schedule out-of-band semantic vector refreshes for note mutations."""

    def schedule_entity_vector_sync(self, *, entity_id: int, project_id: int) -> None: ...


class SearchReindexScheduler(Protocol):
    """Schedule a search-index rebuild for the active project."""

    def schedule_search_reindex(self, *, project_id: int) -> None: ...


class EntityVectorSyncSearchService(Protocol):
    async def sync_entity_vectors(self, entity_id: int) -> object: ...


class SearchReindexService(Protocol):
    async def reindex_all(self) -> object: ...


def _log_task_failure(completed: asyncio.Task) -> None:
    if completed.cancelled():
        return
    try:
        completed.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:  # pragma: no cover
        logger.exception("Background task failed", error=str(exc))


def _schedule_background_coroutine(
    coroutine: Coroutine[Any, Any, object],
    *,
    test_mode: bool,
) -> None:
    # Background tasks outlive pytest fixture cleanup and can race engine disposal.
    # Focused tests call the scheduler classes directly with test_mode=False.
    if test_mode:
        coroutine.close()
        return

    task = asyncio.create_task(coroutine)
    task.add_done_callback(_log_task_failure)


@dataclass(frozen=True, slots=True)
class LocalEntityVectorSyncScheduler:
    search_service: EntityVectorSyncSearchService
    test_mode: bool

    def schedule_entity_vector_sync(self, *, entity_id: int, project_id: int) -> None:
        _ = project_id
        _schedule_background_coroutine(
            self.search_service.sync_entity_vectors(entity_id),
            test_mode=self.test_mode,
        )


@dataclass(frozen=True, slots=True)
class LocalProjectIndexScheduler:
    project_index_runner: ProjectIndexRunner
    test_mode: bool

    def schedule_project_index(self, *, project_id: int, force_full: bool = False) -> None:
        _schedule_background_coroutine(
            self.project_index_runner.index_project(project_id, force_full=force_full),
            test_mode=self.test_mode,
        )


@dataclass(frozen=True, slots=True)
class LocalSearchReindexScheduler:
    search_service: SearchReindexService
    test_mode: bool

    def schedule_search_reindex(self, *, project_id: int) -> None:
        _ = project_id
        _schedule_background_coroutine(
            self.search_service.reindex_all(),
            test_mode=self.test_mode,
        )


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


class RelationResolutionScheduler(Protocol):
    """Schedule background forward-reference resolution after note mutations."""

    def schedule_relation_resolution(self, *, project_id: int) -> None: ...


@dataclass(frozen=True, slots=True)
class LocalRelationResolutionScheduler:
    """Back-resolve dangling forward references off the request path.

    The MCP/API write path inline-indexes the materialized note but never
    back-resolves inbound `[[wikilinks]]` whose target the new note now
    satisfies. The watcher's relation repair does this, but only for files it is
    the first to index, so MCP writes (which pre-index the file) never trigger
    it. Scheduling a project relation pass here gives MCP writes the same
    back-resolution the watcher path already provides (#1015). No-op in test
    mode, consistent with the other local schedulers.
    """

    relation_runtime: RelationResolutionRuntime
    test_mode: bool

    def schedule_relation_resolution(self, *, project_id: int) -> None:
        _ = project_id  # runtime is already bound to the request's project
        _schedule_background_coroutine(
            resolve_project_relations(self.relation_runtime),
            test_mode=self.test_mode,
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


@dataclass(frozen=True, slots=True)
class LocalProjectIndexCommand:
    project_index_runner: ProjectIndexRunner
    project_index_scheduler: ProjectIndexScheduler

    async def index_project(
        self,
        request: ProjectIndexRouteRequest,
    ) -> ProjectIndexRouteResult:
        if request.run_in_background:
            self.project_index_scheduler.schedule_project_index(
                project_id=request.project_id,
                force_full=request.force_full,
            )
            logger.info(
                f"Filesystem indexing initiated for project: {request.project_name} "
                f"(force_full={request.force_full})"
            )

            return {
                "status": "index_started",
                "message": (f"Filesystem indexing initiated for project '{request.project_name}'"),
            }

        result = await self.project_index_runner.index_project(
            request.project_id,
            force_full=request.force_full,
        )
        logger.info(
            f"Filesystem indexing completed for project: {request.project_name} "
            f"(force_full={request.force_full})"
        )
        return ProjectIndexRunResponse.from_result(result)


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
