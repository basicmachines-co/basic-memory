"""Tests for V2 knowledge graph API routes (ID-based endpoints)."""

import os
from datetime import datetime, timezone
from pathlib import Path
import uuid

import pytest
from httpx import AsyncClient

from basic_memory import db
from basic_memory.api.v2.routers.knowledge_router import _canonical_file_path
from basic_memory.file_utils import parse_frontmatter
from basic_memory.ignore_utils import get_bmignore_path
from basic_memory.index.local_project import (
    LocalProjectIndexRuntimeFactory,
    run_local_project_index_for_project,
)
from basic_memory.models import Entity as EntityModel, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.note_content_repository import NoteContentRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.search_repository_base import VectorSyncBatchResult
from basic_memory.runtime.note_content import NOTE_CONTENT_BASE_CHECKSUM_HEADER
from basic_memory.schemas import DeleteEntitiesResponse
from basic_memory.schemas.response import DirectoryMoveResult, DirectoryDeleteResult
from basic_memory.schemas.v2 import EntityResponseV2, EntityResolveResponse
from basic_memory.services.search_service import SearchService


async def _find_all_entities(entity_repository, session_maker):
    async with db.scoped_session(session_maker) as session:
        return await entity_repository.find_all(session)


async def _get_entity_by_external_id(entity_repository, session_maker, external_id: str):
    async with db.scoped_session(session_maker) as session:
        return await entity_repository.get_by_external_id(session, external_id)


async def _get_note_content(session_maker, project_id: int, entity_id: int):
    repository = NoteContentRepository(project_id=project_id)
    async with db.scoped_session(session_maker) as session:
        return await repository.get_by_entity_id(session, entity_id)


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
    assert response.status_code == 202
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
    assert resolved.project_external_id == test_project.external_id
    assert resolved.permalink == created_entity.permalink
    assert resolved.resolution_method == "permalink"


@pytest.mark.asyncio
async def test_resolve_identifier_returns_target_project_external_id_for_cross_project_link(
    client: AsyncClient,
    session_maker,
    tmp_path,
    v2_project_url,
):
    """Cross-project resolves should expose the owning project external ID."""
    project_repository = ProjectRepository()
    now = datetime.now(timezone.utc)
    async with db.scoped_session(session_maker) as session:
        other_project = await project_repository.create(
            session,
            {
                "name": "other-project",
                "description": "Secondary project",
                "path": str(tmp_path / "other-project"),
                "is_active": True,
                "is_default": False,
            },
        )
        other_entity_repository = EntityRepository(project_id=other_project.id)
        target = await other_entity_repository.add(
            session,
            EntityModel(
                title="Cross Project Note",
                note_type="note",
                content_type="text/markdown",
                file_path="docs/Cross Project Note.md",
                permalink=f"{other_project.permalink}/docs/cross-project-note",
                created_at=now,
                updated_at=now,
                project_id=other_project.id,
            ),
        )

    response = await client.post(
        f"{v2_project_url}/knowledge/resolve",
        json={"identifier": "other-project::Cross Project Note", "strict": True},
    )

    assert response.status_code == 200
    resolved = EntityResolveResponse.model_validate(response.json())
    assert resolved.entity_id == target.id
    assert resolved.project_external_id == other_project.external_id


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
    assert response.status_code == 202

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
    assert response.status_code == 202

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
    assert response.status_code == 202
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
async def test_get_entity_by_id_reads_accepted_note_content(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    session_maker,
):
    """Markdown entity reads should return accepted DB content when present."""
    create_response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "AcceptedKnowledge",
            "directory": "test",
            "content": "Original file content",
        },
    )
    assert create_response.status_code == 202
    created = EntityResponseV2.model_validate(create_response.json())

    accepted_content = "# AcceptedKnowledge\n\nAccepted note_content body.\n"
    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        await repository.upsert(
            session,
            {
                "entity_id": created.id,
                "markdown_content": accepted_content,
                "db_version": 42,
                "db_checksum": "accepted-db-checksum",
                "file_write_status": "pending",
                "last_source": "test",
            },
        )

    response = await client.get(f"{v2_project_url}/knowledge/entities/{created.external_id}")

    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())
    assert entity.external_id == created.external_id
    assert entity.content == accepted_content


@pytest.mark.asyncio
async def test_get_entity_by_id_allows_long_relation_type(
    client: AsyncClient,
    v2_project_url,
    relation_repository,
    session_maker,
):
    """GET entity should not fail when stored relation_type exceeds 200 characters."""
    source_response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Long Relation Source",
            "directory": "test",
            "content": "Source entity content",
        },
    )
    assert source_response.status_code == 202
    source_entity = EntityResponseV2.model_validate(source_response.json())

    target_response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Long Relation Target",
            "directory": "test",
            "content": "Target entity content",
        },
    )
    assert target_response.status_code == 202
    target_entity = EntityResponseV2.model_validate(target_response.json())

    long_relation_type = (
        "**Architecture/efficiency concern:** "
        "the orchestration prompt expanded a short edge label into a full descriptive note "
        "that is much longer than 200 characters but should still serialize cleanly."
    )

    async with db.scoped_session(session_maker) as session:
        await relation_repository.create(
            session,
            {
                "from_id": source_entity.id,
                "to_id": target_entity.id,
                "to_name": target_entity.title,
                "relation_type": long_relation_type,
            },
        )
        await NoteContentRepository(project_id=relation_repository.project_id).delete_by_entity_id(
            session,
            source_entity.id,
        )

    response = await client.get(f"{v2_project_url}/knowledge/entities/{source_entity.external_id}")

    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())
    assert len(entity.relations) == 1
    assert entity.relations[0].relation_type == long_relation_type


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

    assert response.status_code == 202
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
    assert response.status_code == 202

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json=data,
        params={"fast": False},
    )
    assert response.status_code == 409
    expected_detail = "Note already exists. Use edit_note to modify it, or delete it first."
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_create_entity_rejects_existing_unindexed_file(
    client: AsyncClient, v2_project_url, project_config
):
    """A create over a file that exists on disk but is not indexed returns 409.

    Regression for the #1002 review: local creates must not commit DB/search
    rows that diverge from on-disk content, which the next watcher pass would
    restore (silently losing the write). The local runtime keeps the storage
    existence check enabled, so the create is rejected up front.
    """
    file_path = project_config.home / "conflict" / "UnindexedNote.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    original = "---\ntitle: UnindexedNote\ntype: note\n---\n\nPre-existing on-disk content\n"
    file_path.write_text(original, encoding="utf-8")

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "UnindexedNote",
            "directory": "conflict",
            "note_type": "note",
            "content_type": "text/markdown",
            "content": "New content from the create request",
        },
        params={"fast": False},
    )

    assert response.status_code == 409
    # The on-disk file must be untouched: no divergence, no silent overwrite.
    assert file_path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_put_create_rejects_existing_unindexed_file(
    client: AsyncClient, v2_project_url, project_config
):
    """PUT-as-create over an unindexed on-disk file returns 409 (#1002 review).

    The create branch of PUT (entity_id not in the DB) must apply the same
    storage check as POST so it cannot commit DB/search state that diverges
    from the file on disk.
    """
    file_path = project_config.home / "conflict" / "PutUnindexed.md"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    original = "---\ntitle: PutUnindexed\ntype: note\n---\n\nPre-existing on-disk content\n"
    file_path.write_text(original, encoding="utf-8")

    new_external_id = "abcdef00-0000-4000-8000-000000000000"
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{new_external_id}",
        json={
            "title": "PutUnindexed",
            "directory": "conflict",
            "content": "New content via PUT",
        },
        params={"fast": False},
    )

    assert response.status_code == 409
    assert file_path.read_text(encoding="utf-8") == original


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
    assert response.status_code == 202
    entity = EntityResponseV2.model_validate(response.json())

    # Content should always be populated with frontmatter
    assert entity.content is not None
    assert "---" in entity.content  # frontmatter markers
    assert "title: TestContentReturn" in entity.content
    assert "type: note" in entity.content
    assert "permalink:" in entity.content
    assert data["content"] in entity.content


