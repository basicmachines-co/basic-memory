"""Tests for EntityService."""

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from basic_memory.config import ProjectConfig, BasicMemoryConfig
from basic_memory.markdown import EntityParser
from basic_memory.models import Entity as EntityModel
from basic_memory.repository import EntityRepository
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services import FileService
from basic_memory.services.entity_service import EntityService
from basic_memory.services.exceptions import EntityCreationError, EntityNotFoundError
from basic_memory.services.search_service import SearchService
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
    entity_data = EntitySchema(
        title="Test Entity",
        folder="",
        entity_type="test",
        content="first",
    )

    # Act
    entity = await entity_service.create_entity(entity_data)

    # Verify file was written
    file_path = file_service.get_entity_path(entity)
    assert await file_service.exists(file_path)

    file_content, _ = await file_service.read_file(file_path)
    assert (
        "---\ntitle: Test Entity\ntype: test\npermalink: test-entity\n---\n\nfirst" == file_content
    )

    entity_data = EntitySchema(
        title="Test Entity",
        folder="",
        entity_type="test",
        content="second",
    )

    with pytest.raises(EntityCreationError):
        await entity_service.create_entity(entity_data)


@pytest.mark.asyncio
async def test_create_entity_unique_permalink(
    project_config,
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
    file_path.rename(project_config.home / "new_path.md")
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
    assert entity.permalink == "git-workflow-guide"
    assert entity.file_path == "test/Git Workflow Guide.md"

    assert len(entity.observations) == 1
    assert entity.observations[0].category == "design"
    assert entity.observations[0].content == "Keep feature branches short-lived #git #workflow"
    assert set(entity.observations[0].tags) == {"git", "workflow"}
    assert entity.observations[0].context == "Reduces merge conflicts"

    assert len(entity.relations) == 4
    assert entity.relations[0].relation_type == "links_to"
    assert entity.relations[0].to_name == "Git"
    assert entity.relations[1].relation_type == "links_to"
    assert entity.relations[1].to_name == "Trunk Based Development"
    assert entity.relations[2].relation_type == "implements"
    assert entity.relations[2].to_name == "Branch Strategy"
    assert entity.relations[2].context == "Our standard workflow"
    assert entity.relations[3].relation_type == "links_to"
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

    # assert custom permalink value
    assert entity.permalink == "git-workflow-guide"

    assert len(entity.observations) == 1
    assert entity.observations[0].category == "design"
    assert entity.observations[0].content == "Keep feature branches short-lived #git #workflow"
    assert set(entity.observations[0].tags) == {"git", "workflow"}
    assert entity.observations[0].context == "Reduces merge conflicts"

    assert len(entity.relations) == 4
    assert entity.relations[0].relation_type == "links_to"
    assert entity.relations[0].to_name == "Git"
    assert entity.relations[1].relation_type == "links_to"
    assert entity.relations[1].to_name == "Trunk Based Development"
    assert entity.relations[2].relation_type == "implements"
    assert entity.relations[2].to_name == "Branch Strategy"
    assert entity.relations[2].context == "Our standard workflow"
    assert entity.relations[3].relation_type == "links_to"
    assert entity.relations[3].to_name == "Git Cheat Sheet"

    # Verify file has new content but preserved metadata
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)

    # assert content is in file
    assert update_content.strip() == file_content


@pytest.mark.asyncio
async def test_create_with_no_frontmatter(
    project_config: ProjectConfig,
    entity_parser: EntityParser,
    entity_service: EntityService,
    file_service: FileService,
):
    # contains no frontmatter
    content = "# Git Workflow Guide"
    file_path = Path("test/Git Workflow Guide.md")
    full_path = project_config.home / file_path

    await file_service.write_file(Path(full_path), content)

    entity_markdown = await entity_parser.parse_file(full_path)
    created = await entity_service.create_entity_from_markdown(file_path, entity_markdown)
    file_content, _ = await file_service.read_file(created.file_path)

    assert file_path.as_posix() == created.file_path
    assert created.title == "Git Workflow Guide"
    assert created.entity_type == "note"
    assert created.permalink is None

    # assert file
    expected = dedent("""
        # Git Workflow Guide
        """).strip()
    assert expected == file_content


@pytest.mark.asyncio
async def test_edit_entity_append(entity_service: EntityService, file_service: FileService):
    """Test appending content to an entity."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="Original content",
        )
    )

    # Edit entity with append operation
    updated = await entity_service.edit_entity(
        identifier=entity.permalink, operation="append", content="Appended content"
    )

    # Verify content was appended
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "Original content" in file_content
    assert "Appended content" in file_content
    assert file_content.index("Original content") < file_content.index("Appended content")


@pytest.mark.asyncio
async def test_edit_entity_prepend(entity_service: EntityService, file_service: FileService):
    """Test prepending content to an entity."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="Original content",
        )
    )

    # Edit entity with prepend operation
    updated = await entity_service.edit_entity(
        identifier=entity.permalink, operation="prepend", content="Prepended content"
    )

    # Verify content was prepended
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "Original content" in file_content
    assert "Prepended content" in file_content
    assert file_content.index("Prepended content") < file_content.index("Original content")


