"""Tests for V2 knowledge graph API routes (ID-based endpoints)."""

import pytest
from httpx import AsyncClient

from basic_memory.models import Project
from basic_memory.schemas import DeleteEntitiesResponse
from basic_memory.schemas.v2 import EntityResponseV2, EntityResolveResponse


@pytest.mark.asyncio
async def test_resolve_identifier_by_permalink(
    client: AsyncClient, test_graph, v2_project_url, test_project: Project, entity_repository
):
    """Test resolving an identifier by permalink returns correct entity ID."""
    # test_graph fixture creates some test entities
    # We'll use one of them to test resolution

    # Create an entity first
    entity_data = {
        "title": "TestResolve",
        "folder": "test",
        "content": "Test content for resolve",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=entity_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return id
    assert created_entity.id is not None
    entity_id = created_entity.id

    # Now resolve it by permalink
    resolve_data = {"identifier": created_entity.permalink}
    response = await client.post(f"{v2_project_url}/knowledge/resolve", json=resolve_data)

    assert response.status_code == 200
    resolved = EntityResolveResponse.model_validate(response.json())
    assert resolved.entity_id == entity_id
    assert resolved.permalink == created_entity.permalink
    assert resolved.resolution_method == "permalink"


@pytest.mark.asyncio
async def test_resolve_identifier_not_found(client: AsyncClient, v2_project_url):
    """Test resolving a non-existent identifier returns 404."""
    resolve_data = {"identifier": "nonexistent/entity"}
    response = await client.post(f"{v2_project_url}/knowledge/resolve", json=resolve_data)

    assert response.status_code == 404
    assert "Could not resolve identifier" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_entity_by_id(client: AsyncClient, test_graph, v2_project_url, entity_repository):
    """Test getting an entity by its numeric ID."""
    # Create an entity first
    entity_data = {
        "title": "TestGetById",
        "folder": "test",
        "content": "Test content for get by ID",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=entity_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return id
    assert created_entity.id is not None
    entity_id = created_entity.id

    # Get it by ID using v2 endpoint
    response = await client.get(f"{v2_project_url}/knowledge/entities/{entity_id}")

    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())
    assert entity.id == entity_id
    assert entity.title == "TestGetById"
    assert entity.api_version == "v2"


@pytest.mark.asyncio
async def test_get_entity_by_id_not_found(client: AsyncClient, v2_project_url):
    """Test getting a non-existent entity by ID returns 404."""
    response = await client.get(f"{v2_project_url}/knowledge/entities/999999")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_entity(client: AsyncClient, file_service, v2_project_url):
    """Test creating an entity via v2 endpoint."""
    data = {
        "title": "TestV2Entity",
        "folder": "test",
        "entity_type": "test",
        "content_type": "text/markdown",
        "content": "TestContent for V2",
    }

    response = await client.post(f"{v2_project_url}/knowledge/entities", json=data)

    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())

    # V2 endpoints must return id field
    assert entity.id is not None
    assert isinstance(entity.id, int)
    assert entity.api_version == "v2"

    assert entity.permalink == "test/test-v2-entity"
    assert entity.file_path == "test/TestV2Entity.md"
    assert entity.entity_type == data["entity_type"]

    # Verify file was created
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)
    assert data["content"] in file_content


@pytest.mark.asyncio
async def test_create_entity_with_observations_and_relations(
    client: AsyncClient, file_service, v2_project_url
):
    """Test creating an entity with observations and relations via v2."""
    data = {
        "title": "TestV2Complex",
        "folder": "test",
        "content": """
# TestV2Complex

## Observations
- [note] This is a test observation #tag1 (context)
- related to [[OtherEntity]]
""",
    }

    response = await client.post(f"{v2_project_url}/knowledge/entities", json=data)

    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())

    # V2 endpoints must return id field
    assert entity.id is not None
    assert isinstance(entity.id, int)
    assert entity.api_version == "v2"

    assert len(entity.observations) == 1
    assert entity.observations[0].category == "note"
    assert entity.observations[0].content == "This is a test observation #tag1"
    assert entity.observations[0].tags == ["tag1"]

    assert len(entity.relations) == 1
    assert entity.relations[0].relation_type == "related to"


@pytest.mark.asyncio
async def test_update_entity_by_id(
    client: AsyncClient, file_service, v2_project_url, entity_repository
):
    """Test updating an entity by ID using PUT (replace)."""
    # Create an entity first
    create_data = {
        "title": "TestUpdate",
        "folder": "test",
        "content": "Original content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return id
    assert created_entity.id is not None
    original_id = created_entity.id

    # Update it by ID
    update_data = {
        "title": "TestUpdate",
        "folder": "test",
        "content": "Updated content via V2",
    }
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{original_id}",
        json=update_data,
    )

    assert response.status_code == 200
    updated_entity = EntityResponseV2.model_validate(response.json())

    # V2 update must return id field
    assert updated_entity.id is not None
    assert isinstance(updated_entity.id, int)
    assert updated_entity.api_version == "v2"

    # Verify file was updated
    file_path = file_service.get_entity_path(updated_entity)
    file_content, _ = await file_service.read_file(file_path)
    assert "Updated content via V2" in file_content
    assert "Original content" not in file_content