@pytest.mark.asyncio
async def test_create_entity_accepts_note_content_and_materializes_file(
    client: AsyncClient,
    test_project: Project,
    v2_project_url: str,
    session_maker,
):
    """Create accepts markdown into note_content and materializes it locally."""
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "AcceptedCreate",
            "directory": "test",
            "content": "Accepted create content",
        },
    )

    assert response.status_code == 202
    entity = EntityResponseV2.model_validate(response.json())
    assert entity.content is not None
    assert entity.db_version == 1
    assert entity.file_write_status == "synced"

    repository = NoteContentRepository(project_id=test_project.id)
    async with db.scoped_session(session_maker) as session:
        note_content = await repository.get_by_entity_id(session, entity.id)

    assert note_content is not None
    assert note_content.db_version == 1
    assert note_content.markdown_content == entity.content
    assert note_content.file_write_status == "synced"
    assert note_content.file_checksum is not None


@pytest.mark.asyncio
async def test_create_entity_returns_indexed_accepted_content(
    client: AsyncClient, file_service, v2_project_url
):
    """Accepted-note creates return materialized content with parsed graph rows."""
    data = {
        "title": "TestV2Complex",
        "directory": "test",
        "content": """
# TestV2Complex

## Observations
- [note] This is a test observation #tag1 (context)
- "related to" [[OtherEntity]]
""",
    }

    response = await client.post(
        f"{v2_project_url}/knowledge/entities", json=data, params={"fast": False}
    )

    assert response.status_code == 202
    entity = EntityResponseV2.model_validate(response.json())

    # V2 endpoints must return id field
    assert entity.id is not None
    assert isinstance(entity.id, int)
    assert entity.api_version == "v2"

    assert entity.content is not None
    assert "This is a test observation #tag1" in entity.content
    assert '"related to" [[OtherEntity]]' in entity.content
    assert len(entity.observations) == 1
    assert entity.observations[0].content == "This is a test observation #tag1"
    assert len(entity.relations) == 1
    assert entity.relations[0].relation_type == "related to"
    assert entity.relations[0].to_name == "OtherEntity"


@pytest.mark.asyncio
async def test_update_entity_by_id(
    client: AsyncClient,
    file_service,
    test_project: Project,
    v2_project_url,
    entity_repository,
    session_maker,
):
    """Test updating an entity by external_id using PUT (replace)."""
    # Create an entity first
    create_data = {
        "title": "TestUpdate",
        "directory": "test",
        "content": "Original content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 202
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
        params={"fast": False},
    )

    assert response.status_code == 202
    updated_entity = EntityResponseV2.model_validate(response.json())

    # V2 update must return external_id field
    assert updated_entity.external_id is not None
    assert updated_entity.api_version == "v2"
    assert updated_entity.content is not None
    assert "Updated content via V2" in updated_entity.content
    assert updated_entity.db_version == 2
    assert updated_entity.file_write_status == "synced"

    # Verify file was updated
    file_path = file_service.get_entity_path(updated_entity)
    file_content, _ = await file_service.read_file(file_path)
    assert "Updated content via V2" in file_content
    assert "Original content" not in file_content

    note_content = await _get_note_content(session_maker, test_project.id, updated_entity.id)
    assert note_content is not None
    assert note_content.db_version == 2
    assert "Updated content via V2" in note_content.markdown_content
    assert "Original content" not in note_content.markdown_content
    assert note_content.file_write_status == "synced"


@pytest.mark.asyncio
async def test_update_entity_by_id_freshens_external_file_metadata_before_accepting(
    client: AsyncClient,
    file_service,
    test_project: Project,
    v2_project_url,
    session_maker,
):
    """PUT reconciles direct file edits before preserving existing frontmatter."""
    create_data = {
        "title": "ExternalMetadata",
        "directory": "test",
        "content": "Original body",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 202
    created_entity = EntityResponseV2.model_validate(response.json())

    file_path = Path(test_project.path) / created_entity.file_path
    external_content = file_path.read_text(encoding="utf-8")
    external_content = external_content.replace(
        "type: note\n",
        "type: note\nstatus: blocked\npriority: high\n",
    )
    external_content = external_content.replace("Original body", "Externally edited body")
    file_path.write_text(external_content, encoding="utf-8")

    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{created_entity.external_id}",
        json={
            "title": "ExternalMetadata",
            "directory": "test",
            "content": "Updated through accepted note API",
        },
    )

    assert response.status_code == 202
    updated_entity = EntityResponseV2.model_validate(response.json())
    assert updated_entity.content is not None
    response_frontmatter = parse_frontmatter(updated_entity.content)
    assert response_frontmatter["status"] == "blocked"
    assert response_frontmatter["priority"] == "high"
    assert "Updated through accepted note API" in updated_entity.content
    assert "Externally edited body" not in updated_entity.content

    file_content, _ = await file_service.read_file(created_entity.file_path)
    file_frontmatter = parse_frontmatter(file_content)
    assert file_frontmatter["status"] == "blocked"
    assert file_frontmatter["priority"] == "high"

    note_content = await _get_note_content(session_maker, test_project.id, updated_entity.id)
    assert note_content is not None
    accepted_frontmatter = parse_frontmatter(note_content.markdown_content)
    assert accepted_frontmatter["status"] == "blocked"
    assert accepted_frontmatter["priority"] == "high"