@pytest.mark.asyncio
async def test_edit_entity_find_replace(entity_service: EntityService, file_service: FileService):
    """Test find and replace operation on an entity."""
    # Create test entity with specific content to replace
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="This is old content that needs updating",
        )
    )

    # Edit entity with find_replace operation
    updated = await entity_service.edit_entity(
        identifier=entity.permalink,
        operation="find_replace",
        content="new content",
        find_text="old content",
    )

    # Verify content was replaced
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "old content" not in file_content
    assert "This is new content that needs updating" in file_content


@pytest.mark.asyncio
async def test_edit_entity_replace_section(
    entity_service: EntityService, file_service: FileService
):
    """Test replacing a specific section in an entity."""
    # Create test entity with sections
    content = dedent("""
        # Main Title
        
        ## Section 1
        Original section 1 content
        
        ## Section 2
        Original section 2 content
        """).strip()

    entity = await entity_service.create_entity(
        EntitySchema(
            title="Sample Note",
            folder="docs",
            entity_type="note",
            content=content,
        )
    )

    # Edit entity with replace_section operation
    updated = await entity_service.edit_entity(
        identifier=entity.permalink,
        operation="replace_section",
        content="New section 1 content",
        section="## Section 1",
    )

    # Verify section was replaced
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "New section 1 content" in file_content
    assert "Original section 1 content" not in file_content
    assert "Original section 2 content" in file_content  # Other sections preserved


@pytest.mark.asyncio
async def test_edit_entity_replace_section_create_new(
    entity_service: EntityService, file_service: FileService
):
    """Test replacing a section that doesn't exist creates it."""
    # Create test entity without the section
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="# Main Title\n\nSome content",
        )
    )

    # Edit entity with replace_section operation for non-existent section
    updated = await entity_service.edit_entity(
        identifier=entity.permalink,
        operation="replace_section",
        content="New section content",
        section="## New Section",
    )

    # Verify section was created
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "## New Section" in file_content
    assert "New section content" in file_content


@pytest.mark.asyncio
async def test_edit_entity_not_found(entity_service: EntityService):
    """Test editing a non-existent entity raises error."""
    with pytest.raises(EntityNotFoundError):
        await entity_service.edit_entity(
            identifier="non-existent", operation="append", content="content"
        )


@pytest.mark.asyncio
async def test_edit_entity_invalid_operation(entity_service: EntityService):
    """Test editing with invalid operation raises error."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="Original content",
        )
    )

    with pytest.raises(ValueError, match="Unsupported operation"):
        await entity_service.edit_entity(
            identifier=entity.permalink, operation="invalid_operation", content="content"
        )


@pytest.mark.asyncio
async def test_edit_entity_find_replace_missing_find_text(entity_service: EntityService):
    """Test find_replace operation without find_text raises error."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="Original content",
        )
    )

    with pytest.raises(ValueError, match="find_text is required"):
        await entity_service.edit_entity(
            identifier=entity.permalink, operation="find_replace", content="new content"
        )


