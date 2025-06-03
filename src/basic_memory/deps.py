"""Dependency injection functions for basic-memory services."""

from typing import Annotated
from loguru import logger

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
)
import pathlib

from basic_memory import db
from basic_memory.config import ProjectConfig, BasicMemoryConfig
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
from basic_memory.repository.search_repository import SearchRepository
from basic_memory.services import EntityService, ProjectService
from basic_memory.services.context_service import ContextService
from basic_memory.services.directory_service import DirectoryService
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService
from basic_memory.sync import SyncService
from basic_memory.config import app_config


def get_app_config() -> BasicMemoryConfig:  # pragma: no cover
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

    project_obj = await project_repository.get_by_permalink(str(project))
    if project_obj:
        return ProjectConfig(name=project_obj.name, home=pathlib.Path(project_obj.path))

    # Not found
    raise HTTPException(  # pragma: no cover
        status_code=status.HTTP_404_NOT_FOUND, detail=f"Project '{project}' not found."
    )


ProjectConfigDep = Annotated[ProjectConfig, Depends(get_project_config)]  # pragma: no cover

## sqlalchemy


async def get_engine_factory(
    app_config: AppConfigDep,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:  # pragma: no cover
    """Get engine and session maker."""
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

    # Try by permalink first (most common case with URL paths)
    project_obj = await project_repository.get_by_permalink(str(project))
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


async def get_observation_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance for the current project."""
    return ObservationRepository(session_maker, project_id=project_id)


ObservationRepositoryDep = Annotated[ObservationRepository, Depends(get_observation_repository)]


async def get_relation_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> RelationRepository:
    """Create a RelationRepository instance for the current project."""
    return RelationRepository(session_maker, project_id=project_id)


RelationRepositoryDep = Annotated[RelationRepository, Depends(get_relation_repository)]


async def get_search_repository(
    session_maker: SessionMakerDep,
    project_id: ProjectIdDep,
) -> SearchRepository:
    """Create a SearchRepository instance for the current project."""
    return SearchRepository(session_maker, project_id=project_id)


SearchRepositoryDep = Annotated[SearchRepository, Depends(get_search_repository)]


# ProjectInfoRepository is deprecated and will be removed in a future version.
# Use ProjectRepository instead, which has the same functionality plus more project-specific operations.

## services


async def get_entity_parser(project_config: ProjectConfigDep) -> EntityParser:
    return EntityParser(project_config.home)


EntityParserDep = Annotated["EntityParser", Depends(get_entity_parser)]


async def get_markdown_processor(entity_parser: EntityParserDep) -> MarkdownProcessor:
    return MarkdownProcessor(entity_parser)


MarkdownProcessorDep = Annotated[MarkdownProcessor, Depends(get_markdown_processor)]


async def get_file_service(
    project_config: ProjectConfigDep, markdown_processor: MarkdownProcessorDep
) -> FileService:
    logger.debug(
        f"Creating FileService for project: {project_config.name}, base_path: {project_config.home}"
    )
    file_service = FileService(project_config.home, markdown_processor)
    logger.debug(f"Created FileService for project: {file_service} ")
    return file_service


FileServiceDep = Annotated[FileService, Depends(get_file_service)]


async def get_entity_service(
    entity_repository: EntityRepositoryDep,
    observation_repository: ObservationRepositoryDep,
    relation_repository: RelationRepositoryDep,
    entity_parser: EntityParserDep,
    file_service: FileServiceDep,
    link_resolver: "LinkResolverDep",
) -> EntityService:
    """Create EntityService with repository."""
    return EntityService(
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        entity_parser=entity_parser,
        file_service=file_service,
        link_resolver=link_resolver,
    )


EntityServiceDep = Annotated[EntityService, Depends(get_entity_service)]


async def get_search_service(
    search_repository: SearchRepositoryDep,
    entity_repository: EntityRepositoryDep,
    file_service: FileServiceDep,
) -> SearchService:
    """Create SearchService with dependencies."""
    return SearchService(search_repository, entity_repository, file_service)


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]


async def get_link_resolver(
    entity_repository: EntityRepositoryDep, search_service: SearchServiceDep
) -> LinkResolver:
    return LinkResolver(entity_repository=entity_repository, search_service=search_service)


LinkResolverDep = Annotated[LinkResolver, Depends(get_link_resolver)]


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


async def get_sync_service(
    entity_service: EntityServiceDep,
    entity_parser: EntityParserDep,
    entity_repository: EntityRepositoryDep,
    relation_repository: RelationRepositoryDep,
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
        search_service=search_service,
        file_service=file_service,
    )


SyncServiceDep = Annotated[SyncService, Depends(get_sync_service)]


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