@pytest.mark.asyncio
async def test_update_entity_by_id_does_not_duplicate(
    client: AsyncClient, v2_project_url, entity_repository, session_maker
):
    """PUT updates the existing external_id without creating duplicates."""
    create_data = {
        "title": "07 - Get Started",
        "directory": "docs",
        "content": "Original content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 202
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
    assert response.status_code == 202

    entities = await _find_all_entities(entity_repository, session_maker)
    assert len(entities) == 1
    assert entities[0].external_id == created_entity.external_id


@pytest.mark.asyncio
async def test_update_entity_stale_base_checksum_returns_structured_409(
    client: AsyncClient, test_project: Project, v2_project_url, session_maker
):
    """A PUT with a stale base-checksum precondition rejects instead of clobbering."""
    create_data = {
        "title": "Preconditioned",
        "directory": "test",
        "content": "Original content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 202
    created_entity = EntityResponseV2.model_validate(response.json())

    note_content = await _get_note_content(session_maker, test_project.id, created_entity.id)
    assert note_content is not None
    current_checksum = note_content.db_checksum

    update_data = {
        "title": "Preconditioned",
        "directory": "test",
        "content": "Stale overwrite",
    }
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{created_entity.external_id}",
        json=update_data,
        headers={NOTE_CONTENT_BASE_CHECKSUM_HEADER: "stale-checksum"},
    )
    assert response.status_code == 409
    # Exact wire shape cloud web/relay clients parse to rebase (issue #1445).
    assert response.json()["detail"] == {
        "message": "Note changed since your last sync",
        "db_checksum": current_checksum,
    }

    # The stale write did not land.
    note_content = await _get_note_content(session_maker, test_project.id, created_entity.id)
    assert note_content is not None
    assert note_content.db_version == 1
    assert "Original content" in note_content.markdown_content

    # Retrying with the current accepted checksum satisfies the precondition.
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{created_entity.external_id}",
        json={
            "title": "Preconditioned",
            "directory": "test",
            "content": "Rebased content",
        },
        headers={NOTE_CONTENT_BASE_CHECKSUM_HEADER: current_checksum},
    )
    assert response.status_code == 202
    note_content = await _get_note_content(session_maker, test_project.id, created_entity.id)
    assert note_content is not None
    assert note_content.db_version == 2
    assert "Rebased content" in note_content.markdown_content


@pytest.mark.asyncio
async def test_update_entity_with_base_checksum_after_delete_returns_409_gone(
    client: AsyncClient,
    test_project: Project,
    v2_project_url,
    entity_repository,
    session_maker,
):
    """PUT with a base checksum must not silently resurrect a just-deleted note."""
    create_data = {
        "title": "Deleted Note",
        "directory": "test",
        "content": "To be deleted",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 202
    created_entity = EntityResponseV2.model_validate(response.json())

    note_content = await _get_note_content(session_maker, test_project.id, created_entity.id)
    assert note_content is not None
    synced_checksum = note_content.db_checksum

    response = await client.delete(
        f"{v2_project_url}/knowledge/entities/{created_entity.external_id}"
    )
    assert response.status_code == 202

    put_data = {
        "title": "Deleted Note",
        "directory": "test",
        "content": "Resurrected?",
    }
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{created_entity.external_id}",
        json=put_data,
        headers={NOTE_CONTENT_BASE_CHECKSUM_HEADER: synced_checksum},
    )
    assert response.status_code == 409
    # db_checksum null tells the caller the note is gone: nothing to rebase against.
    assert response.json()["detail"] == {
        "message": "Note changed since your last sync",
        "db_checksum": None,
    }
    entities = await _find_all_entities(entity_repository, session_maker)
    assert entities == []

    # Without the header the PUT keeps its upsert contract and re-creates the note.
    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{created_entity.external_id}",
        json=put_data,
    )
    assert response.status_code == 202
    entities = await _find_all_entities(entity_repository, session_maker)
    assert len(entities) == 1
    assert entities[0].external_id == created_entity.external_id


@pytest.mark.asyncio
async def test_put_entity_with_fast_param_returns_indexed_accepted_content(
    client: AsyncClient, v2_project_url, entity_repository, session_maker
):
    """PUT ignores the legacy fast param and returns accepted note_content."""
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

    assert response.status_code == 202
    created_entity = EntityResponseV2.model_validate(response.json())
    assert created_entity.external_id == external_id
    assert created_entity.content is not None
    assert "This should be deferred" in created_entity.content
    assert "related_to [[AnotherEntity]]" in created_entity.content
    assert len(created_entity.observations) == 1
    assert created_entity.observations[0].content == "This should be deferred"
    assert len(created_entity.relations) == 1
    assert created_entity.relations[0].relation_type == "related_to"
    assert created_entity.relations[0].to_name == "AnotherEntity"

    db_entity = await _get_entity_by_external_id(entity_repository, session_maker, external_id)
    assert db_entity is not None


@pytest.mark.asyncio
async def test_create_with_fast_param_does_not_schedule_reindex_task(
    client: AsyncClient, v2_project_url, vector_sync_scheduler_spy, app_config
):
    """Legacy fast=true should not resurrect the removed reindex note-write path."""
    app_config.semantic_search_enabled = False
    start_count = len(vector_sync_scheduler_spy)
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "TaskScheduledEntity",
            "directory": "test",
            "content": "Content for task scheduling",
        },
        params={"fast": True},
    )
    assert response.status_code == 202
    assert len(vector_sync_scheduler_spy) == start_count


@pytest.mark.asyncio
async def test_create_schedules_vector_sync_when_semantic_enabled(
    client: AsyncClient, v2_project_url, vector_sync_scheduler_spy, app_config
):
    """Create should schedule vector sync when semantic mode is enabled."""
    app_config.semantic_search_enabled = True
    start_count = len(vector_sync_scheduler_spy)

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "NonFastSemanticEntity",
            "directory": "test",
            "content": "Content for non-fast semantic scheduling",
        },
        params={"fast": False},
    )
    assert response.status_code == 202
    created_entity = EntityResponseV2.model_validate(response.json())

    assert len(vector_sync_scheduler_spy) == start_count + 1
    scheduled = vector_sync_scheduler_spy[-1]
    assert scheduled["entity_id"] == created_entity.id