@pytest.mark.asyncio
async def test_edit_entity_replace_section_missing_section(entity_service: EntityService):
    """Test replace_section operation without section parameter raises error."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="Original content",
        )
    )

    with pytest.raises(ValueError, match="section is required"):
        await entity_service.edit_entity(
            identifier=entity.permalink, operation="replace_section", content="new content"
        )


@pytest.mark.asyncio
async def test_edit_entity_with_observations_and_relations(
    entity_service: EntityService, file_service: FileService
):
    """Test editing entity updates observations and relations correctly."""
    # Create test entity with observations and relations
    content = dedent("""
        # Test Note
        
        - [note] This is an observation
        - links to [[Other Entity]]
        
        Original content
        """).strip()

    entity = await entity_service.create_entity(
        EntitySchema(
            title="Sample Note",
            folder="docs",
            entity_type="note",
            content=content,
        )
    )

    # Verify initial state
    assert len(entity.observations) == 1
    assert len(entity.relations) == 1

    # Edit entity by appending content with new observations/relations
    updated = await entity_service.edit_entity(
        identifier=entity.permalink,
        operation="append",
        content="\n- [category] New observation\n- relates to [[New Entity]]",
    )

    # Verify observations and relations were updated
    assert len(updated.observations) == 2
    assert len(updated.relations) == 2

    # Check new observation
    new_obs = [obs for obs in updated.observations if obs.category == "category"][0]
    assert new_obs.content == "New observation"

    # Check new relation
    new_rel = [rel for rel in updated.relations if rel.to_name == "New Entity"][0]
    assert new_rel.relation_type == "relates to"


@pytest.mark.asyncio
async def test_create_entity_from_markdown_with_upsert(
    entity_service: EntityService, file_service: FileService
):
    """Test that create_entity_from_markdown uses UPSERT approach for conflict resolution."""
    file_path = Path("test/upsert-test.md")

    # Create a mock EntityMarkdown object
    from basic_memory.markdown.schemas import (
        EntityFrontmatter,
        EntityMarkdown as RealEntityMarkdown,
    )
    from datetime import datetime, timezone

    frontmatter = EntityFrontmatter(metadata={"title": "UPSERT Test", "type": "test"})
    markdown = RealEntityMarkdown(
        frontmatter=frontmatter,
        observations=[],
        relations=[],
        created=datetime.now(timezone.utc),
        modified=datetime.now(timezone.utc),
    )

    # Call the method - should succeed without complex exception handling
    result = await entity_service.create_entity_from_markdown(file_path, markdown)

    # Verify it created the entity successfully using the UPSERT approach
    assert result is not None
    assert result.title == "UPSERT Test"
    assert result.file_path == file_path.as_posix()
    # create_entity_from_markdown sets checksum to None (incomplete sync)
    assert result.checksum is None


@pytest.mark.asyncio
async def test_create_entity_from_markdown_error_handling(
    entity_service: EntityService, file_service: FileService
):
    """Test that create_entity_from_markdown handles repository errors gracefully."""
    from unittest.mock import patch
    from basic_memory.services.exceptions import EntityCreationError

    file_path = Path("test/error-test.md")

    # Create a mock EntityMarkdown object
    from basic_memory.markdown.schemas import (
        EntityFrontmatter,
        EntityMarkdown as RealEntityMarkdown,
    )
    from datetime import datetime, timezone

    frontmatter = EntityFrontmatter(metadata={"title": "Error Test", "type": "test"})
    markdown = RealEntityMarkdown(
        frontmatter=frontmatter,
        observations=[],
        relations=[],
        created=datetime.now(timezone.utc),
        modified=datetime.now(timezone.utc),
    )

    # Mock the repository.upsert_entity to raise a general error
    async def mock_upsert(*args, **kwargs):
        # Simulate a general database error
        raise Exception("Database connection failed")

    with patch.object(entity_service.repository, "upsert_entity", side_effect=mock_upsert):
        # Should wrap the error in EntityCreationError
        with pytest.raises(EntityCreationError, match="Failed to create entity"):
            await entity_service.create_entity_from_markdown(file_path, markdown)


# Edge case tests for find_replace operation
@pytest.mark.asyncio
async def test_edit_entity_find_replace_not_found(entity_service: EntityService):
    """Test find_replace operation when text is not found."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="This is some content",
        )
    )

    # Try to replace text that doesn't exist
    with pytest.raises(ValueError, match="Text to replace not found: 'nonexistent'"):
        await entity_service.edit_entity(
            identifier=entity.permalink,
            operation="find_replace",
            content="new content",
            find_text="nonexistent",
        )


@pytest.mark.asyncio
async def test_edit_entity_find_replace_multiple_occurrences_expected_one(
    entity_service: EntityService,
):
    """Test find_replace with multiple occurrences when expecting one."""
    # Create entity with repeated text (avoiding "test" since it appears in frontmatter)
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Sample Note",
            folder="docs",
            entity_type="note",
            content="The word banana appears here. Another banana word here.",
        )
    )

    # Try to replace with expected count of 1 when there are 2
    with pytest.raises(ValueError, match="Expected 1 occurrences of 'banana', but found 2"):
        await entity_service.edit_entity(
            identifier=entity.permalink,
            operation="find_replace",
            content="replacement",
            find_text="banana",
            expected_replacements=1,
        )


@pytest.mark.asyncio
async def test_edit_entity_find_replace_multiple_occurrences_success(
    entity_service: EntityService, file_service: FileService
):
    """Test find_replace with multiple occurrences when expected count matches."""
    # Create test entity with repeated text (avoiding "test" since it appears in frontmatter)
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Sample Note",
            folder="docs",
            entity_type="note",
            content="The word banana appears here. Another banana word here.",
        )
    )

    # Replace with correct expected count
    updated = await entity_service.edit_entity(
        identifier=entity.permalink,
        operation="find_replace",
        content="apple",
        find_text="banana",
        expected_replacements=2,
    )

    # Verify both instances were replaced
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "The word apple appears here. Another apple word here." in file_content


