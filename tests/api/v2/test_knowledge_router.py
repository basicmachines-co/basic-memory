"""Tests for V2 knowledge graph API routes (ID-based endpoints)."""

import uuid

import pytest
from httpx import AsyncClient

from basic_memory.models import Project
from basic_memory.schemas import DeleteEntitiesResponse
from basic_memory.schemas.response import DirectoryMoveResult, DirectoryDeleteResult
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
        "directory": "test",
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
    assert "Entity not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_resolve_identifier_no_fuzzy_match(client: AsyncClient, v2_project_url):
    """Test that resolve uses strict mode - no fuzzy search fallback.

    This ensures wiki links only resolve to exact matches (permalink, title, or path),
    not to similar-sounding entities via fuzzy search.
    """
    # Create an entity with a specific name
    entity_data = {
        "title": "link-test",
        "folder": "testing",
        "content": "A test note",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=entity_data)
    assert response.status_code == 200

    # Try to resolve "nonexistent" - should NOT fuzzy match to "link-test"
    resolve_data = {"identifier": "nonexistent"}
    response = await client.post(f"{v2_project_url}/knowledge/resolve", json=resolve_data)

    # Must return 404, not a fuzzy match to "link-test"
    assert response.status_code == 404
    assert "Entity not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_resolve_identifier_with_source_path_no_fuzzy_match(
    client: AsyncClient, v2_project_url
):
    """Test that context-aware resolution also uses strict mode.

    Even with source_path for context-aware resolution, nonexistent
    links should return 404, not fuzzy match to nearby entities.
    """
    # Create entities in a folder structure
    entity_data = {
        "title": "link-test",
        "folder": "testing/nested",
        "content": "A nested test note",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=entity_data)
    assert response.status_code == 200

    # Try to resolve "nonexistent" with source_path context
    # Should NOT fuzzy match to "link-test" in the same or nearby folder
    resolve_data = {
        "identifier": "nonexistent",
        "source_path": "testing/nested/other-note.md",
    }
    response = await client.post(f"{v2_project_url}/knowledge/resolve", json=resolve_data)

    # Must return 404, not a fuzzy match
    assert response.status_code == 404
    assert "Entity not found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_entity_by_id(client: AsyncClient, test_graph, v2_project_url, entity_repository):
    """Test getting an entity by its external_id (UUID)."""
    # Create an entity first
    entity_data = {
        "title": "TestGetById",
        "directory": "test",
        "content": "Test content for get by ID",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=entity_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return external_id
    assert created_entity.external_id is not None
    entity_external_id = created_entity.external_id

    # Get it by external_id using v2 endpoint
    response = await client.get(f"{v2_project_url}/knowledge/entities/{entity_external_id}")

    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())
    assert entity.external_id == entity_external_id
    assert entity.title == "TestGetById"
    assert entity.api_version == "v2"


@pytest.mark.asyncio
async def test_get_entity_by_id_not_found(client: AsyncClient, v2_project_url):
    """Test getting a non-existent entity by external_id returns 404."""
    # Use a UUID format that doesn't exist
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"{v2_project_url}/knowledge/entities/{fake_uuid}")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_entity(client: AsyncClient, file_service, v2_project_url):
    """Test creating an entity via v2 endpoint."""
    data = {
        "title": "TestV2Entity",
        "directory": "test",
        "note_type": "test",
        "content_type": "text/markdown",
        "content": "TestContent for V2",
    }

    response = await client.post(
        f"{v2_project_url}/knowledge/entities", json=data, params={"fast": False}
    )

    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())

    # V2 endpoints must return id field
    assert entity.id is not None
    assert isinstance(entity.id, int)
    assert entity.api_version == "v2"

    assert entity.permalink == "test-project/test/test-v2-entity"
    assert entity.file_path == "test/TestV2Entity.md"
    assert entity.note_type == data["note_type"]

    # Verify file was created
    file_path = file_service.get_entity_path(entity)
    file_content, _ = await file_service.read_file(file_path)
    assert data["content"] in file_content