@pytest.mark.asyncio
async def test_create_schedules_relation_resolution_regardless_of_semantic(
    client: AsyncClient, v2_project_url, relation_resolution_scheduler_spy, app_config
):
    """Create should schedule forward-reference resolution even when semantic is off.

    Regression for #1015: creating a note must back-resolve inbound forward
    references that name it, matching the watcher's relation repair. Unlike
    vector sync, this is not gated on semantic search.
    """
    app_config.semantic_search_enabled = False
    start_count = len(relation_resolution_scheduler_spy)

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "RelationResolutionTarget",
            "directory": "test",
            "content": "Content that may satisfy a dangling forward reference",
        },
        params={"fast": False},
    )
    assert response.status_code == 202

    # Relation resolution is scheduled by the eager router follow-up AND again by
    # the materializer once the deferred index lands (so a pass runs after the new
    # rows exist); the scheduler coalesces/re-arms them. The #1015 regression is
    # that it runs at all when semantic search is off.
    assert len(relation_resolution_scheduler_spy) >= start_count + 1
    assert relation_resolution_scheduler_spy[-1]["project_id"] is not None


@pytest.mark.asyncio
async def test_create_skips_vector_sync_when_semantic_disabled(
    client: AsyncClient, v2_project_url, vector_sync_scheduler_spy, app_config
):
    """Create should not schedule vector sync when semantic mode is disabled."""
    app_config.semantic_search_enabled = False
    start_count = len(vector_sync_scheduler_spy)

    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "NonFastNoSemanticEntity",
            "directory": "test",
            "content": "Content for non-fast without semantic scheduling",
        },
        params={"fast": False},
    )
    assert response.status_code == 202
    assert len(vector_sync_scheduler_spy) == start_count


@pytest.mark.asyncio
async def test_edit_entity_by_id_append(
    client: AsyncClient,
    file_service,
    test_project: Project,
    v2_project_url,
    entity_repository,
    session_maker,
):
    """Test editing an entity by external_id using PATCH (append operation)."""
    # Create an entity first
    create_data = {
        "title": "TestEdit",
        "directory": "test",
        "content": "# TestEdit\n\nOriginal content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 202
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
        params={"fast": False},
    )

    assert response.status_code == 202
    edited_entity = EntityResponseV2.model_validate(response.json())

    # V2 patch must return external_id field
    assert edited_entity.external_id is not None
    assert edited_entity.api_version == "v2"
    assert edited_entity.content is not None
    assert "Appended content" in edited_entity.content

    # Verify file has both original and appended content
    file_path = file_service.get_entity_path(edited_entity)
    file_content, _ = await file_service.read_file(file_path)
    assert "Original content" in file_content
    assert "Appended content" in file_content

    note_content = await _get_note_content(session_maker, test_project.id, edited_entity.id)
    assert note_content is not None
    assert note_content.db_version == 2
    assert "Original content" in note_content.markdown_content
    assert "Appended content" in note_content.markdown_content
    assert note_content.file_write_status == "synced"