@pytest.mark.asyncio
async def test_edit_entity_find_replace_empty_find_text(entity_service: EntityService):
    """Test find_replace with empty find_text."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="Some content",
        )
    )

    # Try with empty find_text
    with pytest.raises(ValueError, match="find_text cannot be empty or whitespace only"):
        await entity_service.edit_entity(
            identifier=entity.permalink,
            operation="find_replace",
            content="new content",
            find_text="   ",  # whitespace only
        )


@pytest.mark.asyncio
async def test_edit_entity_find_replace_multiline(
    entity_service: EntityService, file_service: FileService
):
    """Test find_replace with multiline text."""
    # Create test entity with multiline content
    content = dedent("""
        # Title
        
        This is a paragraph
        that spans multiple lines
        and needs replacement.
        
        Other content.
        """).strip()

    entity = await entity_service.create_entity(
        EntitySchema(
            title="Sample Note",
            folder="docs",
            entity_type="note",
            content=content,
        )
    )

    # Replace multiline text
    find_text = "This is a paragraph\nthat spans multiple lines\nand needs replacement."
    new_text = "This is new content\nthat replaces the old paragraph."

    updated = await entity_service.edit_entity(
        identifier=entity.permalink, operation="find_replace", content=new_text, find_text=find_text
    )

    # Verify replacement worked
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "This is new content\nthat replaces the old paragraph." in file_content
    assert "Other content." in file_content  # Make sure rest is preserved


# Edge case tests for replace_section operation
@pytest.mark.asyncio
async def test_edit_entity_replace_section_multiple_sections_error(entity_service: EntityService):
    """Test replace_section with multiple sections having same header."""
    # Create test entity with duplicate section headers
    content = dedent("""
        # Main Title
        
        ## Section 1
        First instance content
        
        ## Section 2
        Some content
        
        ## Section 1
        Second instance content
        """).strip()

    entity = await entity_service.create_entity(
        EntitySchema(
            title="Sample Note",
            folder="docs",
            entity_type="note",
            content=content,
        )
    )

    # Try to replace section when multiple exist
    with pytest.raises(ValueError, match="Multiple sections found with header '## Section 1'"):
        await entity_service.edit_entity(
            identifier=entity.permalink,
            operation="replace_section",
            content="New content",
            section="## Section 1",
        )


@pytest.mark.asyncio
async def test_edit_entity_replace_section_empty_section(entity_service: EntityService):
    """Test replace_section with empty section parameter."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="Some content",
        )
    )

    # Try with empty section
    with pytest.raises(ValueError, match="section cannot be empty or whitespace only"):
        await entity_service.edit_entity(
            identifier=entity.permalink,
            operation="replace_section",
            content="new content",
            section="   ",  # whitespace only
        )


@pytest.mark.asyncio
async def test_edit_entity_replace_section_header_variations(
    entity_service: EntityService, file_service: FileService
):
    """Test replace_section with different header formatting."""
    # Create entity with various header formats (avoiding "test" in frontmatter)
    content = dedent("""
        # Main Title
        
        ## Section Name
        Original content
        
        ### Subsection
        Sub content
        """).strip()

    entity = await entity_service.create_entity(
        EntitySchema(
            title="Sample Note",
            folder="docs",
            entity_type="note",
            content=content,
        )
    )

    # Test replacing with different header format (no ##)
    updated = await entity_service.edit_entity(
        identifier=entity.permalink,
        operation="replace_section",
        content="New section content",
        section="Section Name",  # No ## prefix
    )

    # Verify replacement worked
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "New section content" in file_content
    assert "Original content" not in file_content
    assert "### Subsection" in file_content  # Subsection preserved


@pytest.mark.asyncio
async def test_edit_entity_replace_section_at_end_of_document(
    entity_service: EntityService, file_service: FileService
):
    """Test replace_section when section is at the end of document."""
    # Create test entity with section at end
    content = dedent("""
        # Main Title
        
        ## First Section
        First content
        
        ## Last Section
        Last section content""").strip()  # No trailing newline

    entity = await entity_service.create_entity(
        EntitySchema(
            title="Sample Note",
            folder="docs",
            entity_type="note",
            content=content,
        )
    )

    # Replace the last section
    updated = await entity_service.edit_entity(
        identifier=entity.permalink,
        operation="replace_section",
        content="New last section content",
        section="## Last Section",
    )

    # Verify replacement worked
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "New last section content" in file_content
    assert "Last section content" not in file_content
    assert "First content" in file_content  # Previous section preserved


