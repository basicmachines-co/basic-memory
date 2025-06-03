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
async def test_create_entity(client: AsyncClient, file_service, project_url):
    """Should create entity successfully."""

    data = {
        "title": "TestEntity",
        "folder": "test",
        "entity_type": "test",
        "content": "TestContent",
        "project": "Test Project Context",
    }
    # Create an entity
    print(f"Requesting with data: {data}")
    # Use the permalink version of the project name in the path
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    # Print response for debugging
    print(f"Response status: {response.status_code}")
    print(f"Response content: {response.text}")
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
async def test_create_entity_observations_relations(client: AsyncClient, file_service, project_url):
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
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
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

    # Verify file has new content but preserved metadata
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)

    assert data["content"].strip() in file_content


@pytest.mark.asyncio
async def test_get_entity_by_permalink(client: AsyncClient, project_url):
    """Should retrieve an entity by path ID."""
    # First create an entity
    data = {"title": "TestEntity", "folder": "test", "entity_type": "test"}
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    assert response.status_code == 200
    data = response.json()

    # Now get it by permalink
    permalink = data["permalink"]
    response = await client.get(f"{project_url}/knowledge/entities/{permalink}")

    # Verify retrieval
    assert response.status_code == 200
    entity = response.json()
    assert entity["file_path"] == "test/TestEntity.md"
    assert entity["entity_type"] == "test"
    assert entity["permalink"] == "test/test-entity"


@pytest.mark.asyncio
async def test_get_entity_by_file_path(client: AsyncClient, project_url):
    """Should retrieve an entity by path ID."""
    # First create an entity
    data = {"title": "TestEntity", "folder": "test", "entity_type": "test"}
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    assert response.status_code == 200
    data = response.json()

    # Now get it by path
    file_path = data["file_path"]
    response = await client.get(f"{project_url}/knowledge/entities/{file_path}")

    # Verify retrieval
    assert response.status_code == 200
    entity = response.json()
    assert entity["file_path"] == "test/TestEntity.md"
    assert entity["entity_type"] == "test"
    assert entity["permalink"] == "test/test-entity"


