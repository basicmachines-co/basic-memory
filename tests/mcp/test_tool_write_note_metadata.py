"""Tests for the write_note `metadata` parameter.

Covers positive, negative, and edge-case scenarios for passing arbitrary
frontmatter fields through note_metadata.
"""

import pytest

from basic_memory.mcp.tools import write_note, read_note


# --- Positive tests ---


@pytest.mark.asyncio
async def test_metadata_simple_keys(app, test_project):
    """Simple key-value metadata appears as top-level YAML frontmatter."""
    result = await write_note(
        project=test_project.name,
        title="Simple Metadata",
        directory="meta-tests",
        content="# Simple Metadata\n\nBody text.",
        metadata={"author": "Alice", "status": "draft"},
    )

    assert "# Created note" in result

    content = await read_note("meta-tests/simple-metadata", project=test_project.name)
    assert "author: Alice" in content
    assert "status: draft" in content


@pytest.mark.asyncio
async def test_metadata_nested_dict(app, test_project):
    """Nested dict metadata renders as nested YAML."""
    result = await write_note(
        project=test_project.name,
        title="Nested Metadata",
        directory="meta-tests",
        content="# Nested Metadata",
        metadata={
            "schema": {"name": "string", "role?": "string"},
            "settings": {"validation": "warn"},
        },
    )

    assert "# Created note" in result

    content = await read_note("meta-tests/nested-metadata", project=test_project.name)
    assert "schema:" in content
    assert "name: string" in content
    assert "role?: string" in content
    assert "settings:" in content
    assert "validation: warn" in content


@pytest.mark.asyncio
async def test_metadata_with_tags(app, test_project):
    """Metadata and tags coexist — both appear in frontmatter."""
    result = await write_note(
        project=test_project.name,
        title="Tags And Metadata",
        directory="meta-tests",
        content="# Tags And Metadata",
        tags=["one", "two"],
        metadata={"priority": "high"},
    )

    assert "# Created note" in result
    assert "## Tags" in result

    content = await read_note("meta-tests/tags-and-metadata", project=test_project.name)
    assert "priority: high" in content
    assert "- one" in content
    assert "- two" in content


@pytest.mark.asyncio
async def test_metadata_various_value_types(app, test_project):
    """Metadata values of int, bool, and list types survive round-trip."""
    result = await write_note(
        project=test_project.name,
        title="Typed Values",
        directory="meta-tests",
        content="# Typed Values",
        metadata={"version": 3, "active": True, "aliases": ["tv", "typed"]},
    )

    assert "# Created note" in result

    content = await read_note("meta-tests/typed-values", project=test_project.name)
    # YAML normalizes values to strings during frontmatter round-trip
    assert "version:" in content
    assert "active:" in content
    assert "aliases:" in content


@pytest.mark.asyncio
async def test_metadata_survives_update(app, test_project):
    """Metadata set at create time persists through an update cycle."""
    # Create with metadata
    await write_note(
        project=test_project.name,
        title="Update Cycle",
        directory="meta-tests",
        content="# Version 1",
        metadata={"author": "Bob", "version": 1},
    )

    # Update same note with new content + metadata
    result = await write_note(
        project=test_project.name,
        title="Update Cycle",
        directory="meta-tests",
        content="# Version 2",
        metadata={"author": "Bob", "version": 2},
    )

    assert "# Updated note" in result

    content = await read_note("meta-tests/update-cycle", project=test_project.name)
    assert "# Version 2" in content
    assert "author: Bob" in content
    assert "version:" in content


@pytest.mark.asyncio
async def test_metadata_with_note_type(app, test_project):
    """Metadata works together with a custom note_type."""
    result = await write_note(
        project=test_project.name,
        title="Config Entry",
        directory="meta-tests",
        content="# Config Entry",
        note_type="config",
        metadata={"env": "production", "ttl": 300},
    )

    assert "# Created note" in result

    content = await read_note("meta-tests/config-entry", project=test_project.name)
    assert "type: config" in content
    assert "env: production" in content


# --- Edge cases: empty / None ---


