"""Tests for resource router endpoints."""

from datetime import datetime, timezone

import pytest
from pathlib import Path


@pytest.mark.asyncio
async def test_get_resource_content(client, test_config, entity_repository):
    """Test getting content by permalink."""
    # Create a test file
    content = "# Test Content\n\nThis is a test file."
    test_file = Path(test_config.home) / "test" / "test.md"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(content)

    # Create entity referencing the file
    entity = await entity_repository.create(
        {
            "title": "Test Entity",
            "entity_type": "test",
            "permalink": "test/test",
            "file_path": "test/test.md",  # Relative to config.home
            "content_type": "text/markdown",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )

    # Test getting the content
    response = await client.get(f"/resource/{entity.permalink}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.text == content


@pytest.mark.asyncio
async def test_get_resource_by_title(client, test_config, entity_repository):
    """Test getting content by permalink."""
    # Create a test file
    content = "# Test Content\n\nThis is a test file."
    test_file = Path(test_config.home) / "test" / "test.md"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(content)

    # Create entity referencing the file
    entity = await entity_repository.create(
        {
            "title": "Test Entity",
            "entity_type": "test",
            "permalink": "test/test",
            "file_path": "test/test.md",  # Relative to config.home
            "content_type": "text/markdown",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )

    # Test getting the content
    response = await client.get(f"/resource/{entity.title}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_resource_missing_entity(client):
    """Test 404 when entity doesn't exist."""
    response = await client.get("/resource/does/not/exist")
    assert response.status_code == 404
    assert "Entity not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_resource_missing_file(client, test_config, entity_repository):
    """Test 404 when file doesn't exist."""
    # Create entity referencing non-existent file
    entity = await entity_repository.create(
        {
            "title": "Missing File",
            "entity_type": "test",
            "permalink": "test/missing",
            "file_path": "test/missing.md",
            "content_type": "text/markdown",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )

    response = await client.get(f"/resource/{entity.permalink}")
    assert response.status_code == 404
    assert "File not found" in response.json()["detail"]