@pytest.mark.asyncio
async def test_create_entity_conflict_returns_409(client: AsyncClient, v2_project_url):
    """Test creating a duplicate entity returns 409 Conflict."""
    data = {
        "title": "TestV2EntityConflict",
        "directory": "conflict",
        "note_type": "note",
        "content_type": "text/markdown",
        "content": "Original content for conflict",
    }

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json=data,
        params={"fast": False},
    )
    assert response.status_code == 200

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json=data,
        params={"fast": False},
    )
    assert response.status_code == 409
    expected_detail = "Note already exists. Use edit_note to modify it, or delete it first."
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_create_entity_returns_content(client: AsyncClient, file_service, v2_project_url):
    """Test creating an entity always returns file content with frontmatter."""
    data = {
        "title": "TestContentReturn",
        "directory": "test",
        "note_type": "note",
        "content_type": "text/markdown",
        "content": "Body content for return test",
    }

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json=data,
        params={"fast": False},
    )
    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())

    # Content should always be populated with frontmatter
    assert entity.content is not None
    assert "---" in entity.content  # frontmatter markers
    assert "title: TestContentReturn" in entity.content
    assert "type: note" in entity.content
    assert "permalink:" in entity.content
    assert data["content"] in entity.content


@pytest.mark.asyncio
async def test_create_entity_with_observations_and_relations(
    client: AsyncClient, file_service, v2_project_url
):
    """Test creating an entity with observations and relations via v2."""
    data = {
        "title": "TestV2Complex",
        "directory": "test",
        "content": """
# TestV2Complex

## Observations
- [note] This is a test observation #tag1 (context)
- related to [[OtherEntity]]
""",
    }

    response = await client.post(
        f"{v2_project_url}/knowledge/entities", json=data, params={"fast": False}
    )

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
    """Test updating an entity by external_id using PUT (replace)."""
    # Create an entity first
    create_data = {
        "title": "TestUpdate",
        "directory": "test",
        "content": "Original content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return external_id
    assert created_entity.external_id is not None
    original_external_id = created_entity.external_id

    # Update it by external_id
    update_data = {
        "title": "TestUpdate",
        "directory": "test",
        "content": "Updated content via V2",
    }
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{original_external_id}",
        json=update_data,
    )

    assert response.status_code == 200
    updated_entity = EntityResponseV2.model_validate(response.json())

    # V2 update must return external_id field
    assert updated_entity.external_id is not None
    assert updated_entity.api_version == "v2"

    # Verify file was updated
    file_path = file_service.get_entity_path(updated_entity)
    file_content, _ = await file_service.read_file(file_path)
    assert "Updated content via V2" in file_content
    assert "Original content" not in file_content


@pytest.mark.asyncio
async def test_update_entity_by_id_fast_does_not_duplicate(
    client: AsyncClient, v2_project_url, entity_repository
):
    """Fast PUT updates the existing external_id without creating duplicates."""
    create_data = {
        "title": "07 - Get Started",
        "directory": "docs",
        "content": "Original content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    update_data = {
        "title": "07 Get Started",
        "directory": "docs",
        "content": "Updated content",
    }
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{created_entity.external_id}",
        json=update_data,
    )
    assert response.status_code == 200

    entities = await entity_repository.find_all()
    assert len(entities) == 1
    assert entities[0].external_id == created_entity.external_id


@pytest.mark.asyncio
async def test_put_entity_fast_returns_minimal_row(
    client: AsyncClient, v2_project_url, entity_repository
):
    """Fast PUT returns a minimal row and persists the external_id immediately."""
    external_id = str(uuid.uuid4())
    update_data = {
        "title": "FastPutEntity",
        "directory": "test",
        "content": """
# FastPutEntity

## Observations
- [note] This should be deferred

- related_to [[AnotherEntity]]
""",
    }
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{external_id}",
        json=update_data,
        params={"fast": True},
    )

    assert response.status_code == 201
    created_entity = EntityResponseV2.model_validate(response.json())
    assert created_entity.external_id == external_id
    assert created_entity.observations == []
    assert created_entity.relations == []

    db_entity = await entity_repository.get_by_external_id(external_id)
    assert db_entity is not None


