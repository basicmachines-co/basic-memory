"""Tests for media-type dispatch and the parser-neutral artifact mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import override
from uuid import UUID

import pytest

from basic_memory.document_ingestion.pdf_inspector import PdfInspector, PdfInspectorOutput
from basic_memory.document_ingestion.raw_document import (
    DocumentSourceEntity,
    DocumentSourceSnapshot,
    ExtractedDocument,
    RawDocumentArtifacts,
    RawDocumentRuntime,
    RawDocumentWriteResult,
    RawPdfDocumentRuntime,
    UnsupportedDocumentMediaTypeError,
    build_raw_document_artifacts_from_extracted,
    raw_document_matches,
)
from basic_memory.schemas.document import (
    DocumentExtractionStatus,
    DocumentExtractionV1,
    parse_document_markdown,
)

SOURCE_EXTERNAL_ID = UUID("11111111-1111-1111-1111-111111111111")
STARTED_AT = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)


def csv_entity() -> DocumentSourceEntity:
    return DocumentSourceEntity(
        entity_id=3,
        external_id=SOURCE_EXTERNAL_ID,
        file_path="data/riders.csv",
        media_type="text/csv",
    )


def snapshot(entity: DocumentSourceEntity, *, checksum_char: str = "a") -> DocumentSourceSnapshot:
    return DocumentSourceSnapshot(
        entity=entity,
        content=b"team,name\n",
        checksum="sha256:" + checksum_char * 64,
        size_bytes=10,
        storage_etag="sha256:" + checksum_char * 64,
    )


def extracted(*, pipeline_version: str = "csv-raw-v1") -> ExtractedDocument:
    return ExtractedDocument(
        extraction=DocumentExtractionV1(
            engine="basic-memory/csv",
            engine_version="0.23.2",
            profile="csv-preview-v1",
            options_hash="sha256:" + "b" * 64,
            classification="csv",
            status=DocumentExtractionStatus.complete,
            extracted_at=datetime(2026, 9, 7, 3, 1, tzinfo=UTC),
            duration_ms=1,
            page_count=0,
            extracted_page_count=0,
            requires_ocr=False,
            ocr_page_count=0,
            has_tables=True,
        ),
        markdown="| team | name |\n| --- | --- |\n",
        kind="csv",
        pipeline_version=pipeline_version,
    )


def artifacts(
    *, checksum_char: str = "a", pipeline_version: str = "csv-raw-v1"
) -> RawDocumentArtifacts:
    return build_raw_document_artifacts_from_extracted(
        snapshot(csv_entity(), checksum_char=checksum_char),
        extracted(pipeline_version=pipeline_version),
        started_at=STARTED_AT,
    )


def test_build_from_extracted_tags_the_kind_and_derives_the_sidecar_path() -> None:
    built = artifacts()

    document = parse_document_markdown(built.document_markdown)
    assert document.frontmatter.title == "riders.csv"
    assert document.frontmatter.tags == ("document", "csv", "generated")
    assert document.frontmatter.document.kind == "csv"
    assert document.frontmatter.source.media_type == "text/csv"
    assert document.frontmatter.ingestion.pipeline_version == "csv-raw-v1"
    assert document.frontmatter.bm_parse_semantics is False
    assert document.body.strip() == "| team | name |\n| --- | --- |"
    assert built.document_file_path == "data/riders.csv.md"
    assert built.run_file_path == f"document-ingestion-runs/{built.run_id}.md"


def test_run_identity_is_deterministic_and_tracks_the_pipeline_version() -> None:
    assert artifacts().run_id == artifacts().run_id
    assert artifacts().run_id != artifacts(pipeline_version="csv-raw-v2").run_id
    assert artifacts().run_id != artifacts(checksum_char="b").run_id


def test_raw_document_matches_compares_source_ingestion_and_engine() -> None:
    built = artifacts()
    document = parse_document_markdown(built.document_markdown)

    assert raw_document_matches(document, built)
    assert not raw_document_matches(document, artifacts(checksum_char="b"))


# --- Dispatch ---


class RecordingResolver:
    def __init__(self, events: list[str], entity: DocumentSourceEntity) -> None:
        self.events = events
        self.entity = entity

    async def resolve(self, file_path: str) -> DocumentSourceEntity:
        self.events.append(f"resolve:{file_path}")
        return self.entity


class RecordingReader:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def read(
        self, entity: DocumentSourceEntity, *, observed_etag: str | None
    ) -> DocumentSourceSnapshot:
        self.events.append("read")
        return snapshot(entity)

    async def require_current(self, snapshot: DocumentSourceSnapshot) -> None:
        self.events.append("require_current")


class RecordingExtractor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def extract(self, content: bytes, *, file_name: str) -> ExtractedDocument:
        self.events.append(f"extract:{file_name}")
        return extracted()


class RecordingWriter:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.written: RawDocumentArtifacts | None = None

    async def write(self, artifacts: RawDocumentArtifacts) -> RawDocumentWriteResult:
        self.events.append("write")
        self.written = artifacts
        return RawDocumentWriteResult(
            document_external_id=artifacts.document_external_id,
            document_file_path=artifacts.document_file_path,
            document_db_checksum="sha256:" + "c" * 64,
            run_id=artifacts.run_id,
            run_file_path=artifacts.run_file_path,
            document_created=True,
            run_created=True,
        )


@pytest.mark.asyncio
async def test_runtime_dispatches_on_the_source_media_type() -> None:
    events: list[str] = []
    writer = RecordingWriter(events)
    runtime = RawDocumentRuntime(
        source_resolver=RecordingResolver(events, csv_entity()),
        source_reader=RecordingReader(events),
        extractors={"text/csv": RecordingExtractor(events)},
        writer=writer,
    )

    result = await runtime.ingest(file_path="data/riders.csv", observed_etag=None)

    assert events == [
        "resolve:data/riders.csv",
        "read",
        "extract:riders.csv",
        "resolve:data/riders.csv",
        "require_current",
        "write",
    ]
    assert result.document_file_path == "data/riders.csv.md"
    assert writer.written is not None
    assert "- csv" in writer.written.document_markdown


@pytest.mark.asyncio
async def test_runtime_rejects_an_unsupported_media_type_before_reading() -> None:
    events: list[str] = []
    runtime = RawDocumentRuntime(
        source_resolver=RecordingResolver(events, csv_entity()),
        source_reader=RecordingReader(events),
        extractors={},
        writer=RecordingWriter(events),
    )

    with pytest.raises(UnsupportedDocumentMediaTypeError, match="text/csv"):
        await runtime.ingest(file_path="data/riders.csv", observed_etag=None)

    assert events == ["resolve:data/riders.csv"]


class NeverPdfInspector(PdfInspector):
    @override
    async def extract(self, pdf_bytes: bytes) -> PdfInspectorOutput:
        raise AssertionError("the PDF extractor must not run for a non-PDF source")


@pytest.mark.asyncio
async def test_pdf_runtime_only_accepts_pdf_sources() -> None:
    events: list[str] = []
    runtime = RawPdfDocumentRuntime(
        source_resolver=RecordingResolver(events, csv_entity()),
        source_reader=RecordingReader(events),
        extractor=NeverPdfInspector(),
        writer=RecordingWriter(events),
    )

    with pytest.raises(UnsupportedDocumentMediaTypeError):
        await runtime.ingest(file_path="data/riders.csv", observed_etag=None)