@pytest.mark.asyncio
async def test_get_entities(client: AsyncClient, project_url):
    """Should open multiple entities by path IDs."""
    # Create a few entities with different names
    await client.post(
        f"{project_url}/knowledge/entities",
        json={"title": "AlphaTest", "folder": "", "entity_type": "test"},
    )
    await client.post(
        f"{project_url}/knowledge/entities",
        json={"title": "BetaTest", "folder": "", "entity_type": "test"},
    )

    # Open nodes by path IDs
    response = await client.get(
        f"{project_url}/knowledge/entities?permalink=alpha-test&permalink=beta-test",
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
async def test_delete_entity(client: AsyncClient, project_url):
    """Test DELETE /knowledge/entities with path ID."""
    # Create test entity
    entity_data = {"file_path": "TestEntity", "entity_type": "test"}
    await client.post(f"{project_url}/knowledge/entities", json=entity_data)

    # Test deletion
    response = await client.post(
        f"{project_url}/knowledge/entities/delete", json={"permalinks": ["test-entity"]}
    )
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # Verify entity is gone
    permalink = quote("test/TestEntity")
    response = await client.get(f"{project_url}/knowledge/entities/{permalink}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_single_entity(client: AsyncClient, project_url):
    """Test DELETE /knowledge/entities with path ID."""
    # Create test entity
    entity_data = {"title": "TestEntity", "folder": "", "entity_type": "test"}
    await client.post(f"{project_url}/knowledge/entities", json=entity_data)

    # Test deletion
    response = await client.delete(f"{project_url}/knowledge/entities/test-entity")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # Verify entity is gone
    permalink = quote("test/TestEntity")
    response = await client.get(f"{project_url}/knowledge/entities/{permalink}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_single_entity_by_title(client: AsyncClient, project_url):
    """Test DELETE /knowledge/entities with file path."""
    # Create test entity
    entity_data = {"title": "TestEntity", "folder": "", "entity_type": "test"}
    response = await client.post(f"{project_url}/knowledge/entities", json=entity_data)
    assert response.status_code == 200
    data = response.json()

    # Test deletion
    response = await client.delete(f"{project_url}/knowledge/entities/TestEntity")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # Verify entity is gone
    file_path = quote(data["file_path"])
    response = await client.get(f"{project_url}/knowledge/entities/{file_path}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_single_entity_not_found(client: AsyncClient, project_url):
    """Test DELETE /knowledge/entities with path ID."""

    # Test deletion
    response = await client.delete(f"{project_url}/knowledge/entities/test-not-found")
    assert response.status_code == 200
    assert response.json() == {"deleted": False}


@pytest.mark.asyncio
async def test_delete_entity_bulk(client: AsyncClient, project_url):
    """Test bulk entity deletion using path IDs."""
    # Create test entities
    await client.post(
        f"{project_url}/knowledge/entities", json={"file_path": "Entity1", "entity_type": "test"}
    )
    await client.post(
        f"{project_url}/knowledge/entities", json={"file_path": "Entity2", "entity_type": "test"}
    )

    # Test deletion
    response = await client.post(
        f"{project_url}/knowledge/entities/delete", json={"permalinks": ["Entity1", "Entity2"]}
    )
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # Verify entities are gone
    for name in ["Entity1", "Entity2"]:
        permalink = quote(f"{name}")
        response = await client.get(f"{project_url}/knowledge/entities/{permalink}")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_entity(client: AsyncClient, project_url):
    """Test deleting a nonexistent entity by path ID."""
    response = await client.post(
        f"{project_url}/knowledge/entities/delete", json={"permalinks": ["non_existent"]}
    )
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


@pytest.mark.asyncio
async def test_entity_indexing(client: AsyncClient, project_url):
    """Test entity creation includes search indexing."""
    # Create entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
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
        f"{project_url}/search/",
        json={"text": "search", "entity_types": [SearchItemType.ENTITY.value]},
    )
    assert search_response.status_code == 200
    search_result = SearchResponse.model_validate(search_response.json())
    assert len(search_result.results) == 1
    assert search_result.results[0].permalink == "search-test"
    assert search_result.results[0].type == SearchItemType.ENTITY.value


@pytest.mark.asyncio
async def test_entity_delete_indexing(client: AsyncClient, project_url):
    """Test deleted entities are removed from search index."""

    # Create entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
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
        f"{project_url}/search/",
        json={"text": "delete", "entity_types": [SearchItemType.ENTITY.value]},
    )
    search_result = SearchResponse.model_validate(search_response.json())
    assert len(search_result.results) == 1

    # Delete entity
    delete_response = await client.post(
        f"{project_url}/knowledge/entities/delete", json={"permalinks": [entity["permalink"]]}
    )
    assert delete_response.status_code == 200

    # Verify it's no longer searchable
    search_response = await client.post(
        f"{project_url}/search/", json={"text": "delete", "types": [SearchItemType.ENTITY.value]}
    )
    search_result = SearchResponse.model_validate(search_response.json())
    assert len(search_result.results) == 0


@pytest.mark.asyncio
async def test_update_entity_basic(client: AsyncClient, project_url):
    """Test basic entity field updates."""
    # Create initial entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
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

    response = await client.put(
        f"{project_url}/knowledge/entities/{entity.permalink}", json=entity.model_dump()
    )
    assert response.status_code == 200
    updated = response.json()

    # Verify updates
    assert updated["entity_metadata"]["status"] == "final"  # Preserved

    response = await client.get(f"{project_url}/resource/{updated['permalink']}?content=true")

    # raw markdown content
    fetched = response.text
    assert "Updated summary" in fetched


@pytest.mark.asyncio
async def test_update_entity_content(client: AsyncClient, project_url):
    """Test updating content for different entity types."""
    # Create a note entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={"title": "test-note", "folder": "", "entity_type": "note", "summary": "Test note"},
    )
    note = response.json()

    # Update fields
    entity = Entity(**note, folder="")
    entity.content = "# Updated Note\n\nNew content."

    response = await client.put(
        f"{project_url}/knowledge/entities/{note['permalink']}", json=entity.model_dump()
    )
    assert response.status_code == 200
    updated = response.json()

    # Verify through get request to check file
    response = await client.get(f"{project_url}/resource/{updated['permalink']}?content=true")

    # raw markdown content
    fetched = response.text
    assert "# Updated Note" in fetched
    assert "New content" in fetched


@pytest.mark.asyncio
async def test_update_entity_type_conversion(client: AsyncClient, project_url):
    """Test converting between note and knowledge types."""
    # Create a note
    note_data = {
        "title": "test-note",
        "folder": "",
        "entity_type": "note",
        "summary": "Test note",
        "content": "# Test Note\n\nInitial content.",
    }
    response = await client.post(f"{project_url}/knowledge/entities", json=note_data)
    note = response.json()

    # Update fields
    entity = Entity(**note, folder="")
    entity.entity_type = "test"

    response = await client.put(
        f"{project_url}/knowledge/entities/{note['permalink']}", json=entity.model_dump()
    )
    assert response.status_code == 200
    updated = response.json()

    # Verify conversion
    assert updated["entity_type"] == "test"

    # Get latest to verify file format
    response = await client.get(f"{project_url}/knowledge/entities/{updated['permalink']}")
    knowledge = response.json()
    assert knowledge.get("content") is None


@pytest.mark.asyncio
async def test_update_entity_metadata(client: AsyncClient, project_url):
    """Test updating entity metadata."""
    # Create entity
    data = {
        "title": "test",
        "folder": "",
        "entity_type": "test",
        "entity_metadata": {"status": "draft"},
    }
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    entity_response = response.json()

    # Update fields
    entity = Entity(**entity_response, folder="")
    entity.entity_metadata["status"] = "final"
    entity.entity_metadata["reviewed"] = True

    # Update metadata
    response = await client.put(
        f"{project_url}/knowledge/entities/{entity.permalink}", json=entity.model_dump()
    )
    assert response.status_code == 200
    updated = response.json()

    # Verify metadata was merged, not replaced
    assert updated["entity_metadata"]["status"] == "final"
    assert updated["entity_metadata"]["reviewed"] in (True, "True")


@pytest.mark.asyncio
async def test_update_entity_not_found_does_create(client: AsyncClient, project_url):
    """Test updating non-existent entity does a create"""

    data = {
        "title": "nonexistent",
        "folder": "",
        "entity_type": "test",
        "observations": ["First observation", "Second observation"],
    }
    entity = Entity(**data)
    response = await client.put(
        f"{project_url}/knowledge/entities/nonexistent", json=entity.model_dump()
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_update_entity_incorrect_permalink(client: AsyncClient, project_url):
    """Test updating non-existent entity does a create"""

    data = {
        "title": "Test Entity",
        "folder": "",
        "entity_type": "test",
        "observations": ["First observation", "Second observation"],
    }
    entity = Entity(**data)
    response = await client.put(
        f"{project_url}/knowledge/entities/nonexistent", json=entity.model_dump()
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_entity_search_index(client: AsyncClient, project_url):
    """Test search index is updated after entity changes."""
    # Create entity
    data = {
        "title": "test",
        "folder": "",
        "entity_type": "test",
        "content": "Initial searchable content",
    }
    response = await client.post(f"{project_url}/knowledge/entities", json=data)
    entity_response = response.json()

    # Update fields
    entity = Entity(**entity_response, folder="")
    entity.content = "Updated with unique sphinx marker"

    response = await client.put(
        f"{project_url}/knowledge/entities/{entity.permalink}", json=entity.model_dump()
    )
    assert response.status_code == 200

    # Search should find new content
    search_response = await client.post(
        f"{project_url}/search/",
        json={"text": "sphinx marker", "entity_types": [SearchItemType.ENTITY.value]},
    )
    results = search_response.json()["results"]
    assert len(results) == 1
    assert results[0]["permalink"] == entity.permalink


# PATCH edit entity endpoint tests


@pytest.mark.asyncio
async def test_edit_entity_append(client: AsyncClient, project_url):
    """Test appending content to an entity via PATCH endpoint."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Test Note",
            "folder": "test",
            "entity_type": "note",
            "content": "Original content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Edit entity with append operation
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={"operation": "append", "content": "Appended content"},
    )
    if response.status_code != 200:
        print(f"PATCH failed with status {response.status_code}")
        print(f"Response content: {response.text}")
    assert response.status_code == 200
    updated = response.json()

    # Verify content was appended by reading the file
    response = await client.get(f"{project_url}/resource/{updated['permalink']}?content=true")
    file_content = response.text
    assert "Original content" in file_content
    assert "Appended content" in file_content
    assert file_content.index("Original content") < file_content.index("Appended content")


@pytest.mark.asyncio
async def test_edit_entity_prepend(client: AsyncClient, project_url):
    """Test prepending content to an entity via PATCH endpoint."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Test Note",
            "folder": "test",
            "entity_type": "note",
            "content": "Original content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Edit entity with prepend operation
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={"operation": "prepend", "content": "Prepended content"},
    )
    if response.status_code != 200:
        print(f"PATCH prepend failed with status {response.status_code}")
        print(f"Response content: {response.text}")
    assert response.status_code == 200
    updated = response.json()

    # Verify the entire file content structure
    response = await client.get(f"{project_url}/resource/{updated['permalink']}?content=true")
    file_content = response.text

    # Expected content with frontmatter preserved and content prepended to body
    expected_content = """---
title: Test Note
type: note
permalink: test/test-note
---

Prepended content
Original content"""

    assert file_content.strip() == expected_content.strip()


@pytest.mark.asyncio
async def test_edit_entity_find_replace(client: AsyncClient, project_url):
    """Test find and replace operation via PATCH endpoint."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Test Note",
            "folder": "test",
            "entity_type": "note",
            "content": "This is old content that needs updating",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Edit entity with find_replace operation
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={"operation": "find_replace", "content": "new content", "find_text": "old content"},
    )
    assert response.status_code == 200
    updated = response.json()

    # Verify content was replaced
    response = await client.get(f"{project_url}/resource/{updated['permalink']}?content=true")
    file_content = response.text
    assert "old content" not in file_content
    assert "This is new content that needs updating" in file_content


@pytest.mark.asyncio
async def test_edit_entity_find_replace_with_expected_replacements(
    client: AsyncClient, project_url
):
    """Test find and replace with expected_replacements parameter."""
    # Create test entity with repeated text
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Sample Note",
            "folder": "docs",
            "entity_type": "note",
            "content": "The word banana appears here. Another banana word here.",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Edit entity with find_replace operation, expecting 2 replacements
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={
            "operation": "find_replace",
            "content": "apple",
            "find_text": "banana",
            "expected_replacements": 2,
        },
    )
    assert response.status_code == 200
    updated = response.json()

    # Verify both instances were replaced
    response = await client.get(f"{project_url}/resource/{updated['permalink']}?content=true")
    file_content = response.text
    assert "The word apple appears here. Another apple word here." in file_content


@pytest.mark.asyncio
async def test_edit_entity_replace_section(client: AsyncClient, project_url):
    """Test replacing a section via PATCH endpoint."""
    # Create test entity with sections
    content = """# Main Title

## Section 1
Original section 1 content

## Section 2
Original section 2 content"""

    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Sample Note",
            "folder": "docs",
            "entity_type": "note",
            "content": content,
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Edit entity with replace_section operation
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={
            "operation": "replace_section",
            "content": "New section 1 content",
            "section": "## Section 1",
        },
    )
    assert response.status_code == 200
    updated = response.json()

    # Verify section was replaced
    response = await client.get(f"{project_url}/resource/{updated['permalink']}?content=true")
    file_content = response.text
    assert "New section 1 content" in file_content
    assert "Original section 1 content" not in file_content
    assert "Original section 2 content" in file_content  # Other sections preserved


@pytest.mark.asyncio
async def test_edit_entity_not_found(client: AsyncClient, project_url):
    """Test editing a non-existent entity returns 400."""
    response = await client.patch(
        f"{project_url}/knowledge/entities/non-existent",
        json={"operation": "append", "content": "content"},
    )
    assert response.status_code == 400
    assert "Entity not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_edit_entity_invalid_operation(client: AsyncClient, project_url):
    """Test editing with invalid operation returns 400."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Test Note",
            "folder": "test",
            "entity_type": "note",
            "content": "Original content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Try invalid operation
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={"operation": "invalid_operation", "content": "content"},
    )
    assert response.status_code == 422
    assert "invalid_operation" in response.json()["detail"][0]["input"]


@pytest.mark.asyncio
async def test_edit_entity_find_replace_missing_find_text(client: AsyncClient, project_url):
    """Test find_replace without find_text returns 400."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Test Note",
            "folder": "test",
            "entity_type": "note",
            "content": "Original content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Try find_replace without find_text
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={"operation": "find_replace", "content": "new content"},
    )
    assert response.status_code == 400
    assert "find_text is required" in response.json()["detail"]


@pytest.mark.asyncio
async def test_edit_entity_replace_section_missing_section(client: AsyncClient, project_url):
    """Test replace_section without section parameter returns 400."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Test Note",
            "folder": "test",
            "entity_type": "note",
            "content": "Original content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Try replace_section without section
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={"operation": "replace_section", "content": "new content"},
    )
    assert response.status_code == 400
    assert "section is required" in response.json()["detail"]


