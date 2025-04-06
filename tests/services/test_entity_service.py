"""Tests for EntityService."""

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from basic_memory.config import ProjectConfig
from basic_memory.markdown import EntityParser
from basic_memory.models import Entity as EntityModel
from basic_memory.repository import EntityRepository
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services import FileService
from basic_memory.services.entity_service import EntityService
from basic_memory.services.exceptions import EntityCreationError, EntityNotFoundError
from basic_memory.utils import generate_permalink


@pytest.mark.asyncio
async def test_create_entity(entity_service: EntityService, file_service: FileService):
    """Test successful entity creation."""
    entity_data = EntitySchema(
        title="Test Entity",
        folder="",
        entity_type="test",
    )

    # Act
    entity = await entity_service.create_entity(entity_data)

    # Assert Entity
    assert isinstance(entity, EntityModel)
    assert entity.permalink == entity_data.permalink
    assert entity.file_path == entity_data.file_path
    assert entity.entity_type == "test"
    assert entity.created_at is not None
    assert len(entity.relations) == 0

    # Verify we can retrieve it using permalink
    retrieved = await entity_service.get_by_permalink(entity_data.permalink)
    assert retrieved.title == "Test Entity"
    assert retrieved.entity_type == "test"
    assert retrieved.created_at is not None

    # Verify file was written
    file_path = file_service.get_entity_path(entity)
    assert await file_service.exists(file_path)

    file_content, _ = await file_service.read_file(file_path)
    _, frontmatter, doc_content = file_content.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    # Verify frontmatter contents
    assert metadata["permalink"] == entity.permalink
    assert metadata["type"] == entity.entity_type


@pytest.mark.asyncio
async def test_create_entity_file_exists(entity_service: EntityService, file_service: FileService):
    """Test successful entity creation."""
    entity_data = EntitySchema(title="Test Entity", folder="", entity_type="test", content="first")

    # Act
    entity = await entity_service.create_entity(entity_data)

    # Verify file was written
    file_path = file_service.get_entity_path(entity)
    assert await file_service.exists(file_path)

    file_content, _ = await file_service.read_file(file_path)
    assert (
        "---\ntitle: Test Entity\ntype: test\npermalink: test-entity\n---\n\nfirst" == file_content
    )

    entity_data = EntitySchema(title="Test Entity", folder="", entity_type="test", content="second")

    with pytest.raises(EntityCreationError):
        await entity_service.create_entity(entity_data)


@pytest.mark.asyncio
async def test_create_entity_unique_permalink(
    test_config,
    entity_service: EntityService,
    file_service: FileService,
    entity_repository: EntityRepository,
):
    """Test successful entity creation."""
    entity_data = EntitySchema(
        title="Test Entity",
        folder="test",
        entity_type="test",
    )

    entity = await entity_service.create_entity(entity_data)

    # default permalink
    assert entity.permalink == generate_permalink(entity.file_path)

    # move file
    file_path = file_service.get_entity_path(entity)
    file_path.rename(test_config.home / "new_path.md")
    await entity_repository.update(entity.id, {"file_path": "new_path.md"})

    # create again
    entity2 = await entity_service.create_entity(entity_data)
    assert entity2.permalink == f"{entity.permalink}-1"

    file_path = file_service.get_entity_path(entity2)
    file_content, _ = await file_service.read_file(file_path)
    _, frontmatter, doc_content = file_content.split("---", 2)
    metadata = yaml.safe_load(frontmatter)

    # Verify frontmatter contents
    assert metadata["permalink"] == entity2.permalink


@pytest.mark.asyncio
async def test_get_by_permalink(entity_service: EntityService):
    """Test finding entity by type and name combination."""
    entity1_data = EntitySchema(
        title="TestEntity1",
        folder="test",
        entity_type="test",
    )
    entity1 = await entity_service.create_entity(entity1_data)

    entity2_data = EntitySchema(
        title="TestEntity2",
        folder="test",
        entity_type="test",
    )
    entity2 = await entity_service.create_entity(entity2_data)

    # Find by type1 and name
    found = await entity_service.get_by_permalink(entity1_data.permalink)
    assert found is not None
    assert found.id == entity1.id
    assert found.entity_type == entity1.entity_type

    # Find by type2 and name
    found = await entity_service.get_by_permalink(entity2_data.permalink)
    assert found is not None
    assert found.id == entity2.id
    assert found.entity_type == entity2.entity_type

    # Test not found case
    with pytest.raises(EntityNotFoundError):
        await entity_service.get_by_permalink("nonexistent/test_entity")


