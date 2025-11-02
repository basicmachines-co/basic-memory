"""Test that YAML date parsing doesn't break frontmatter processing.

This test reproduces GitHub issue #236 from basic-memory-cloud where date fields
in YAML frontmatter are automatically parsed as datetime.date objects by PyYAML,
but later code expects strings and calls .strip() on them, causing AttributeError.
"""

import pytest
from basic_memory.markdown.entity_parser import EntityParser


@pytest.fixture
def test_file_with_date(tmp_path):
    """Create a test file with date fields in frontmatter."""
    test_file = tmp_path / "test_note.md"
    content = """---
title: Test Note
date: 2025-10-24
created: 2025-10-24
tags:
  - python
  - testing
---

# Test Content

This file has date fields in frontmatter that PyYAML will parse as datetime.date objects.
"""
    test_file.write_text(content)
    return test_file


@pytest.fixture
def test_file_with_date_in_tags(tmp_path):
    """Create a test file with a date value in tags (edge case)."""
    test_file = tmp_path / "test_note_date_tags.md"
    content = """---
title: Test Note with Date Tags
tags: 2025-10-24
---

# Test Content

This file has a date value as tags, which will be parsed as datetime.date.
"""
    test_file.write_text(content)
    return test_file


@pytest.fixture
def test_file_with_dates_in_tag_list(tmp_path):
    """Create a test file with dates in a tag list (edge case)."""
    test_file = tmp_path / "test_note_dates_in_list.md"
    content = """---
title: Test Note with Dates in Tags List
tags:
  - valid-tag
  - 2025-10-24
  - another-tag
---

# Test Content

This file has date values mixed into tags list.
"""
    test_file.write_text(content)
    return test_file


@pytest.mark.asyncio
async def test_parse_file_with_date_fields(test_file_with_date, tmp_path):
    """Test that files with date fields in frontmatter can be parsed without errors."""
    parser = EntityParser(tmp_path)

    # This should not raise AttributeError about .strip()
    entity_markdown = await parser.parse_file(test_file_with_date)

    # Verify basic parsing worked
    assert entity_markdown.frontmatter.title == "Test Note"

    # Date fields should be converted to ISO format strings
    date_field = entity_markdown.frontmatter.metadata.get("date")
    assert date_field is not None
    assert isinstance(date_field, str), "Date should be converted to string"
    assert date_field == "2025-10-24", "Date should be in ISO format"

    created_field = entity_markdown.frontmatter.metadata.get("created")
    assert created_field is not None
    assert isinstance(created_field, str), "Created date should be converted to string"
    assert created_field == "2025-10-24", "Created date should be in ISO format"


@pytest.mark.asyncio
async def test_parse_file_with_date_as_tags(test_file_with_date_in_tags, tmp_path):
    """Test that date values in tags field don't cause errors."""
    parser = EntityParser(tmp_path)

    # This should not raise AttributeError - date should be converted to string
    entity_markdown = await parser.parse_file(test_file_with_date_in_tags)
    assert entity_markdown.frontmatter.title == "Test Note with Date Tags"

    # The date should be converted to ISO format string before parse_tags processes it
    tags = entity_markdown.frontmatter.tags
    assert tags is not None
    assert isinstance(tags, list)
    # The date value should be converted to string
    assert "2025-10-24" in tags


@pytest.mark.asyncio
async def test_parse_file_with_dates_in_tag_list(test_file_with_dates_in_tag_list, tmp_path):
    """Test that date values in a tags list don't cause errors."""
    parser = EntityParser(tmp_path)

    # This should not raise AttributeError - dates should be converted to strings
    entity_markdown = await parser.parse_file(test_file_with_dates_in_tag_list)
    assert entity_markdown.frontmatter.title == "Test Note with Dates in Tags List"

    # Tags should be parsed
    tags = entity_markdown.frontmatter.tags
    assert tags is not None
    assert isinstance(tags, list)

    # Should have 3 tags (2 valid + 1 date converted to ISO string)
    assert len(tags) == 3
    assert "valid-tag" in tags
    assert "another-tag" in tags
    # Date should be converted to ISO format string
    assert "2025-10-24" in tags