@pytest.mark.asyncio
async def test_edit_entity_by_id_find_replace(
    client: AsyncClient,
    file_service,
    test_project: Project,
    v2_project_url,
    entity_repository,
    session_maker,
):
    """Test editing an entity by external_id using PATCH (find/replace operation)."""
    # Create an entity first
    create_data = {
        "title": "TestFindReplace",
        "directory": "test",
        "content": "# TestFindReplace\n\nOld text that will be replaced",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 202
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

    assert response.status_code == 202
    edited_entity = EntityResponseV2.model_validate(response.json())

    # V2 patch must return external_id field
    assert edited_entity.external_id is not None
    assert edited_entity.api_version == "v2"

    # Verify replacement
    file_path = file_service.get_entity_path(created_entity)
    file_content, _ = await file_service.read_file(file_path)
    assert "New text" in file_content
    assert "Old text" not in file_content

    note_content = await _get_note_content(session_maker, test_project.id, edited_entity.id)
    assert note_content is not None
    assert note_content.db_version == 2
    assert "New text" in note_content.markdown_content
    assert "Old text" not in note_content.markdown_content
    assert note_content.file_write_status == "synced"


@pytest.mark.asyncio
async def test_edit_entity_by_id_metadata_merges_frontmatter(
    client: AsyncClient,
    file_service,
    test_project: Project,
    v2_project_url,
    entity_repository,
    session_maker,
):
    """PATCH with `metadata` merges frontmatter fields independent of `operation` (issue #1011)."""
    create_data = {
        "title": "TestMetadataMerge",
        "directory": "test",
        "content": "---\nstatus: open\nowner: alice\n---\n# TestMetadataMerge\n\nBody content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 202
    created_entity = EntityResponseV2.model_validate(response.json())
    original_external_id = created_entity.external_id
    assert original_external_id is not None

    edit_data = {
        "operation": "append",
        "content": "\n\nResolution notes.",
        "metadata": {"status": "resolved", "closed_at": "2026-06-18T10:42:00Z"},
    }
    response = await client.patch(
        f"{v2_project_url}/knowledge/entities/{original_external_id}",
        json=edit_data,
    )

    assert response.status_code == 202
    edited_entity = EntityResponseV2.model_validate(response.json())
    assert edited_entity.external_id is not None

    file_path = file_service.get_entity_path(edited_entity)
    file_content, _ = await file_service.read_file(file_path)
    frontmatter = parse_frontmatter(file_content)

    # New key added, existing key overwritten, unrelated key and body preserved.
    assert frontmatter["closed_at"] == "2026-06-18T10:42:00Z"
    assert frontmatter["status"] == "resolved"
    assert frontmatter["owner"] == "alice"
    assert "Body content" in file_content
    assert "Resolution notes." in file_content

    note_content = await _get_note_content(session_maker, test_project.id, edited_entity.id)
    assert note_content is not None
    assert parse_frontmatter(note_content.markdown_content)["status"] == "resolved"


@pytest.mark.asyncio
async def test_delete_entity_by_id(
    client: AsyncClient,
    file_service,
    test_project: Project,
    v2_project_url,
    entity_repository,
    session_maker,
):
    """Test deleting an entity by external_id."""
    # Create an entity first
    create_data = {
        "title": "TestDelete",
        "directory": "test",
        "content": "Content to be deleted",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 202
    created_entity = EntityResponseV2.model_validate(response.json())

    # V2 create must return external_id
    assert created_entity.external_id is not None
    entity_external_id = created_entity.external_id

    # Delete it by external_id
    response = await client.delete(f"{v2_project_url}/knowledge/entities/{entity_external_id}")

    assert response.status_code == 202
    delete_response = DeleteEntitiesResponse.model_validate(response.json())
    assert delete_response.deleted is True

    # Verify it's gone - trying to get it should return 404
    response = await client.get(f"{v2_project_url}/knowledge/entities/{entity_external_id}")
    assert response.status_code == 404

    note_content = await _get_note_content(session_maker, test_project.id, created_entity.id)
    assert note_content is None


@pytest.mark.asyncio
async def test_delete_entity_by_id_not_found(client: AsyncClient, v2_project_url):
    """Test deleting a non-existent entity returns deleted=False (idempotent)."""
    # Use a UUID format that doesn't exist
    fake_uuid = "00000000-0000-0000-0000-000000000000"
    response = await client.delete(f"{v2_project_url}/knowledge/entities/{fake_uuid}")

    # Delete is idempotent - returns accepted with deleted=False
    assert response.status_code == 202
    delete_response = DeleteEntitiesResponse.model_validate(response.json())
    assert delete_response.deleted is False


@pytest.mark.asyncio
async def test_delete_entity_by_id_non_markdown_file_entity(
    client: AsyncClient,
    test_project: Project,
    project_config,
    v2_project_url,
    entity_repository,
    session_maker,
    search_repository,
    config_manager,
):
    """Regression test for #1033: single-entity DELETE must handle file-type entities.

    Non-markdown files are indexed as note_type="file" entities with no permalink and
    no note_content row. The per-entity delete endpoint used to 500 on them because it
    ran markdown-specific cleanup; it now shares the accepted-note delete path with
    delete-directory, removing entity and search rows by entity id.
    """
    del config_manager

    # Index a real non-markdown file through the local project indexer.
    csv_path = project_config.home / "regression1033.csv"
    csv_path.write_text("id,name\n1,alpha\n")

    index_result = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
    )
    assert index_result.enqueued_files == 1

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "regression1033.csv")
    assert entity is not None
    assert entity.note_type == "file"
    assert entity.permalink is None
    entity_id = entity.id
    entity_external_id = entity.external_id

    # The indexer writes a search row for file entities keyed by filename; assert it
    # exists up front so the post-delete check is not vacuous.
    rows_before = await search_repository.search(search_text="regression1033")
    assert any(row.entity_id == entity_id for row in rows_before)

    response = await client.delete(f"{v2_project_url}/knowledge/entities/{entity_external_id}")

    assert response.status_code == 202
    delete_response = DeleteEntitiesResponse.model_validate(response.json())
    assert delete_response.deleted is True

    # Entity row is gone.
    async with db.scoped_session(session_maker) as session:
        assert await entity_repository.get_by_file_path(session, "regression1033.csv") is None
        assert await entity_repository.get_by_external_id(session, entity_external_id) is None

    # Search index rows for the entity are gone.
    rows_after = await search_repository.search(search_text="regression1033")
    assert not any(row.entity_id == entity_id for row in rows_after)

    # The file is part of the delete contract too. Leaving it behind would let the
    # next project scan recreate the entity and make the API deletion temporary.
    assert not csv_path.exists()


@pytest.mark.asyncio
async def test_move_entity(
    client: AsyncClient,
    file_service,
    test_project: Project,
    v2_project_url,
    entity_repository,
    session_maker,
):
    """Test moving an entity to a new location."""
    # Create an entity first
    create_data = {
        "title": "TestMove",
        "directory": "test",
        "content": "Content to be moved",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=create_data)
    assert response.status_code == 202
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

    assert response.status_code == 202
    moved_entity = EntityResponseV2.model_validate(response.json())

    # V2 move must return external_id field
    assert moved_entity.external_id is not None
    assert isinstance(moved_entity.external_id, str)
    assert moved_entity.api_version == "v2"

    # external_id should remain the same (stable reference)
    assert moved_entity.external_id == original_external_id
    assert moved_entity.file_path == "moved/MovedEntity.md"

    note_content = await _get_note_content(session_maker, test_project.id, moved_entity.id)
    assert note_content is not None
    assert note_content.file_path == "moved/MovedEntity.md"
    assert note_content.db_version == 2
    assert note_content.file_write_status == "synced"


@pytest.mark.asyncio
async def test_move_entity_rejects_existing_unindexed_destination(
    client: AsyncClient, v2_project_url, project_config
):
    """A move onto a file that exists on disk but is not indexed returns 409.

    Regression for the #1002 review: local moves must apply the same storage guard
    as creates, or DB/search would point at an unchanged destination file while the
    source is left on disk to be reindexed as a duplicate.
    """
    create = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={"title": "MoveSource", "directory": "src", "content": "Source content"},
        params={"fast": False},
    )
    assert create.status_code == 202
    source_external_id = EntityResponseV2.model_validate(create.json()).external_id

    # An unindexed file already occupies the move destination.
    destination = project_config.home / "dest" / "Existing.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    original = "---\ntitle: Existing\ntype: note\n---\n\nPre-existing on-disk content\n"
    destination.write_text(original, encoding="utf-8")

    response = await client.put(
        f"{v2_project_url}/knowledge/entities/{source_external_id}/move",
        json={"destination_path": "dest/Existing.md"},
    )

    assert response.status_code == 409
    # The on-disk destination must be untouched (no overwrite, no DB/search pointing at it).
    assert destination.read_text(encoding="utf-8") == original


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
    assert response.status_code == 202
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
        assert response.status_code == 202

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
        assert response.status_code == 202

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
    # Runtime cleanup fields ride alongside the client schema in the raw payload.
    assert response.json()["file_delete_status"] == "pending"

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

    # Exact snapshot of the route JSON: the typed result must keep serializing
    # the existing directory-delete response contract byte-for-byte.
    assert response.json() == {
        "total_files": 0,
        "successful_deletes": 0,
        "failed_deletes": 0,
        "deleted_files": [],
        "errors": [],
        "file_delete_status": "complete",
    }


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
async def test_delete_directory_v2_refreshes_surviving_relation_sources(
    client: AsyncClient,
    v2_project_url,
    entity_repository,
    session_maker,
    search_service: SearchService,
):
    """A surviving note that linked into the deleted directory must have its stale
    search relation rows refreshed, not left dangling until an unrelated reindex."""
    from basic_memory.schemas.search import SearchItemType

    # Target inside the directory that will be deleted.
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Cleanup Target",
            "directory": "v2-relcleanup",
            "content": "# Cleanup Target",
        },
    )
    assert response.status_code == 202

    # Keeper outside the directory linking into it (resolved at write time).
    response = await client.post(
        f"{v2_project_url}/knowledge/entities",
        json={
            "title": "Cleanup Keeper",
            "directory": "v2-relkeep",
            "content": "# Cleanup Keeper\n\n- relates_to [[Cleanup Target]]",
        },
    )
    assert response.status_code == 202

    async with db.scoped_session(session_maker) as session:
        keeper = await entity_repository.get_by_file_path(session, "v2-relkeep/Cleanup Keeper.md")
        assert keeper is not None
        assert len(keeper.outgoing_relations) == 1
        relation_permalink = keeper.outgoing_relations[0].permalink
        assert relation_permalink is not None
        stale_rows_before = await search_service.repository.search(
            permalink=relation_permalink,
            search_item_types=[SearchItemType.RELATION],
            session=session,
        )
    assert stale_rows_before != []

    response = await client.post(
        f"{v2_project_url}/knowledge/delete-directory",
        json={"directory": "v2-relcleanup"},
    )
    assert response.status_code == 200

    async with db.scoped_session(session_maker) as session:
        stale_rows_after = await search_service.repository.search(
            permalink=relation_permalink,
            search_item_types=[SearchItemType.RELATION],
            session=session,
        )
    assert stale_rows_after == []


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
        assert response.status_code == 202

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
async def test_entity_response_includes_user_tracking_fields(client: AsyncClient, v2_project_url):
    """EntityResponseV2 includes created_by and last_updated_by fields (null for local)."""
    entity_data = {
        "title": "UserTrackingTest",
        "directory": "test",
        "content": "Test content",
    }
    response = await client.post(f"{v2_project_url}/knowledge/entities", json=entity_data)
    assert response.status_code == 202

    body = response.json()
    # Fields should be present in the response (null for local/CLI usage)
    assert "created_by" in body
    assert "last_updated_by" in body
    assert body["created_by"] is None
    assert body["last_updated_by"] is None


