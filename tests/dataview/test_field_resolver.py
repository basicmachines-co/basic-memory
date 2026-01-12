"""Tests for FieldResolver."""

import pytest

from basic_memory.dataview.executor.field_resolver import FieldResolver


class TestFieldResolverFileFields:
    """Test resolving file.* fields."""

    def test_resolve_file_name(self, note_with_frontmatter):
        """Test resolving file.name."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "file.name")
        assert value == "Test Note"

    def test_resolve_file_link(self, note_with_frontmatter):
        """Test resolving file.link."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "file.link")
        assert value == "[[Test Note]]"

    def test_resolve_file_path(self, note_with_frontmatter):
        """Test resolving file.path."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "file.path")
        assert value == "test.md"

    def test_resolve_file_folder(self, note_with_frontmatter):
        """Test resolving file.folder."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "file.folder")
        assert value == "test"

    def test_resolve_file_ctime(self, note_with_frontmatter):
        """Test resolving file.ctime."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "file.ctime")
        assert value == "2026-01-01"

    def test_resolve_file_mtime(self, note_with_frontmatter):
        """Test resolving file.mtime."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "file.mtime")
        assert value == "2026-01-10"


class TestFieldResolverFrontmatterFields:
    """Test resolving frontmatter fields."""

    def test_resolve_status(self, note_with_frontmatter):
        """Test resolving status field."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "status")
        assert value == "active"

    def test_resolve_priority(self, note_with_frontmatter):
        """Test resolving priority field."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "priority")
        assert value == 1

    def test_resolve_tags(self, note_with_frontmatter):
        """Test resolving tags field."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "tags")
        assert value == ["test", "dev"]

    def test_resolve_due(self, note_with_frontmatter):
        """Test resolving due field."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "due")
        assert value == "2026-01-15"


class TestFieldResolverDirectFields:
    """Test resolving direct note fields."""

    def test_resolve_title(self, note_with_frontmatter):
        """Test resolving title field."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "title")
        assert value == "Test Note"

    def test_resolve_content(self, note_with_frontmatter):
        """Test resolving content field."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "content")
        assert "Task 1" in value


class TestFieldResolverMissingFields:
    """Test resolving missing fields."""

    def test_resolve_missing_field(self, note_with_frontmatter):
        """Test resolving non-existent field."""
        value = FieldResolver.resolve_field(note_with_frontmatter, "nonexistent")
        assert value is None

    def test_resolve_field_without_frontmatter(self, note_without_frontmatter):
        """Test resolving field when no frontmatter."""
        value = FieldResolver.resolve_field(note_without_frontmatter, "status")
        assert value is None


class TestFieldResolverHasField:
    """Test has_field method."""

    def test_has_file_field(self, note_with_frontmatter):
        """Test has_field for file.* fields."""
        assert FieldResolver.has_field(note_with_frontmatter, "file.name") is True
        assert FieldResolver.has_field(note_with_frontmatter, "file.link") is True

    def test_has_frontmatter_field(self, note_with_frontmatter):
        """Test has_field for frontmatter fields."""
        assert FieldResolver.has_field(note_with_frontmatter, "status") is True
        assert FieldResolver.has_field(note_with_frontmatter, "priority") is True

    def test_has_direct_field(self, note_with_frontmatter):
        """Test has_field for direct fields."""
        assert FieldResolver.has_field(note_with_frontmatter, "title") is True
        assert FieldResolver.has_field(note_with_frontmatter, "content") is True

    def test_does_not_have_field(self, note_with_frontmatter):
        """Test has_field for non-existent field."""
        assert FieldResolver.has_field(note_with_frontmatter, "nonexistent") is False


class TestFieldResolverEdgeCases:
    """Test edge cases."""

    def test_resolve_field_empty_note(self):
        """Test resolving field from empty note."""
        note = {}
        value = FieldResolver.resolve_field(note, "status")
        assert value is None

    def test_resolve_file_name_no_title(self):
        """Test resolving file.name when no title."""
        note = {"id": 1}
        value = FieldResolver.resolve_field(note, "file.name")
        assert value == ""

    def test_resolve_field_none_value(self):
        """Test resolving field with None value."""
        note = {"frontmatter": {"status": None}}
        value = FieldResolver.resolve_field(note, "status")
        assert value is None