@pytest.mark.asyncio
async def test_edit_entity_find_replace_not_found(client: AsyncClient, project_url):
    """Test find_replace when text is not found returns 400."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Test Note",
            "folder": "test",
            "entity_type": "note",
            "content": "This is some content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Try to replace text that doesn't exist
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={"operation": "find_replace", "content": "new content", "find_text": "nonexistent"},
    )
    assert response.status_code == 400
    assert "Text to replace not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_edit_entity_find_replace_wrong_expected_count(client: AsyncClient, project_url):
    """Test find_replace with wrong expected_replacements count returns 400."""
    # Create test entity with repeated text
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Sample Note",
            "folder": "docs",
            "entity_type": "note",
            "content": "The word banana appears here. Another banana word here.",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Try to replace with wrong expected count
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={
            "operation": "find_replace",
            "content": "replacement",
            "find_text": "banana",
            "expected_replacements": 1,  # Wrong - there are actually 2
        },
    )
    assert response.status_code == 400
    assert "Expected 1 occurrences" in response.json()["detail"]
    assert "but found 2" in response.json()["detail"]


@pytest.mark.asyncio
async def test_edit_entity_search_reindex(client: AsyncClient, project_url):
    """Test that edited entities are reindexed for search."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "Search Test",
            "folder": "test",
            "entity_type": "note",
            "content": "Original searchable content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Edit the entity
    response = await client.patch(
        f"{project_url}/knowledge/entities/{entity['permalink']}",
        json={"operation": "append", "content": " with unique zebra marker"},
    )
    assert response.status_code == 200

    # Search should find the new content
    search_response = await client.post(
        f"{project_url}/search/",
        json={"text": "zebra marker", "entity_types": ["entity"]},
    )
    results = search_response.json()["results"]
    assert len(results) == 1
    assert results[0]["permalink"] == entity["permalink"]


