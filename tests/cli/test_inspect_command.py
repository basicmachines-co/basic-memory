"""CLI tests for ``bm inspect chunks``."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError
from pydantic import ValidationError
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app
import basic_memory.cli.commands.inspect as inspect_command
from basic_memory.schemas.inspect import (
    ChunkStatus,
    InspectChunk,
    InspectChunkReadiness,
    InspectChunksResponse,
    InspectDetachedSearchRow,
    InspectFreshness,
    InspectIndexBehindRowsDetail,
    InspectMatchedChunk,
    InspectQueryCandidate,
    InspectQueryEngine,
    InspectQueryRejectionDetail,
    InspectQueryResponse,
    InspectQueryReranker,
    InspectQueryScores,
    InspectQueryStage,
    InspectQueryTimings,
    InspectQueryWindow,
    InspectRowsBehindFileDetail,
    InspectSearchRow,
)
from basic_memory.schemas.search import SearchQuery, SearchRetrievalMode

runner = CliRunner()


def _inspection_response() -> InspectChunksResponse:
    updated_at = datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc)
    statuses: tuple[ChunkStatus, ...] = ("ready", "pending", "stale", "orphaned")
    chunks = [
        InspectChunk(
            chunk_key=f"entity:7:{ordinal}",
            ordinal=ordinal,
            text=("Chunk text " * (ordinal + 1)).strip(),
            source_hash=f"source-{ordinal}",
            embedding_model="FastEmbedEmbeddingProvider:test-model:384",
            vector_index="sqlite-vec",
            status=status,
            updated_at=updated_at,
        )
        for ordinal, status in enumerate(statuses)
    ]
    return InspectChunksResponse(
        entity_id=7,
        external_id="11111111-1111-1111-1111-111111111111",
        permalink="notes/retrieval-inspection",
        file_path="notes/Retrieval Inspection.md",
        title="Retrieval [Inspection]",
        entity_checksum="checksum-7",
        configured_embedding_model="FastEmbedEmbeddingProvider:test-model:384",
        configured_vector_index="sqlite-vec",
        readiness=InspectChunkReadiness(
            total=4,
            ready=1,
            pending=1,
            stale=1,
            orphaned=1,
            missing=2,
        ),
        entity_fingerprint_indexed="old-fingerprint",
        entity_fingerprint_current="current-fingerprint",
        stale=True,
        freshness="index_behind_rows",
        freshness_detail=InspectIndexBehindRowsDetail(
            entity_fingerprint_indexed="old-fingerprint",
            entity_fingerprint_current="current-fingerprint",
            missing_chunk_count=2,
        ),
        rows=[
            InspectSearchRow(
                type="entity",
                id=7,
                title="Retrieval [Inspection]",
                category=None,
                relation_type=None,
                content_preview="Entity content",
                chunks=chunks[:-1],
            ),
            InspectSearchRow(
                type="observation",
                id=8,
                title="Retrieval [Inspection]",
                category="fact",
                relation_type=None,
                content_preview="Observation content",
                chunks=[],
            ),
            InspectSearchRow(
                type="relation",
                id=9,
                title="Retrieval [Inspection]",
                category=None,
                relation_type="supports",
                content_preview="Relation content",
                chunks=[],
            ),
        ],
        detached=[
            InspectDetachedSearchRow(
                type="relation",
                id=10,
                chunks=[chunks[-1]],
            )
        ],
    )


def _rows_only_response() -> InspectChunksResponse:
    response = _inspection_response()
    return response.model_copy(
        update={
            "readiness": InspectChunkReadiness(
                total=0,
                ready=0,
                pending=0,
                stale=0,
                orphaned=0,
                missing=0,
            ),
            "entity_fingerprint_indexed": None,
            "stale": False,
            "freshness": "fresh",
            "freshness_detail": None,
            "rows": [row.model_copy(update={"chunks": []}) for row in response.rows],
            "detached": [],
        }
    )


def _response_with_freshness(freshness: InspectFreshness) -> InspectChunksResponse:
    """Build a schema-valid CLI fixture for every closed freshness state."""
    response = _inspection_response()
    if freshness == "fresh":
        detail = None
    elif freshness == "index_behind_rows":
        detail = InspectIndexBehindRowsDetail(
            entity_fingerprint_indexed="old-fingerprint",
            entity_fingerprint_current="current-fingerprint",
            missing_chunk_count=2,
        )
    else:
        detail = InspectRowsBehindFileDetail(
            entity_checksum="entity-checksum",
            current_file_checksum=(
                "current-file-checksum" if freshness == "rows_behind_file" else None
            ),
            db_checksum="db-checksum",
            file_checksum="lineage-file-checksum",
            file_write_status="external_change_detected",
        )
    return InspectChunksResponse.model_validate(
        {
            **response.model_dump(),
            "freshness": freshness,
            "freshness_detail": detail,
        }
    )


def _query_response(
    retrieval_mode: SearchRetrievalMode = SearchRetrievalMode.HYBRID,
) -> InspectQueryResponse:
    return InspectQueryResponse(
        query="auth retrieval",
        retrieval_mode=retrieval_mode,
        project_id=7,
        window=InspectQueryWindow(
            limit=10,
            offset=0,
            candidate_limit=100,
            rerank_pool=2,
        ),
        engine=InspectQueryEngine(
            embedding_model="TraceEmbedding:model:4",
            vector_index="sqlite-vec",
            ready_rows=12,
            pending_rows=2,
            other_identity_rows=3,
            fusion_formula="max+0.3*min/v1",
            min_similarity=0.2,
            min_similarity_source="config",
            reranker=InspectQueryReranker(
                enabled=True,
                model="trace-reranker",
                candidates=20,
                applied=True,
                skipped_reason=None,
            ),
        ),
        stages=[
            InspectQueryStage(
                name="fts",
                count_in=3,
                count_out=3,
                dropped=0,
                ms=0.8,
                relaxed_fallback_used=True,
            ),
            InspectQueryStage(name="embedding", count_in=1, count_out=1, dropped=0, ms=1.2),
            InspectQueryStage(name="vector", count_in=5, count_out=2, dropped=3, ms=2.3),
            InspectQueryStage(name="fusion", count_in=3, count_out=3, dropped=0, ms=0.4),
            InspectQueryStage(name="rerank", count_in=2, count_out=2, dropped=0, ms=4.5),
        ],
        candidates=[
            InspectQueryCandidate(
                type="entity",
                id=1,
                external_id="11111111-1111-1111-1111-111111111111",
                title="Authentication Guide",
                permalink="notes/authentication-guide",
                file_path="Authentication Guide.md",
                disposition="returned",
                rejection_detail=None,
                matched_chunks=[InspectMatchedChunk(chunk_key="entity:1:0", similarity=0.9)],
                dropped_chunks=[],
                scores=InspectQueryScores(
                    vector_similarity=0.9,
                    vector_rank=2,
                    fused_score=1.1,
                    fused_rank=2,
                    pre_rerank_rank=2,
                    pre_rerank_score=1.1,
                    rerank_score=0.95,
                    post_rerank_rank=1,
                    final_rank=1,
                    final_score=0.95,
                ),
            ),
            InspectQueryCandidate(
                type="entity",
                id=2,
                external_id=None,
                title=None,
                permalink=None,
                file_path=None,
                disposition="below_threshold",
                rejection_detail=InspectQueryRejectionDetail(
                    reason="below_threshold",
                    similarity=0.1,
                    threshold=0.2,
                ),
                matched_chunks=[InspectMatchedChunk(chunk_key="entity:2:0", similarity=0.1)],
                dropped_chunks=[],
                scores=InspectQueryScores(vector_similarity=0.1, vector_rank=5),
            ),
        ],
        timings_ms=InspectQueryTimings(
            total=8.4,
            embedding=1.2,
            vector_query=2.3,
            fts=0.5,
            fusion=0.4,
            rerank=4.5,
        ),
    )


@patch("basic_memory.cli.commands.tool._use_rich", return_value=True)
@patch("basic_memory.cli.commands.inspect.run_inspect_chunks", new_callable=AsyncMock)
def test_inspect_chunks_rich_rendering(mock_run, _mock_use_rich):
    mock_run.return_value = _inspection_response()

    result = runner.invoke(cli_app, ["inspect", "chunks", "notes/retrieval-inspection"])

    assert result.exit_code == 0, result.output
    assert "Retrieval [Inspection]" in result.output
    assert "1 ready, 1 pending, 1 stale, 1 orphaned, 2 missing" in result.output
    assert "entity:7" in result.output
    assert "category=fact" in result.output
    assert "relation=supports" in result.output
    assert "relation:10 · source row gone" in result.output
    assert all(status in result.output for status in ("ready", "pending", "stale", "orphaned"))
    assert "Freshness: index_behind_rows" in result.output


@patch("basic_memory.cli.commands.inspect.run_inspect_chunks", new_callable=AsyncMock)
def test_inspect_chunks_plain_rendering(mock_run):
    mock_run.return_value = _inspection_response()

    result = runner.invoke(
        cli_app,
        ["inspect", "chunks", "notes/retrieval-inspection", "--plain"],
    )

    assert result.exit_code == 0, result.output
    assert "Engine: sqlite-vec / FastEmbedEmbeddingProvider:test-model:384" in result.output
    assert "Fingerprint match: no" in result.output
    assert "Freshness: index_behind_rows" in result.output
    assert "Indexed fingerprint: old-fingerprint" in result.output
    assert "0  ready" in result.output
    assert "relation:10 · source row gone" in result.output
    assert "─" not in result.output
    assert "│" not in result.output


@patch("basic_memory.cli.commands.inspect.run_inspect_chunks", new_callable=AsyncMock)
def test_inspect_chunks_json_is_pydantic_schema_locked(mock_run):
    """Machine output is exactly the API response schema serialized by Pydantic."""
    expected = _inspection_response()
    mock_run.return_value = expected

    result = runner.invoke(
        cli_app,
        [
            "inspect",
            "chunks",
            "notes/retrieval-inspection",
            "--json",
            "--project",
            "research",
            "--project-id",
            "22222222-2222-2222-2222-222222222222",
        ],
    )

    assert result.exit_code == 0, result.output
    validated = InspectChunksResponse.model_validate_json(result.output)
    assert validated == expected
    mock_run.assert_awaited_once_with(
        "notes/retrieval-inspection",
        project="research",
        project_id="22222222-2222-2222-2222-222222222222",
    )


@patch("basic_memory.cli.commands.inspect.run_inspect_chunks", new_callable=AsyncMock)
def test_inspect_chunks_piped_output_defaults_to_json(mock_run):
    expected = _inspection_response()
    mock_run.return_value = expected

    result = runner.invoke(cli_app, ["inspect", "chunks", "notes/retrieval-inspection"])

    assert result.exit_code == 0, result.output
    assert InspectChunksResponse.model_validate_json(result.output) == expected


@patch("basic_memory.cli.commands.inspect.run_inspect_chunks", new_callable=AsyncMock)
def test_inspect_chunks_rows_only_note_does_not_error(mock_run):
    mock_run.return_value = _rows_only_response()

    result = runner.invoke(
        cli_app,
        ["inspect", "chunks", "notes/retrieval-inspection", "--plain"],
    )

    assert result.exit_code == 0, result.output
    assert "showing search rows only" in result.output
    assert "Semantic search may be disabled" in result.output
    assert "(no chunks)" in result.output
    assert "Fingerprint match: not indexed" in result.output
    assert "Freshness: fresh" in result.output


@pytest.mark.parametrize(
    ("freshness", "expected_detail"),
    [
        ("fresh", None),
        ("index_behind_rows", "Indexed fingerprint: old-fingerprint"),
        ("rows_behind_file", "Current file checksum: current-file-checksum"),
        ("unknown", "Current file checksum: -"),
    ],
)
@patch("basic_memory.cli.commands.inspect.run_inspect_chunks", new_callable=AsyncMock)
def test_inspect_chunks_plain_renders_every_freshness_value(
    mock_run,
    freshness: InspectFreshness,
    expected_detail: str | None,
):
    mock_run.return_value = _response_with_freshness(freshness)

    result = runner.invoke(cli_app, ["inspect", "chunks", "note", "--plain"])

    assert result.exit_code == 0, result.output
    assert f"Freshness: {freshness}" in result.output
    if expected_detail is not None:
        assert expected_detail in result.output


@pytest.mark.parametrize(
    ("freshness", "style"),
    [
        ("fresh", "green"),
        ("index_behind_rows", "yellow"),
        ("rows_behind_file", "red"),
        ("unknown", "dim"),
    ],
)
def test_rich_freshness_uses_diagnostic_colors(
    freshness: InspectFreshness,
    style: str,
):
    assert inspect_command._rich_freshness(freshness).style == style


def test_inspect_chunks_schema_rejects_fresh_with_divergence_detail():
    payload = _inspection_response().model_dump()
    payload["freshness"] = "fresh"

    with pytest.raises(ValidationError, match="Invalid detail for freshness=fresh"):
        InspectChunksResponse.model_validate(payload)


def test_inspect_chunks_rejects_mutually_exclusive_output_flags():
    result = runner.invoke(
        cli_app,
        ["inspect", "chunks", "note", "--json", "--plain"],
    )

    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_inspect_chunks_rejects_mutually_exclusive_routing_flags():
    result = runner.invoke(
        cli_app,
        ["inspect", "chunks", "note", "--local", "--cloud"],
    )

    assert result.exit_code == 1
    assert "Cannot specify both --local and --cloud" in result.output


@patch("basic_memory.cli.commands.inspect.run_inspect_chunks", new_callable=AsyncMock)
def test_inspect_chunks_api_error_exits_nonzero(mock_run):
    mock_run.side_effect = ToolError("Entity not found")

    result = runner.invoke(cli_app, ["inspect", "chunks", "missing", "--plain"])

    assert result.exit_code == 1
    assert "Error: Entity not found" in result.output


@pytest.mark.asyncio
async def test_run_inspect_chunks_uses_typed_client_and_project_route(monkeypatch):
    expected = _inspection_response()
    http_client = MagicMock()
    active_project = SimpleNamespace(external_id="33333333-3333-3333-3333-333333333333")

    @asynccontextmanager
    async def fake_get_project_client(*, project=None, project_id=None):
        assert project == "research"
        assert project_id == "33333333-3333-3333-3333-333333333333"
        yield http_client, active_project

    response = MagicMock()
    response.json.return_value = expected.model_dump(mode="json")
    call_post = AsyncMock(return_value=response)
    monkeypatch.setattr(inspect_command, "get_project_client", fake_get_project_client)
    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", call_post)

    result = await inspect_command.run_inspect_chunks(
        "notes/retrieval-inspection",
        project="research",
        project_id="33333333-3333-3333-3333-333333333333",
    )

    assert result == expected
    call_post.assert_awaited_once()
    await_args = call_post.await_args
    assert await_args is not None
    assert await_args.args[1] == (
        "/v2/projects/33333333-3333-3333-3333-333333333333/inspect/chunks"
    )


@patch("basic_memory.cli.commands.tool._use_rich", return_value=True)
@patch("basic_memory.cli.commands.inspect.run_inspect_query", new_callable=AsyncMock)
def test_inspect_query_rich_renders_query_plan_and_grouped_misses(mock_run, _mock_use_rich):
    mock_run.return_value = _query_response()

    result = runner.invoke(
        cli_app,
        [
            "inspect",
            "query",
            "auth retrieval",
            "--mode",
            "hybrid",
            "--show-misses",
            "--show-ids",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Retrieval query" in result.output
    assert "Project: 7" in result.output
    assert "12 ready, 2 pending, 3 other identity" in result.output
    assert "max+0.3*min/v1" in result.output
    assert "Ranked results" in result.output
    assert "fts (relaxed fallback)" in result.output
    assert "Authentication Guide" in result.output
    assert "notes/authentication-guide" in result.output
    assert "11111111-1111-1111-1111-111111111111" in result.output
    assert "+1" in result.output
    assert "bounded window — not exhaustive" in result.output
    assert "below_threshold" in result.output


@patch("basic_memory.cli.commands.tool._use_rich", return_value=True)
@patch("basic_memory.cli.commands.inspect.run_inspect_query", new_callable=AsyncMock)
def test_inspect_query_renders_fts_readiness_as_not_applicable(mock_run, _mock_use_rich):
    """An FTS-only trace has no manifest snapshot; zeros would misreport the index."""
    response = _query_response()
    mock_run.return_value = response.model_copy(
        update={
            "engine": response.engine.model_copy(
                update={
                    "ready_rows": None,
                    "pending_rows": None,
                    "other_identity_rows": None,
                }
            )
        }
    )

    rich_result = runner.invoke(cli_app, ["inspect", "query", "auth retrieval"])
    assert rich_result.exit_code == 0, rich_result.output
    assert "Readiness: n/a" in rich_result.output
    assert "0 ready" not in rich_result.output

    plain_result = runner.invoke(cli_app, ["inspect", "query", "auth retrieval", "--plain"])
    assert plain_result.exit_code == 0, plain_result.output
    assert "Readiness: n/a" in plain_result.output
    assert "ready=0" not in plain_result.output


@patch("basic_memory.cli.commands.inspect.run_inspect_query", new_callable=AsyncMock)
def test_inspect_query_plain_hides_misses_without_show_misses(mock_run):
    mock_run.return_value = _query_response()

    result = runner.invoke(
        cli_app,
        ["inspect", "query", "auth retrieval", "--mode", "vector", "--plain"],
    )

    assert result.exit_code == 0, result.output
    assert "Engine:" in result.output
    assert "relaxed_fallback=yes" in result.output
    assert "dropped=3" in result.output
    assert "delta=+1" in result.output
    assert "permalink=notes/authentication-guide" in result.output
    assert "11111111-1111-1111-1111-111111111111" not in result.output
    assert "id=entity:1" not in result.output
    assert "bounded window" not in result.output
    query = mock_run.await_args.args[0]
    assert query.retrieval_mode == SearchRetrievalMode.VECTOR


@patch("basic_memory.cli.commands.inspect.run_inspect_query", new_callable=AsyncMock)
def test_inspect_query_plain_show_misses_renders_rejection_evidence(mock_run):
    response = _query_response()
    model_mismatch = InspectQueryCandidate(
        type="entity",
        id=3,
        external_id=None,
        title="Stale model candidate",
        permalink=None,
        file_path=None,
        disposition="model_mismatch",
        rejection_detail=InspectQueryRejectionDetail(
            reason="model_mismatch",
            chunk_key="entity:3:0",
            similarity=0.3,
            stored_model="old-model",
            stored_index="sqlite-vec",
        ),
        matched_chunks=[],
        dropped_chunks=[],
        scores=InspectQueryScores(vector_similarity=0.3),
    )
    mock_run.return_value = response.model_copy(
        update={"candidates": [*response.candidates, model_mismatch]}
    )

    result = runner.invoke(
        cli_app,
        ["inspect", "query", "auth retrieval", "--plain", "--show-misses"],
    )

    assert result.exit_code == 0, result.output
    assert 'score=0.1000 detail={"reason":"below_threshold"' in result.output
    assert '"similarity":0.1,"threshold":0.2' in result.output
    assert 'score=0.3000 detail={"reason":"model_mismatch"' in result.output
    assert '"stored_model":"old-model","stored_index":"sqlite-vec"' in result.output


@patch("basic_memory.cli.commands.inspect.run_inspect_query", new_callable=AsyncMock)
def test_inspect_query_json_is_schema_locked_and_always_includes_misses(mock_run):
    expected = _query_response()
    mock_run.return_value = expected

    result = runner.invoke(
        cli_app,
        [
            "inspect",
            "query",
            "auth retrieval",
            "--json",
            "--page",
            "2",
            "--page-size",
            "5",
            "--show-ids",
        ],
    )

    assert result.exit_code == 0, result.output
    validated = InspectQueryResponse.model_validate_json(result.output)
    assert validated == expected
    assert {candidate.disposition for candidate in validated.candidates} == {
        "returned",
        "below_threshold",
    }
    assert mock_run.await_args.kwargs["limit"] == 5
    assert mock_run.await_args.kwargs["offset"] == 5


@patch("basic_memory.cli.commands.inspect.run_inspect_query", new_callable=AsyncMock)
def test_inspect_query_plain_show_ids_adds_external_id(mock_run):
    mock_run.return_value = _query_response()

    result = runner.invoke(
        cli_app,
        ["inspect", "query", "auth retrieval", "--plain", "--show-ids"],
    )

    assert result.exit_code == 0, result.output
    assert "permalink=notes/authentication-guide" in result.output
    assert "id=11111111-1111-1111-1111-111111111111" in result.output
    assert "id=entity:1" not in result.output


@patch("basic_memory.cli.commands.inspect.run_inspect_query", new_callable=AsyncMock)
def test_inspect_query_plain_show_ids_falls_back_to_type_qualified_trace_key(mock_run):
    response = _query_response()
    returned = response.candidates[0].model_copy(update={"external_id": None})
    mock_run.return_value = response.model_copy(
        update={"candidates": [returned, *response.candidates[1:]]}
    )

    result = runner.invoke(
        cli_app,
        ["inspect", "query", "auth retrieval", "--plain", "--show-ids"],
    )

    assert result.exit_code == 0, result.output
    assert "id=entity:1" in result.output


@patch("basic_memory.cli.commands.inspect.run_inspect_query", new_callable=AsyncMock)
def test_inspect_query_plain_preserves_malformed_drop_chunk_key(mock_run):
    response = _query_response()
    malformed = InspectQueryCandidate(
        type=None,
        id=None,
        external_id=None,
        title=None,
        permalink=None,
        file_path=None,
        disposition="not_in_manifest",
        rejection_detail=InspectQueryRejectionDetail(
            reason="not_in_manifest",
            chunk_key="malformed-key",
            similarity=0.4,
        ),
        matched_chunks=[InspectMatchedChunk(chunk_key="malformed-key", similarity=0.4)],
        dropped_chunks=[],
        scores=InspectQueryScores(vector_similarity=0.4),
    )
    mock_run.return_value = response.model_copy(
        update={"candidates": [*response.candidates, malformed]}
    )

    result = runner.invoke(
        cli_app,
        ["inspect", "query", "auth retrieval", "--plain", "--show-misses"],
    )

    assert result.exit_code == 0, result.output
    assert "malformed-key" in result.output
    assert "None:None" not in result.output


@patch("basic_memory.cli.commands.inspect.run_inspect_query", new_callable=AsyncMock)
def test_inspect_query_fts_show_misses_explains_sql_limit(mock_run):
    response = _query_response(SearchRetrievalMode.FTS)
    response.engine.reranker.applied = False
    response.engine.reranker.skipped_reason = "fts_mode"
    mock_run.return_value = response

    result = runner.invoke(
        cli_app,
        ["inspect", "query", "auth retrieval", "--show-misses", "--plain"],
    )

    assert result.exit_code == 0, result.output
    assert "show-misses not applicable: FTS window is the SQL LIMIT" in result.output


@pytest.mark.asyncio
async def test_run_inspect_query_uses_typed_client_and_project_route(monkeypatch):
    expected = _query_response()
    http_client = MagicMock()
    active_project = SimpleNamespace(external_id="33333333-3333-3333-3333-333333333333")

    @asynccontextmanager
    async def fake_get_project_client(*, project=None, project_id=None):
        assert project == "research"
        assert project_id == "33333333-3333-3333-3333-333333333333"
        yield http_client, active_project

    response = MagicMock()
    response.json.return_value = expected.model_dump(mode="json")
    call_post = AsyncMock(return_value=response)
    monkeypatch.setattr(inspect_command, "get_project_client", fake_get_project_client)
    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", call_post)

    query = SearchQuery(text="auth retrieval", retrieval_mode=SearchRetrievalMode.HYBRID)
    result = await inspect_command.run_inspect_query(
        query,
        limit=10,
        offset=0,
        project="research",
        project_id="33333333-3333-3333-3333-333333333333",
    )

    assert result == expected
    call_post.assert_awaited_once()
    await_args = call_post.await_args
    assert await_args is not None
    assert await_args.args[1] == ("/v2/projects/33333333-3333-3333-3333-333333333333/inspect/query")
