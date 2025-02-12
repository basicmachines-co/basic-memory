"""Tests for knowledge graph API routes."""

from urllib.parse import quote

import pytest
from httpx import AsyncClient

from basic_memory.schemas import (
    Entity,
    EntityResponse,
)
from basic_memory.schemas.search import SearchItemType, SearchResponse


@pytest.mark.asyncio
async def test_create_entity(client: AsyncClient, file_service):
    """Should create entity successfully."""

    data = {
        "title": "TestEntity",
        "folder": "test",
        "entity_type": "test",
        "content": "TestContent",
    }
    # Create an entity
    response = await client.post("/knowledge/entities", json=data)
    # Verify creation
    assert response.status_code == 200
    entity = EntityResponse.model_validate(response.json())

    assert entity.permalink == "test/test-entity"
    assert entity.file_path == "test/TestEntity.md"
    assert entity.entity_type == data["entity_type"]
    assert entity.content_type == "text/markdown"

    # Verify file has new content but preserved metadata
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)

    assert data["content"] in file_content


@pytest.mark.asyncio
async def test_create_entity_observations_relations(client: AsyncClient, file_service):
    """Should create entity successfully."""

    data = {
        "title": "TestEntity",
        "folder": "test",
        "content": """
# TestContent

## Observations
- [note] This is notable #tag1 (testing)
- related to [[SomeOtherThing]]
""",
    }
    # Create an entity
    response = await client.post("/knowledge/entities", json=data)
    # Verify creation
    assert response.status_code == 200
    entity = EntityResponse.model_validate(response.json())

    assert entity.permalink == "test/test-entity"
    assert entity.file_path == "test/TestEntity.md"
    assert entity.entity_type == "note"
    assert entity.content_type == "text/markdown"

    assert len(entity.observations) == 1
    assert entity.observations[0].category == "note"
    assert entity.observations[0].content == "This is notable #tag1"
    assert entity.observations[0].tags == ["tag1"]
    assert entity.observations[0].context == "testing"

    assert len(entity.relations) == 1
    assert entity.relations[0].relation_type == "related to"
    assert entity.relations[0].from_id == "test/test-entity"
    assert entity.relations[0].to_id is None

    # TODO Relation.to_id should be name from link

    # Verify file has new content but preserved metadata
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)

    assert data["content"].strip() in file_content


@pytest.mark.asyncio
async def test_get_entity(client: AsyncClient):
    """Should retrieve an entity by path ID."""
    # First create an entity
    data = {"title": "TestEntity", "folder": "test", "entity_type": "test"}
    response = await client.post("/knowledge/entities", json=data)
    assert response.status_code == 200
    data = response.json()

    # Now get it by path
    permalink = data["permalink"]
    response = await client.get(f"/knowledge/entities/{permalink}")

    # Verify retrieval
    assert response.status_code == 200
    entity = response.json()
    assert entity["file_path"] == "test/TestEntity.md"
    assert entity["entity_type"] == "test"
    assert entity["permalink"] == "test/test-entity"


@pytest.mark.asyncio
async def test_get_entities(client: AsyncClient):
    """Should open multiple entities by path IDs."""
    # Create a few entities with different names
    await client.post(
        "/knowledge/entities", json={"title": "AlphaTest", "folder": "", "entity_type": "test"}
    )
    await client.post(
        "/knowledge/entities", json={"title": "BetaTest", "folder": "", "entity_type": "test"}
    )

    # Open nodes by path IDs
    response = await client.get(
        "/knowledge/entities?permalink=alpha-test&permalink=beta-test",
    )

    # Verify results
    assert response.status_code == 200
    data = response.json()
    assert len(data["entities"]) == 2

    entity_0 = data["entities"][0]
    assert entity_0["title"] == "AlphaTest"
    assert entity_0["file_path"] == "AlphaTest.md"
    assert entity_0["entity_type"] == "test"
    assert entity_0["permalink"] == "alpha-test"

    entity_1 = data["entities"][1]
    assert entity_1["title"] == "BetaTest"
    assert entity_1["file_path"] == "BetaTest.md"
    assert entity_1["entity_type"] == "test"
    assert entity_1["permalink"] == "beta-test"


