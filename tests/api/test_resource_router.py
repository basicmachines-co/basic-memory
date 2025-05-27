"""Tests for resource router endpoints."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from basic_memory.schemas import EntityResponse


@pytest.mark.asyncio
async def test_get_resource_content(client, project_config, entity_repository, project_url):
    """Test getting content by permalink."""
    # Create a test file
    content = "# Test Content\n\nThis is a test file."
    test_file = Path(project_config.home) / "test" / "test.md"
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
    response = await client.get(f"{project_url}/resource/{entity.permalink}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.text == content


@pytest.mark.asyncio
async def test_get_resource_pagination(client, project_config, entity_repository, project_url):
    """Test getting content by permalink with pagination."""
    # Create a test file
    content = "# Test Content\n\nThis is a test file."
    test_file = Path(project_config.home) / "test" / "test.md"
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
    response = await client.get(
        f"{project_url}/resource/{entity.permalink}", params={"page": 1, "page_size": 1}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.text == content


@pytest.mark.asyncio
async def test_get_resource_by_title(client, project_config, entity_repository, project_url):
    """Test getting content by permalink."""
    # Create a test file
    content = "# Test Content\n\nThis is a test file."
    test_file = Path(project_config.home) / "test" / "test.md"
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
    response = await client.get(f"{project_url}/resource/{entity.title}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_resource_missing_entity(client, project_url):
    """Test 404 when entity doesn't exist."""
    response = await client.get(f"{project_url}/resource/does/not/exist")
    assert response.status_code == 404
    assert "Resource not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_resource_missing_file(client, project_config, entity_repository, project_url):
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

    response = await client.get(f"{project_url}/resource/{entity.permalink}")
    assert response.status_code == 404
    assert "File not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_resource_observation(client, project_config, entity_repository, project_url):
    """Test getting content by observation permalink."""
    # Create entity
    content = "# Test Content\n\n- [note] an observation."
    data = {
        "title": "Test Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content}",
    }
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    entity_response = response.json()
    entity = EntityResponse(**entity_response)

    assert len(entity.observations) == 1
    observation = entity.observations[0]

    # Test getting the content via the observation
    response = await client.get(f"{project_url}/resource/{observation.permalink}")
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
async def test_get_resource_entities(client, project_config, entity_repository, project_url):
    """Test getting content by permalink match."""
    # Create entity
    content1 = "# Test Content\n"
    data = {
        "title": "Test Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content1}",
    }
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    entity_response = response.json()
    entity1 = EntityResponse(**entity_response)

    content2 = "# Related Content\n- links to [[Test Entity]]"
    data = {
        "title": "Related Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content2}",
    }
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    entity_response = response.json()
    entity2 = EntityResponse(**entity_response)

    assert len(entity2.relations) == 1

    # Test getting the content via the relation
    response = await client.get(f"{project_url}/resource/test/*")
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
async def test_get_resource_entities_pagination(
    client, project_config, entity_repository, project_url
):
    """Test getting content by permalink match."""
    # Create entity
    content1 = "# Test Content\n"
    data = {
        "title": "Test Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content1}",
    }
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    entity_response = response.json()
    entity1 = EntityResponse(**entity_response)
    assert entity1

    content2 = "# Related Content\n- links to [[Test Entity]]"
    data = {
        "title": "Related Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content2}",
    }
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    entity_response = response.json()
    entity2 = EntityResponse(**entity_response)

    assert len(entity2.relations) == 1

    # Test getting second result
    response = await client.get(
        f"{project_url}/resource/test/*", params={"page": 2, "page_size": 1}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert (
        """
---
title: Related Entity
type: test
permalink: test/related-entity
---

# Related Content
- links to [[Test Entity]]
""".strip()
        in response.text
    )


@pytest.mark.asyncio
async def test_get_resource_relation(client, project_config, entity_repository, project_url):
    """Test getting content by relation permalink."""
    # Create entity
    content1 = "# Test Content\n"
    data = {
        "title": "Test Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content1}",
    }
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    entity_response = response.json()
    entity1 = EntityResponse(**entity_response)

    content2 = "# Related Content\n- links to [[Test Entity]]"
    data = {
        "title": "Related Entity",
        "folder": "test",
        "entity_type": "test",
        "content": f"{content2}",
    }
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    entity_response = response.json()
    entity2 = EntityResponse(**entity_response)

    assert len(entity2.relations) == 1
    relation = entity2.relations[0]

    # Test getting the content via the relation
    response = await client.get(f"{project_url}/resource/{relation.permalink}")
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
async def test_put_resource_new_file(
    client, project_config, entity_repository, search_repository, project_url
):
    """Test creating a new file via PUT."""
    # Test data
    file_path = "visualizations/test.canvas"
    canvas_data = {
        "nodes": [
            {
                "id": "node1",
                "type": "text",
                "text": "Test node content",
                "x": 100,
                "y": 200,
                "width": 400,
                "height": 300,
            }
        ],
        "edges": [],
    }

    # Make sure the file doesn't exist yet
    full_path = Path(project_config.home) / file_path
    if full_path.exists():
        full_path.unlink()

    # Execute PUT request
    response = await client.put(
        f"{project_url}/resource/{file_path}", json=json.dumps(canvas_data, indent=2)
    )

    # Verify response
    assert response.status_code == 201
    response_data = response.json()
    assert response_data["file_path"] == file_path
    assert "checksum" in response_data
    assert "size" in response_data

    # Verify file was created
    full_path = Path(project_config.home) / file_path
    assert full_path.exists()

    # Verify file content
    file_content = full_path.read_text(encoding="utf-8")
    assert json.loads(file_content) == canvas_data

    # Verify entity was created in DB
    entity = await entity_repository.get_by_file_path(file_path)
    assert entity is not None
    assert entity.entity_type == "canvas"
    assert entity.content_type == "application/json"

    # Verify entity was indexed for search
    search_results = await search_repository.search(title="test.canvas")
    assert len(search_results) > 0


@pytest.mark.asyncio
async def test_put_resource_update_existing(client, project_config, entity_repository, project_url):
    """Test updating an existing file via PUT."""
    # Create an initial file and entity
    file_path = "visualizations/update-test.canvas"
    full_path = Path(project_config.home) / file_path
    full_path.parent.mkdir(parents=True, exist_ok=True)

    initial_data = {
        "nodes": [
            {
                "id": "initial",
                "type": "text",
                "text": "Initial content",
                "x": 0,
                "y": 0,
                "width": 200,
                "height": 100,
            }
        ],
        "edges": [],
    }
    full_path.write_text(json.dumps(initial_data))

    # Create the initial entity
    initial_entity = await entity_repository.create(
        {
            "title": "update-test.canvas",
            "entity_type": "canvas",
            "file_path": file_path,
            "content_type": "application/json",
            "checksum": "initial123",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    )

    # New data for update
    updated_data = {
        "nodes": [
            {
                "id": "updated",
                "type": "text",
                "text": "Updated content",
                "x": 100,
                "y": 100,
                "width": 300,
                "height": 200,
            }
        ],
        "edges": [],
    }

    # Execute PUT request to update
    response = await client.put(
        f"{project_url}/resource/{file_path}", json=json.dumps(updated_data, indent=2)
    )

    # Verify response
    assert response.status_code == 200

    # Verify file was updated
    updated_content = full_path.read_text(encoding="utf-8")
    assert json.loads(updated_content) == updated_data

    # Verify entity was updated
    updated_entity = await entity_repository.get_by_file_path(file_path)
    assert updated_entity.id == initial_entity.id  # Same entity, updated
    assert updated_entity.checksum != initial_entity.checksum  # Checksum changed
