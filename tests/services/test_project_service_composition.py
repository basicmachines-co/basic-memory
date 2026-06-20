"""Tests for runtime-neutral Basic Memory service composition."""

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.repository import RelationRepository
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.services import EntityService, FileService
from basic_memory.services.composition import (
    build_default_project_search_bundle,
    build_default_project_service_bundle,
)


class CustomEntityService(EntityService):
    """Concrete subclass used to prove runtime-specific service injection."""


def test_build_default_project_service_bundle_wires_default_project_graph(tmp_path: Path) -> None:
    session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker()
    app_config = BasicMemoryConfig()
    entity_parser = EntityParser(tmp_path)
    markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
    file_service = FileService(tmp_path, markdown_processor, app_config=app_config)
    sync_relation_repository = RelationRepository(project_id=7)

    bundle = build_default_project_service_bundle(
        project_id=7,
        session_maker=session_maker,
        entity_parser=entity_parser,
        file_service=file_service,
        app_config=app_config,
        database_backend=DatabaseBackend.POSTGRES,
        entity_service_factory=CustomEntityService,
        sync_relation_repository=sync_relation_repository,
    )

    assert bundle.project_id == 7
    assert bundle.entity_repository.project_id == 7
    assert bundle.observation_repository.project_id == 7
    assert bundle.relation_repository.project_id == 7
    assert isinstance(bundle.search_repository, PostgresSearchRepository)
    assert bundle.search_service.repository is bundle.search_repository
    assert bundle.search_service.entity_repository is bundle.entity_repository
    assert bundle.search_service.file_service is file_service
    assert bundle.link_resolver.entity_repository is bundle.entity_repository
    assert bundle.entity_service.__class__ is CustomEntityService
    assert bundle.entity_service.repository is bundle.entity_repository
    assert bundle.entity_service.relation_repository is bundle.relation_repository
    assert bundle.entity_service.file_service is file_service
    assert bundle.entity_service.search_service is bundle.search_service
    assert bundle.relation_resolution.relation_repository is sync_relation_repository
    assert bundle.relation_resolution.entity_repository is bundle.entity_repository
    assert bundle.relation_resolution.link_resolver is bundle.link_resolver
    assert bundle.relation_resolution.entity_indexer is bundle.search_service
    assert bundle.sync_service.entity_service is bundle.entity_service
    assert bundle.sync_service.entity_repository is bundle.entity_repository
    assert bundle.sync_service.relation_repository is sync_relation_repository
    assert bundle.sync_service.search_service is bundle.search_service
    assert bundle.sync_service.file_service is file_service


def test_build_default_project_search_bundle_wires_project_search_service(
    tmp_path: Path,
) -> None:
    session_maker: async_sessionmaker[AsyncSession] = async_sessionmaker()
    app_config = BasicMemoryConfig()
    entity_parser = EntityParser(tmp_path)
    markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
    file_service = FileService(tmp_path, markdown_processor, app_config=app_config)

    bundle = build_default_project_search_bundle(
        project_id=11,
        session_maker=session_maker,
        file_service=file_service,
        app_config=app_config,
        database_backend=DatabaseBackend.POSTGRES,
    )

    assert bundle.project_id == 11
    assert bundle.entity_repository.project_id == 11
    assert isinstance(bundle.search_repository, PostgresSearchRepository)
    assert bundle.search_service.repository is bundle.search_repository
    assert bundle.search_service.entity_repository is bundle.entity_repository
    assert bundle.search_service.file_service is file_service
