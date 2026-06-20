"""Runtime-neutral Basic Memory service composition helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.indexing.relation_resolution import RepositoryRelationResolutionRuntime
from basic_memory.markdown import EntityParser
from basic_memory.repository import (
    EntityRepository,
    ObservationRepository,
    ProjectRepository,
    RelationRepository,
)
from basic_memory.repository.search_repository import SearchRepository, create_search_repository
from basic_memory.runtime import ProjectId
from basic_memory.services.entity_service import EntityService
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService
from basic_memory.sync import SyncService


class ProjectEntityServiceFactory(Protocol):
    """Factory for runtime-specific EntityService subclasses."""

    def __call__(
        self,
        *,
        entity_parser: EntityParser,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        relation_repository: RelationRepository,
        file_service: FileService,
        link_resolver: LinkResolver,
        session_maker: async_sessionmaker[AsyncSession],
        search_service: SearchService,
        app_config: BasicMemoryConfig,
    ) -> EntityService: ...


@dataclass(frozen=True, slots=True)
class BasicMemoryProjectServiceBundle:
    """Repository and service graph for one Basic Memory project."""

    project_id: ProjectId
    entity_repository: EntityRepository
    observation_repository: ObservationRepository
    relation_repository: RelationRepository
    project_repository: ProjectRepository
    search_repository: SearchRepository
    search_service: SearchService
    link_resolver: LinkResolver
    entity_service: EntityService
    relation_resolution: RepositoryRelationResolutionRuntime
    sync_service: SyncService


@dataclass(frozen=True, slots=True)
class BasicMemoryProjectSearchBundle:
    """Search repository and service graph for one Basic Memory project."""

    project_id: ProjectId
    entity_repository: EntityRepository
    search_repository: SearchRepository
    search_service: SearchService


def build_default_project_search_bundle(
    *,
    project_id: ProjectId,
    session_maker: async_sessionmaker[AsyncSession],
    file_service: FileService,
    app_config: BasicMemoryConfig | None = None,
    database_backend: DatabaseBackend | None = None,
) -> BasicMemoryProjectSearchBundle:
    """Compose default project-scoped search services without entity/sync services."""
    entity_repository = EntityRepository(project_id=project_id)
    search_repository = create_search_repository(
        session_maker,
        project_id=project_id,
        app_config=app_config,
        database_backend=database_backend,
    )
    search_service = SearchService(
        search_repository,
        entity_repository,
        file_service,
        session_maker,
    )
    return BasicMemoryProjectSearchBundle(
        project_id=project_id,
        entity_repository=entity_repository,
        search_repository=search_repository,
        search_service=search_service,
    )


def build_default_project_service_bundle(
    *,
    project_id: ProjectId,
    session_maker: async_sessionmaker[AsyncSession],
    entity_parser: EntityParser,
    file_service: FileService,
    app_config: BasicMemoryConfig,
    database_backend: DatabaseBackend | None = None,
    entity_service_factory: ProjectEntityServiceFactory | None = None,
    sync_relation_repository: RelationRepository | None = None,
    project_repository: ProjectRepository | None = None,
) -> BasicMemoryProjectServiceBundle:
    """Compose the default repository-backed Basic Memory project services."""
    entity_repository = EntityRepository(project_id=project_id)
    observation_repository = ObservationRepository(project_id=project_id)
    relation_repository = RelationRepository(project_id=project_id)
    project_repository = project_repository or ProjectRepository()
    sync_relation_repository = sync_relation_repository or relation_repository
    search_repository = create_search_repository(
        session_maker,
        project_id=project_id,
        app_config=app_config,
        database_backend=database_backend,
    )
    search_service = SearchService(
        search_repository,
        entity_repository,
        file_service,
        session_maker,
    )
    link_resolver = LinkResolver(
        entity_repository=entity_repository,
        search_service=search_service,
        session_maker=session_maker,
    )

    if entity_service_factory is None:
        entity_service = EntityService(
            entity_parser=entity_parser,
            entity_repository=entity_repository,
            observation_repository=observation_repository,
            relation_repository=relation_repository,
            file_service=file_service,
            link_resolver=link_resolver,
            session_maker=session_maker,
            search_service=search_service,
            app_config=app_config,
        )
    else:
        entity_service = entity_service_factory(
            entity_parser=entity_parser,
            entity_repository=entity_repository,
            observation_repository=observation_repository,
            relation_repository=relation_repository,
            file_service=file_service,
            link_resolver=link_resolver,
            session_maker=session_maker,
            search_service=search_service,
            app_config=app_config,
        )

    relation_resolution = RepositoryRelationResolutionRuntime(
        session_maker=session_maker,
        relation_repository=sync_relation_repository,
        entity_repository=entity_repository,
        link_resolver=link_resolver,
        entity_indexer=search_service,
    )
    sync_service = SyncService(
        app_config=app_config,
        entity_service=entity_service,
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        relation_repository=sync_relation_repository,
        project_repository=project_repository,
        search_service=search_service,
        file_service=file_service,
        session_maker=session_maker,
    )

    return BasicMemoryProjectServiceBundle(
        project_id=project_id,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        project_repository=project_repository,
        search_repository=search_repository,
        search_service=search_service,
        link_resolver=link_resolver,
        entity_service=entity_service,
        relation_resolution=relation_resolution,
        sync_service=sync_service,
    )