@pytest.mark.asyncio
async def test_get_entity_success(entity_service: EntityService):
    """Test successful entity retrieval."""
    entity_data = EntitySchema(
        title="TestEntity",
        folder="test",
        entity_type="test",
    )
    await entity_service.create_entity(entity_data)

    # Get by permalink
    retrieved = await entity_service.get_by_permalink(entity_data.permalink)

    assert isinstance(retrieved, EntityModel)
    assert retrieved.title == "TestEntity"
    assert retrieved.entity_type == "test"


@pytest.mark.asyncio
async def test_delete_entity_success(entity_service: EntityService):
    """Test successful entity deletion."""
    entity_data = EntitySchema(
        title="TestEntity",
        folder="test",
        entity_type="test",
    )
    await entity_service.create_entity(entity_data)

    # Act using permalink
    result = await entity_service.delete_entity(entity_data.permalink)

    # Assert
    assert result is True
    with pytest.raises(EntityNotFoundError):
        await entity_service.get_by_permalink(entity_data.permalink)


@pytest.mark.asyncio
async def test_delete_entity_by_id(entity_service: EntityService):
    """Test successful entity deletion."""
    entity_data = EntitySchema(
        title="TestEntity",
        folder="test",
        entity_type="test",
    )
    created = await entity_service.create_entity(entity_data)

    # Act using permalink
    result = await entity_service.delete_entity(created.id)

    # Assert
    assert result is True
    with pytest.raises(EntityNotFoundError):
        await entity_service.get_by_permalink(entity_data.permalink)


@pytest.mark.asyncio
async def test_get_entity_by_permalink_not_found(entity_service: EntityService):
    """Test handling of non-existent entity retrieval."""
    with pytest.raises(EntityNotFoundError):
        await entity_service.get_by_permalink("test/non_existent")


@pytest.mark.asyncio
async def test_delete_nonexistent_entity(entity_service: EntityService):
    """Test deleting an entity that doesn't exist."""
    assert await entity_service.delete_entity("test/non_existent") is True


@pytest.mark.asyncio
async def test_create_entity_with_special_chars(entity_service: EntityService):
    """Test entity creation with special characters in name and description."""
    name = "TestEntity_$pecial chars & symbols!"  # Note: Using valid path characters
    entity_data = EntitySchema(
        title=name,
        folder="test",
        entity_type="test",
    )
    entity = await entity_service.create_entity(entity_data)

    assert entity.title == name

    # Verify after retrieval using permalink
    await entity_service.get_by_permalink(entity_data.permalink)


@pytest.mark.asyncio
async def test_get_entities_by_permalinks(entity_service: EntityService):
    """Test opening multiple entities by path IDs."""
    # Create test entities
    entity1_data = EntitySchema(
        title="Entity1",
        folder="test",
        entity_type="test",
    )
    entity2_data = EntitySchema(
        title="Entity2",
        folder="test",
        entity_type="test",
    )
    await entity_service.create_entity(entity1_data)
    await entity_service.create_entity(entity2_data)

    # Open nodes by path IDs
    permalinks = [entity1_data.permalink, entity2_data.permalink]
    found = await entity_service.get_entities_by_permalinks(permalinks)

    assert len(found) == 2
    names = {e.title for e in found}
    assert names == {"Entity1", "Entity2"}


@pytest.mark.asyncio
async def test_get_entities_empty_input(entity_service: EntityService):
    """Test opening nodes with empty path ID list."""
    found = await entity_service.get_entities_by_permalinks([])
    assert len(found) == 0


@pytest.mark.asyncio
async def test_get_entities_some_not_found(entity_service: EntityService):
    """Test opening nodes with mix of existing and non-existent path IDs."""
    # Create one test entity
    entity_data = EntitySchema(
        title="Entity1",
        folder="test",
        entity_type="test",
    )
    await entity_service.create_entity(entity_data)

    # Try to open two nodes, one exists, one doesn't
    permalinks = [entity_data.permalink, "type1/non_existent"]
    found = await entity_service.get_entities_by_permalinks(permalinks)

    assert len(found) == 1
    assert found[0].title == "Entity1"


