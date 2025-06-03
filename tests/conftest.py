"""Common test fixtures."""
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import AsyncGenerator
from unittest import mock
from unittest.mock import patch

import pytest
import pytest_asyncio
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import basic_memory.mcp.project_session
from basic_memory import db
from basic_memory.config import ProjectConfig, BasicMemoryConfig, ConfigManager
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


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).parent.parent

@pytest.fixture
def config_home(tmp_path, monkeypatch) -> Path:
    # Patch HOME environment variable for the duration of the test
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path

@pytest.fixture(scope="function", autouse=True)
def app_config(config_home, tmp_path, monkeypatch) -> BasicMemoryConfig:
    """Create test app configuration."""
    # Create a basic config without depending on test_project to avoid circular dependency
    projects = {"test-project": str(config_home)}
    app_config = BasicMemoryConfig(env="test", projects=projects, default_project="test-project", update_permalinks_on_move=True)
    

    # Patch the module app_config instance for the duration of the test
    monkeypatch.setattr("basic_memory.config.app_config", app_config)
    return app_config

@pytest.fixture(autouse=True)
def config_manager(app_config: BasicMemoryConfig, project_config: ProjectConfig, config_home: Path, monkeypatch) -> ConfigManager:
    # Create a new ConfigManager that uses the test home directory
    config_manager = ConfigManager()
    # Update its paths to use the test directory
    config_manager.config_dir = config_home / ".basic-memory"
    config_manager.config_file = config_manager.config_dir / "config.json"
    config_manager.config_dir.mkdir(parents=True, exist_ok=True)
    
    # Override the config directly instead of relying on disk load
    config_manager.config = app_config
    
    # Ensure the config file is written to disk
    config_manager.save_config(app_config)
    
    # Patch the config_manager in all locations where it's imported
    monkeypatch.setattr("basic_memory.config.config_manager", config_manager)
    monkeypatch.setattr("basic_memory.services.project_service.config_manager", config_manager)
    # Mock get_project_config to return test project config for test-project, fallback for others
    def mock_get_project_config(project_name=None):
        if project_name == "test-project" or project_name is None:
            return project_config
        # For any other project name, return a default config pointing to test location
        fallback_config = ProjectConfig(name=project_name or "main", home=Path(config_home))
        return fallback_config
    monkeypatch.setattr("basic_memory.mcp.project_session.get_project_config", mock_get_project_config)
    
    # Patch the project config that CLI commands import (only modules that actually import config)
    monkeypatch.setattr("basic_memory.cli.commands.project.config", project_config)
    monkeypatch.setattr("basic_memory.cli.commands.sync.config", project_config)
    monkeypatch.setattr("basic_memory.cli.commands.status.config", project_config)
    monkeypatch.setattr("basic_memory.cli.commands.import_memory_json.config", project_config)
    monkeypatch.setattr("basic_memory.cli.commands.import_claude_projects.config", project_config)
    monkeypatch.setattr("basic_memory.cli.commands.import_claude_conversations.config", project_config)
    monkeypatch.setattr("basic_memory.cli.commands.import_chatgpt.config", project_config)
    return config_manager

@pytest.fixture(autouse=True)
def project_session(test_project: Project):
    # initialize the project session with the test project
    basic_memory.mcp.project_session.session.initialize(test_project.name)
    # Explicitly set current project as well to ensure it's used
    basic_memory.mcp.project_session.session.set_current_project(test_project.name)
    return basic_memory.mcp.project_session.session


@pytest.fixture(scope="function", autouse=True)
def project_config(test_project, monkeypatch):
    """Create test project configuration."""

    project_config = ProjectConfig(
        name=test_project.name,
        home=Path(test_project.path),
    )

    # Patch the config module project config for the duration of the test
    monkeypatch.setattr("basic_memory.config.config", project_config)
    return project_config


@dataclass
class TestConfig:
    config_home: Path
    project_config: ProjectConfig
    app_config: BasicMemoryConfig
    config_manager: ConfigManager

@pytest.fixture
def test_config(config_home, project_config, app_config, config_manager) -> TestConfig:
    """All test configuration fixtures"""
    
    @dataclass
    class TestConfig:
        config_home: Path
        project_config: ProjectConfig
        app_config: BasicMemoryConfig
        config_manager: ConfigManager
        
    return TestConfig(config_home, project_config, app_config, config_manager)


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
async def test_project(config_home, engine_factory) -> Project:
    """Create a test project to be used as context for other repositories."""
    project_data = {
        "name": "test-project",
        "description": "Project used as context for tests",
        "path": str(config_home),
        "is_active": True,
        "is_default": True,  # Explicitly set as the default project
    }
    engine, session_maker = engine_factory
    project_repository = ProjectRepository(session_maker)
    project = await project_repository.create(project_data)
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
def file_service(
    project_config: ProjectConfig, markdown_processor: MarkdownProcessor
) -> FileService:
    """Create FileService instance."""
    return FileService(project_config.home, markdown_processor)


@pytest.fixture
def markdown_processor(entity_parser: EntityParser) -> MarkdownProcessor:
    """Create writer instance."""
    return MarkdownProcessor(entity_parser)


@pytest.fixture
def link_resolver(entity_repository: EntityRepository, search_service: SearchService):
    """Create parser instance."""
    return LinkResolver(entity_repository, search_service)


@pytest.fixture
def entity_parser(project_config):
    """Create parser instance."""
    return EntityParser(project_config.home)


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
async def directory_service(entity_repository, project_config) -> DirectoryService:
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
def test_files(project_config, project_root) -> dict[str, Path]:
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
        dest_path = project_config.home / src_path.name
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file
        dest_path.write_bytes(content)
        project_files[name] = dest_path

    return project_files


@pytest_asyncio.fixture
async def synced_files(sync_service, project_config, test_files):
    # Initial sync - should create forward reference
    await sync_service.sync(project_config.home)
    return test_files