@pytest.mark.asyncio
async def test_fast_create_schedules_reindex_task(
    client: AsyncClient, v2_project_url, task_scheduler_spy
):
    """Fast create should enqueue a background reindex task."""
    start_count = len(task_scheduler_spy)
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "TaskScheduledEntity",
            "directory": "test",
            "content": "Content for task scheduling",
        },
        params={"fast": True},
    )
    assert response.status_code == 200
    assert len(task_scheduler_spy) == start_count + 1
    created_entity = EntityResponseV2.model_validate(response.json())
    scheduled = task_scheduler_spy[-1]
    assert scheduled["task_name"] == "reindex_entity"
    assert scheduled["payload"]["entity_id"] == created_entity.id


@pytest.mark.asyncio
async def test_non_fast_create_schedules_vector_sync_when_semantic_enabled(
    client: AsyncClient, v2_project_url, task_scheduler_spy, app_config
):
    """Non-fast create should schedule vector sync when semantic mode is enabled."""
    app_config.semantic_search_enabled = True
    start_count = len(task_scheduler_spy)

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "NonFastSemanticEntity",
            "directory": "test",
            "content": "Content for non-fast semantic scheduling",
        },
        params={"fast": False},
    )
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    assert len(task_scheduler_spy) == start_count + 1
    scheduled = task_scheduler_spy[-1]
    assert scheduled["task_name"] == "sync_entity_vectors"
    assert scheduled["payload"]["entity_id"] == created_entity.id


@pytest.mark.asyncio
async def test_non_fast_create_skips_vector_sync_when_semantic_disabled(
    client: AsyncClient, v2_project_url, task_scheduler_spy, app_config
):
    """Non-fast create should not schedule vector sync when semantic mode is disabled."""
    app_config.semantic_search_enabled = False
    start_count = len(task_scheduler_spy)

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "NonFastNoSemanticEntity",
            "directory": "test",
            "content": "Content for non-fast without semantic scheduling",
        },
        params={"fast": False},
    )
    assert response.status_code == 200
    assert len(task_scheduler_spy) == start_count


