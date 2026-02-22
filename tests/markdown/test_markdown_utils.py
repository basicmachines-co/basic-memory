"""Tests for markdown/utils.py - entity model conversion utilities."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from basic_memory.markdown.schemas import EntityMarkdown, EntityFrontmatter, Observation
from basic_memory.markdown.utils import entity_model_from_markdown
from basic_memory.models import Entity


class TestEntityModelFromMarkdown:
    """Tests for entity_model_from_markdown function."""

    def _create_markdown(
        self,
        title: str = "Test Entity",
        note_type: str = "note",
        permalink: str = "test/test-entity",
        created: datetime | None = None,
        modified: datetime | None = None,
        observations: list[Observation] | None = None,
    ) -> EntityMarkdown:
        """Helper to create test EntityMarkdown objects."""
        now = datetime.now(timezone.utc)
        return EntityMarkdown(
            frontmatter=EntityFrontmatter(
                title=title,
                type=note_type,
                permalink=permalink,
            ),
            content=f"# {title}\n\nTest content.",
            observations=observations or [],
            relations=[],
            created=created or now,
            modified=modified or now,
        )

    def test_new_entity_has_external_id(self):
        """Test that a new entity always gets an external_id set.

        This is a regression test for GitHub issue #512 where SQLite failed
        with NOT NULL constraint on external_id because SQLAlchemy's Python-side
        default wasn't always evaluated.
        """
        markdown = self._create_markdown()
        file_path = Path("test/test-entity.md")

        entity = entity_model_from_markdown(file_path, markdown)

        # external_id must be set (non-None, non-empty)
        assert entity.external_id is not None
        assert entity.external_id != ""
        # Should be a valid UUID format (36 chars with hyphens)
        assert len(entity.external_id) == 36
        assert entity.external_id.count("-") == 4

    def test_existing_entity_preserves_external_id(self):
        """Test that an existing entity's external_id is preserved."""
        markdown = self._create_markdown()
        file_path = Path("test/test-entity.md")

        # Create existing entity with known external_id
        existing_external_id = "12345678-1234-1234-1234-123456789012"
        existing_entity = Entity()
        existing_entity.external_id = existing_external_id

        entity = entity_model_from_markdown(file_path, markdown, entity=existing_entity)

        # Should preserve the existing external_id
        assert entity.external_id == existing_external_id

    def test_entity_with_empty_external_id_gets_new_one(self):
        """Test that an entity with empty string external_id gets a new UUID."""
        markdown = self._create_markdown()
        file_path = Path("test/test-entity.md")

        # Create existing entity with empty external_id
        existing_entity = Entity()
        existing_entity.external_id = ""

        entity = entity_model_from_markdown(file_path, markdown, entity=existing_entity)

        # Should have a new external_id
        assert entity.external_id is not None
        assert entity.external_id != ""
        assert len(entity.external_id) == 36

    def test_entity_with_none_external_id_gets_new_one(self):
        """Test that an entity with None external_id gets a new UUID."""
        markdown = self._create_markdown()
        file_path = Path("test/test-entity.md")

        # Create existing entity with None external_id
        existing_entity = Entity()
        # Explicitly set to None to test this case
        object.__setattr__(existing_entity, "external_id", None)

        entity = entity_model_from_markdown(file_path, markdown, entity=existing_entity)

        # Should have a new external_id
        assert entity.external_id is not None
        assert entity.external_id != ""
        assert len(entity.external_id) == 36

    def test_multiple_calls_generate_unique_ids(self):
        """Test that multiple new entities get unique external_ids."""
        markdown1 = self._create_markdown(title="Entity 1", permalink="test/entity-1")
        markdown2 = self._create_markdown(title="Entity 2", permalink="test/entity-2")

        entity1 = entity_model_from_markdown(Path("test/entity-1.md"), markdown1)
        entity2 = entity_model_from_markdown(Path("test/entity-2.md"), markdown2)

        # Both should have external_ids
        assert entity1.external_id is not None
        assert entity2.external_id is not None

        # They should be unique
        assert entity1.external_id != entity2.external_id

    def test_entity_model_fields_populated_with_external_id(self):
        """Test that entity fields are populated correctly, including external_id.

        This is a basic sanity check that entity_model_from_markdown sets
        the key fields we care about for the #512 fix.
        """
        markdown = self._create_markdown(
            title="My Test Entity",
            note_type="component",
            permalink="components/my-test",
        )
        file_path = Path("components/my-test.md")

        entity = entity_model_from_markdown(file_path, markdown, project_id=1)

        # The key assertion: external_id must always be set
        assert entity.external_id is not None
        assert len(entity.external_id) == 36  # UUID format

        # Verify file_path is set correctly (uses posix format)
        assert entity.file_path == "components/my-test.md"

        # Timestamps should be set from markdown
        assert entity.created_at is not None
        assert entity.updated_at is not None

    def test_missing_dates_raises_error(self):
        """Test that missing created/modified dates raise ValueError."""
        markdown = EntityMarkdown(
            frontmatter=EntityFrontmatter(
                title="Test",
                type="note",
            ),
            content="# Test",
            observations=[],
            relations=[],
            created=None,
            modified=None,
        )

        with pytest.raises(ValueError, match="Both created and modified dates are required"):
            entity_model_from_markdown(Path("test.md"), markdown)
