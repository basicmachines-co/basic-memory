"""Dependency injection functions for basic-memory services."""

from typing import Annotated
from loguru import logger

from fastapi import Depends, HTTPException, Path, status, Request
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
)
import pathlib

from basic_memory import db
from basic_memory.config import ProjectConfig, BasicMemoryConfig, ConfigManager
from basic_memory.importers import (
    ChatGPTImporter,
    ClaudeConversationsImporter,
    ClaudeProjectsImporter,
    MemoryJsonImporter,
)
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.repository.search_repository import SearchRepository, create_search_repository
from basic_memory.services import EntityService, ProjectService
from basic_memory.services.context_service import ContextService
from basic_memory.services.directory_service import DirectoryService
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService
from basic_memory.sync import SyncService
from basic_memory.utils import generate_permalink


def get_app_config() -> BasicMemoryConfig:  # pragma: no cover
    app_config = ConfigManager().config
    return app_config


AppConfigDep = Annotated[BasicMemoryConfig, Depends(get_app_config)]  # pragma: no cover


## project


async def get_project_config(
    project: "ProjectPathDep", project_repository: "ProjectRepositoryDep"
) -> ProjectConfig:  # pragma: no cover
    """Get the current project referenced from request state.

    Args:
        request: The current request object
        project_repository: Repository for project operations

    Returns:
        The resolved project config

    Raises:
        HTTPException: If project is not found
    """
    # Convert project name to permalink for lookup
    project_permalink = generate_permalink(str(project))
    project_obj = await project_repository.get_by_permalink(project_permalink)
    if project_obj:
        return ProjectConfig(name=project_obj.name, home=pathlib.Path(project_obj.path))

    # Not found
    raise HTTPException(  # pragma: no cover
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project}' not found."
    )


ProjectConfigDep = Annotated[ProjectConfig, Depends(get_project_config)]  # pragma: no cover


async def get_project_config_v2(
    project_id: "ProjectIdPathDep", project_repository: "ProjectRepositoryDep"
) -> ProjectConfig:  # pragma: no cover
    """Get the project config for v2 API (uses integer project_id from path).

    Args:
        project_id: The validated numeric project ID from the URL path
        project_repository: Repository for project operations

    Returns:
        The resolved project config

    Raises:
        HTTPException: If project is not found
    """
    project_obj = await project_repository.get_by_id(project_id)
    if project_obj:
        return ProjectConfig(name=project_obj.name, home=pathlib.Path(project_obj.path))

    # Not found (this should not happen since ProjectIdPathDep already validates existence)
    raise HTTPException(  # pragma: no cover
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Project with ID {project_id} not found."
    )


ProjectConfigV2Dep = Annotated[ProjectConfig, Depends(get_project_config_v2)]  # pragma: no cover

## sqlalchemy


async def get_engine_factory(
    request: Request,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:  # pragma: no cover
    """Get cached engine and session maker from app state.

    For API requests, returns cached connections from app.state for optimal performance.
    For non-API contexts (CLI), falls back to direct database connection.
    """
    # Try to get cached connections from app state (API context)
    if (
        hasattr(request, "app")
        and hasattr(request.app.state, "engine")
        and hasattr(request.app.state, "session_maker")
    ):
        return request.app.state.engine, request.app.state.session_maker

    # Fallback for non-API contexts (CLI)
    logger.debug("Using fallback database connection for non-API context")
    app_config = get_app_config()
    engine, session_maker = await db.get_or_create_db(app_config.database_path)
    return engine, session_maker


EngineFactoryDep = Annotated[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]], Depends(get_engine_factory)
]


async def get_session_maker(engine_factory: EngineFactoryDep) -> async_sessionmaker[AsyncSession]:
    """Get session maker."""
    _, session_maker = engine_factory
    return session_maker


SessionMakerDep = Annotated[async_sessionmaker, Depends(get_session_maker)]


## repositories


async def get_project_repository(
    session_maker: SessionMakerDep,
) -> ProjectRepository:
    """Get the project repository."""
    return ProjectRepository(session_maker)


ProjectRepositoryDep = Annotated[ProjectRepository, Depends(get_project_repository)]
ProjectPathDep = Annotated[str, Path()]  # Use Path dependency to extract from URL