@pytest.mark.asyncio
async def test_edit_entity_by_id_append(
    client: AsyncClient, file_service, v2_project_url, entity_repository
):
    """Test editing an entity by external_id using PATCH (append operation)."""
    # Create an entity first
    create_data = {
        "title": "TestEdit",
        "directory": "test",
        "content": "# TestEdit\n\nOriginal content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return external_id
    assert created_entity.external_id is not None
    original_external_id = created_entity.external_id

    # Edit it by appending
    edit_data = {
        "operation": "append",
        "content": "\n\n## New Section\n\nAppended content",
    }
    response = await client.patch(
        f"{v2_project_url}/knowledge/entities/{original_external_id}",
        json=edit_data,
    )

    assert response.status_code == 200
    edited_entity = EntityResponseV2.model_validate(response.json())

    # V2 patch must return external_id field
    assert edited_entity.external_id is not None
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
    """Test editing an entity by external_id using PATCH (find/replace operation)."""
    # Create an entity first
    create_data = {
        "title": "TestFindReplace",
        "directory": "test",
        "content": "# TestFindReplace\n\nOld text that will be replaced",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return external_id
    assert created_entity.external_id is not None
    original_external_id = created_entity.external_id

    # Edit using find/replace
    edit_data = {
        "operation": "find_replace",
        "find_text": "Old text",
        "content": "New text",
    }
    response = await client.patch(
        f"{v2_project_url}/knowledge/entities/{original_external_id}",
        json=edit_data,
    )

    assert response.status_code == 200
    edited_entity = EntityResponseV2.model_validate(response.json())

    # V2 patch must return external_id field
    assert edited_entity.external_id is not None
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
    """Test deleting an entity by external_id."""
    # Create an entity first
    create_data = {
        "title": "TestDelete",
        "directory": "test",
        "content": "Content to be deleted",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return external_id
    assert created_entity.external_id is not None
    entity_external_id = created_entity.external_id

    # Delete it by external_id
    response = await client.delete(f"{v2_project_url}/knowledge/entities/{entity_external_id}")

    assert response.status_code == 200
    delete_response = DeleteEntitiesResponse.model_validate(response.json())
    assert delete_response.deleted is True

    # Verify it's gone - trying to get it should return 404
    response = await client.get(f"{v2_project_url}/knowledge/entities/{entity_external_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_entity_by_id_not_found(client: AsyncClient, v2_project_url):
    """Test deleting a non-existent entity returns deleted=False (idempotent)."""
    # Use a UUID format that doesn't exist
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.delete(f"{v2_project_url}/knowledge/entities/{fake_uuid}")

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
        "directory": "test",
        "content": "Content to be moved",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return external_id
    assert created_entity.external_id is not None
    original_external_id = created_entity.external_id

    # Move it to a new folder (V2 uses entity external_id in path)
    move_data = {
        "destination_path": "moved/MovedEntity.md",
    }
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{created_entity.external_id}/move", json=move_data
    )

    assert response.status_code == 200
    moved_entity = EntityResponseV2.model_validate(response.json())

    # V2 move must return external_id field
    assert moved_entity.external_id is not None
    assert isinstance(moved_entity.external_id, str)
    assert moved_entity.api_version == "v2"

    # external_id should remain the same (stable reference)
    assert moved_entity.external_id == original_external_id
    assert moved_entity.file_path == "moved/MovedEntity.md"


@pytest.mark.asyncio
async def test_v2_endpoints_use_project_id_not_name(client: AsyncClient, test_project: Project):
    """Verify v2 endpoints require project external_id UUID, not name."""
    # Try using project name instead of external_id - should fail
    fake_entity_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.get(
        f"/v2/projects/{test_project.name}/knowledge/entities/{fake_entity_uuid}"
    )

    # Should get 404 because name is not a valid project external_id
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_entity_response_v2_has_api_version(
    client: AsyncClient, v2_project_url, entity_repository
):
    """Test that EntityResponseV2 includes api_version field."""
    # Create an entity
    entity_data = {
        "title": "TestApiVersion",
        "directory": "test",
        "content": "Test content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=entity_data)
    assert response.status_code == 200
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return external_id and api_version
    assert created_entity.external_id is not None
    assert created_entity.api_version == "v2"
    entity_external_id = created_entity.external_id

    # Get it via v2 endpoint
    response = await client.get(f"{v2_project_url}/knowledge/entities/{entity_external_id}")
    assert response.status_code == 200

    entity_v2 = EntityResponseV2.model_validate(response.json())
    assert entity_v2.api_version == "v2"
    assert entity_v2.external_id == entity_external_id


# --- Move directory tests (V2) ---


@pytest.mark.asyncio
async def test_move_directory_v2_success(client: AsyncClient, v2_project_url):
    """Test POST /v2/.../move-directory endpoint successfully moves all files."""
    # Create multiple notes in a source directory
    for i in range(3):
        response = await client.post(
            f"{v2_project_url}/knowledge/entities",
            json={
                "title": f"V2DirMoveDoc{i + 1}",
                "directory": "v2-move-source",
                "content": f"Content for document {i + 1}",
            },
        )
        assert response.status_code == 200

    # Move the entire directory
    move_data = {
        "source_directory": "v2-move-source",
        "destination_directory": "v2-move-dest",
    }
    response = await client.post(f"{v2_project_url}/knowledge/move-directory", json=move_data)
    assert response.status_code == 200

    result = DirectoryMoveResult.model_validate(response.json())
    assert result.total_files == 3
    assert result.successful_moves == 3
    assert result.failed_moves == 0
    assert len(result.moved_files) == 3


@pytest.mark.asyncio
async def test_move_directory_v2_empty_directory(client: AsyncClient, v2_project_url):
    """Test move_directory V2 with no files in source returns zero counts."""
    move_data = {
        "source_directory": "v2-nonexistent-source",
        "destination_directory": "v2-some-dest",
    }
    response = await client.post(f"{v2_project_url}/knowledge/move-directory", json=move_data)
    assert response.status_code == 200

    result = DirectoryMoveResult.model_validate(response.json())
    assert result.total_files == 0
    assert result.successful_moves == 0
    assert result.failed_moves == 0


@pytest.mark.asyncio
async def test_move_directory_v2_validation_error(client: AsyncClient, v2_project_url):
    """Test move_directory V2 with missing required fields returns validation error."""
    # Missing destination_directory
    response = await client.post(
        f"{v2_project_url}/knowledge/move-directory",
        json={"source_directory": "some-source"},
    )
    assert response.status_code == 422

    # Missing source_directory
    response = await client.post(
        f"{v2_project_url}/knowledge/move-directory",
        json={"destination_directory": "some-dest"},
    )
    assert response.status_code == 422


# --- Delete directory tests (V2) ---


@pytest.mark.asyncio
async def test_delete_directory_v2_success(client: AsyncClient, v2_project_url):
    """Test POST /v2/.../delete-directory endpoint successfully deletes all files."""
    # Create multiple notes in a directory to delete
    for i in range(3):
        response = await client.post(
            f"{v2_project_url}/knowledge/entities",
            json={
                "title": f"V2DeleteDoc{i + 1}",
                "directory": "v2-delete-dir",
                "content": f"Content for document {i + 1}",
            },
        )
        assert response.status_code == 200

    # Verify notes exist
    created_entity = EntityResponseV2.model_validate(response.json())
    get_response = await client.get(
        f"{v2_project_url}/knowledge/entities/{created_entity.external_id}"
    )
    assert get_response.status_code == 200

    # Delete the entire directory
    delete_data = {
        "directory": "v2-delete-dir",
    }
    response = await client.post(f"{v2_project_url}/knowledge/delete-directory", json=delete_data)
    assert response.status_code == 200

    result = DirectoryDeleteResult.model_validate(response.json())
    assert result.total_files == 3
    assert result.successful_deletes == 3
    assert result.failed_deletes == 0
    assert len(result.deleted_files) == 3

    # Verify entity is no longer accessible
    get_response = await client.get(
        f"{v2_project_url}/knowledge/entities/{created_entity.external_id}"
    )
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_directory_v2_empty_directory(client: AsyncClient, v2_project_url):
    """Test delete_directory V2 with no files returns zero counts."""
    delete_data = {
        "directory": "v2-nonexistent-delete-dir",
    }
    response = await client.post(f"{v2_project_url}/knowledge/delete-directory", json=delete_data)
    assert response.status_code == 200

    result = DirectoryDeleteResult.model_validate(response.json())
    assert result.total_files == 0
    assert result.successful_deletes == 0
    assert result.failed_deletes == 0


@pytest.mark.asyncio
async def test_delete_directory_v2_validation_error(client: AsyncClient, v2_project_url):
    """Test delete_directory V2 with missing required fields returns validation error."""
    # Missing directory field
    response = await client.post(
        f"{v2_project_url}/knowledge/delete-directory",
        json={},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_directory_v2_nested_structure(client: AsyncClient, v2_project_url):
    """Test delete_directory V2 handles nested directory structure."""
    # Create notes in nested structure
    directories = [
        "v2-nested-delete/2024",
        "v2-nested-delete/2024/q1",
    ]

    for dir_path in directories:
        response = await client.post(
            f"{v2_project_url}/knowledge/entities",
            json={
                "title": f"Note in {dir_path.split('/')[-1]}",
                "directory": dir_path,
                "content": f"Content in {dir_path}",
            },
        )
        assert response.status_code == 200

    # Delete the parent directory
    delete_data = {
        "directory": "v2-nested-delete/2024",
    }
    response = await client.post(f"{v2_project_url}/knowledge/delete-directory", json=delete_data)
    assert response.status_code == 200

    result = DirectoryDeleteResult.model_validate(response.json())
    assert result.total_files == 2
    assert result.successful_deletes == 2
    assert result.failed_deletes == 0


@pytest.mark.asyncio
async def test_entity_response_includes_user_tracking_fields(
    client: AsyncClient, v2_project_url
):
    """EntityResponseV2 includes created_by and last_updated_by fields (null for local)."""
    entity_data = {
        "title": "UserTrackingTest",
        "directory": "test",
        "content": "Test content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=entity_data)
    assert response.status_code == 200

    body = response.json()
    # Fields should be present in the response (null for local/CLI usage)
    assert "created_by" in body
    assert "last_updated_by" in body
    assert body["created_by"] is None
    assert body["last_updated_by"] is None
