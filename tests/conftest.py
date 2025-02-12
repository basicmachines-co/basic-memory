"""Common test fixtures."""
from textwrap import dedent
from typing import AsyncGenerator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, async_sessionmaker

from basic_memory import db
from basic_memory.config import ProjectConfig
from basic_memory.db import DatabaseType, init_db
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Base
from basic_memory.models.knowledge import Entity, Observation, ObservationCategory, Relation
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.repository.search_repository import SearchRepository
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.services import (
EntityService,
DatabaseService,
)
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService
from basic_memory.sync import FileChangeScanner
from basic_memory.sync.sync_service import SyncService
from basic_memory.sync.watch_service import WatchService


@pytest_asyncio.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
def test_config(tmp_path) -> ProjectConfig:
    """Test configuration using in-memory DB."""
    config = ProjectConfig(
        project="test-project",
    )
    config.home = tmp_path

    (tmp_path / config.home.name).mkdir(parents=True, exist_ok=True)
    logger.info(f"project config home: {config.home}")
    return config


@pytest_asyncio.fixture(scope="function")
async def engine_factory(
    test_config,
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]], None]:
    """Create engine and session factory using in-memory SQLite database."""
    async with db.engine_session_factory(
        db_path=test_config.database_path, db_type=DatabaseType.MEMORY
    ) as (engine, session_maker):
        # Initialize database
        async with db.scoped_session(session_maker) as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            conn = await session.connection()
            await conn.run_sync(Base.metadata.create_all)

        yield engine, session_maker


@pytest_asyncio.fixture
async def session_maker(engine_factory) -> async_sessionmaker[AsyncSession]:
    """Get session maker for tests."""
    _, session_maker = engine_factory
    return session_maker


@pytest_asyncio.fixture(scope="function")
async def entity_repository(session_maker: async_sessionmaker[AsyncSession]) -> EntityRepository:
    """Create an EntityRepository instance."""
    return EntityRepository(session_maker)


@pytest_asyncio.fixture(scope="function")
async def observation_repository(
    session_maker: async_sessionmaker[AsyncSession],
) -> ObservationRepository:
    """Create an ObservationRepository instance."""
    return ObservationRepository(session_maker)


@pytest_asyncio.fixture(scope="function")
async def relation_repository(
    session_maker: async_sessionmaker[AsyncSession],
) -> RelationRepository:
    """Create a RelationRepository instance."""
    return RelationRepository(session_maker)


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
def file_change_scanner(entity_repository) -> FileChangeScanner:
    """Create FileChangeScanner instance."""
    return FileChangeScanner(entity_repository)



@pytest_asyncio.fixture
async def sync_service(
    file_change_scanner: FileChangeScanner,
    entity_service: EntityService,
    entity_parser: EntityParser,
    entity_repository: EntityRepository,
    relation_repository: RelationRepository,
    search_service: SearchService,
) -> SyncService:
    """Create sync service for testing."""
    return SyncService(
        scanner=file_change_scanner,
        entity_service=entity_service,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        entity_parser=entity_parser,
        search_service=search_service,
    )


@pytest_asyncio.fixture
async def search_repository(session_maker):
    """Create SearchRepository instance"""
    return SearchRepository(session_maker)


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
    #await file_service.write_entity_file(full_entity)
    return entity


