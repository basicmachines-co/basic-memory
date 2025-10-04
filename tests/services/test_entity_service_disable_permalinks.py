"""Tests for EntityService with disable_permalinks flag."""

from textwrap import dedent
import pytest
import yaml

from basic_memory.config import BasicMemoryConfig
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services import FileService
from basic_memory.services.entity_service import EntityService


@pytest.mark.asyncio
async def test_create_entity_with_permalinks_disabled(
    entity_repository,
    observation_repository,
    relation_repository,
    entity_parser,
    file_service: FileService,
    link_resolver,
):
    """Test that entities created with disable_permalinks=True don't have permalinks."""
    # Create entity service with permalinks disabled
    app_config = BasicMemoryConfig(disable_permalinks=True)
    entity_service = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=app_config,
    )

    entity_data = EntitySchema(
        title="Test Entity",
        folder="test",
        entity_type="note",
        content="Test content",
    )

    # Create entity
    entity = await entity_service.create_entity(entity_data)

    # Assert entity has no permalink
    assert entity.permalink is None

    # Verify file frontmatter doesn't contain permalink
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)
    _, frontmatter, doc_content = file_content.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    assert "permalink" not in metadata
    assert metadata["title"] == "Test Entity"
    assert metadata["type"] == "note"


@pytest.mark.asyncio
async def test_update_entity_with_permalinks_disabled(
    entity_repository,
    observation_repository,
    relation_repository,
    entity_parser,
    file_service: FileService,
    link_resolver,
):
    """Test that entities updated with disable_permalinks=True don't get permalinks added."""
    # First create with permalinks enabled
    app_config_enabled = BasicMemoryConfig(disable_permalinks=False)
    entity_service_enabled = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=app_config_enabled,
    )

    entity_data = EntitySchema(
        title="Test Entity",
        folder="test",
        entity_type="note",
        content="Original content",
    )

    # Create entity with permalinks enabled
    entity = await entity_service_enabled.create_entity(entity_data)
    assert entity.permalink is not None
    original_permalink = entity.permalink

    # Now create service with permalinks disabled
    app_config_disabled = BasicMemoryConfig(disable_permalinks=True)
    entity_service_disabled = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=app_config_disabled,
    )

    # Update entity with permalinks disabled
    entity_data.content = "Updated content"
    updated = await entity_service_disabled.update_entity(entity, entity_data)

    # Permalink should remain unchanged (not removed, just not updated)
    assert updated.permalink == original_permalink

    # Verify file still has the original permalink
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "Updated content" in file_content
    assert f"permalink: {original_permalink}" in file_content


@pytest.mark.asyncio
async def test_create_entity_with_content_frontmatter_permalinks_disabled(
    entity_repository,
    observation_repository,
    relation_repository,
    entity_parser,
    file_service: FileService,
    link_resolver,
):
    """Test that content frontmatter permalinks are ignored when disabled."""
    # Create entity service with permalinks disabled
    app_config = BasicMemoryConfig(disable_permalinks=True)
    entity_service = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=app_config,
    )

    # Content with frontmatter containing permalink
    content = dedent(
        """
        ---
        permalink: custom-permalink
        ---
        # Test Content
        """
    ).strip()

    entity_data = EntitySchema(
        title="Test Entity",
        folder="test",
        entity_type="note",
        content=content,
    )

    # Create entity
    entity = await entity_service.create_entity(entity_data)

    # Entity should not have a permalink set
    assert entity.permalink is None

    # Verify file doesn't have permalink in frontmatter
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)
    _, frontmatter, doc_content = file_content.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    # The permalink from content frontmatter should not be present
    assert "permalink" not in metadata


@pytest.mark.asyncio
async def test_move_entity_with_permalinks_disabled(
    entity_repository,
    observation_repository,
    relation_repository,
    entity_parser,
    file_service: FileService,
    link_resolver,
    project_config,
):
    """Test that moving an entity with disable_permalinks=True doesn't update permalinks."""
    # First create with permalinks enabled
    app_config = BasicMemoryConfig(disable_permalinks=False, update_permalinks_on_move=True)
    entity_service = EntityService(
        entity_parser=entity_parser,
        entity_repository=entity_repository,
        observation_repository=observation_repository,
        relation_repository=relation_repository,
        file_service=file_service,
        link_resolver=link_resolver,
        app_config=app_config,
    )

    entity_data = EntitySchema(
        title="Test Entity",
        folder="test",
        entity_type="note",
        content="Test content",
    )

    # Create entity
    entity = await entity_service.create_entity(entity_data)
    original_permalink = entity.permalink

    # Now disable permalinks
    app_config_disabled = BasicMemoryConfig(disable_permalinks=True, update_permalinks_on_move=True)

    # Move entity
    moved = await entity_service.move_entity(
        identifier=entity.permalink,
        destination_path="new_folder/test_entity.md",
        project_config=project_config,
        app_config=app_config_disabled,
    )

    # Permalink should remain unchanged even though update_permalinks_on_move is True
    assert moved.permalink == original_permalink