@pytest.mark.asyncio
async def test_edit_entity_replace_section_with_subsections(
    entity_service: EntityService, file_service: FileService
):
    """Test replace_section preserves subsections (stops at any header)."""
    # Create test entity with nested sections
    content = dedent("""
        # Main Title
        
        ## Parent Section
        Parent content
        
        ### Child Section 1
        Child 1 content
        
        ### Child Section 2  
        Child 2 content
        
        ## Another Section
        Other content
        """).strip()

    entity = await entity_service.create_entity(
        EntitySchema(
            title="Sample Note",
            folder="docs",
            entity_type="note",
            content=content,
        )
    )

    # Replace parent section (should only replace content until first subsection)
    updated = await entity_service.edit_entity(
        identifier=entity.permalink,
        operation="replace_section",
        content="New parent content",
        section="## Parent Section",
    )

    # Verify replacement worked - only immediate content replaced, subsections preserved
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)
    assert "New parent content" in file_content
    assert "Parent content" not in file_content  # Original content replaced
    assert "Child 1 content" in file_content  # Child sections preserved
    assert "Child 2 content" in file_content  # Child sections preserved
    assert "## Another Section" in file_content  # Next section preserved
    assert "Other content" in file_content


@pytest.mark.asyncio
async def test_edit_entity_replace_section_strips_duplicate_header(
    entity_service: EntityService, file_service: FileService
):
    """Test that replace_section strips duplicate header from content (issue #390)."""
    # Create test entity with a section
    content = dedent("""
        # Main Title

        ## Testing
        Original content

        ## Another Section
        Other content
        """).strip()

    entity = await entity_service.create_entity(
        EntitySchema(
            title="Sample Note",
            folder="docs",
            entity_type="note",
            content=content,
        )
    )

    # Replace section with content that includes the duplicate header
    # (This is what LLMs sometimes do)
    updated = await entity_service.edit_entity(
        identifier=entity.permalink,
        operation="replace_section",
        content="## Testing\nNew content for testing section",
        section="## Testing",
    )

    # Verify that we don't have duplicate headers
    file_path = file_service.get_entity_path(updated)
    file_content, _ = await file_service.read_file(file_path)

    # Count occurrences of "## Testing" - should only be 1
    testing_header_count = file_content.count("## Testing")
    assert testing_header_count == 1, (
        f"Expected 1 '## Testing' header, found {testing_header_count}"
    )

    assert "New content for testing section" in file_content
    assert "Original content" not in file_content
    assert "## Another Section" in file_content  # Other sections preserved


# Move entity tests
@pytest.mark.asyncio
async def test_move_entity_success(
    entity_service: EntityService,
    file_service: FileService,
    project_config: ProjectConfig,
):
    """Test successful entity move with basic settings."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="original",
            entity_type="note",
            content="Original content",
        )
    )

    # Verify original file exists
    original_path = file_service.get_entity_path(entity)
    assert await file_service.exists(original_path)

    # Create app config with permalinks disabled
    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    # Move entity
    assert entity.permalink == "original/test-note"
    await entity_service.move_entity(
        identifier=entity.permalink,
        destination_path="moved/test-note.md",
        project_config=project_config,
        app_config=app_config,
    )

    # Verify original file no longer exists
    assert not await file_service.exists(original_path)

    # Verify new file exists
    new_path = project_config.home / "moved/test-note.md"
    assert new_path.exists()

    # Verify database was updated
    updated_entity = await entity_service.get_by_permalink(entity.permalink)
    assert updated_entity.file_path == "moved/test-note.md"

    # Verify file content is preserved
    new_content, _ = await file_service.read_file("moved/test-note.md")
    assert "Original content" in new_content


@pytest.mark.asyncio
async def test_move_entity_with_permalink_update(
    entity_service: EntityService,
    file_service: FileService,
    project_config: ProjectConfig,
):
    """Test entity move with permalink updates enabled."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="original",
            entity_type="note",
            content="Original content",
        )
    )

    original_permalink = entity.permalink

    # Create app config with permalinks enabled
    app_config = BasicMemoryConfig(update_permalinks_on_move=True)

    # Move entity
    await entity_service.move_entity(
        identifier=entity.permalink,
        destination_path="moved/test-note.md",
        project_config=project_config,
        app_config=app_config,
    )

    # Verify entity was found by new path (since permalink changed)
    moved_entity = await entity_service.link_resolver.resolve_link("moved/test-note.md")
    assert moved_entity is not None
    assert moved_entity.file_path == "moved/test-note.md"
    assert moved_entity.permalink != original_permalink

    # Verify frontmatter was updated with new permalink
    new_content, _ = await file_service.read_file("moved/test-note.md")
    assert moved_entity.permalink in new_content


