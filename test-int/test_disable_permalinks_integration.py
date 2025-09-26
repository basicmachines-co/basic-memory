"""Integration tests for the disable_permalinks configuration."""

import json
import pytest
from pathlib import Path
from textwrap import dedent
import tempfile
import shutil

from basic_memory.config import BasicMemoryConfig, ConfigManager


@pytest.mark.asyncio
async def test_disable_permalinks_full_workflow():
    """Test full workflow with disable_permalinks enabled."""

    # Create temporary directories for testing
    test_home = Path(tempfile.mkdtemp())
    config_home = Path(tempfile.mkdtemp())

    try:
        # Setup config with disable_permalinks=True
        config_file = config_home / ".basic-memory" / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)

        config_data = {
            "projects": {
                "test": str(test_home)
            },
            "default_project": "test",
            "disable_permalinks": True,
            "sync_changes": False  # Disable for testing
        }

        config_file.write_text(json.dumps(config_data))

        # Create a test markdown file without frontmatter
        test_file = test_home / "test_note.md"
        test_file.write_text("# Test Note\nThis is test content.")

        # Initialize services
        from basic_memory.config import ConfigManager, get_project_config
        from basic_memory.markdown import EntityParser
        from basic_memory.services import FileService
        from basic_memory.services.entity_service import EntityService
        from basic_memory.services.link_resolver import LinkResolver
        from basic_memory.services.search_service import SearchService
        from basic_memory.services.sync_service import SyncService
        from basic_memory.markdown import MarkdownProcessor
        from basic_memory.repository import (
            EntityRepository,
            ObservationRepository,
            RelationRepository,
            SearchRepository
        )
        from basic_memory.persistence import get_async_session_maker
        import os

        # Set HOME to config_home for this test
        original_home = os.environ.get("HOME")
        os.environ["HOME"] = str(config_home)

        # Load config
        config_manager = ConfigManager()
        app_config = config_manager.load_config()
        project_config = get_project_config("test")

        # Create session maker with in-memory database
        session_maker = await get_async_session_maker("sqlite+aiosqlite:///:memory:")

        # Create repositories
        entity_repository = EntityRepository(session_maker, project_id=1)
        observation_repository = ObservationRepository(session_maker, project_id=1)
        relation_repository = RelationRepository(session_maker, project_id=1)
        search_repository = SearchRepository(session_maker, project_id=1)

        # Create services
        entity_parser = EntityParser(project_config.home)
        markdown_processor = MarkdownProcessor(entity_parser)
        file_service = FileService(project_config.home, markdown_processor)
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
            app_config=app_config
        )

        sync_service = SyncService(
            app_config=app_config,
            entity_service=entity_service,
            entity_repository=entity_repository,
            relation_repository=relation_repository,
            entity_parser=entity_parser,
            search_service=search_service,
            file_service=file_service,
        )

        # Run sync
        report = await sync_service.analyze_changes()
        assert len(report.new) == 1
        assert "test_note.md" in report.new

        # Sync the file
        await sync_service.sync_file("test_note.md", new=True)

        # Read the file and verify no permalink was added
        content = test_file.read_text()
        assert "permalink:" not in content
        assert "# Test Note" in content

        # Verify entity in database has no permalink
        entities = await entity_repository.get_all()
        assert len(entities) == 1
        assert entities[0].permalink is None
        assert entities[0].title == "Test Note"

        # Create another file with frontmatter that includes a permalink
        test_file2 = test_home / "test_note2.md"
        test_file2.write_text(dedent("""
            ---
            title: Test Note 2
            permalink: should-be-ignored
            ---
            # Test Note 2
            This note has frontmatter with a permalink that should be ignored.
        """).strip())

        # Sync the second file
        await sync_service.sync_file("test_note2.md", new=True)

        # Read the file and verify the permalink was not changed
        content2 = test_file2.read_text()
        # The original frontmatter permalink should remain but not be processed
        assert "permalink: should-be-ignored" in content2

        # Verify entity in database has no permalink
        entities = await entity_repository.get_all()
        entity2 = [e for e in entities if e.title == "Test Note 2"][0]
        assert entity2.permalink is None

        # Now test with the API
        from basic_memory.schemas import Entity as EntitySchema

        entity_data = EntitySchema(
            title="API Created Note",
            folder="",
            entity_type="note",
            content="Content created via API"
        )

        created = await entity_service.create_entity(entity_data)
        assert created.permalink is None

        # Verify the file doesn't have a permalink
        api_file = test_home / "API Created Note.md"
        api_content = api_file.read_text()
        assert "permalink:" not in api_content

    finally:
        # Cleanup
        if original_home:
            os.environ["HOME"] = original_home
        else:
            os.environ.pop("HOME", None)
        shutil.rmtree(test_home, ignore_errors=True)
        shutil.rmtree(config_home, ignore_errors=True)