# Move entity endpoint tests


@pytest.mark.asyncio
async def test_move_entity_success(client: AsyncClient, project_url):
    """Test successfully moving an entity to a new location."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "TestNote",
            "folder": "source",
            "entity_type": "note",
            "content": "Test content",
        },
    )
    assert response.status_code == 200
    entity = response.json()
    original_permalink = entity["permalink"]

    # Move entity
    move_data = {
        "identifier": original_permalink,
        "destination_path": "target/MovedNote.md",
    }
    response = await client.post(f"{project_url}/knowledge/move", json=move_data)
    assert response.status_code == 200
    response_model = EntityResponse.model_validate(response.json())
    assert response_model.file_path == "target/MovedNote.md"
    
    # Verify original entity no longer exists
    response = await client.get(f"{project_url}/knowledge/entities/{original_permalink}")
    assert response.status_code == 404

    # Verify entity exists at new location
    response = await client.get(f"{project_url}/knowledge/entities/target/moved-note")
    assert response.status_code == 200
    moved_entity = response.json()
    assert moved_entity["file_path"] == "target/MovedNote.md"
    assert moved_entity["permalink"] == "target/moved-note"

    # Verify file content using resource endpoint
    response = await client.get(f"{project_url}/resource/target/moved-note?content=true")
    assert response.status_code == 200
    file_content = response.text
    assert "Test content" in file_content


@pytest.mark.asyncio
async def test_move_entity_with_folder_creation(client: AsyncClient, project_url):
    """Test moving entity creates necessary folders."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "TestNote",
            "folder": "",
            "entity_type": "note",
            "content": "Test content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Move to deeply nested path
    move_data = {
        "identifier": entity["permalink"],
        "destination_path": "deeply/nested/folder/MovedNote.md",
    }
    response = await client.post(f"{project_url}/knowledge/move", json=move_data)
    assert response.status_code == 200

    # Verify entity exists at new location
    response = await client.get(f"{project_url}/knowledge/entities/deeply/nested/folder/moved-note")
    assert response.status_code == 200
    moved_entity = response.json()
    assert moved_entity["file_path"] == "deeply/nested/folder/MovedNote.md"


