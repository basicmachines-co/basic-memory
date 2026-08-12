"""API contract tests for note-level retrieval inspection."""

from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from basic_memory import db
from basic_memory.models import Project
from basic_memory.repository.semantic_chunking import (
    build_entity_fingerprint,
    build_vector_chunk_records,
)
from basic_memory.schemas.inspect import InspectChunksResponse, InspectRowsBehindFileDetail


async def _create_indexed_entity(
    *,
    test_project: Project,
    title: str,
    file_name: str,
    entity_repository,
    search_service,
    file_service,
):
    content = f"# {title}\n\nRetrieval inspection content for {title}."
    file_path = Path(test_project.path) / file_name
    checksum = await file_service.write_file(file_path, content)
    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.create(
            session,
            {
                "title": title,
                "note_type": "note",
                "content_type": "text/markdown",
                "file_path": file_name,
                "permalink": f"notes/{file_name.removesuffix('.md')}",
                "checksum": checksum,
            },
        )
    await search_service.index_entity(entity)
    return entity


async def _seed_current_manifest(search_repository, session_maker, entity_id: int) -> int:
    rows = await search_repository.get_entity_search_rows(entity_id)
    records = build_vector_chunk_records(rows).records
    fingerprint = build_entity_fingerprint(records)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "INSERT INTO search_vector_chunks ("
                "project_id, entity_id, chunk_key, chunk_text, source_hash, "
                "entity_fingerprint, embedding_model, vector_index, embedding_status"
                ") VALUES ("
                ":project_id, :entity_id, :chunk_key, :chunk_text, :source_hash, "
                ":entity_fingerprint, :embedding_model, :vector_index, 'ready')"
            ),
            [
                {
                    "project_id": search_repository.project_id,
                    "entity_id": entity_id,
                    "chunk_key": record["chunk_key"],
                    "chunk_text": record["chunk_text"],
                    "source_hash": record["source_hash"],
                    "entity_fingerprint": fingerprint,
                    "embedding_model": search_repository.configured_embedding_model,
                    "vector_index": search_repository.configured_vector_index,
                }
                for record in records
            ],
        )
        await session.commit()
    return len(records)


@pytest.mark.asyncio
async def test_inspect_chunks_returns_valid_schema_for_seeded_corpus(
    client: AsyncClient,
    v2_project_url: str,
    test_project: Project,
    entity_repository,
    search_repository,
    search_service,
    file_service,
    session_maker,
):
    entity = await _create_indexed_entity(
        test_project=test_project,
        title="API Chunk Inspection",
        file_name="api-chunk-inspection.md",
        entity_repository=entity_repository,
        search_service=search_service,
        file_service=file_service,
    )
    chunk_count = await _seed_current_manifest(search_repository, session_maker, entity.id)

    response = await client.post(
        f"{v2_project_url}/inspect/chunks",
        json={"identifier": entity.permalink},
    )

    assert response.status_code == 200, response.text
    inspection = InspectChunksResponse.model_validate(response.json())
    assert inspection.entity_id == entity.id
    assert inspection.external_id == entity.external_id
    assert inspection.entity_checksum == entity.checksum
    assert inspection.readiness.total == chunk_count
    assert inspection.readiness.ready == chunk_count
    assert inspection.stale is False
    assert inspection.freshness == "fresh"
    assert inspection.freshness_detail is None
    assert [row.type for row in inspection.rows] == ["entity"]
    assert sum(len(row.chunks) for row in inspection.rows) == chunk_count
    assert inspection.detached == []


@pytest.mark.asyncio
async def test_inspect_chunks_displays_manifest_rows_with_missing_sources_as_detached(
    client: AsyncClient,
    v2_project_url: str,
    test_project: Project,
    entity_repository,
    search_repository,
    search_service,
    file_service,
    session_maker,
):
    entity = await _create_indexed_entity(
        test_project=test_project,
        title="Detached Chunk Inspection",
        file_name="detached-chunk-inspection.md",
        entity_repository=entity_repository,
        search_service=search_service,
        file_service=file_service,
    )
    chunk_count = await _seed_current_manifest(search_repository, session_maker, entity.id)
    async with db.scoped_session(session_maker) as session:
        await session.execute(
            text(
                "DELETE FROM search_index WHERE project_id = :project_id "
                "AND type = 'entity' AND id = :entity_id"
            ),
            {"project_id": test_project.id, "entity_id": entity.id},
        )
        await session.commit()

    response = await client.post(
        f"{v2_project_url}/inspect/chunks",
        json={"identifier": entity.external_id},
    )

    assert response.status_code == 200, response.text
    inspection = InspectChunksResponse.model_validate(response.json())
    assert inspection.rows == []
    assert [(row.type, row.id, row.source_row_gone) for row in inspection.detached] == [
        ("entity", entity.id, True)
    ]
    assert sum(len(row.chunks) for row in inspection.detached) == chunk_count
    assert inspection.readiness.total == chunk_count


@pytest.mark.asyncio
async def test_inspect_chunks_reports_rows_behind_file_with_checksum_detail(
    client: AsyncClient,
    v2_project_url: str,
    test_project: Project,
    entity_repository,
    search_service,
    file_service,
):
    entity = await _create_indexed_entity(
        test_project=test_project,
        title="Rows Behind File",
        file_name="rows-behind-file.md",
        entity_repository=entity_repository,
        search_service=search_service,
        file_service=file_service,
    )
    file_service.get_entity_path(entity).write_bytes(b"# Edited outside the index\n")

    response = await client.post(
        f"{v2_project_url}/inspect/chunks",
        json={"identifier": entity.external_id},
    )

    assert response.status_code == 200, response.text
    inspection = InspectChunksResponse.model_validate(response.json())
    assert inspection.freshness == "rows_behind_file"
    assert isinstance(inspection.freshness_detail, InspectRowsBehindFileDetail)
    assert inspection.freshness_detail.entity_checksum == entity.checksum
    assert inspection.freshness_detail.current_file_checksum != entity.checksum


@pytest.mark.asyncio
async def test_inspect_chunks_returns_404_for_unresolved_identifier(
    client: AsyncClient,
    v2_project_url: str,
):
    response = await client.post(
        f"{v2_project_url}/inspect/chunks",
        json={"identifier": "notes/does-not-exist"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Entity not found: 'notes/does-not-exist'"


@pytest.mark.asyncio
async def test_inspect_chunks_semantic_disabled_returns_rows_only(
    client: AsyncClient,
    v2_project_url: str,
    test_project: Project,
    app_config,
    entity_repository,
    search_service,
    file_service,
):
    assert app_config.semantic_search_enabled is False
    entity = await _create_indexed_entity(
        test_project=test_project,
        title="Rows Only Inspection",
        file_name="rows-only-inspection.md",
        entity_repository=entity_repository,
        search_service=search_service,
        file_service=file_service,
    )
    async with db.scoped_session(search_service.session_maker) as session:
        await session.execute(text("DROP TABLE search_vector_chunks"))
        await session.commit()

    response = await client.post(
        f"{v2_project_url}/inspect/chunks",
        json={"identifier": entity.external_id},
    )

    assert response.status_code == 200, response.text
    inspection = InspectChunksResponse.model_validate(response.json())
    assert inspection.readiness.model_dump() == {
        "total": 0,
        "ready": 0,
        "pending": 0,
        "stale": 0,
        "orphaned": 0,
        "missing": 0,
    }
    assert inspection.rows
    assert all(not row.chunks for row in inspection.rows)
    assert inspection.detached == []
    assert inspection.entity_fingerprint_indexed is None
    assert inspection.stale is False