@pytest.mark.asyncio
async def test_disable_permalinks_move_operation():
    """Test that move operations respect disable_permalinks setting."""

    # Create temporary directories
    test_home = Path(tempfile.mkdtemp())
    config_home = Path(tempfile.mkdtemp())

    try:
        # Setup config with disable_permalinks=False initially
        config_file = config_home / ".basic-memory" / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)

        config_data = {
            "projects": {
                "test": str(test_home)
            },
            "default_project": "test",
            "disable_permalinks": False,
            "update_permalinks_on_move": True,
            "sync_changes": False
        }

        config_file.write_text(json.dumps(config_data))

        # Create subdirectories
        (test_home / "folder1").mkdir()
        (test_home / "folder2").mkdir()

        # Create a test file
        test_file = test_home / "folder1" / "test_note.md"
        test_file.write_text("# Test Note\nContent to be moved.")

        # Initialize services
        from basic_memory.config import ConfigManager, get_project_config
        from basic_memory.markdown import EntityParser
        from basic_memory.services import FileService
        from basic_memory.services.entity_service import EntityService
        from basic_memory.services.link_resolver import LinkResolver
        from basic_memory.services.search_service import SearchService
        from basic_memory.markdown import MarkdownProcessor
        from basic_memory.repository import (
            EntityRepository,
            ObservationRepository,
            RelationRepository,
            SearchRepository
        )
        from basic_memory.persistence import get_async_session_maker
        import os

        original_home = os.environ.get("HOME")
        os.environ["HOME"] = str(config_home)

        # Load config
        config_manager = ConfigManager()
        app_config = config_manager.load_config()
        project_config = get_project_config("test")

        # Create session maker
        session_maker = await get_async_session_maker("sqlite+aiosqlite:///:memory:")

        # Create repositories
        entity_repository = EntityRepository(session_maker, project_id=1)
        observation_repository = ObservationRepository(session_maker, project_id=1)
        relation_repository = RelationRepository(session_maker, project_id=1)
        search_repository = SearchRepository(session_maker, project_id=1)

        # Create services
        entity_parser = EntityParser(project_config.home)
        markdown_processor = MarkdownProcessor(entity_parser)
        file_service = FileService(project_config.home, markdown_processor)
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
            app_config=app_config
        )

        # Create entity with permalinks enabled
        from basic_memory.schemas import Entity as EntitySchema

        entity_data = EntitySchema(
            title="Test Note",
            folder="folder1",
            entity_type="note",
            content="# Test Note\nContent to be moved."
        )

        created = await entity_service.create_entity(entity_data)
        original_permalink = created.permalink
        assert original_permalink is not None

        # Now update config to disable permalinks
        config_data["disable_permalinks"] = True
        config_file.write_text(json.dumps(config_data))
        app_config = config_manager.load_config()

        # Move the entity
        moved = await entity_service.move_entity(
            identifier=original_permalink,
            destination_path="folder2/moved_note.md",
            project_config=project_config,
            app_config=app_config
        )

        # Verify permalink wasn't updated despite update_permalinks_on_move=True
        assert moved.permalink == original_permalink

        # Verify file was moved
        assert not test_file.exists()
        moved_file = test_home / "folder2" / "moved_note.md"
        assert moved_file.exists()

        # Verify the permalink in the file wasn't changed
        content = moved_file.read_text()
        assert f"permalink: {original_permalink}" in content

    finally:
        # Cleanup
        if original_home:
            os.environ["HOME"] = original_home
        else:
            os.environ.pop("HOME", None)
        shutil.rmtree(test_home, ignore_errors=True)
        shutil.rmtree(config_home, ignore_errors=True)