@pytest.mark.asyncio
async def test_get_entity_path(entity_service: EntityService):
    """Should generate correct filesystem path for entity."""
    entity = EntityModel(
        permalink="test-entity",
        file_path="test-entity.md",
        entity_type="test",
    )
    path = entity_service.file_service.get_entity_path(entity)
    assert path == Path(entity_service.file_service.base_path / "test-entity.md")


@pytest.mark.asyncio
async def test_update_note_entity_content(entity_service: EntityService, file_service: FileService):
    """Should update note content directly."""
    # Create test entity
    schema = EntitySchema(
        title="test",
        folder="test",
        entity_type="note",
        entity_metadata={"status": "draft"},
    )

    entity = await entity_service.create_entity(schema)
    assert entity.entity_metadata.get("status") == "draft"

    # Update content with a relation
    schema.content = """
# Updated [[Content]]
- references [[new content]]
- [note] This is new content.
"""
    updated = await entity_service.update_entity(entity, schema)

    # Verify file has new content but preserved metadata
    file_path = file_service.get_entity_path(updated)
    content, _ = await file_service.read_file(file_path)

    assert "# Updated [[Content]]" in content
    assert "- references [[new content]]" in content
    assert "- [note] This is new content" in content

    # Verify metadata was preserved
    _, frontmatter, _ = content.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata.get("status") == "draft"


@pytest.mark.asyncio
async def test_create_or_update_new(entity_service: EntityService, file_service: FileService):
    """Should create a new entity."""
    # Create test entity
    entity, created = await entity_service.create_or_update_entity(
        EntitySchema(
            title="test",
            folder="test",
            entity_type="test",
            entity_metadata={"status": "draft"},
        )
    )
    assert entity.title == "test"
    assert created is True


