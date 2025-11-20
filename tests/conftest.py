"""Common test fixtures."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import AsyncGenerator, Literal

import os
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import ProjectConfig, BasicMemoryConfig, ConfigManager, DatabaseBackend
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


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgres", id="postgres", marks=pytest.mark.postgres),
    ]
)
def db_backend(request) -> Literal["sqlite", "postgres"]:
    """Parametrize tests to run against both SQLite and Postgres.

    Usage:
        pytest                          # Runs tests against SQLite only (default)
        pytest -m postgres              # Runs tests against Postgres only
        pytest -m "not postgres"        # Runs tests against SQLite only
        pytest --run-all-backends       # Runs tests against both backends

    Note: Only tests that use database fixtures (engine_factory, session_maker, etc.)
    will be parametrized. Tests that don't use the database won't be affected.
    """
    return request.param


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).parent.parent


@pytest.fixture
def config_home(tmp_path, monkeypatch) -> Path:
    # Patch HOME environment variable for the duration of the test
    monkeypatch.setenv("HOME", str(tmp_path))
    # On Windows, also set USERPROFILE
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # Set BASIC_MEMORY_HOME to the test directory
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    return tmp_path


@pytest.fixture(scope="function")
def app_config(
    config_home, db_backend: Literal["sqlite", "postgres"], monkeypatch
) -> BasicMemoryConfig:
    """Create test app configuration."""
    # Create a basic config without depending on test_project to avoid circular dependency
    projects = {"test-project": str(config_home)}

    # Configure database backend based on test parameter
    if db_backend == "postgres":
        database_backend = DatabaseBackend.POSTGRES
        # Use env var if set, otherwise use default matching docker-compose-postgres.yml
        # These are local test credentials only - NOT for production
        database_url = os.getenv(
            "POSTGRES_TEST_URL",
            "postgresql+asyncpg://basic_memory_user:dev_password@localhost:5433/basic_memory_test",
        )
    else:
        database_backend = DatabaseBackend.SQLITE
        database_url = None

    app_config = BasicMemoryConfig(
        env="test",
        projects=projects,
        default_project="test-project",
        update_permalinks_on_move=True,
        database_backend=database_backend,
        database_url=database_url,
    )

    return app_config


@pytest.fixture
def config_manager(app_config: BasicMemoryConfig, config_home: Path, monkeypatch) -> ConfigManager:
    # Invalidate config cache to ensure clean state for each test
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None

    # Create a new ConfigManager that uses the test home directory
    config_manager = ConfigManager()
    # Update its paths to use the test directory
    config_manager.config_dir = config_home / ".basic-memory"
    config_manager.config_file = config_manager.config_dir / "config.json"
    config_manager.config_dir.mkdir(parents=True, exist_ok=True)

    # Ensure the config file is written to disk
    config_manager.save_config(app_config)
    return config_manager


@pytest.fixture(scope="function")
def project_config(test_project):
    """Create test project configuration."""

    project_config = ProjectConfig(
        name=test_project.name,
        home=Path(test_project.path),
    )

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
    return TestConfig(config_home, project_config, app_config, config_manager)


@pytest_asyncio.fixture(scope="function")
async def engine_factory(
    app_config,
    config_manager,
    db_backend: Literal["sqlite", "postgres"],
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]], None]:
    """Create engine and session factory for the configured database backend."""
    from basic_memory.models.search import CREATE_SEARCH_INDEX

    if db_backend == "postgres":
        # Postgres: Create fresh engine for each test with full schema reset
        config_manager._config = app_config
        db_type = DatabaseType.FILESYSTEM

        # Use context manager to handle engine disposal properly
        async with db.engine_session_factory(db_path=app_config.database_path, db_type=db_type) as (
            engine,
            session_maker,
        ):
            # Drop and recreate schema for complete isolation
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                await conn.execute(text("CREATE SCHEMA public"))
                await conn.execute(text("GRANT ALL ON SCHEMA public TO basic_memory_user"))
                await conn.execute(text("GRANT ALL ON SCHEMA public TO public"))

            # Run migrations to create production tables (including search_index with correct schema)
            # Alembic handles duplicate migration checks, so it's safe to call this for each test
            from basic_memory.db import run_migrations

            await run_migrations(app_config, db_type)

            # For Postgres, migrations create all production tables with correct schemas
            # We only need to create test-specific tables (like ModelTest) that aren't in migrations
            # Don't create search_index via ORM - it's already created by migration with composite PK
            async with engine.begin() as conn:
                # List of tables created by migrations - don't recreate them via ORM
                production_tables = {
                    "entity",
                    "observation",
                    "relation",
                    "project",
                    "search_index",
                    "alembic_version",
                }

                # Get test-specific tables that aren't created by migrations
                test_tables = [
                    table
                    for table in Base.metadata.sorted_tables
                    if table.name not in production_tables
                ]
                if test_tables:
                    await conn.run_sync(
                        lambda sync_conn: Base.metadata.create_all(sync_conn, tables=test_tables)
                    )

            yield engine, session_maker
    else:
        # SQLite: Create fresh in-memory database for each test
        db_type = DatabaseType.MEMORY
        async with db.engine_session_factory(db_path=app_config.database_path, db_type=db_type) as (
            engine,
            session_maker,
        ):
            # Create all tables via ORM
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            # Drop any SearchIndex ORM table, then create FTS5 virtual table
            async with db.scoped_session(session_maker) as session:
                await session.execute(text("DROP TABLE IF EXISTS search_index"))
                await session.execute(CREATE_SEARCH_INDEX)
                await session.commit()

            # Yield after setup is complete
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
        "is_default": True,  # Explicitly set as the default project (for cli operations)
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
    app_config: BasicMemoryConfig,
) -> EntityService:
    """Create EntityService."""
    return EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=app_config,
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
    project_repository: ProjectRepository,
    entity_repository: EntityRepository,
    relation_repository: RelationRepository,
    search_service: SearchService,
    file_service: FileService,
) -> SyncService:
    """Create sync service for testing."""
    return SyncService(
        app_config=app_config,
        entity_service=entity_service,
        project_repository=project_repository,
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
async def search_repository(session_maker, test_project: Project, app_config: BasicMemoryConfig):
    """Create backend-appropriate SearchRepository instance with project context"""
    from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
    from basic_memory.repository.postgres_search_repository import PostgresSearchRepository

    if app_config.database_backend == DatabaseBackend.POSTGRES:
        return PostgresSearchRepository(session_maker, project_id=test_project.id)
    else:
        return SQLiteSearchRepository(session_maker, project_id=test_project.id)


@pytest_asyncio.fixture
async def search_service(
    search_repository,
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
