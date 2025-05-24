"""Common test fixtures."""

from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import ProjectConfig, BasicMemoryConfig
from basic_memory.db import DatabaseType
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Base
from basic_memory.models.knowledge import Entity
from basic_memory.models.project import Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.repository.search_repository import SearchRepository
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.services import (
    EntityService,
    ProjectService,
)
from basic_memory.services.directory_service import DirectoryService
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService
from basic_memory.sync.sync_service import SyncService
from basic_memory.sync.watch_service import WatchService
from basic_memory.config import app_config as basic_memory_app_config  # noqa: F401


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).parent.parent


@pytest.fixture
def app_config(test_config: ProjectConfig, monkeypatch) -> BasicMemoryConfig:
    projects = {test_config.name: str(test_config.home)}
    app_config = BasicMemoryConfig(env="test", projects=projects, default_project=test_config.name)

    # set the module app_config instance project list
    basic_memory_app_config.projects = projects
    basic_memory_app_config.default_project = test_config.name

    return app_config


@pytest.fixture
def test_config(tmp_path) -> ProjectConfig:
    """Test configuration using in-memory DB."""
    config = ProjectConfig(name="test-project", home=tmp_path)

    (tmp_path / config.home.name).mkdir(parents=True, exist_ok=True)
    logger.info(f"project config home: {config.home}")
    return config


@pytest_asyncio.fixture(scope="function")
async def engine_factory(
    app_config,
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]], None]:
    """Create an engine and session factory using an in-memory SQLite database."""
    async with db.engine_session_factory(
        db_path=app_config.database_path, db_type=DatabaseType.MEMORY
    ) as (engine, session_maker):
        # Create all tables for the DB the engine is connected to
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        yield engine, session_maker


@pytest_asyncio.fixture
async def session_maker(engine_factory) -> async_sessionmaker[AsyncSession]:
    """Get session maker for tests."""
    _, session_maker = engine_factory
    return session_maker


## Repositories


@pytest_asyncio.fixture(scope="function")
async def entity_repository(
    session_maker: async_sessionmaker[AsyncSession], test_project: Project
) -> EntityRepository:
    """Create an EntityRepository instance with project context."""
    return EntityRepository(session_maker, project_id=test_project.id)


@pytest_asyncio.fixture(scope="function")
async def observation_repository(
    session_maker: async_sessionmaker[AsyncSession], test_project: Project
) -> ObservationRepository:
    """Create an ObservationRepository instance with project context."""
    return ObservationRepository(session_maker, project_id=test_project.id)


@pytest_asyncio.fixture(scope="function")
async def relation_repository(
    session_maker: async_sessionmaker[AsyncSession], test_project: Project
) -> RelationRepository:
    """Create a RelationRepository instance with project context."""
    return RelationRepository(session_maker, project_id=test_project.id)


@pytest_asyncio.fixture(scope="function")
async def project_repository(
    session_maker: async_sessionmaker[AsyncSession],
) -> ProjectRepository:
    """Create a ProjectRepository instance."""
    return ProjectRepository(session_maker)


@pytest_asyncio.fixture(scope="function")
async def test_project(test_config, project_repository: ProjectRepository) -> Project:
    """Create a test project to be used as context for other repositories."""
    project_data = {
        "name": test_config.name,
        "description": "Project used as context for tests",
        "path": str(test_config.home),
        "is_active": True,
        "is_default": True,  # Explicitly set as the default project
    }
    project = await project_repository.create(project_data)
    logger.info(f"Created test project with permalink: {project.permalink}")
    return project


## Services


@pytest_asyncio.fixture
async def entity_service(
    entity_repository: EntityRepository,
    observation_repository: ObservationRepository,
    relation_repository: RelationRepository,
    entity_parser: EntityParser,
    file_service: FileService,
    link_resolver: LinkResolver,
) -> EntityService:
    """Create EntityService."""
    return EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
    )


@pytest.fixture
def file_service(test_config: ProjectConfig, markdown_processor: MarkdownProcessor) -> FileService:
    """Create FileService instance."""
    return FileService(test_config.home, markdown_processor)


@pytest.fixture
def markdown_processor(entity_parser: EntityParser) -> MarkdownProcessor:
    """Create writer instance."""
    return MarkdownProcessor(entity_parser)


@pytest.fixture
def link_resolver(entity_repository: EntityRepository, search_service: SearchService):
    """Create parser instance."""
    return LinkResolver(entity_repository, search_service)


@pytest.fixture
def entity_parser(test_config):
    """Create parser instance."""
    return EntityParser(test_config.home)


@pytest_asyncio.fixture
async def sync_service(
    app_config: BasicMemoryConfig,
    entity_service: EntityService,
    entity_parser: EntityParser,
    entity_repository: EntityRepository,
    relation_repository: RelationRepository,
    search_service: SearchService,
    file_service: FileService,
) -> SyncService:
    """Create sync service for testing."""
    return SyncService(
        app_config=app_config,
        entity_service=entity_service,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        entity_parser=entity_parser,
        search_service=search_service,
        file_service=file_service,
    )