async def validate_project_id(
    project_id: int,
    project_repository: ProjectRepositoryDep,
) -> int:
    """Validate that a numeric project ID exists in the database.

    This is used for v2 API endpoints that take project IDs as integers in the path.
    The project_id parameter will be automatically extracted from the URL path by FastAPI.

    Args:
        project_id: The numeric project ID from the URL path
        project_repository: Repository for project operations

    Returns:
        The validated project ID

    Raises:
        HTTPException: If project with that ID is not found
    """
    project_obj = await project_repository.get_by_id(project_id)
    if not project_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project with ID {project_id} not found.",
        )
    return project_id


# V2 API: Validated integer project ID from path
ProjectIdPathDep = Annotated[int, Depends(validate_project_id)]


async def get_project_id(
    project_repository: ProjectRepositoryDep,
    project: ProjectPathDep,
) -> int:
    """Get the current project ID from request state.

    When using sub-applications with /{project} mounting, the project value
    is stored in request.state by middleware.

    Args:
        request: The current request object
        project_repository: Repository for project operations

    Returns:
        The resolved project ID

    Raises:
        HTTPException: If project is not found
    """
    # Convert project name to permalink for lookup
    project_permalink = generate_permalink(str(project))
    project_obj = await project_repository.get_by_permalink(project_permalink)
    if project_obj:
        return project_obj.id

    # Try by name if permalink lookup fails
    project_obj = await project_repository.get_by_name(str(project))  # pragma: no cover
    if project_obj:  # pragma: no cover
        return project_obj.id

    # Not found
    raise HTTPException(  # pragma: no cover
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project}' not found."
    )


"""
The project_id dependency is used in the following:
- EntityRepository
- ObservationRepository
- RelationRepository
- SearchRepository
- ProjectInfoRepository
"""
ProjectIdDep = Annotated[int, Depends(get_project_id)]


async def get_entity_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> EntityRepository:
    """Create an EntityRepository instance for the current project."""
    return EntityRepository(session_maker, project_id=project_id)


EntityRepositoryDep = Annotated[EntityRepository, Depends(get_entity_repository)]


