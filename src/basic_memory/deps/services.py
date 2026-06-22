"""Service dependency injection for basic-memory.

This module provides service-layer dependencies:
- EntityParser, MarkdownProcessor
- FileService, EntityService
- SearchService, LinkResolver, ContextService
- ProjectService, DirectoryService
"""

import asyncio
import os
from pathlib import Path
from typing import Annotated, Any, Callable, Coroutine, Mapping, Protocol

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
from basic_memory.index import LocalProjectIndexRunner, build_local_markdown_file_indexer
from basic_memory.indexing import BatchIndexer, IndexFileExecutor, StorageIndexFileWriter
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


# --- Project Indexing ---


class ScheduledProjectIndexRunner(Protocol):
    """Capability needed by background scheduling to run a project index."""

    async def index_project(self, project_id: int, *, force_full: bool = False) -> object: ...


async def get_project_index_runner(
    project_repository: ProjectRepositoryDep,
    session_maker: SessionMakerDep,
) -> LocalProjectIndexRunner:
    """Create the local project-index runner used by API routes and tasks."""
    return LocalProjectIndexRunner(
        project_repository=project_repository,
        session_maker=session_maker,
    )


ProjectIndexRunnerDep = Annotated[LocalProjectIndexRunner, Depends(get_project_index_runner)]
ScheduledProjectIndexRunnerDep = Annotated[
    ScheduledProjectIndexRunner,
    Depends(get_project_index_runner),
]


# --- Background Task Scheduler ---


class TaskScheduler(Protocol):
    def schedule(self, task_name: str, **payload: Any) -> None:
        """Schedule a background task by name."""


def _log_task_failure(completed: asyncio.Task) -> None:
    if completed.cancelled():
        return
    try:
        completed.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:  # pragma: no cover
        logger.exception("Background task failed", error=str(exc))


class LocalTaskScheduler:
    """Default scheduler that runs tasks in-process via asyncio.create_task.

    In test mode (BASIC_MEMORY_ENV=test), tasks run as no-ops to avoid
    background asyncio tasks racing against test teardown and causing
    SQLite 'cannot commit transaction' errors.
    """

    def __init__(
        self,
        handlers: Mapping[str, Callable[..., Coroutine[Any, Any, None]]],
        test_mode: bool | None = None,
    ) -> None:
        self._handlers = handlers
        self._test_mode = (
            test_mode if test_mode is not None else os.environ.get("BASIC_MEMORY_ENV") == "test"
        )

    def schedule(self, task_name: str, **payload: Any) -> None:
        handler = self._handlers.get(task_name)
        # Trigger: task name is not registered
        # Why: avoid silently dropping background work
        # Outcome: fail fast to surface misconfiguration
        if not handler:
            raise ValueError(f"Unknown task name: {task_name}")

        # Trigger: running inside pytest (BASIC_MEMORY_ENV=test)
        # Why: background create_task() outlives test fixtures and races
        #      against engine disposal, causing flaky SQLite errors
        # Outcome: skip background scheduling; focused tests run the
        #          index operation directly when they need to.
        if self._test_mode:
            return

        task = asyncio.create_task(handler(**payload))
        task.add_done_callback(_log_task_failure)


async def get_task_scheduler(
    project_index_runner: ScheduledProjectIndexRunnerDep,
    search_service: SearchServiceV2ExternalDep,
    app_config: AppConfigDep,
) -> TaskScheduler:
    """Create a scheduler that maps task specs to coroutines."""

    async def _sync_entity_vectors(entity_id: int, **_: Any) -> None:
        await search_service.sync_entity_vectors(entity_id)

    async def _index_project(
        project_id: int | None = None, force_full: bool = False, **_: Any
    ) -> None:
        if project_id is None:
            raise ValueError("index_project requires project_id")
        await project_index_runner.index_project(project_id, force_full=force_full)

    async def _reindex_project(**_: Any) -> None:
        await search_service.reindex_all()

    scheduler = LocalTaskScheduler(
        {
            "sync_entity_vectors": _sync_entity_vectors,
            "index_project": _index_project,
            "reindex_project": _reindex_project,
        },
        test_mode=app_config.is_test_env,
    )
    return scheduler


TaskSchedulerDep = Annotated[TaskScheduler, Depends(get_task_scheduler)]


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