@pytest_asyncio.fixture
async def directory_service(entity_repository, test_config) -> DirectoryService:
    """Create directory service for testing."""
    return DirectoryService(
        entity_repository=entity_repository,
    )


@pytest_asyncio.fixture
async def search_repository(session_maker, test_project: Project):
    """Create SearchRepository instance with project context"""
    return SearchRepository(session_maker, project_id=test_project.id)


@pytest_asyncio.fixture(autouse=True)
async def init_search_index(search_service):
    await search_service.init_search_index()


@pytest_asyncio.fixture
async def search_service(
    search_repository: SearchRepository,
    entity_repository: EntityRepository,
    file_service: FileService,
) -> SearchService:
    """Create and initialize search service"""
    service = SearchService(search_repository, entity_repository, file_service)
    await service.init_search_index()
    return service


@pytest_asyncio.fixture(scope="function")
async def sample_entity(entity_repository: EntityRepository) -> Entity:
    """Create a sample entity for testing."""
    entity_data = {
        "project_id": entity_repository.project_id,
        "title": "Test Entity",
        "entity_type": "test",
        "permalink": "test/test-entity",
        "file_path": "test/test_entity.md",
        "content_type": "text/markdown",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    return await entity_repository.create(entity_data)


@pytest_asyncio.fixture
async def project_service(
    project_repository: ProjectRepository,
) -> ProjectService:
    """Create ProjectService with repository."""
    return ProjectService(repository=project_repository)


@pytest_asyncio.fixture
async def full_entity(sample_entity, entity_repository, file_service, entity_service) -> Entity:
    """Create a search test entity."""

    # Create test entity
    entity, created = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Search_Entity",
            folder="test",
            entity_type="test",
            project=entity_repository.project_id,
            content=dedent("""
                ## Observations
                - [tech] Tech note
                - [design] Design note

                ## Relations
                - out1 [[Test Entity]]
                - out2 [[Test Entity]]
                """),
        )
    )
    return entity


@pytest_asyncio.fixture
async def test_graph(
    entity_repository,
    relation_repository,
    observation_repository,
    search_service,
    file_service,
    entity_service,
):
    """Create a test knowledge graph with entities, relations and observations."""

    # Create some test entities in reverse order so they will be linked
    deeper, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Deeper Entity",
            entity_type="deeper",
            folder="test",
            project=entity_repository.project_id,
            content=dedent("""
                # Deeper Entity
                """),
        )
    )

    deep, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Deep Entity",
            entity_type="deep",
            folder="test",
            project=entity_repository.project_id,
            content=dedent("""
                # Deep Entity
                - deeper_connection [[Deeper Entity]]
                """),
        )
    )

    connected_2, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Connected Entity 2",
            entity_type="test",
            folder="test",
            project=entity_repository.project_id,
            content=dedent("""
                # Connected Entity 2
                - deep_connection [[Deep Entity]]
                """),
        )
    )

    connected_1, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Connected Entity 1",
            entity_type="test",
            folder="test",
            project=entity_repository.project_id,
            content=dedent("""
                # Connected Entity 1
                - [note] Connected 1 note
                - connected_to [[Connected Entity 2]]
                """),
        )
    )

    root, _ = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Root",
            entity_type="test",
            folder="test",
            project=entity_repository.project_id,
            content=dedent("""
                # Root Entity
                - [note] Root note 1
                - [tech] Root tech note
                - connects_to [[Connected Entity 1]]
                """),
        )
    )

    # get latest
    entities = await entity_repository.find_all()
    relations = await relation_repository.find_all()

    # Index everything for search
    for entity in entities:
        await search_service.index_entity(entity)

    return {
        "root": root,
        "connected1": connected_1,
        "connected2": connected_2,
        "deep": deep,
        "observations": [e.observations for e in entities],
        "relations": relations,
    }


@pytest.fixture
def watch_service(app_config: BasicMemoryConfig, project_repository) -> WatchService:
    return WatchService(app_config=app_config, project_repository=project_repository)


@pytest.fixture
def test_files(test_config, project_root) -> dict[str, Path]:
    """Copy test files into the project directory.

    Returns a dict mapping file names to their paths in the project dir.
    """
    # Source files relative to tests directory
    source_files = {
        "pdf": Path(project_root / "tests/Non-MarkdownFileSupport.pdf"),
        "image": Path(project_root / "tests/Screenshot.png"),
    }

    # Create copies in temp project directory
    project_files = {}
    for name, src_path in source_files.items():
        # Read source file
        content = src_path.read_bytes()

        # Create destination path and ensure parent dirs exist
        dest_path = test_config.home / src_path.name
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file
        dest_path.write_bytes(content)
        project_files[name] = dest_path

    return project_files


@pytest_asyncio.fixture
async def synced_files(sync_service, test_config, test_files):
    # Initial sync - should create forward reference
    await sync_service.sync(test_config.home)
    return test_files