@pytest.mark.asyncio
async def test_metadata_none_default(app, test_project):
    """metadata=None (default) produces the same output as before the feature existed."""
    result = await write_note(
        project=test_project.name,
        title="No Metadata",
        directory="meta-tests",
        content="# No Metadata",
        tags=["plain"],
        metadata=None,
    )

    assert "# Created note" in result

    content = await read_note("meta-tests/no-metadata", project=test_project.name)
    # Only standard keys should be present
    assert "title: No Metadata" in content
    assert "type: note" in content
    assert "- plain" in content


@pytest.mark.asyncio
async def test_metadata_empty_dict(app, test_project):
    """metadata={} behaves identically to metadata=None."""
    result = await write_note(
        project=test_project.name,
        title="Empty Dict",
        directory="meta-tests",
        content="# Empty Dict",
        metadata={},
    )

    assert "# Created note" in result

    content = await read_note("meta-tests/empty-dict", project=test_project.name)
    assert "title: Empty Dict" in content
    assert "type: note" in content


# --- Edge cases: key conflicts ---


@pytest.mark.asyncio
async def test_metadata_title_key_stripped(app, test_project):
    """A 'title' key in metadata does not override the title parameter.

    schema_to_markdown pops 'title' from note_metadata so the Entity.title wins.
    """
    result = await write_note(
        project=test_project.name,
        title="Real Title",
        directory="meta-tests",
        content="# Real Title",
        metadata={"title": "Fake Title"},
    )

    assert "# Created note" in result

    content = await read_note("meta-tests/real-title", project=test_project.name)
    assert "title: Real Title" in content
    assert "Fake Title" not in content


@pytest.mark.asyncio
async def test_metadata_type_key_stripped(app, test_project):
    """A 'type' key in metadata does not override note_type parameter.

    schema_to_markdown pops 'type' from note_metadata so Entity.note_type wins.
    """
    result = await write_note(
        project=test_project.name,
        title="Type Conflict",
        directory="meta-tests",
        content="# Type Conflict",
        note_type="guide",
        metadata={"type": "evil"},
    )

    assert "# Created note" in result

    content = await read_note("meta-tests/type-conflict", project=test_project.name)
    assert "type: guide" in content
    assert "evil" not in content


@pytest.mark.asyncio
async def test_metadata_permalink_key_stripped(app, test_project):
    """A 'permalink' key in metadata does not hijack the canonical permalink.

    schema_to_markdown pops 'permalink' from note_metadata.
    """
    result = await write_note(
        project=test_project.name,
        title="Permalink Conflict",
        directory="meta-tests",
        content="# Permalink Conflict",
        metadata={"permalink": "hacked/path"},
    )

    assert "# Created note" in result
    # The canonical permalink should be based on title/directory, not the metadata value
    assert "meta-tests/permalink-conflict" in result

    content = await read_note("meta-tests/permalink-conflict", project=test_project.name)
    assert "hacked/path" not in content


@pytest.mark.asyncio
async def test_tags_param_wins_over_metadata_tags(app, test_project):
    """When both tags param and metadata['tags'] are provided, the explicit param wins.

    The explicit tags parameter is applied after metadata.update(), so it takes
    precedence. The summary and file contents stay consistent.
    """
    result = await write_note(
        project=test_project.name,
        title="Tags Override",
        directory="meta-tests",
        content="# Tags Override",
        tags=["from-param"],
        metadata={"tags": ["from-metadata"]},
    )

    assert "# Created note" in result
    # Summary should reflect the winning tags
    assert "from-param" in result

    content = await read_note("meta-tests/tags-override", project=test_project.name)
    # Explicit tags parameter wins over metadata tags key
    assert "- from-param" in content
    assert "from-metadata" not in content


@pytest.mark.asyncio
async def test_metadata_tags_key_works_when_no_tags_param(app, test_project):
    """When only metadata['tags'] is provided (no tags param), it is used."""
    result = await write_note(
        project=test_project.name,
        title="Metadata Tags Only",
        directory="meta-tests",
        content="# Metadata Tags Only",
        metadata={"tags": ["meta-tag-1", "meta-tag-2"]},
    )

    assert "# Created note" in result

    content = await read_note("meta-tests/metadata-tags-only", project=test_project.name)
    assert "- meta-tag-1" in content
    assert "- meta-tag-2" in content