@pytest.mark.asyncio
async def test_move_entity_creates_destination_directory(
    entity_service: EntityService,
    file_service: FileService,
    project_config: ProjectConfig,
):
    """Test that moving creates destination directory if it doesn't exist."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="original",
            entity_type="note",
            content="Original content",
        )
    )

    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    # Move to deeply nested path that doesn't exist
    await entity_service.move_entity(
        identifier=entity.permalink,
        destination_path="deeply/nested/folders/test-note.md",
        project_config=project_config,
        app_config=app_config,
    )

    # Verify directory was created
    new_path = project_config.home / "deeply/nested/folders/test-note.md"
    assert new_path.exists()
    assert new_path.parent.exists()


@pytest.mark.asyncio
async def test_move_entity_not_found(
    entity_service: EntityService,
    project_config: ProjectConfig,
):
    """Test moving non-existent entity raises error."""
    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    with pytest.raises(EntityNotFoundError, match="Entity not found: non-existent"):
        await entity_service.move_entity(
            identifier="non-existent",
            destination_path="new/path.md",
            project_config=project_config,
            app_config=app_config,
        )


@pytest.mark.asyncio
async def test_move_entity_source_file_missing(
    entity_service: EntityService,
    file_service: FileService,
    project_config: ProjectConfig,
):
    """Test moving when source file doesn't exist on filesystem."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="Original content",
        )
    )

    # Manually delete the file (simulating corruption/external deletion)
    file_path = file_service.get_entity_path(entity)
    file_path.unlink()

    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    with pytest.raises(ValueError, match="Source file not found:"):
        await entity_service.move_entity(
            identifier=entity.permalink,
            destination_path="new/path.md",
            project_config=project_config,
            app_config=app_config,
        )


@pytest.mark.asyncio
async def test_move_entity_destination_exists(
    entity_service: EntityService,
    file_service: FileService,
    project_config: ProjectConfig,
):
    """Test moving to existing destination fails."""
    # Create two test entities
    entity1 = await entity_service.create_entity(
        EntitySchema(
            title="Test Note 1",
            folder="test",
            entity_type="note",
            content="Content 1",
        )
    )

    entity2 = await entity_service.create_entity(
        EntitySchema(
            title="Test Note 2",
            folder="test",
            entity_type="note",
            content="Content 2",
        )
    )

    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    # Try to move entity1 to entity2's location
    with pytest.raises(ValueError, match="Destination already exists:"):
        await entity_service.move_entity(
            identifier=entity1.permalink,
            destination_path=entity2.file_path,
            project_config=project_config,
            app_config=app_config,
        )


@pytest.mark.asyncio
async def test_move_entity_invalid_destination_path(
    entity_service: EntityService,
    project_config: ProjectConfig,
):
    """Test moving with invalid destination paths."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="test",
            entity_type="note",
            content="Original content",
        )
    )

    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    # Test absolute path
    with pytest.raises(ValueError, match="Invalid destination path:"):
        await entity_service.move_entity(
            identifier=entity.permalink,
            destination_path="/absolute/path.md",
            project_config=project_config,
            app_config=app_config,
        )

    # Test empty path
    with pytest.raises(ValueError, match="Invalid destination path:"):
        await entity_service.move_entity(
            identifier=entity.permalink,
            destination_path="",
            project_config=project_config,
            app_config=app_config,
        )


@pytest.mark.asyncio
async def test_move_entity_by_title(
    entity_service: EntityService,
    file_service: FileService,
    project_config: ProjectConfig,
    app_config: BasicMemoryConfig,
):
    """Test moving entity by title instead of permalink."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="original",
            entity_type="note",
            content="Original content",
        )
    )

    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    # Move by title
    await entity_service.move_entity(
        identifier="Test Note",  # Use title instead of permalink
        destination_path="moved/test-note.md",
        project_config=project_config,
        app_config=app_config,
    )

    # Verify old path no longer exists
    new_path = project_config.home / entity.file_path
    assert not new_path.exists()

    # Verify new file exists
    new_path = project_config.home / "moved/test-note.md"
    assert new_path.exists()


@pytest.mark.asyncio
async def test_move_entity_preserves_observations_and_relations(
    entity_service: EntityService,
    file_service: FileService,
    project_config: ProjectConfig,
):
    """Test that moving preserves entity observations and relations."""
    # Create test entity with observations and relations
    content = dedent("""
        # Test Note
        
        - [note] This is an observation #test
        - links to [[Other Entity]]
        
        Original content
        """).strip()

    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="original",
            entity_type="note",
            content=content,
        )
    )

    # Verify initial observations and relations
    assert len(entity.observations) == 1
    assert len(entity.relations) == 1

    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    # Move entity
    await entity_service.move_entity(
        identifier=entity.permalink,
        destination_path="moved/test-note.md",
        project_config=project_config,
        app_config=app_config,
    )

    # Get moved entity
    moved_entity = await entity_service.link_resolver.resolve_link("moved/test-note.md")

    # Verify observations and relations are preserved
    assert len(moved_entity.observations) == 1
    assert moved_entity.observations[0].content == "This is an observation #test"
    assert len(moved_entity.relations) == 1
    assert moved_entity.relations[0].to_name == "Other Entity"

    # Verify file content includes observations and relations
    new_content, _ = await file_service.read_file("moved/test-note.md")
    assert "- [note] This is an observation #test" in new_content
    assert "- links to [[Other Entity]]" in new_content