## Single-file indexing endpoint tests


@pytest.mark.asyncio
async def test_index_file_indexes_file_on_disk(
    client: AsyncClient, v2_project_url, test_project: Project
):
    """A markdown file written directly to disk becomes resolvable after index-file (#581)."""
    note_path = Path(test_project.path) / "incoming" / "disk-note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Disk Note\n\nWritten directly to disk.\n", encoding="utf-8")

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "incoming/disk-note.md"},
    )
    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())
    assert entity.file_path == "incoming/disk-note.md"

    # The file is now resolvable by path, which is what edit_note retries with
    resolve_response = await client.post(
        f"{v2_project_url}/knowledge/resolve",
        json={"identifier": "incoming/disk-note.md", "strict": True},
    )
    assert resolve_response.status_code == 200
    resolved = EntityResolveResponse.model_validate(resolve_response.json())
    assert resolved.external_id == entity.external_id


@pytest.mark.asyncio
async def test_index_file_uses_event_indexer_not_sync_service(
    client: AsyncClient,
    v2_project_url,
    test_project: Project,
):
    """index-file should use the new event-indexing primitive, not legacy SyncService."""
    note_path = Path(test_project.path) / "incoming" / "event-indexed-note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Event Indexed\n\nWritten directly to disk.\n", encoding="utf-8")

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "incoming/event-indexed-note.md"},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_index_file_already_indexed_is_idempotent(
    client: AsyncClient, v2_project_url, test_project: Project
):
    """index-file on an already indexed, unchanged file returns the existing entity."""
    entity_data = {
        "title": "AlreadyIndexed",
        "directory": "test",
        "content": "Already indexed content",
    }
    create_response = await client.post(f"{v2_project_url}/knowledge/entities", json=entity_data)
    assert create_response.status_code == 202
    created = EntityResponseV2.model_validate(create_response.json())

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": created.file_path},
    )
    assert response.status_code == 200
    synced = EntityResponseV2.model_validate(response.json())
    assert synced.external_id == created.external_id
    assert synced.file_path == created.file_path


@pytest.mark.asyncio
async def test_index_file_syncs_vectors_when_semantic_enabled(
    client: AsyncClient,
    v2_project_url,
    test_project: Project,
    app_config,
    monkeypatch: pytest.MonkeyPatch,
):
    """index-file refreshes semantic vectors for the indexed entity.

    Mirrors the inline sync_entity_vectors_batch() pass the project index flow runs
    after indexing changed files; without it, a note recovered via index-file
    stays missing from semantic search until a later edit or full project index.
    Fixtures run with semantic search disabled, so enable it here and stub
    the service-level vector batch (like test_search_service.py::test_reindex_vectors
    stubs the repository batch) to exercise the wiring without the embedding stack.
    """
    app_config.semantic_search_enabled = True

    synced_batches: list[list[int]] = []

    async def stub_sync_entity_vectors_batch(
        self, entity_ids: list[int], progress_callback=None
    ) -> VectorSyncBatchResult:
        synced_batches.append(list(entity_ids))
        return VectorSyncBatchResult(
            entities_total=len(entity_ids),
            entities_synced=len(entity_ids),
            entities_failed=0,
        )

    # The router builds its SearchService per request, so patch the class method
    # rather than a fixture instance.
    monkeypatch.setattr(SearchService, "sync_entity_vectors_batch", stub_sync_entity_vectors_batch)

    note_path = Path(test_project.path) / "incoming" / "semantic-note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Semantic Note\n\nNeeds vectors after recovery.\n", encoding="utf-8")

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "incoming/semantic-note.md"},
    )
    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())

    assert synced_batches == [[entity.id]]