@pytest.mark.asyncio
async def test_create_or_update_existing(entity_service: EntityService, file_service: FileService):
    """Should update entity name in both DB and frontmatter."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="test",
            folder="test",
            entity_type="test",
            content="Test entity",
            entity_metadata={"status": "final"},
        )
    )

    entity.content = "Updated content"

    # Update name
    updated, created = await entity_service.create_or_update_entity(entity)

    assert updated.title == "test"
    assert updated.entity_metadata["status"] == "final"
    assert created is False


@pytest.mark.asyncio
async def test_create_with_content(entity_service: EntityService, file_service: FileService):
    # contains frontmatter
    content = dedent(
        """
        ---
        permalink: git-workflow-guide
        ---
        # Git Workflow Guide
                
        A guide to our [[Git]] workflow. This uses some ideas from [[Trunk Based Development]].
        
        ## Best Practices
        Use branches effectively:
        - [design] Keep feature branches short-lived #git #workflow (Reduces merge conflicts)
        - implements [[Branch Strategy]] (Our standard workflow)
        
        ## Common Commands
        See the [[Git Cheat Sheet]] for reference.
        """
    )

    # Create test entity
    entity, created = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Git Workflow Guide",
            folder="test",
            entity_type="test",
            content=content,
        )
    )

    assert created is True
    assert entity.title == "Git Workflow Guide"
    assert entity.entity_type == "test"
    assert entity.permalink == "test/git-workflow-guide"
    assert entity.file_path == "test/Git Workflow Guide.md"

    assert len(entity.observations) == 1
    assert entity.observations[0].category == "design"
    assert entity.observations[0].content == "Keep feature branches short-lived #git #workflow"
    assert set(entity.observations[0].tags) == {"git", "workflow"}
    assert entity.observations[0].context == "Reduces merge conflicts"

    assert len(entity.relations) == 4
    assert entity.relations[0].relation_type == "links to"
    assert entity.relations[0].to_name == "Git"
    assert entity.relations[1].relation_type == "links to"
    assert entity.relations[1].to_name == "Trunk Based Development"
    assert entity.relations[2].relation_type == "implements"
    assert entity.relations[2].to_name == "Branch Strategy"
    assert entity.relations[2].context == "Our standard workflow"
    assert entity.relations[3].relation_type == "links to"
    assert entity.relations[3].to_name == "Git Cheat Sheet"

    # Verify file has new content but preserved metadata
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)

    # assert file
    # note the permalink value is corrected
    expected = dedent("""
        ---
        title: Git Workflow Guide
        type: test
        permalink: test/git-workflow-guide
        ---
        
        # Git Workflow Guide
                
        A guide to our [[Git]] workflow. This uses some ideas from [[Trunk Based Development]].
        
        ## Best Practices
        Use branches effectively:
        - [design] Keep feature branches short-lived #git #workflow (Reduces merge conflicts)
        - implements [[Branch Strategy]] (Our standard workflow)
        
        ## Common Commands
        See the [[Git Cheat Sheet]] for reference.

        """).strip()
    assert expected == file_content


@pytest.mark.asyncio
async def test_update_with_content(entity_service: EntityService, file_service: FileService):
    content = """# Git Workflow Guide"""

    # Create test entity
    entity, created = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Git Workflow Guide",
            entity_type="test",
            folder="test",
            content=content,
        )
    )

    assert created is True
    assert entity.title == "Git Workflow Guide"

    assert len(entity.observations) == 0
    assert len(entity.relations) == 0

    # Verify file has new content but preserved metadata
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)

    # assert content is in file
    assert (
        dedent(
            """
            ---
            title: Git Workflow Guide
            type: test
            permalink: test/git-workflow-guide
            ---
            
            # Git Workflow Guide
            """
        ).strip()
        == file_content
    )

    # now update the content
    update_content = dedent(
        """
        ---
        title: Git Workflow Guide
        type: test
        permalink: test/git-workflow-guide
        ---
        
        # Git Workflow Guide
        
        A guide to our [[Git]] workflow. This uses some ideas from [[Trunk Based Development]].
        
        ## Best Practices
        Use branches effectively:
        - [design] Keep feature branches short-lived #git #workflow (Reduces merge conflicts)
        - implements [[Branch Strategy]] (Our standard workflow)
        
        ## Common Commands
        See the [[Git Cheat Sheet]] for reference.
        """
    ).strip()

    # update entity
    entity, created = await entity_service.create_or_update_entity(
        EntitySchema(
            title="Git Workflow Guide",
            folder="test",
            entity_type="test",
            content=update_content,
        )
    )

    assert created is False
    assert entity.title == "Git Workflow Guide"

    assert len(entity.observations) == 1
    assert entity.observations[0].category == "design"
    assert entity.observations[0].content == "Keep feature branches short-lived #git #workflow"
    assert set(entity.observations[0].tags) == {"git", "workflow"}
    assert entity.observations[0].context == "Reduces merge conflicts"

    assert len(entity.relations) == 4
    assert entity.relations[0].relation_type == "links to"
    assert entity.relations[0].to_name == "Git"
    assert entity.relations[1].relation_type == "links to"
    assert entity.relations[1].to_name == "Trunk Based Development"
    assert entity.relations[2].relation_type == "implements"
    assert entity.relations[2].to_name == "Branch Strategy"
    assert entity.relations[2].context == "Our standard workflow"
    assert entity.relations[3].relation_type == "links to"
    assert entity.relations[3].to_name == "Git Cheat Sheet"

    # Verify file has new content but preserved metadata
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)

    # assert content is in file
    assert update_content.strip() == file_content


@pytest.mark.asyncio
async def test_create_with_no_frontmatter(
    test_config: ProjectConfig,
    entity_parser: EntityParser,
    entity_service: EntityService,
    file_service: FileService,
):
    # contains no frontmatter
    content = "# Git Workflow Guide"
    file_path = Path("test/Git Workflow Guide.md")
    full_path = test_config.home / file_path

    await file_service.write_file(Path(full_path), content)

    entity_markdown = await entity_parser.parse_file(full_path)
    created = await entity_service.create_entity_from_markdown(file_path, entity_markdown)
    file_content, _ = await file_service.read_file(created.file_path)

    assert str(file_path) == str(created.file_path)
    assert created.title == "Git Workflow Guide"
    assert created.entity_type == "note"
    assert created.permalink is None

    # assert file
    expected = dedent("""
        # Git Workflow Guide
        """).strip()
    assert expected == file_content
