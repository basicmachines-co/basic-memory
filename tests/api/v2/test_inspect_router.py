"""API contract tests for note-level retrieval inspection."""

from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from basic_memory import db
from basic_memory.deps.services import get_search_service_v2_external
from basic_memory.models import Project
from basic_memory.repository.semantic_errors import (
    RerankProviderContractError,
    RerankTransientError,
)
from basic_memory.repository.semantic_chunking import (
    build_entity_fingerprint,
    build_vector_chunk_records,
)
from basic_memory.repository.search_trace import (
    FinalResultEntry,
    HybridQueryTrace,
    HydrationDropped,
    ManifestReadiness,
    QueryMeta,
    RerankerConfigSummary,
    build_fts_page_stage,
    build_fusion_stage,
    build_vector_stage,
)
from basic_memory.schemas.inspect import (
    InspectChunksResponse,
    InspectQueryRequest,
    InspectQueryResponse,
    InspectRowsBehindFileDetail,
)
from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode

inspect_router_module = import_module("basic_memory.api.v2.routers.inspect_router")


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


@pytest.mark.asyncio
async def test_inspect_query_returns_schema_for_seeded_fts_corpus(
    client: AsyncClient,
    v2_project_url: str,
    test_project: Project,
    entity_repository,
    search_service,
    file_service,
):
    entity = await _create_indexed_entity(
        test_project=test_project,
        title="API Query Inspection",
        file_name="api-query-inspection.md",
        entity_repository=entity_repository,
        search_service=search_service,
        file_service=file_service,
    )

    response = await client.post(
        f"{v2_project_url}/inspect/query",
        json={
            "query": {"text": "inspection", "retrieval_mode": "fts"},
            "limit": 5,
            "offset": 0,
        },
    )

    assert response.status_code == 200, response.text
    inspection = InspectQueryResponse.model_validate(response.json())
    assert inspection.query == 'text="inspection"'
    assert inspection.retrieval_mode.value == "fts"
    assert inspection.window.limit == 5
    assert inspection.candidates[0].permalink == entity.permalink
    assert inspection.candidates[0].external_id == str(entity.external_id)
    assert inspection.candidates[0].disposition == "returned"
    assert [stage.name for stage in inspection.stages] == ["fts"]


@pytest.mark.asyncio
async def test_inspect_query_batch_enriches_all_known_external_ids(monkeypatch):
    trace = HybridQueryTrace(
        meta=QueryMeta(
            query_text="inspection",
            retrieval_mode="hybrid",
            limit=5,
            offset=0,
            project_id=1,
            candidate_limit=10,
            rerank_pool_size=0,
            embedding_model="embedding",
            vector_index="index",
            fusion_formula_version="max+0.3*min/v1",
            min_similarity=0.2,
            min_similarity_source="config",
            reranker=RerankerConfigSummary(enabled=False, model=None, candidates=20),
            rerank_applied=False,
            rerank_skipped_reason="disabled",
            total_ms=1.0,
        ),
        readiness=ManifestReadiness("index", "embedding", 0, 0, 0),
        fts=build_fts_page_stage(
            [(("entity", 1), -1.0), (("entity", 3), -0.7)],
            normalized_scores={("entity", 1): 1.0, ("entity", 3): 0.7},
            entity_ids={("entity", 1): 1, ("entity", 3): 3},
            fts_max_abs=1.0,
            relaxed_fallback_used=False,
        ),
        vector=build_vector_stage(
            candidate_limit=10,
            adapter_match_count=1,
            hydrated_count=0,
            drops=(
                HydrationDropped(
                    entity_id=2,
                    chunk_key="entity:2:0",
                    similarity=0.8,
                    reason="not_in_manifest",
                    stored_model=None,
                    stored_index=None,
                ),
            ),
        ),
        fusion=build_fusion_stage(
            formula_version="max+0.3*min/v1",
            bonus=0.3,
            fts_scores={("entity", 1): 1.0, ("entity", 3): 0.7},
            fts_ranks={("entity", 1): 0, ("entity", 3): 1},
            vector_scores={},
            vector_ranks={},
            ranked_scores=[(("entity", 1), 1.0), (("entity", 3), 0.7)],
            fusion_ms=0.1,
        ),
        rerank=None,
        final=(
            FinalResultEntry(
                key=("entity", 1),
                entity_id=1,
                title="One",
                permalink="one",
                file_path="one.md",
                final_rank=1,
                final_score=0.9,
            ),
        ),
    )

    async def fake_explain_query(*_args, **_kwargs):
        return trace

    entity_service = MagicMock()
    entity_service.get_entities_by_id = AsyncMock(
        return_value=[
            SimpleNamespace(id=1, external_id="external-1"),
            SimpleNamespace(id=2, external_id="external-2"),
            SimpleNamespace(id=3, external_id="external-3"),
        ]
    )
    monkeypatch.setattr(inspect_router_module, "explain_query", fake_explain_query)

    inspection = await inspect_router_module.inspect_query(
        data=InspectQueryRequest(
            query=SearchQuery(text="inspection", retrieval_mode=SearchRetrievalMode.HYBRID)
        ),
        project_id=1,
        entity_service=entity_service,
        search_service=MagicMock(),
    )

    assert {candidate.external_id for candidate in inspection.candidates} == {
        "external-1",
        "external-2",
        "external-3",
    }
    fts_only_miss = next(candidate for candidate in inspection.candidates if candidate.id == 3)
    assert fts_only_miss.disposition == "beyond_page_window"
    entity_service.get_entities_by_id.assert_awaited_once_with([1, 2, 3])


@pytest.mark.asyncio
async def test_inspect_query_maps_semantic_disabled_to_400(
    client: AsyncClient,
    v2_project_url: str,
):
    response = await client.post(
        f"{v2_project_url}/inspect/query",
        json={"query": {"text": "inspection", "retrieval_mode": "vector"}},
    )

    assert response.status_code == 400
    assert "Semantic search is disabled" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (RerankTransientError("reranker unavailable"), 503),
        (RerankProviderContractError("malformed reranker response"), 502),
    ],
)
async def test_inspect_query_maps_reranker_errors(
    client: AsyncClient,
    v2_project_url: str,
    app,
    error: Exception,
    status_code: int,
):
    class RaisingSearchService:
        async def search(self, *args, **kwargs):
            raise error

    app.dependency_overrides[get_search_service_v2_external] = lambda: RaisingSearchService()
    try:
        response = await client.post(
            f"{v2_project_url}/inspect/query",
            json={"query": {"text": "inspection", "retrieval_mode": "hybrid"}},
        )
    finally:
        app.dependency_overrides.pop(get_search_service_v2_external, None)

    assert response.status_code == status_code
    assert response.json()["detail"] == str(error)
