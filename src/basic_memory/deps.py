"""Dependency injection functions for basic-memory services."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
)

from basic_memory import db
from basic_memory.config import ProjectConfig, config
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.repository.search_repository import SearchRepository
from basic_memory.services import (
    EntityService,
)
from basic_memory.services.context_service import ContextService
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService


## project


def get_project_config() -> ProjectConfig:  # pragma: no cover
    return config


ProjectConfigDep = Annotated[ProjectConfig, Depends(get_project_config)]  # pragma: no cover


## sqlalchemy


async def get_engine_factory(
    project_config: ProjectConfigDep,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:  # pragma: no cover
    """Get engine and session maker."""
    return await db.get_or_create_db(project_config.database_path)


EngineFactoryDep = Annotated[
    tuple[AsyncEngine, async_sessionmaker[AsyncSession]], Depends(get_engine_factory)
]


async def get_session_maker(engine_factory: EngineFactoryDep) -> async_sessionmaker[AsyncSession]:
    """Get session maker."""
    _, session_maker = engine_factory
    return session_maker


SessionMakerDep = Annotated[async_sessionmaker, Depends(get_session_maker)]


## repositories


async def get_entity_repository(
    session_maker: SessionMakerDep,
) -> EntityRepository:
    """Create an EntityRepository instance."""
    return EntityRepository(session_maker)


EntityRepositoryDep = Annotated[EntityRepository, Depends(get_entity_repository)]


async def get_observation_repository(
    session_maker: SessionMakerDep,
) -> ObservationRepository:
    """Create an ObservationRepository instance."""
    return ObservationRepository(session_maker)


ObservationRepositoryDep = Annotated[ObservationRepository, Depends(get_observation_repository)]


async def get_relation_repository(
    session_maker: SessionMakerDep,
) -> RelationRepository:
    """Create a RelationRepository instance."""
    return RelationRepository(session_maker)


RelationRepositoryDep = Annotated[RelationRepository, Depends(get_relation_repository)]


async def get_search_repository(
    session_maker: SessionMakerDep,
) -> SearchRepository:
    """Create a SearchRepository instance."""
    return SearchRepository(session_maker)


SearchRepositoryDep = Annotated[SearchRepository, Depends(get_search_repository)]


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
    return FileService(project_config.home, markdown_processor)


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
    search_repository: SearchRepositoryDep, entity_repository: EntityRepositoryDep
) -> ContextService:
    return ContextService(search_repository, entity_repository)


ContextServiceDep = Annotated[ContextService, Depends(get_context_service)]