@pytest_asyncio.fixture
async def test_graph(
        entity_repository, relation_repository, observation_repository, search_service, file_service, entity_service
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

    search_content = await entity_repository.execute_query(text("select * from search_index"),
                                                           use_query_options=False)
    for row in search_content:
        print(row)


    print("relation:")
    search_content = await entity_repository.execute_query(text("select * from search_index where type = 'relation'"),
                                                           use_query_options=False)
    for row in search_content:
        print(row)

    # In test_graph fixture after creating everything:
    print("Entities:")
    entities = await entity_repository.find_all()
    for e in entities:
        print(f"- {e.title} (id={e.id})")

    print("\nRelations:")
    relations = await relation_repository.find_all()
    for r in relations:
        print(f"- {r.from_id} -> {r.to_id} ({r.relation_type})")
        
    return {
        "root": root,
        "connected1": connected_1,
        "connected2": connected_2,
        "deep": deep,
        "observations": [e.observations for e in entities],
        "relations": relations,
    }

@pytest_asyncio.fixture
async def test_graph_old(
    entity_repository, relation_repository, observation_repository, search_service, file_service, entity_service
):
    """Create a test knowledge graph with entities, relations and observations."""
    # Create some test entities
    entities = [
        Entity(
            title="Root Entity",
            entity_type="test",
            permalink="test/root",
            file_path="test/root.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        Entity(
            title="Connected Entity 1",
            entity_type="test",
            permalink="test/connected1",
            file_path="test/connected1.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        Entity(
            title="Connected Entity 2",
            entity_type="test",
            permalink="test/connected2",
            file_path="test/connected2.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        Entity(
            title="Deep Entity",
            entity_type="deep",
            permalink="test/deep",
            file_path="test/deep.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
        Entity(
            title="Deeper Entity",
            entity_type="deeper",
            permalink="test/deeper",
            file_path="test/deeper.md",
            content_type="text/markdown",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ),
    ]
    entities = await entity_repository.add_all(entities)
    root, conn1, conn2, deep, deeper = entities

    # Add some observations
    root.observations = [
        Observation(
            content="Root note 1",
            category=ObservationCategory.NOTE,
        ),
        Observation(
            content="Root tech note",
            category=ObservationCategory.TECH,
        ),
    ]

    conn1.observations = [
        Observation(
            content="Connected 1 note",
            category=ObservationCategory.NOTE,
        )
    ]
    await observation_repository.add_all(root.observations)
    await observation_repository.add_all(conn1.observations)

    # Add relations
    relations = [
        # Direct connections to root
        Relation(
            from_id=root.id,
            to_id=conn1.id,
            to_name=conn1.title,
            relation_type="connects_to",
        ),
        Relation(
            from_id=conn1.id,
            to_id=conn2.id,
            to_name=conn2.title,
            relation_type="connected_to",
        ),
        # Deep connection
        Relation(
            from_id=conn2.id,
            to_id=deep.id,
            to_name=deep.title,
            relation_type="deep_connection",
        ),
        # Deeper connection
        Relation(
            from_id=deep.id,
            to_id=deeper.id,
            to_name=deeper.title,
            relation_type="deeper_connection",
        ),
    ]

    # Save relations
    related_entities = await relation_repository.add_all(relations)

    # get latest
    entities = await entity_repository.find_all()

    # make sure we have files for entities
    for entity in entities:
        await file_service.write_entity_file(entity)

    # Index everything for search
    for entity in entities:
        await search_service.index_entity(entity)
        
    search_content = await entity_repository.execute_query(text("select * from search_index"), use_query_options=False)
    for row in search_content:
        print(row)

    print("relation:")
    search_content = await entity_repository.execute_query(text("select * from search_index where type = 'relation'"),
                                                           use_query_options=False)
    for row in search_content:
        print(row)


    # In test_graph fixture after creating everything:
    print("Entities:")
    entities = await entity_repository.find_all()
    for e in entities:
        print(f"- {e.title} (id={e.id})")

    print("\nRelations:")
    relations = await relation_repository.find_all()
    for r in relations:
        print(f"- {r.from_id} -> {r.to_id} ({r.relation_type})")

    return {
        "root": entities[0],
        "connected1": conn1,
        "connected2": conn2,
        "deep": deep,
        "observations": [e.observations for e in entities],
        "relations": relations,
    }


@pytest_asyncio.fixture
def watch_service(sync_service, file_service, test_config):
    return WatchService(
        sync_service=sync_service,
        file_service=file_service,
        config=test_config
    )
