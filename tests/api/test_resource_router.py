"""Tests for resource router endpoints."""

from datetime import datetime, timezone

import pytest
from pathlib import Path

from basic_memory.schemas import EntityResponse


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
    assert "Resource not found" in response.json()["detail"]


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


@pytest.mark.asyncio
async def test_get_resource_observation(client, test_config, entity_repository):
    """Test getting content by observation permalink."""
    # Create entity
    content = "# Test Content\n\n- [note] an observation."
    data = {
        "title": "Test Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content}",
    }
    response = await client.post("/knowledge/entities", json=data)
    entity_response = response.json()
    entity = EntityResponse(**entity_response)

    assert len(entity.observations) == 1
    observation = entity.observations[0]

    # Test getting the content via the observation
    response = await client.get(f"/resource/{observation.permalink}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert (
        """
---
title: Test Entity
type: test
permalink: test/test-entity
---

# Test Content

- [note] an observation.
    """.strip()
        in response.text
    )


@pytest.mark.asyncio
async def test_get_resource_entities(client, test_config, entity_repository):
    """Test getting content by permalink match."""
    # Create entity
    content1 = "# Test Content\n"
    data = {
        "title": "Test Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content1}",
    }
    response = await client.post("/knowledge/entities", json=data)
    entity_response = response.json()
    entity1 = EntityResponse(**entity_response)

    content2 = "# Related Content\n- links to [[Test Entity]]"
    data = {
        "title": "Related Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content2}",
    }
    response = await client.post("/knowledge/entities", json=data)
    entity_response = response.json()
    entity2 = EntityResponse(**entity_response)

    assert len(entity2.relations) == 1

    # Test getting the content via the relation
    response = await client.get("/resource/test/*")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert (
        f"""
--- memory://test/test-entity {entity1.updated_at.isoformat()} {entity1.checksum[:8]}

# Test Content

--- memory://test/related-entity {entity2.updated_at.isoformat()} {entity2.checksum[:8]}

# Related Content
- links to [[Test Entity]]

    """.strip()
        in response.text
    )


@pytest.mark.asyncio
async def test_get_resource_relation(client, test_config, entity_repository):
    """Test getting content by relation permalink."""
    # Create entity
    content1 = "# Test Content\n"
    data = {
        "title": "Test Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content1}",
    }
    response = await client.post("/knowledge/entities", json=data)
    entity_response = response.json()
    entity1 = EntityResponse(**entity_response)

    content2 = "# Related Content\n- links to [[Test Entity]]"
    data = {
        "title": "Related Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content2}",
    }
    response = await client.post("/knowledge/entities", json=data)
    entity_response = response.json()
    entity2 = EntityResponse(**entity_response)

    assert len(entity2.relations) == 1
    relation = entity2.relations[0]

    # Test getting the content via the relation
    response = await client.get(f"/resource/{relation.permalink}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert (
        f"""
--- memory://test/test-entity {entity1.updated_at.isoformat()} {entity1.checksum[:8]}

# Test Content

--- memory://test/related-entity {entity2.updated_at.isoformat()} {entity2.checksum[:8]}

# Related Content
- links to [[Test Entity]]
    
    """.strip()
        in response.text
    )