async def get_entity_repository_v2(
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> EntityRepository:
    """Create an EntityRepository instance for v2 API (uses integer project_id from path)."""
    return EntityRepository(session_maker, project_id=project_id)


EntityRepositoryV2Dep = Annotated[EntityRepository, Depends(get_entity_repository_v2)]


async def get_observation_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance for the current project."""
    return ObservationRepository(session_maker, project_id=project_id)


ObservationRepositoryDep = Annotated[ObservationRepository, Depends(get_observation_repository)]


async def get_observation_repository_v2(
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance for v2 API."""
    return ObservationRepository(session_maker, project_id=project_id)


ObservationRepositoryV2Dep = Annotated[
    ObservationRepository, Depends(get_observation_repository_v2)
]


async def get_relation_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> RelationRepository:
    """Create a RelationRepository instance for the current project."""
    return RelationRepository(session_maker, project_id=project_id)


RelationRepositoryDep = Annotated[RelationRepository, Depends(get_relation_repository)]


async def get_relation_repository_v2(
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> RelationRepository:
    """Create a RelationRepository instance for v2 API."""
    return RelationRepository(session_maker, project_id=project_id)


RelationRepositoryV2Dep = Annotated[RelationRepository, Depends(get_relation_repository_v2)]


async def get_search_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> SearchRepository:
    """Create a backend-specific SearchRepository instance for the current project.

    Uses factory function to return SQLiteSearchRepository or PostgresSearchRepository
    based on database backend configuration.
    """
    return create_search_repository(session_maker, project_id=project_id)


SearchRepositoryDep = Annotated[SearchRepository, Depends(get_search_repository)]


async def get_search_repository_v2(
    session_maker: SessionMakerDep,
    project_id: ProjectIdPathDep,
) -> SearchRepository:
    """Create a SearchRepository instance for v2 API."""
    return create_search_repository(session_maker, project_id=project_id)


SearchRepositoryV2Dep = Annotated[SearchRepository, Depends(get_search_repository_v2)]


# ProjectInfoRepository is deprecated and will be removed in a future version.
# Use ProjectRepository instead, which has the same functionality plus more project-specific operations.

## services


async def get_entity_parser(project_config: ProjectConfigDep) -> EntityParser:
    return EntityParser(project_config.home)


EntityParserDep = Annotated["EntityParser", Depends(get_entity_parser)]


async def get_entity_parser_v2(project_config: ProjectConfigV2Dep) -> EntityParser:
    return EntityParser(project_config.home)


EntityParserV2Dep = Annotated["EntityParser", Depends(get_entity_parser_v2)]


async def get_markdown_processor(
    entity_parser: EntityParserDep, app_config: AppConfigDep
) -> MarkdownProcessor:
    return MarkdownProcessor(entity_parser, app_config=app_config)


MarkdownProcessorDep = Annotated[MarkdownProcessor, Depends(get_markdown_processor)]


async def get_markdown_processor_v2(
    entity_parser: EntityParserV2Dep, app_config: AppConfigDep
) -> MarkdownProcessor:
    return MarkdownProcessor(entity_parser, app_config=app_config)


MarkdownProcessorV2Dep = Annotated[MarkdownProcessor, Depends(get_markdown_processor_v2)]


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


async def get_file_service_v2(
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


async def get_entity_service(
    entity_repository: EntityRepositoryDep,
    observation_repository: ObservationRepositoryDep,
    relation_repository: RelationRepositoryDep,
    entity_parser: EntityParserDep,
    file_service: FileServiceDep,
    link_resolver: "LinkResolverDep",
    search_service: "SearchServiceDep",
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
        search_service=search_service,
        app_config=app_config,
    )


EntityServiceDep = Annotated[EntityService, Depends(get_entity_service)]


async def get_entity_service_v2(
    entity_repository: EntityRepositoryV2Dep,
    observation_repository: ObservationRepositoryV2Dep,
    relation_repository: RelationRepositoryV2Dep,
    entity_parser: EntityParserV2Dep,
    file_service: FileServiceV2Dep,
    link_resolver: "LinkResolverV2Dep",
    search_service: "SearchServiceV2Dep",
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
        search_service=search_service,
        app_config=app_config,
    )


EntityServiceV2Dep = Annotated[EntityService, Depends(get_entity_service_v2)]


async def get_search_service(
    search_repository: SearchRepositoryDep,
    entity_repository: EntityRepositoryDep,
    file_service: FileServiceDep,
) -> SearchService:
    """Create SearchService with dependencies."""
    return SearchService(search_repository, entity_repository, file_service)


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]


async def get_search_service_v2(
    search_repository: SearchRepositoryV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    file_service: FileServiceV2Dep,
) -> SearchService:
    """Create SearchService for v2 API."""
    return SearchService(search_repository, entity_repository, file_service)


SearchServiceV2Dep = Annotated[SearchService, Depends(get_search_service_v2)]


async def get_link_resolver(
    entity_repository: EntityRepositoryDep, search_service: SearchServiceDep
) -> LinkResolver:
    return LinkResolver(entity_repository=entity_repository, search_service=search_service)


LinkResolverDep = Annotated[LinkResolver, Depends(get_link_resolver)]


async def get_link_resolver_v2(
    entity_repository: EntityRepositoryV2Dep, search_service: SearchServiceV2Dep
) -> LinkResolver:
    return LinkResolver(entity_repository=entity_repository, search_service=search_service)


LinkResolverV2Dep = Annotated[LinkResolver, Depends(get_link_resolver_v2)]


async def get_context_service(
    search_repository: SearchRepositoryDep,
    entity_repository: EntityRepositoryDep,
    observation_repository: ObservationRepositoryDep,
) -> ContextService:
    return ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
    )


ContextServiceDep = Annotated[ContextService, Depends(get_context_service)]


async def get_context_service_v2(
    search_repository: SearchRepositoryV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    observation_repository: ObservationRepositoryV2Dep,
) -> ContextService:
    """Create ContextService for v2 API."""
    return ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
    )


ContextServiceV2Dep = Annotated[ContextService, Depends(get_context_service_v2)]


async def get_sync_service(
    app_config: AppConfigDep,
    entity_service: EntityServiceDep,
    entity_parser: EntityParserDep,
    entity_repository: EntityRepositoryDep,
    relation_repository: RelationRepositoryDep,
    project_repository: ProjectRepositoryDep,
    search_service: SearchServiceDep,
    file_service: FileServiceDep,
) -> SyncService:  # pragma: no cover
    """

    :rtype: object
    """
    return SyncService(
        app_config=app_config,
        entity_service=entity_service,
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        project_repository=project_repository,
        search_service=search_service,
        file_service=file_service,
    )


SyncServiceDep = Annotated[SyncService, Depends(get_sync_service)]


async def get_sync_service_v2(
    app_config: AppConfigDep,
    entity_service: EntityServiceV2Dep,
    entity_parser: EntityParserV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    relation_repository: RelationRepositoryV2Dep,
    project_repository: ProjectRepositoryDep,
    search_service: SearchServiceV2Dep,
    file_service: FileServiceV2Dep,
) -> SyncService:  # pragma: no cover
    """Create SyncService for v2 API."""
    return SyncService(
        app_config=app_config,
        entity_service=entity_service,
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        project_repository=project_repository,
        search_service=search_service,
        file_service=file_service,
    )


