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
    InspectRowsBehindFileDetail,
    InspectSearchRow,
)

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