@pytest.mark.asyncio
async def test_parse_file_with_various_yaml_types(tmp_path):
    """Test that various YAML types in frontmatter don't cause errors.

    This reproduces the broader issue from GitHub #236 where ANY non-string
    YAML type (dates, lists, numbers, booleans) can cause AttributeError
    when code expects strings and calls .strip().
    """
    test_file = tmp_path / "test_yaml_types.md"
    content = """---
title: Test YAML Types
date: 2025-10-24
priority: 1
completed: true
tags:
  - python
  - testing
metadata:
  author: Test User
  version: 1.0
---

# Test Content

This file has various YAML types that need to be normalized.
"""
    test_file.write_text(content)

    parser = EntityParser(tmp_path)
    entity_markdown = await parser.parse_file(test_file)

    # All values should be accessible without AttributeError
    assert entity_markdown.frontmatter.title == "Test YAML Types"

    # Date should be converted to ISO string
    date_field = entity_markdown.frontmatter.metadata.get("date")
    assert isinstance(date_field, str)
    assert date_field == "2025-10-24"

    # Number should be converted to string
    priority = entity_markdown.frontmatter.metadata.get("priority")
    assert isinstance(priority, str)
    assert priority == "1"

    # Boolean should be converted to string
    completed = entity_markdown.frontmatter.metadata.get("completed")
    assert isinstance(completed, str)
    assert completed == "True"  # Python's str(True) always returns "True"

    # List should be preserved as list, but items should be strings
    tags = entity_markdown.frontmatter.tags
    assert isinstance(tags, list)
    assert all(isinstance(tag, str) for tag in tags)
    assert "python" in tags
    assert "testing" in tags

    # Dict should be preserved as dict, but nested values should be strings
    metadata = entity_markdown.frontmatter.metadata.get("metadata")
    assert isinstance(metadata, dict)
    assert isinstance(metadata.get("author"), str)
    assert metadata.get("author") == "Test User"
    assert isinstance(metadata.get("version"), str)
    assert metadata.get("version") in ("1.0", "1")


@pytest.mark.asyncio
async def test_parse_file_with_datetime_objects(tmp_path):
    """Test that datetime objects (not just date objects) are properly normalized.

    This tests the edge case where frontmatter might contain datetime values
    with time components (as parsed by PyYAML), ensuring they're converted to ISO format strings.
    """
    test_file = tmp_path / "test_datetime.md"

    # YAML datetime strings that PyYAML will parse as datetime objects
    # Format: YYYY-MM-DD HH:MM:SS or YYYY-MM-DDTHH:MM:SS
    content = """---
title: Test Datetime
created_at: 2025-10-24 14:30:00
updated_at: 2025-10-24T00:00:00
---

# Test Content

This file has datetime values in frontmatter that PyYAML will parse as datetime objects.
"""
    test_file.write_text(content)

    parser = EntityParser(tmp_path)
    entity_markdown = await parser.parse_file(test_file)

    # Verify datetime objects are converted to ISO format strings
    created_at = entity_markdown.frontmatter.metadata.get("created_at")
    assert isinstance(created_at, str), "Datetime should be converted to string"
    # PyYAML parses "2025-10-24 14:30:00" as datetime, which we normalize to ISO
    assert "2025-10-24" in created_at and "14:30:00" in created_at, (
        f"Datetime with time should be normalized to ISO format, got: {created_at}"
    )

    updated_at = entity_markdown.frontmatter.metadata.get("updated_at")
    assert isinstance(updated_at, str), "Datetime should be converted to string"
    # PyYAML parses "2025-10-24T00:00:00" as datetime, which we normalize to ISO
    assert "2025-10-24" in updated_at and "00:00:00" in updated_at, (
        f"Datetime at midnight should be normalized to ISO format, got: {updated_at}"
    )