SyncServiceV2Dep = Annotated[SyncService, Depends(get_sync_service_v2)]


async def get_project_service(
    project_repository: ProjectRepositoryDep,
) -> ProjectService:
    """Create ProjectService with repository."""
    return ProjectService(repository=project_repository)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


async def get_directory_service(
    entity_repository: EntityRepositoryDep,
) -> DirectoryService:
    """Create DirectoryService with dependencies."""
    return DirectoryService(
        entity_repository=entity_repository,
    )


DirectoryServiceDep = Annotated[DirectoryService, Depends(get_directory_service)]


async def get_directory_service_v2(
    entity_repository: EntityRepositoryV2Dep,
) -> DirectoryService:
    """Create DirectoryService for v2 API (uses integer project_id from path)."""
    return DirectoryService(
        entity_repository=entity_repository,
    )


DirectoryServiceV2Dep = Annotated[DirectoryService, Depends(get_directory_service_v2)]


# Import


async def get_chatgpt_importer(
    project_config: ProjectConfigDep, markdown_processor: MarkdownProcessorDep
) -> ChatGPTImporter:
    """Create ChatGPTImporter with dependencies."""
    return ChatGPTImporter(project_config.home, markdown_processor)


ChatGPTImporterDep = Annotated[ChatGPTImporter, Depends(get_chatgpt_importer)]


async def get_claude_conversations_importer(
    project_config: ProjectConfigDep, markdown_processor: MarkdownProcessorDep
) -> ClaudeConversationsImporter:
    """Create ChatGPTImporter with dependencies."""
    return ClaudeConversationsImporter(project_config.home, markdown_processor)


ClaudeConversationsImporterDep = Annotated[
    ClaudeConversationsImporter, Depends(get_claude_conversations_importer)
]


async def get_claude_projects_importer(
    project_config: ProjectConfigDep, markdown_processor: MarkdownProcessorDep
) -> ClaudeProjectsImporter:
    """Create ChatGPTImporter with dependencies."""
    return ClaudeProjectsImporter(project_config.home, markdown_processor)


ClaudeProjectsImporterDep = Annotated[ClaudeProjectsImporter, Depends(get_claude_projects_importer)]


async def get_memory_json_importer(
    project_config: ProjectConfigDep, markdown_processor: MarkdownProcessorDep
) -> MemoryJsonImporter:
    """Create ChatGPTImporter with dependencies."""
    return MemoryJsonImporter(project_config.home, markdown_processor)


MemoryJsonImporterDep = Annotated[MemoryJsonImporter, Depends(get_memory_json_importer)]


# V2 Import dependencies


async def get_chatgpt_importer_v2(
    project_config: ProjectConfigV2Dep, markdown_processor: MarkdownProcessorV2Dep
) -> ChatGPTImporter:
    """Create ChatGPTImporter with v2 dependencies."""
    return ChatGPTImporter(project_config.home, markdown_processor)


ChatGPTImporterV2Dep = Annotated[ChatGPTImporter, Depends(get_chatgpt_importer_v2)]


async def get_claude_conversations_importer_v2(
    project_config: ProjectConfigV2Dep, markdown_processor: MarkdownProcessorV2Dep
) -> ClaudeConversationsImporter:
    """Create ClaudeConversationsImporter with v2 dependencies."""
    return ClaudeConversationsImporter(project_config.home, markdown_processor)


ClaudeConversationsImporterV2Dep = Annotated[
    ClaudeConversationsImporter, Depends(get_claude_conversations_importer_v2)
]


async def get_claude_projects_importer_v2(
    project_config: ProjectConfigV2Dep, markdown_processor: MarkdownProcessorV2Dep
) -> ClaudeProjectsImporter:
    """Create ClaudeProjectsImporter with v2 dependencies."""
    return ClaudeProjectsImporter(project_config.home, markdown_processor)


ClaudeProjectsImporterV2Dep = Annotated[
    ClaudeProjectsImporter, Depends(get_claude_projects_importer_v2)
]


async def get_memory_json_importer_v2(
    project_config: ProjectConfigV2Dep, markdown_processor: MarkdownProcessorV2Dep
) -> MemoryJsonImporter:
    """Create MemoryJsonImporter with v2 dependencies."""
    return MemoryJsonImporter(project_config.home, markdown_processor)


MemoryJsonImporterV2Dep = Annotated[MemoryJsonImporter, Depends(get_memory_json_importer_v2)]
