"""Tests for the `bm import document` command."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
import typer
from typer.testing import CliRunner

from basic_memory.cli.commands.import_document import import_document
from basic_memory.cli.main import app as cli_app
from basic_memory.document_ingestion.csv_extractor import CSV_MEDIA_TYPE
from basic_memory.document_ingestion.local_runtime import (
    ApiDocumentSourceEntityResolver,
    LocalDocumentSourceReader,
    LocalRawDocumentWriter,
)
from basic_memory.document_ingestion.markitdown_extractor import DOCX_MEDIA_TYPE, PPTX_MEDIA_TYPE
from basic_memory.document_ingestion.raw_document import PDF_MEDIA_TYPE, RawDocumentWriteResult

runner = CliRunner()

RESULT = RawDocumentWriteResult(
    document_external_id=UUID("33333333-3333-3333-3333-333333333333"),
    document_file_path="docs/plan.docx.md",
    document_db_checksum="sha256:" + "a" * 64,
    run_id=UUID("44444444-4444-4444-4444-444444444444"),
    run_file_path="document-ingestion-runs/44444444-4444-4444-4444-444444444444.md",
    document_created=True,
    run_created=False,
)


# --- Command wrapper ---


@patch("basic_memory.cli.commands.import_document.import_document", new_callable=AsyncMock)
def test_import_document_reports_the_written_notes(mock_import: AsyncMock) -> None:
    mock_import.return_value = ("main", RESULT)

    result = runner.invoke(cli_app, ["import", "document", "docs/plan.docx", "--project", "main"])

    assert result.exit_code == 0, result.output
    assert "docs/plan.docx.md (created)" in result.output
    assert "already recorded" in result.output
    mock_import.assert_awaited_once_with(Path("docs/plan.docx"), "main")


@patch("basic_memory.cli.commands.import_document.import_document", new_callable=AsyncMock)
def test_import_document_reports_a_bad_path(mock_import: AsyncMock) -> None:
    mock_import.side_effect = typer.BadParameter("File not found: docs/missing.docx")

    result = runner.invoke(cli_app, ["import", "document", "docs/missing.docx"])

    assert result.exit_code == 1
    assert "Error: File not found: docs/missing.docx" in result.output


@patch("basic_memory.cli.commands.import_document.import_document", new_callable=AsyncMock)
def test_import_document_reports_extraction_failures(mock_import: AsyncMock) -> None:
    mock_import.side_effect = RuntimeError("Office document extraction exceeded the deadline")

    result = runner.invoke(cli_app, ["import", "document", "docs/plan.docx"])

    assert result.exit_code == 1
    assert "Error during import: Office document extraction exceeded the deadline" in result.output


# --- Wiring ---


@asynccontextmanager
async def fake_get_client(project_name: str | None = None) -> AsyncIterator[MagicMock]:
    yield MagicMock(name="http-client")


def project_item(project_home: Path) -> SimpleNamespace:
    return SimpleNamespace(name="main", external_id="project-ext", path=str(project_home))


@pytest.mark.asyncio
async def test_import_document_indexes_the_project_then_runs_the_local_runtime(
    tmp_path: Path,
) -> None:
    project_home = tmp_path / "project"
    source = project_home / "docs" / "plan.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"docx-bytes")
    project_client = MagicMock()
    project_client.index = AsyncMock(return_value={})
    runtime_instance = MagicMock()
    runtime_instance.ingest = AsyncMock(return_value=RESULT)

    with (
        patch("basic_memory.mcp.async_client.get_client", fake_get_client),
        patch(
            "basic_memory.mcp.project_context.get_active_project",
            AsyncMock(return_value=project_item(project_home)),
        ),
        patch("basic_memory.mcp.clients.ProjectClient", return_value=project_client),
        patch(
            "basic_memory.document_ingestion.raw_document.RawDocumentRuntime",
            return_value=runtime_instance,
        ) as runtime_class,
    ):
        project_name, result = await import_document(source, "main")

    assert (project_name, result) == ("main", RESULT)
    project_client.index.assert_awaited_once_with(
        "project-ext", force_full=False, run_in_background=False
    )
    runtime_instance.ingest.assert_awaited_once_with(file_path="docs/plan.docx", observed_etag=None)
    wiring = runtime_class.call_args.kwargs
    assert isinstance(wiring["source_resolver"], ApiDocumentSourceEntityResolver)
    assert isinstance(wiring["source_reader"], LocalDocumentSourceReader)
    assert wiring["source_reader"].project_home == project_home.resolve()
    assert isinstance(wiring["writer"], LocalRawDocumentWriter)
    assert set(wiring["extractors"]) == {
        PDF_MEDIA_TYPE,
        CSV_MEDIA_TYPE,
        DOCX_MEDIA_TYPE,
        PPTX_MEDIA_TYPE,
    }


@pytest.mark.asyncio
async def test_import_document_rejects_a_missing_file(tmp_path: Path) -> None:
    project_home = tmp_path / "project"
    project_home.mkdir()

    with (
        patch("basic_memory.mcp.async_client.get_client", fake_get_client),
        patch(
            "basic_memory.mcp.project_context.get_active_project",
            AsyncMock(return_value=project_item(project_home)),
        ),
        pytest.raises(typer.BadParameter, match="File not found"),
    ):
        await import_document(project_home / "docs" / "missing.docx", "main")


@pytest.mark.asyncio
async def test_import_document_rejects_a_file_outside_the_project(tmp_path: Path) -> None:
    project_home = tmp_path / "project"
    project_home.mkdir()
    outside = tmp_path / "elsewhere.docx"
    outside.write_bytes(b"docx-bytes")

    with (
        patch("basic_memory.mcp.async_client.get_client", fake_get_client),
        patch(
            "basic_memory.mcp.project_context.get_active_project",
            AsyncMock(return_value=project_item(project_home)),
        ),
        pytest.raises(typer.BadParameter, match="not inside project 'main'"),
    ):
        await import_document(outside, "main")