@pytest.mark.asyncio
async def test_move_entity_rollback_on_database_failure(
    entity_service: EntityService,
    file_service: FileService,
    project_config: ProjectConfig,
    entity_repository: EntityRepository,
):
    """Test that filesystem changes are rolled back on database failures."""
    # Create test entity
    entity = await entity_service.create_entity(
        EntitySchema(
            title="Test Note",
            folder="original",
            entity_type="note",
            content="Original content",
        )
    )

    original_path = file_service.get_entity_path(entity)
    assert await file_service.exists(original_path)

    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    # Mock repository update to fail
    original_update = entity_repository.update

    async def failing_update(*args, **kwargs):
        return None  # Simulate failure

    entity_repository.update = failing_update

    try:
        with pytest.raises(ValueError, match="Move failed:"):
            await entity_service.move_entity(
                identifier=entity.permalink,
                destination_path="moved/test-note.md",
                project_config=project_config,
                app_config=app_config,
            )

        # Verify rollback - original file should still exist
        assert await file_service.exists(original_path)

        # Verify destination file was cleaned up
        destination_path = project_config.home / "moved/test-note.md"
        assert not destination_path.exists()

    finally:
        # Restore original update method
        entity_repository.update = original_update


@pytest.mark.asyncio
async def test_move_entity_with_complex_observations(
    entity_service: EntityService,
    file_service: FileService,
    project_config: ProjectConfig,
):
    """Test moving entity with complex observations (tags, context)."""
    content = dedent("""
        # Complex Note
        
        - [design] Keep feature branches short-lived #git #workflow (Reduces merge conflicts)
        - [tech] Using SQLite for storage #implementation (Fast and reliable)
        - implements [[Branch Strategy]] (Our standard workflow)
        
        Complex content with [[Multiple]] [[Links]].
        """).strip()

    entity = await entity_service.create_entity(
        EntitySchema(
            title="Complex Note",
            folder="docs",
            entity_type="note",
            content=content,
        )
    )

    # Verify complex structure
    assert len(entity.observations) == 2
    assert len(entity.relations) == 3  # 1 explicit + 2 wikilinks

    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    # Move entity
    await entity_service.move_entity(
        identifier=entity.permalink,
        destination_path="moved/complex-note.md",
        project_config=project_config,
        app_config=app_config,
    )

    # Verify moved entity maintains structure
    moved_entity = await entity_service.link_resolver.resolve_link("moved/complex-note.md")

    # Check observations with tags and context
    design_obs = [obs for obs in moved_entity.observations if obs.category == "design"][0]
    assert "git" in design_obs.tags
    assert "workflow" in design_obs.tags
    assert design_obs.context == "Reduces merge conflicts"

    tech_obs = [obs for obs in moved_entity.observations if obs.category == "tech"][0]
    assert "implementation" in tech_obs.tags
    assert tech_obs.context == "Fast and reliable"

    # Check relations
    relation_types = {rel.relation_type for rel in moved_entity.relations}
    assert "implements" in relation_types
    assert "links_to" in relation_types

    relation_targets = {rel.to_name for rel in moved_entity.relations}
    assert "Branch Strategy" in relation_targets
    assert "Multiple" in relation_targets
    assert "Links" in relation_targets


@pytest.mark.asyncio
async def test_move_entity_with_null_permalink_generates_permalink(
    entity_service: EntityService,
    project_config: ProjectConfig,
    entity_repository: EntityRepository,
):
    """Test that moving entity with null permalink generates a new permalink automatically.

    This tests the fix for issue #155 where entities with null permalinks from the database
    migration would fail validation when being moved. The fix ensures that entities with
    null permalinks get a generated permalink during move operations, regardless of the
    update_permalinks_on_move setting.
    """
    # Create entity through direct database insertion to simulate migrated entity with null permalink
    from datetime import datetime, timezone

    # Create an entity with null permalink directly in database (simulating migrated data)
    entity_data = {
        "title": "Test Entity",
        "file_path": "test/null-permalink-entity.md",
        "entity_type": "note",
        "content_type": "text/markdown",
        "permalink": None,  # This is the key - null permalink from migration
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }

    # Create the entity directly in database
    created_entity = await entity_repository.create(entity_data)
    assert created_entity.permalink is None

    # Create the physical file
    file_path = project_config.home / created_entity.file_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("# Test Entity\n\nContent here.")

    # Configure move without permalink updates (the default setting that previously triggered the bug)
    app_config = BasicMemoryConfig(update_permalinks_on_move=False)

    # Move entity - this should now succeed and generate a permalink
    moved_entity = await entity_service.move_entity(
        identifier=created_entity.title,  # Use title since permalink is None
        destination_path="moved/test-entity.md",
        project_config=project_config,
        app_config=app_config,
    )

    # Verify the move succeeded and a permalink was generated
    assert moved_entity is not None
    assert moved_entity.file_path == "moved/test-entity.md"
    assert moved_entity.permalink is not None
    assert moved_entity.permalink != ""

    # Verify the moved entity can be used to create an EntityResponse without validation errors
    from basic_memory.schemas.response import EntityResponse

    response = EntityResponse.model_validate(moved_entity)
    assert response.permalink == moved_entity.permalink

    # Verify the physical file was moved
    old_path = project_config.home / "test/null-permalink-entity.md"
    new_path = project_config.home / "moved/test-entity.md"
    assert not old_path.exists()
    assert new_path.exists()