@pytest.mark.asyncio
async def test_move_entity_with_observations_and_relations(client: AsyncClient, project_url):
    """Test moving entity preserves observations and relations."""
    # Create test entity with complex content
    content = """# Complex Entity

## Observations
- [note] Important observation #tag1
- [feature] Key feature #feature
- relation to [[SomeOtherEntity]]
- depends on [[Dependency]]

Some additional content."""

    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "ComplexEntity",
            "folder": "source",
            "entity_type": "note",
            "content": content,
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Verify original observations and relations
    assert len(entity["observations"]) == 2
    assert len(entity["relations"]) == 2

    # Move entity
    move_data = {
        "identifier": entity["permalink"],
        "destination_path": "target/MovedComplex.md",
    }
    response = await client.post(f"{project_url}/knowledge/move", json=move_data)
    assert response.status_code == 200

    # Verify moved entity preserves data
    response = await client.get(f"{project_url}/knowledge/entities/target/moved-complex")
    assert response.status_code == 200
    moved_entity = response.json()

    # Check observations preserved
    assert len(moved_entity["observations"]) == 2
    obs_categories = {obs["category"] for obs in moved_entity["observations"]}
    assert obs_categories == {"note", "feature"}

    # Check relations preserved
    assert len(moved_entity["relations"]) == 2
    rel_types = {rel["relation_type"] for rel in moved_entity["relations"]}
    assert rel_types == {"relation to", "depends on"}

    # Verify file content preserved
    response = await client.get(f"{project_url}/resource/target/moved-complex?content=true")
    assert response.status_code == 200
    file_content = response.text
    assert "Important observation #tag1" in file_content
    assert "[[SomeOtherEntity]]" in file_content


