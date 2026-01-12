"""Integration tests for the disable_permalinks configuration."""

import pytest

from basic_memory.markdown import EntityParser, MarkdownProcessor
from basic_memory.repository import (
    EntityRepository,
    ObservationRepository,
    RelationRepository,
    ProjectRepository,
)
from basic_memory.repository.postgres_search_repository import PostgresSearchRepository
from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services import FileService
from basic_memory.services.entity_service import EntityService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.search_service import SearchService
from basic_memory.sync.sync_service import SyncService


@pytest.mark.asyncio
async def test_disable_permalinks_create_entity(tmp_path, engine_factory, app_config, test_project):
    """Test that entities created with disable_permalinks=True don't have permalinks."""
    from basic_memory.config import DatabaseBackend

    engine, session_maker = engine_factory

    # Override app config to enable disable_permalinks
    app_config.disable_permalinks = True

    # Setup repositories
    entity_repository = EntityRepository(session_maker, project_id=test_project.id)
    observation_repository = ObservationRepository(session_maker, project_id=test_project.id)
    relation_repository = RelationRepository(session_maker, project_id=test_project.id)

    # Use database-specific search repository
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        search_repository = PostgresSearchRepository(session_maker, project_id=test_project.id)
    else:
        search_repository = SQLiteSearchRepository(session_maker, project_id=test_project.id)

    # Setup services
    entity_parser = EntityParser(tmp_path)
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(tmp_path, markdown_processor)
    search_service = SearchService(search_repository, entity_repository, file_service)
    await search_service.init_search_index()
    link_resolver = LinkResolver(entity_repository, search_service)

    entity_service = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=app_config,
    )

    # Create entity via API
    entity_data = EntitySchema(
        title="Test Note",
        folder="test",
        entity_type="note",
        content="Test content",
    )

    created = await entity_service.create_entity(entity_data)

    # Verify entity has no permalink
    assert created.permalink is None

    # Verify file has no permalink in frontmatter
    file_path = tmp_path / "test" / "Test Note.md"
    assert file_path.exists()
    content = file_path.read_text()
    assert "permalink:" not in content
    assert "Test content" in content


@pytest.mark.asyncio
async def test_disable_permalinks_sync_workflow(tmp_path, engine_factory, app_config, test_project):
    """Test full sync workflow with disable_permalinks enabled."""
    from basic_memory.config import DatabaseBackend

    engine, session_maker = engine_factory

    # Override app config to enable disable_permalinks
    app_config.disable_permalinks = True

    # Create a test markdown file without frontmatter
    test_file = tmp_path / "test_note.md"
    test_file.write_text("# Test Note\nThis is test content.")

    # Setup repositories
    entity_repository = EntityRepository(session_maker, project_id=test_project.id)
    observation_repository = ObservationRepository(session_maker, project_id=test_project.id)
    relation_repository = RelationRepository(session_maker, project_id=test_project.id)

    # Use database-specific search repository
    if app_config.database_backend == DatabaseBackend.POSTGRES:
        search_repository = PostgresSearchRepository(session_maker, project_id=test_project.id)
    else:
        search_repository = SQLiteSearchRepository(session_maker, project_id=test_project.id)

    project_repository = ProjectRepository(session_maker)

    # Setup services
    entity_parser = EntityParser(tmp_path)
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(tmp_path, markdown_processor)
    search_service = SearchService(search_repository, entity_repository, file_service)
    await search_service.init_search_index()
    link_resolver = LinkResolver(entity_repository, search_service)

    entity_service = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=app_config,
    )

    sync_service = SyncService(
        app_config=app_config,
        entity_service=entity_service,
        project_repository=project_repository,
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        file_service=file_service,
    )

    # Run sync
    report = await sync_service.scan(tmp_path)
    # Note: scan may pick up database files too, so just check our file is there
    assert "test_note.md" in report.new

    # Sync the file
    await sync_service.sync_file("test_note.md", new=True)

    # Verify file has no permalink added
    content = test_file.read_text()
    assert "permalink:" not in content
    assert "# Test Note" in content

    # Verify entity in database has no permalink
    entities = await entity_repository.find_all()
    assert len(entities) == 1
    assert entities[0].permalink is None
    # Title is extracted from filename when no frontmatter, or from frontmatter when present
    assert entities[0].title in ("test_note", "Test Note")