@pytest.mark.asyncio
async def test_index_file_skips_vector_sync_when_semantic_disabled(
    client: AsyncClient,
    v2_project_url,
    test_project: Project,
    app_config,
    monkeypatch: pytest.MonkeyPatch,
):
    """index-file does not touch the vector pipeline when semantic search is disabled."""
    assert app_config.semantic_search_enabled is False

    synced_batches: list[list[int]] = []

    async def stub_sync_entity_vectors_batch(
        self, entity_ids: list[int], progress_callback=None
    ) -> VectorSyncBatchResult:
        synced_batches.append(list(entity_ids))
        return VectorSyncBatchResult(
            entities_total=len(entity_ids),
            entities_synced=len(entity_ids),
            entities_failed=0,
        )

    monkeypatch.setattr(SearchService, "sync_entity_vectors_batch", stub_sync_entity_vectors_batch)

    note_path = Path(test_project.path) / "incoming" / "plain-note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Plain Note\n\nNo vectors needed.\n", encoding="utf-8")

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "incoming/plain-note.md"},
    )
    assert response.status_code == 200
    assert synced_batches == []


@pytest.mark.asyncio
async def test_index_file_missing_file_returns_404(client: AsyncClient, v2_project_url):
    """index-file fails fast when the file does not exist on disk."""
    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "missing/never-written.md"},
    )
    assert response.status_code == 404
    assert "File not found on disk" in response.json()["detail"]


@pytest.mark.asyncio
async def test_index_file_rejects_path_traversal(client: AsyncClient, v2_project_url):
    """index-file rejects paths that escape the project root."""
    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "../outside-project.md"},
    )
    assert response.status_code == 400
    assert "project boundaries" in response.json()["detail"]


@pytest.mark.asyncio
async def test_index_file_rejects_symlink_escape(
    client: AsyncClient, v2_project_url, test_project: Project, entity_repository, session_maker
):
    """index-file rejects paths whose canonical target escapes the project via symlink.

    The exact-cased request ('link/secret.md') is rejected by the pre-canonicalization
    boundary check: the path exists, so resolve() follows the symlink and detects the
    escape. The wrong-cased request ('LINK/secret.md') is the regression case — on a
    case-sensitive filesystem that path does not exist, the pre-check resolves it
    lexically and passes; canonicalization then matches the real 'link' segment but
    stops at the project boundary and reports the path as not found (404). On
    case-insensitive filesystems the pre-check catches both with 400.
    """
    project_path = Path(test_project.path)
    outside_dir = project_path.parent / "index-file-outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "secret.md").write_text(
        "# Outside\n\nMust never be indexed.\n", encoding="utf-8"
    )
    (project_path / "link").symlink_to(outside_dir, target_is_directory=True)

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "link/secret.md"},
    )
    assert response.status_code == 400
    assert "project boundaries" in response.json()["detail"]

    # 400 (pre-check, case-insensitive FS) or 404 (canonicalization stops at the
    # boundary, case-sensitive FS) — either way the escape is rejected.
    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "LINK/secret.md"},
    )
    assert response.status_code in (400, 404)

    # Nothing outside the project root was indexed
    assert await _find_all_entities(entity_repository, session_maker) == []


@pytest.mark.asyncio
async def test_index_file_symlink_escape_never_scans_outside_directory(
    client: AsyncClient,
    v2_project_url,
    test_project: Project,
    entity_repository,
    session_maker,
    monkeypatch: pytest.MonkeyPatch,
):
    """Canonicalization never scans directories outside the project root.

    Rejecting the request is not enough: before this fix, the wrong-cased request
    ('LINK/secret.md') passed the pre-check on a case-sensitive filesystem, and
    canonicalization followed the escaping 'link' symlink with os.scandir before the
    post-canonicalization containment check fired — an information touch outside the
    boundary. We spy on os.scandir and assert the outside directory is never scanned.
    The spy is what makes this test meaningful on case-insensitive macOS too, where
    the request is already rejected by the pre-check: the assertion proves no layer
    scanned past the boundary either way.
    """
    project_path = Path(test_project.path)
    outside_dir = (project_path.parent / "index-file-outside-scan").resolve()
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "secret.md").write_text(
        "# Outside\n\nMust never be scanned.\n", encoding="utf-8"
    )
    (project_path / "link").symlink_to(outside_dir, target_is_directory=True)

    real_scandir = os.scandir
    scanned: list[Path] = []

    def recording_scandir(path=".", *args, **kwargs):
        # Record the resolved path so a scandir on the symlinked 'link' directory
        # shows up as the outside directory it actually reads.
        scanned.append(Path(path).resolve())
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr(os, "scandir", recording_scandir)

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "LINK/secret.md"},
    )
    assert response.status_code in (400, 404)
    assert outside_dir not in scanned

    assert await _find_all_entities(entity_repository, session_maker) == []


@pytest.mark.asyncio
async def test_index_file_symlink_escape_never_probes_outside_target(
    client: AsyncClient,
    v2_project_url,
    test_project: Project,
    entity_repository,
    session_maker,
    monkeypatch: pytest.MonkeyPatch,
):
    """The containment check runs before any filesystem probe that follows symlinks.

    Regression: a wrong-cased request ('SECRET.md') canonicalizes onto an in-project
    FILE symlink ('secret.md' -> outside target) on a case-sensitive filesystem.
    Before the fix, the endpoint's is_file() existence probe ran before the
    resolved-containment check, so it followed the symlink and stat'ed the target
    outside the project boundary (and the response flipped between 404 and 400
    depending on whether the external target existed). We spy on Path.is_file and
    assert no probe ever resolves to the outside target; the escape is always
    rejected with 400 — by the pre-check on case-insensitive filesystems, by the
    post-canonicalization containment check on case-sensitive ones.
    """
    project_path = Path(test_project.path)
    outside_dir = (project_path.parent / "index-file-outside-probe").resolve()
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_target = outside_dir / "secret.md"
    outside_target.write_text("# Outside\n\nMust never be probed.\n", encoding="utf-8")
    (project_path / "secret.md").symlink_to(outside_target)

    real_is_file = Path.is_file
    probed: list[Path] = []

    def recording_is_file(self, *args, **kwargs):
        # Record the resolved path so a probe on the in-project symlink name shows
        # up as the outside target it would actually stat.
        probed.append(self.resolve())
        return real_is_file(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", recording_is_file)

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "SECRET.md"},
    )
    assert response.status_code == 400
    assert "project boundaries" in response.json()["detail"]
    assert outside_target not in probed

    assert await _find_all_entities(entity_repository, session_maker) == []