@pytest.mark.asyncio
async def test_create_or_update_entity_fuzzy_search_bug(
    entity_service: EntityService,
    file_service: FileService,
    project_config: ProjectConfig,
    search_service: SearchService,
):
    """Test that create_or_update_entity doesn't incorrectly match similar entities via fuzzy search.

    This reproduces the critical bug where creating "Node C" overwrote "Node A.md"
    because fuzzy search incorrectly matched the similar file paths.

    Root cause: link_resolver.resolve_link() uses fuzzy search fallback which matches
    "edge-cases/Node C.md" to existing "edge-cases/Node A.md" because they share
    similar words ("edge-cases", "Node").

    Expected: Create new entity "Node C" with its own file
    Actual Bug: Updates existing "Node A" entity, overwriting its file
    """
    # Step 1: Create first entity "Node A"
    entity_a = EntitySchema(
        title="Node A",
        folder="edge-cases",
        entity_type="note",
        content="# Node A\n\nOriginal content for Node A",
    )

    created_a, is_new_a = await entity_service.create_or_update_entity(entity_a)
    assert is_new_a is True, "Node A should be created as new entity"
    assert created_a.title == "Node A"
    assert created_a.file_path == "edge-cases/Node A.md"

    # CRITICAL: Index Node A in search to enable fuzzy search fallback
    # This is what triggers the bug - without indexing, fuzzy search returns no results
    await search_service.index_entity(created_a)

    # Verify Node A file exists with correct content
    file_a = project_config.home / "edge-cases" / "Node A.md"
    assert file_a.exists(), "Node A.md file should exist"
    content_a = file_a.read_text()
    assert "Node A" in content_a
    assert "Original content for Node A" in content_a

    # Step 2: Create Node B to match live test scenario
    entity_b = EntitySchema(
        title="Node B",
        folder="edge-cases",
        entity_type="note",
        content="# Node B\n\nContent for Node B",
    )

    created_b, is_new_b = await entity_service.create_or_update_entity(entity_b)
    assert is_new_b is True
    await search_service.index_entity(created_b)

    # Step 3: Create Node C - this is where the bug occurs in live testing
    # BUG: This will incorrectly match Node A via fuzzy search
    entity_c = EntitySchema(
        title="Node C",
        folder="edge-cases",
        entity_type="note",
        content="# Node C\n\nContent for Node C",
    )

    created_c, is_new_c = await entity_service.create_or_update_entity(entity_c)

    # CRITICAL ASSERTIONS: Node C should be created as NEW entity, not update Node A
    assert is_new_c is True, "Node C should be created as NEW entity, not update existing"
    assert created_c.title == "Node C", "Created entity should have title 'Node C'"
    assert created_c.file_path == "edge-cases/Node C.md", "Should create Node C.md file"
    assert created_c.id != created_a.id, "Node C should have different ID than Node A"

    # Verify both files exist with correct content
    file_c = project_config.home / "edge-cases" / "Node C.md"
    assert file_c.exists(), "Node C.md file should exist as separate file"

    # Re-read Node A file to ensure it wasn't overwritten
    content_a_after = file_a.read_text()
    assert "title: Node A" in content_a_after, "Node A.md should still have Node A title"
    assert "Original content for Node A" in content_a_after, (
        "Node A.md should NOT be overwritten with Node C content"
    )
    assert "Content for Node C" not in content_a_after, (
        "Node A.md should not contain Node C content"
    )

    # Verify Node C file has correct content
    content_c = file_c.read_text()
    assert "title: Node C" in content_c, "Node C.md should have Node C title"
    assert "Content for Node C" in content_c, "Node C.md should have Node C content"
    assert "Original content for Node A" not in content_c, (
        "Node C.md should not contain Node A content"
    )
