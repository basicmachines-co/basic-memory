"""Service dependency injection for basic-memory.

This module provides service-layer dependencies:
- EntityParser, MarkdownProcessor
- FileService, EntityService
- SearchService, LinkResolver, ContextService
- SyncService, ProjectService, DirectoryService
"""

from typing import Annotated

from fastapi import Depends
from loguru import logger

from basic_memory.deps.config import AppConfigDep
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
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.services import EntityService, ProjectService
from basic_memory.services.context_service import ContextService
from basic_memory.services.directory_service import DirectoryService
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService
from basic_memory.sync import SyncService


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
) -> SearchService:
    """Create SearchService with dependencies."""
    return SearchService(search_repository, entity_repository, file_service)


SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]


async def get_search_service_v2(  # pragma: no cover
    search_repository: SearchRepositoryV2Dep,
    entity_repository: EntityRepositoryV2Dep,
    file_service: FileServiceV2Dep,
) -> SearchService:
    """Create SearchService for v2 API."""
    return SearchService(search_repository, entity_repository, file_service)


SearchServiceV2Dep = Annotated[SearchService, Depends(get_search_service_v2)]


async def get_search_service_v2_external(
    search_repository: SearchRepositoryV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
) -> SearchService:
    """Create SearchService for v2 API (uses external_id)."""
    return SearchService(search_repository, entity_repository, file_service)


SearchServiceV2ExternalDep = Annotated[SearchService, Depends(get_search_service_v2_external)]


# --- Link Resolver ---


async def get_link_resolver(
    entity_repository: EntityRepositoryDep, search_service: SearchServiceDep
) -> LinkResolver:
    return LinkResolver(entity_repository=entity_repository, search_service=search_service)


LinkResolverDep = Annotated[LinkResolver, Depends(get_link_resolver)]


async def get_link_resolver_v2(  # pragma: no cover
    entity_repository: EntityRepositoryV2Dep, search_service: SearchServiceV2Dep
) -> LinkResolver:
    return LinkResolver(entity_repository=entity_repository, search_service=search_service)


LinkResolverV2Dep = Annotated[LinkResolver, Depends(get_link_resolver_v2)]


async def get_link_resolver_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep, search_service: SearchServiceV2ExternalDep
) -> LinkResolver:
    return LinkResolver(entity_repository=entity_repository, search_service=search_service)


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


async def get_entity_service_v2(  # pragma: no cover
    entity_repository: EntityRepositoryV2Dep,
    observation_repository: ObservationRepositoryV2Dep,
    relation_repository: RelationRepositoryV2Dep,
    entity_parser: EntityParserV2Dep,
    file_service: FileServiceV2Dep,
    link_resolver: LinkResolverV2Dep,
    search_service: SearchServiceV2Dep,
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


async def get_entity_service_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep,
    observation_repository: ObservationRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    entity_parser: EntityParserV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
    link_resolver: LinkResolverV2ExternalDep,
    search_service: SearchServiceV2ExternalDep,
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
        search_service=search_service,
        app_config=app_config,
    )


EntityServiceV2ExternalDep = Annotated[EntityService, Depends(get_entity_service_v2_external)]


# --- Context Service ---


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


async def get_context_service_v2(  # pragma: no cover
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


async def get_context_service_v2_external(
    search_repository: SearchRepositoryV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    observation_repository: ObservationRepositoryV2ExternalDep,
) -> ContextService:
    """Create ContextService for v2 API (uses external_id)."""
    return ContextService(
        search_repository=search_repository,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
    )


ContextServiceV2ExternalDep = Annotated[ContextService, Depends(get_context_service_v2_external)]


# --- Sync Service ---


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


async def get_sync_service_v2_external(
    app_config: AppConfigDep,
    entity_service: EntityServiceV2ExternalDep,
    entity_parser: EntityParserV2ExternalDep,
    entity_repository: EntityRepositoryV2ExternalDep,
    relation_repository: RelationRepositoryV2ExternalDep,
    project_repository: ProjectRepositoryDep,
    search_service: SearchServiceV2ExternalDep,
    file_service: FileServiceV2ExternalDep,
) -> SyncService:  # pragma: no cover
    """Create SyncService for v2 API (uses external_id)."""
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


SyncServiceV2ExternalDep = Annotated[SyncService, Depends(get_sync_service_v2_external)]


# --- Project Service ---


async def get_project_service(
    project_repository: ProjectRepositoryDep,
) -> ProjectService:
    """Create ProjectService with repository."""
    return ProjectService(repository=project_repository)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


# --- Directory Service ---


async def get_directory_service(
    entity_repository: EntityRepositoryDep,
) -> DirectoryService:
    """Create DirectoryService with dependencies."""
    return DirectoryService(
        entity_repository=entity_repository,
    )


DirectoryServiceDep = Annotated[DirectoryService, Depends(get_directory_service)]


async def get_directory_service_v2(  # pragma: no cover
    entity_repository: EntityRepositoryV2Dep,
) -> DirectoryService:
    """Create DirectoryService for v2 API (uses integer project_id from path)."""
    return DirectoryService(
        entity_repository=entity_repository,
    )


DirectoryServiceV2Dep = Annotated[DirectoryService, Depends(get_directory_service_v2)]


async def get_directory_service_v2_external(
    entity_repository: EntityRepositoryV2ExternalDep,
) -> DirectoryService:
    """Create DirectoryService for v2 API (uses external_id from path)."""
    return DirectoryService(
        entity_repository=entity_repository,
    )


DirectoryServiceV2ExternalDep = Annotated[
    DirectoryService, Depends(get_directory_service_v2_external)
]