def test_canonical_file_path_stops_at_project_boundary(tmp_path: Path):
    """_canonical_file_path bails before descending past the resolved project home.

    Exercised directly (not via the endpoint) so the boundary bail is covered on
    case-insensitive filesystems too, where the endpoint pre-check rejects the
    request before canonicalization runs.
    """
    home = tmp_path / "project"
    home.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("# Outside\n", encoding="utf-8")
    (home / "link").symlink_to(outside_dir, target_is_directory=True)

    assert _canonical_file_path(home, ["link", "secret.md"]) is None

    # Symlinks that stay inside the project keep canonicalizing as before.
    real_dir = home / "real"
    real_dir.mkdir()
    (real_dir / "inside.md").write_text("# Inside\n", encoding="utf-8")
    (home / "alias").symlink_to(real_dir, target_is_directory=True)

    assert _canonical_file_path(home, ["alias", "inside.md"]) == "alias/inside.md"


@pytest.mark.asyncio
async def test_index_file_symlink_inside_project_still_indexes(
    client: AsyncClient, v2_project_url, test_project: Project
):
    """A symlinked directory that stays inside the project is still accepted.

    Pre-existing behavior we preserve: the containment check follows the symlink,
    sees the resolved target inside the project root, and indexes the entity under
    the requested (symlinked) path — only escapes outside the root are rejected.
    """
    project_path = Path(test_project.path)
    real_dir = project_path / "real"
    real_dir.mkdir(parents=True, exist_ok=True)
    (real_dir / "inside.md").write_text("# Inside\n\nReachable via alias.\n", encoding="utf-8")
    (project_path / "alias").symlink_to(real_dir, target_is_directory=True)

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "alias/inside.md"},
    )
    assert response.status_code == 200
    entity = EntityResponseV2.model_validate(response.json())
    assert entity.file_path == "alias/inside.md"


@pytest.mark.asyncio
async def test_index_file_wrong_cased_path_does_not_create_duplicate(
    client: AsyncClient, v2_project_url, test_project: Project, entity_repository, session_maker
):
    """A wrong-cased path resolves to the canonical on-disk file without duplicating it.

    On case-insensitive filesystems (macOS/Windows) a wrong-cased path passes existence
    checks; without canonicalization the indexer would insert a second entity keyed by
    the wrong-cased path. The endpoint matches real directory entries, so the request
    behaves identically on case-sensitive and case-insensitive filesystems.
    """
    note_path = Path(test_project.path) / "notes" / "disk-note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Disk Note\n\nWritten directly to disk.\n", encoding="utf-8")

    first = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "notes/disk-note.md"},
    )
    assert first.status_code == 200
    canonical = EntityResponseV2.model_validate(first.json())
    assert canonical.file_path == "notes/disk-note.md"

    second = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "notes/Disk-Note.md"},
    )
    assert second.status_code == 200
    synced = EntityResponseV2.model_validate(second.json())
    assert synced.file_path == "notes/disk-note.md"
    assert synced.external_id == canonical.external_id

    entities = await _find_all_entities(entity_repository, session_maker)
    assert [entity.file_path for entity in entities] == ["notes/disk-note.md"]


@pytest.mark.asyncio
async def test_index_file_rejects_non_normalized_segments(
    client: AsyncClient, v2_project_url, test_project: Project
):
    """index-file rejects './' and '//' style segments instead of indexing them verbatim."""
    note_path = Path(test_project.path) / "notes" / "disk-note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Disk Note\n", encoding="utf-8")

    for non_normalized in ("./notes/disk-note.md", "notes//disk-note.md"):
        response = await client.post(
            f"{v2_project_url}/knowledge/index-file",
            json={"file_path": non_normalized},
        )
        assert response.status_code == 400
        assert "not normalized" in response.json()["detail"]


@pytest.mark.asyncio
async def test_index_file_directory_returns_404(
    client: AsyncClient, v2_project_url, test_project: Project
):
    """index-file refuses a path that canonicalizes to a directory instead of a file."""
    (Path(test_project.path) / "just-a-directory").mkdir(parents=True, exist_ok=True)

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "just-a-directory"},
    )
    assert response.status_code == 404
    assert "File not found on disk" in response.json()["detail"]


@pytest.mark.asyncio
async def test_index_file_path_through_file_returns_404(
    client: AsyncClient, v2_project_url, test_project: Project
):
    """index-file fails fast when a parent segment resolves to a file, not a directory."""
    note_path = Path(test_project.path) / "notes" / "disk-note.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Disk Note\n", encoding="utf-8")

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "notes/disk-note.md/child.md"},
    )
    assert response.status_code == 404
    assert "File not found on disk" in response.json()["detail"]


@pytest.mark.asyncio
async def test_index_file_rejects_hidden_file(
    client: AsyncClient, v2_project_url, test_project: Project
):
    """index-file refuses hidden files, matching the default '.*' ignore pattern."""
    hidden_path = Path(test_project.path) / ".secrets.md"
    hidden_path.write_text("# Hidden\n\nShould never be indexed.\n", encoding="utf-8")

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": ".secrets.md"},
    )
    assert response.status_code == 400
    assert "ignore rules" in response.json()["detail"]


@pytest.mark.asyncio
async def test_index_file_rejects_gitignored_file(
    client: AsyncClient, v2_project_url, test_project: Project
):
    """index-file honors the project .gitignore, matching scan/watch filtering."""
    project_path = Path(test_project.path)
    (project_path / ".gitignore").write_text("private/\n", encoding="utf-8")
    note_path = project_path / "private" / "secret.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Secret\n\nGitignored content.\n", encoding="utf-8")

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "private/secret.md"},
    )
    assert response.status_code == 400
    assert "ignore rules" in response.json()["detail"]


@pytest.mark.asyncio
async def test_index_file_rejects_bmignored_file(
    client: AsyncClient, v2_project_url, test_project: Project
):
    """index-file honors user .bmignore patterns, matching scan/watch filtering."""
    bmignore_path = get_bmignore_path()
    bmignore_path.parent.mkdir(parents=True, exist_ok=True)
    bmignore_path.write_text("drafts-wip\n", encoding="utf-8")

    note_path = Path(test_project.path) / "drafts-wip" / "scratch.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Scratch\n\nBmignored content.\n", encoding="utf-8")

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "drafts-wip/scratch.md"},
    )
    assert response.status_code == 400
    assert "ignore rules" in response.json()["detail"]


@pytest.mark.asyncio
async def test_index_file_rejects_non_markdown(
    client: AsyncClient, v2_project_url, test_project: Project
):
    """index-file only indexes markdown notes."""
    file_path = Path(test_project.path) / "data" / "records.csv"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("a,b,c\n", encoding="utf-8")

    response = await client.post(
        f"{v2_project_url}/knowledge/index-file",
        json={"file_path": "data/records.csv"},
    )
    assert response.status_code == 400
    assert "Only markdown files" in response.json()["detail"]