@pytest.mark.asyncio
async def test_edit_entity_by_id_append(
    client: AsyncClient, file_service, v2_project_url, entity_repository
):
    """Test editing an entity by ID using PATCH (append operation)."""
    # Create an entity first
    create_data = {
        "title": "TestEdit",
        "folder": "test",
        "content": "# TestEdit\n\nOriginal content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return id
    assert created_entity.id is not None
    original_id = created_entity.id

    # Edit it by appending
    edit_data = {
        "operation": "append",
        "content": "\n\n## New Section\n\nAppended content",
    }
    response = await client.patch(
        f"{v2_project_url}/knowledge/entities/{original_id}",
        json=edit_data,
    )

    assert response.status_code == 200
    edited_entity = EntityResponseV2.model_validate(response.json())

    # V2 patch must return id field
    assert edited_entity.id is not None
    assert isinstance(edited_entity.id, int)
    assert edited_entity.api_version == "v2"

    # Verify file has both original and appended content
    file_path = file_service.get_entity_path(edited_entity)
    file_content, _ = await file_service.read_file(file_path)
    assert "Original content" in file_content
    assert "Appended content" in file_content


@pytest.mark.asyncio
async def test_edit_entity_by_id_find_replace(
    client: AsyncClient, file_service, v2_project_url, entity_repository
):
    """Test editing an entity by ID using PATCH (find/replace operation)."""
    # Create an entity first
    create_data = {
        "title": "TestFindReplace",
        "folder": "test",
        "content": "# TestFindReplace\n\nOld text that will be replaced",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return id
    assert created_entity.id is not None
    original_id = created_entity.id

    # Edit using find/replace
    edit_data = {
        "operation": "find_replace",
        "find_text": "Old text",
        "content": "New text",
    }
    response = await client.patch(
        f"{v2_project_url}/knowledge/entities/{original_id}",
        json=edit_data,
    )

    assert response.status_code == 200
    edited_entity = EntityResponseV2.model_validate(response.json())

    # V2 patch must return id field
    assert edited_entity.id is not None
    assert isinstance(edited_entity.id, int)
    assert edited_entity.api_version == "v2"

    # Verify replacement
    file_path = file_service.get_entity_path(created_entity)
    file_content, _ = await file_service.read_file(file_path)
    assert "New text" in file_content
    assert "Old text" not in file_content


@pytest.mark.asyncio
async def test_delete_entity_by_id(
    client: AsyncClient, file_service, v2_project_url, entity_repository
):
    """Test deleting an entity by ID."""
    # Create an entity first
    create_data = {
        "title": "TestDelete",
        "folder": "test",
        "content": "Content to be deleted",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return id
    assert created_entity.id is not None
    entity_id = created_entity.id

    # Delete it by ID
    response = await client.delete(f"{v2_project_url}/knowledge/entities/{entity_id}")

    assert response.status_code == 200
    delete_response = DeleteEntitiesResponse.model_validate(response.json())
    assert delete_response.deleted is True

    # Verify it's gone - trying to get it should return 404
    response = await client.get(f"{v2_project_url}/knowledge/entities/{entity_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_entity_by_id_not_found(client: AsyncClient, v2_project_url):
    """Test deleting a non-existent entity returns deleted=False (idempotent)."""
    response = await client.delete(f"{v2_project_url}/knowledge/entities/999999")

    # Delete is idempotent - returns 200 with deleted=False
    assert response.status_code == 200
    delete_response = DeleteEntitiesResponse.model_validate(response.json())
    assert delete_response.deleted is False


@pytest.mark.asyncio
async def test_move_entity(client: AsyncClient, file_service, v2_project_url, entity_repository):
    """Test moving an entity to a new location."""
    # Create an entity first
    create_data = {
        "title": "TestMove",
        "folder": "test",
        "content": "Content to be moved",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return id
    assert created_entity.id is not None
    original_id = created_entity.id

    # Move it to a new folder (V2 uses entity ID in path)
    move_data = {
        "destination_path": "moved/MovedEntity.md",
    }
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{created_entity.id}/move", json=move_data
    )

    assert response.status_code == 200
    moved_entity = EntityResponseV2.model_validate(response.json())

    # V2 move must return id field
    assert moved_entity.id is not None
    assert isinstance(moved_entity.id, int)
    assert moved_entity.api_version == "v2"

    # ID should remain the same (stable reference)
    assert moved_entity.id == original_id
    assert moved_entity.file_path == "moved/MovedEntity.md"


@pytest.mark.asyncio
async def test_v2_endpoints_use_project_id_not_name(client: AsyncClient, test_project: Project):
    """Verify v2 endpoints require project ID, not name."""
    # Try using project name instead of ID - should fail
    response = await client.get(f"/v2/{test_project.name}/knowledge/entities/1")

    # Should get validation error or 404 because name is not a valid integer
    assert response.status_code in [404, 422]


@pytest.mark.asyncio
async def test_entity_response_v2_has_api_version(
    client: AsyncClient, v2_project_url, entity_repository
):
    """Test that EntityResponseV2 includes api_version field."""
    # Create an entity
    entity_data = {
        "title": "TestApiVersion",
        "folder": "test",
        "content": "Test content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=entity_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return id and api_version
    assert created_entity.id is not None
    assert created_entity.api_version == "v2"
    entity_id = created_entity.id

    # Get it via v2 endpoint
    response = await client.get(f"{v2_project_url}/knowledge/entities/{entity_id}")
    assert response.status_code == 200

    entity_v2 = EntityResponseV2.model_validate(response.json())
    assert entity_v2.api_version == "v2"
    assert entity_v2.id == entity_id