@pytest.mark.asyncio
async def test_move_entity_search_reindexing(client: AsyncClient, project_url):
    """Test that moved entities are properly reindexed for search."""
    # Create searchable entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "SearchableNote",
            "folder": "source",
            "entity_type": "note",
            "content": "Unique searchable elephant content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Move entity
    move_data = {
        "identifier": entity["permalink"],
        "destination_path": "target/MovedSearchable.md",
    }
    response = await client.post(f"{project_url}/knowledge/move", json=move_data)
    assert response.status_code == 200

    # Search should find entity at new location
    search_response = await client.post(
        f"{project_url}/search/",
        json={"text": "elephant", "entity_types": [SearchItemType.ENTITY.value]},
    )
    results = search_response.json()["results"]
    assert len(results) == 1
    assert results[0]["permalink"] == "target/moved-searchable"


@pytest.mark.asyncio
async def test_move_entity_not_found(client: AsyncClient, project_url):
    """Test moving non-existent entity returns 400 error."""
    move_data = {
        "identifier": "non-existent-entity",
        "destination_path": "target/SomeFile.md",
    }
    response = await client.post(f"{project_url}/knowledge/move", json=move_data)
    assert response.status_code == 400
    assert "Entity not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_move_entity_invalid_destination_path(client: AsyncClient, project_url):
    """Test moving entity with invalid destination path."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "TestNote",
            "folder": "",
            "entity_type": "note",
            "content": "Test content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Test various invalid paths
    invalid_paths = [
        "/absolute/path.md",  # Absolute path
        "../parent/path.md",  # Parent directory
        "",  # Empty string
        "   ",  # Whitespace only
    ]

    for invalid_path in invalid_paths:
        move_data = {
            "identifier": entity["permalink"],
            "destination_path": invalid_path,
        }
        response = await client.post(f"{project_url}/knowledge/move", json=move_data)
        assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_move_entity_destination_exists(client: AsyncClient, project_url):
    """Test moving entity to existing destination returns error."""
    # Create source entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "SourceNote",
            "folder": "source",
            "entity_type": "note",
            "content": "Source content",
        },
    )
    assert response.status_code == 200
    source_entity = response.json()

    # Create destination entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "DestinationNote",
            "folder": "target",
            "entity_type": "note",
            "content": "Destination content",
        },
    )
    assert response.status_code == 200

    # Try to move source to existing destination
    move_data = {
        "identifier": source_entity["permalink"],
        "destination_path": "target/DestinationNote.md",
    }
    response = await client.post(f"{project_url}/knowledge/move", json=move_data)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_move_entity_missing_identifier(client: AsyncClient, project_url):
    """Test move request with missing identifier."""
    move_data = {
        "destination_path": "target/SomeFile.md",
    }
    response = await client.post(f"{project_url}/knowledge/move", json=move_data)
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_move_entity_missing_destination(client: AsyncClient, project_url):
    """Test move request with missing destination path."""
    move_data = {
        "identifier": "some-entity",
    }
    response = await client.post(f"{project_url}/knowledge/move", json=move_data)
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_move_entity_by_file_path(client: AsyncClient, project_url):
    """Test moving entity using file path as identifier."""
    # Create test entity
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "TestNote",
            "folder": "source",
            "entity_type": "note",
            "content": "Test content",
        },
    )
    assert response.status_code == 200
    entity = response.json()

    # Move using file path as identifier
    move_data = {
        "identifier": entity["file_path"],
        "destination_path": "target/MovedByPath.md",
    }
    response = await client.post(f"{project_url}/knowledge/move", json=move_data)
    assert response.status_code == 200

    # Verify entity exists at new location
    response = await client.get(f"{project_url}/knowledge/entities/target/moved-by-path")
    assert response.status_code == 200
    moved_entity = response.json()
    assert moved_entity["file_path"] == "target/MovedByPath.md"


@pytest.mark.asyncio
async def test_move_entity_by_title(client: AsyncClient, project_url):
    """Test moving entity using title as identifier."""
    # Create test entity with unique title
    response = await client.post(
        f"{project_url}/knowledge/entities",
        json={
            "title": "UniqueTestTitle",
            "folder": "source",
            "entity_type": "note",
            "content": "Test content",
        },
    )
    assert response.status_code == 200

    # Move using title as identifier
    move_data = {
        "identifier": "UniqueTestTitle",
        "destination_path": "target/MovedByTitle.md",
    }
    response = await client.post(f"{project_url}/knowledge/move", json=move_data)
    assert response.status_code == 200

    # Verify entity exists at new location
    response = await client.get(f"{project_url}/knowledge/entities/target/moved-by-title")
    assert response.status_code == 200
    moved_entity = response.json()
    assert moved_entity["file_path"] == "target/MovedByTitle.md"
    assert moved_entity["title"] == "UniqueTestTitle"