@pytest.mark.asyncio
async def test_delete_entity(client: AsyncClient):
    """Test DELETE /knowledge/entities with path ID."""
    # Create test entity
    entity_data = {"file_path": "TestEntity", "entity_type": "test"}
    await client.post("/knowledge/entities", json=entity_data)

    # Test deletion
    response = await client.post("/knowledge/entities/delete", json={"permalinks": ["test-entity"]})
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # Verify entity is gone
    permalink = quote("test/TestEntity")
    response = await client.get(f"/knowledge/entities/{permalink}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_single_entity(client: AsyncClient):
    """Test DELETE /knowledge/entities with path ID."""
    # Create test entity
    entity_data = {"title": "TestEntity", "folder": "", "entity_type": "test"}
    await client.post("/knowledge/entities", json=entity_data)

    # Test deletion
    response = await client.delete("/knowledge/entities/test-entity")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # Verify entity is gone
    permalink = quote("test/TestEntity")
    response = await client.get(f"/knowledge/entities/{permalink}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_single_entity_by_title(client: AsyncClient):
    """Test DELETE /knowledge/entities with path ID."""
    # Create test entity
    entity_data = {"title": "TestEntity", "folder": "", "entity_type": "test"}
    await client.post("/knowledge/entities", json=entity_data)

    # Test deletion
    response = await client.delete("/knowledge/entities/TestEntity")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # Verify entity is gone
    permalink = quote("test/TestEntity")
    response = await client.get(f"/knowledge/entities/{permalink}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_single_entity_not_found(client: AsyncClient):
    """Test DELETE /knowledge/entities with path ID."""

    # Test deletion
    response = await client.delete("/knowledge/entities/test-not-found")
    assert response.status_code == 200
    assert response.json() == {"deleted": False}


@pytest.mark.asyncio
async def test_delete_entity_bulk(client: AsyncClient):
    """Test bulk entity deletion using path IDs."""
    # Create test entities
    await client.post("/knowledge/entities", json={"file_path": "Entity1", "entity_type": "test"})
    await client.post("/knowledge/entities", json={"file_path": "Entity2", "entity_type": "test"})

    # Test deletion
    response = await client.post(
        "/knowledge/entities/delete", json={"permalinks": ["Entity1", "Entity2"]}
    )
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # Verify entities are gone
    for name in ["Entity1", "Entity2"]:
        permalink = quote(f"{name}")
        response = await client.get(f"/knowledge/entities/{permalink}")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_entity(client: AsyncClient):
    """Test deleting a nonexistent entity by path ID."""
    response = await client.post(
        "/knowledge/entities/delete", json={"permalinks": ["non_existent"]}
    )
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


@pytest.mark.asyncio
async def test_entity_indexing(client: AsyncClient):
    """Test entity creation includes search indexing."""
    # Create entity
    response = await client.post(
        "/knowledge/entities",
        json={
            "title": "SearchTest",
            "folder": "",
            "entity_type": "test",
            "observations": ["Unique searchable observation"],
        },
    )
    assert response.status_code == 200

    # Verify it's searchable
    search_response = await client.post(
        "/search/", json={"text": "search", "types": [SearchItemType.ENTITY.value]}
    )
    assert search_response.status_code == 200
    search_result = SearchResponse.model_validate(search_response.json())
    assert len(search_result.results) == 1
    assert search_result.results[0].permalink == "search-test"
    assert search_result.results[0].type == SearchItemType.ENTITY.value


@pytest.mark.asyncio
async def test_entity_delete_indexing(client: AsyncClient):
    """Test deleted entities are removed from search index."""

    # Create entity
    response = await client.post(
        "/knowledge/entities",
        json={
            "title": "DeleteTest",
            "folder": "",
            "entity_type": "test",
            "observations": ["Searchable observation that should be removed"],
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Verify it's initially searchable
    search_response = await client.post(
        "/search/", json={"text": "delete", "types": [SearchItemType.ENTITY.value]}
    )
    search_result = SearchResponse.model_validate(search_response.json())
    assert len(search_result.results) == 1

    # Delete entity
    delete_response = await client.post(
        "/knowledge/entities/delete", json={"permalinks": [entity["permalink"]]}
    )
    assert delete_response.status_code == 200

    # Verify it's no longer searchable
    search_response = await client.post(
        "/search/", json={"text": "delete", "types": [SearchItemType.ENTITY.value]}
    )
    search_result = SearchResponse.model_validate(search_response.json())
    assert len(search_result.results) == 0


@pytest.mark.asyncio
async def test_update_entity_basic(client: AsyncClient):
    """Test basic entity field updates."""
    # Create initial entity
    response = await client.post(
        "/knowledge/entities",
        json={
            "title": "test",
            "folder": "",
            "entity_type": "test",
            "content": "Initial summary",
            "entity_metadata": {"status": "draft"},
        },
    )
    entity_response = response.json()

    # Update fields
    entity = Entity(**entity_response, folder="")
    entity.entity_metadata["status"] = "final"
    entity.content = "Updated summary"

    response = await client.put(f"/knowledge/entities/{entity.permalink}", json=entity.model_dump())
    assert response.status_code == 200
    updated = response.json()

    # Verify updates
    assert updated["entity_metadata"]["status"] == "final"  # Preserved

    response = await client.get(f"/resource/{updated['permalink']}?content=true")

    # raw markdown content
    fetched = response.text
    assert "Updated summary" in fetched


@pytest.mark.asyncio
async def test_update_entity_content(client: AsyncClient):
    """Test updating content for different entity types."""
    # Create a note entity
    response = await client.post(
        "/knowledge/entities",
        json={"title": "test-note", "folder": "", "entity_type": "note", "summary": "Test note"},
    )
    note = response.json()

    # Update fields
    entity = Entity(**note, folder="")
    entity.content = "# Updated Note\n\nNew content."

    response = await client.put(
        f"/knowledge/entities/{note['permalink']}", json=entity.model_dump()
    )
    assert response.status_code == 200
    updated = response.json()

    # Verify through get request to check file
    response = await client.get(f"/resource/{updated['permalink']}?content=true")

    # raw markdown content
    fetched = response.text
    assert "# Updated Note" in fetched
    assert "New content" in fetched


@pytest.mark.asyncio
async def test_update_entity_type_conversion(client: AsyncClient):
    """Test converting between note and knowledge types."""
    # Create a note
    note_data = {
        "title": "test-note",
        "folder": "",
        "entity_type": "note",
        "summary": "Test note",
        "content": "# Test Note\n\nInitial content.",
    }
    response = await client.post("/knowledge/entities", json=note_data)
    note = response.json()

    # Update fields
    entity = Entity(**note, folder="")
    entity.entity_type = "test"

    response = await client.put(
        f"/knowledge/entities/{note['permalink']}", json=entity.model_dump()
    )
    assert response.status_code == 200
    updated = response.json()

    # Verify conversion
    assert updated["entity_type"] == "test"

    # Get latest to verify file format
    response = await client.get(f"/knowledge/entities/{updated['permalink']}")
    knowledge = response.json()
    assert knowledge.get("content") is None


@pytest.mark.asyncio
async def test_update_entity_metadata(client: AsyncClient):
    """Test updating entity metadata."""
    # Create entity
    data = {
        "title": "test",
        "folder": "",
        "entity_type": "test",
        "entity_metadata": {"status": "draft"},
    }
    response = await client.post("/knowledge/entities", json=data)
    entity_response = response.json()

    # Update fields
    entity = Entity(**entity_response, folder="")
    entity.entity_metadata["status"] = "final"
    entity.entity_metadata["reviewed"] = True

    # Update metadata
    response = await client.put(f"/knowledge/entities/{entity.permalink}", json=entity.model_dump())
    assert response.status_code == 200
    updated = response.json()

    # Verify metadata was merged, not replaced
    assert updated["entity_metadata"]["status"] == "final"
    assert updated["entity_metadata"]["reviewed"] in (True, "True")


@pytest.mark.asyncio
async def test_update_entity_not_found_does_create(client: AsyncClient):
    """Test updating non-existent entity does a create"""

    data = {
        "title": "nonexistent",
        "folder": "",
        "entity_type": "test",
        "observations": ["First observation", "Second observation"],
    }
    entity = Entity(**data)
    response = await client.put("/knowledge/entities/nonexistent", json=entity.model_dump())
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_update_entity_incorrect_permalink(client: AsyncClient):
    """Test updating non-existent entity does a create"""

    data = {
        "title": "Test Entity",
        "folder": "",
        "entity_type": "test",
        "observations": ["First observation", "Second observation"],
    }
    entity = Entity(**data)
    response = await client.put("/knowledge/entities/nonexistent", json=entity.model_dump())
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_entity_search_index(client: AsyncClient):
    """Test search index is updated after entity changes."""
    # Create entity
    data = {
        "title": "test",
        "folder": "",
        "entity_type": "test",
        "content": "Initial searchable content",
    }
    response = await client.post("/knowledge/entities", json=data)
    entity_response = response.json()

    # Update fields
    entity = Entity(**entity_response, folder="")
    entity.content = "Updated with unique sphinx marker"

    response = await client.put(f"/knowledge/entities/{entity.permalink}", json=entity.model_dump())
    assert response.status_code == 200

    # Search should find new content
    search_response = await client.post(
        "/search/", json={"text": "sphinx marker", "types": [SearchItemType.ENTITY.value]}
    )
    results = search_response.json()["results"]
    assert len(results) == 1
    assert results[0]["permalink"] == entity.permalink
