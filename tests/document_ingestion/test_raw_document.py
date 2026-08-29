"""Tests for the portable raw document contract mapping and runtime shell."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import override
from uuid import UUID

import pytest

from basic_memory.document_ingestion.pdf_inspector import (
    PdfInspector,
    PdfInspectorLimits,
    PdfInspectorOutput,
    PdfInspectorPdfType,
)
from basic_memory.document_ingestion.raw_document import (
    DocumentSourceChangedError,
    DocumentSourceEntity,
    DocumentSourceSnapshot,
    RawDocumentArtifacts,
    RawDocumentWriteResult,
    RawPdfDocumentRuntime,
    build_raw_document_artifacts,
    build_raw_ingestion_run_markdown,
    canonical_db_checksum,
    extraction_options_checksum,
    require_document_run_identity,
    require_matching_raw_document,
    require_matching_source_identity,
)
from basic_memory.schemas.document import (
    DocumentExtractionStatus,
    DocumentIngestionStage,
    DocumentIngestionV1,
    DocumentMarkdownV1,
    parse_document_ingestion_run_markdown,
    parse_document_markdown,
)

SOURCE_EXTERNAL_ID = UUID("11111111-1111-1111-1111-111111111111")
STARTED_AT = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
EXTRACTED_AT = datetime(2026, 8, 1, 3, 1, tzinfo=UTC)


def source_entity() -> DocumentSourceEntity:
    return DocumentSourceEntity(
        entity_id=7,
        external_id=SOURCE_EXTERNAL_ID,
        file_path="research/report.pdf",
        media_type="application/pdf",
    )


def source_snapshot(*, checksum_char: str = "a") -> DocumentSourceSnapshot:
    return DocumentSourceSnapshot(
        entity=source_entity(),
        content=b"%PDF-test",
        checksum="sha256:" + checksum_char * 64,
        size_bytes=9,
        storage_etag="etag-1",
    )


def extraction_output(*, engine_version: str = "0.2.6") -> PdfInspectorOutput:
    return PdfInspectorOutput(
        engine_version=engine_version,
        pdf_type=PdfInspectorPdfType.text_based,
        markdown="<!-- Page 1 -->\n\nExtracted text.\n",
        title="Report",
        page_count=1,
        extracted_page_count=1,
        confidence=1.0,
        processing_time_ms=12,
        is_complex_layout=False,
        has_encoding_issues=False,
    )


def artifacts(
    *,
    checksum_char: str = "a",
    engine_version: str = "0.2.6",
    limits: PdfInspectorLimits | None = None,
) -> RawDocumentArtifacts:
    return build_raw_document_artifacts(
        source_snapshot(checksum_char=checksum_char),
        extraction_output(engine_version=engine_version),
        limits=limits or PdfInspectorLimits(),
        started_at=STARTED_AT,
        extracted_at=EXTRACTED_AT,
    )


def test_build_raw_document_artifacts_is_typed_and_deterministic() -> None:
    limits = PdfInspectorLimits()

    first = artifacts(limits=limits)
    second = artifacts(limits=limits)

    assert first == second
    assert first.document_file_path == "research/report.pdf.md"
    assert first.run_file_path == f"document-ingestion-runs/{first.run_id}.md"
    parsed = parse_document_markdown(first.document_markdown)
    assert parsed.frontmatter.type == "document"
    assert parsed.frontmatter.source.storage_etag == "etag-1"
    assert parsed.frontmatter.extraction.status is DocumentExtractionStatus.complete
    assert parsed.frontmatter.extraction.profile == "pdf-inspector-v1"
    assert parsed.frontmatter.extraction.options_hash == extraction_options_checksum(limits)
    assert parsed.frontmatter.extraction.classification == "text_based"
    assert parsed.frontmatter.extraction.duration_ms == 12
    assert parsed.frontmatter.ingestion.stage is DocumentIngestionStage.raw
    assert parsed.frontmatter.bm_parse_semantics is False
    assert parsed.body == "<!-- Page 1 -->\n\nExtracted text.\n"


def test_engine_version_is_part_of_run_identity() -> None:
    assert artifacts().run_id != artifacts(engine_version="1.17.0").run_id


def test_raw_run_note_references_the_accepted_document_checksum() -> None:
    built = artifacts()
    markdown = build_raw_ingestion_run_markdown(
        built,
        raw_checksum="sha256:" + "b" * 64,
        raw_created_at=datetime(2026, 8, 1, 3, 2, tzinfo=UTC),
    )

    run = parse_document_ingestion_run_markdown(markdown)
    assert run.frontmatter.type == "document_ingestion_run"
    assert run.frontmatter.extraction == built.extraction
    assert run.frontmatter.output is not None
    assert run.frontmatter.output.raw.checksum == "sha256:" + "b" * 64
    assert run.frontmatter.output.raw.storage_version_id is None
    assert run.frontmatter.bm_parse_semantics is False


def test_extraction_options_checksum_changes_with_a_shaping_limit() -> None:
    default = extraction_options_checksum(PdfInspectorLimits())
    changed = extraction_options_checksum(PdfInspectorLimits(max_pages=50))

    assert default.startswith("sha256:")
    assert default != changed


# --- Identity checks ---


def accepted_document(built: RawDocumentArtifacts) -> DocumentMarkdownV1:
    return parse_document_markdown(built.document_markdown)


def with_ready_stage(document: DocumentMarkdownV1) -> DocumentMarkdownV1:
    ingestion = document.frontmatter.ingestion
    enriched = DocumentIngestionV1(
        stage=DocumentIngestionStage.ready,
        pipeline_version=ingestion.pipeline_version,
        run_id=ingestion.run_id,
        input_checksum=ingestion.input_checksum,
        base_checksum="sha256:" + "c" * 64,
    )
    frontmatter = document.frontmatter.model_copy(update={"ingestion": enriched})
    return document.model_copy(update={"frontmatter": frontmatter})


def test_require_matching_raw_document_accepts_the_deterministic_document() -> None:
    built = artifacts()

    require_matching_raw_document(accepted_document(built), built)


def test_require_matching_raw_document_rejects_another_engine_version() -> None:
    document = accepted_document(artifacts())

    with pytest.raises(RuntimeError, match="deterministic run identity"):
        require_matching_raw_document(document, artifacts(engine_version="1.17.0"))


def test_require_matching_raw_document_rejects_an_enriched_document() -> None:
    built = artifacts()

    with pytest.raises(RuntimeError, match="no longer at the raw stage"):
        require_matching_raw_document(with_ready_stage(accepted_document(built)), built)


def test_require_matching_source_identity_rejects_different_bytes() -> None:
    require_matching_source_identity(artifacts().source, artifacts().source)

    with pytest.raises(RuntimeError, match="source identity"):
        require_matching_source_identity(artifacts().source, artifacts(checksum_char="d").source)


def accepted_run(built: RawDocumentArtifacts):
    return parse_document_ingestion_run_markdown(
        build_raw_ingestion_run_markdown(
            built,
            raw_checksum="sha256:" + "b" * 64,
            raw_created_at=datetime(2026, 8, 1, 3, 2, tzinfo=UTC),
        )
    )


def test_require_document_run_identity_accepts_a_matching_pair() -> None:
    built = artifacts()

    require_document_run_identity(accepted_document(built), accepted_run(built))


def test_require_document_run_identity_rejects_a_run_without_extraction() -> None:
    built = artifacts()
    run = accepted_run(built)
    run = run.model_copy(
        update={"frontmatter": run.frontmatter.model_copy(update={"extraction": None})}
    )

    with pytest.raises(RuntimeError, match="no extraction metadata"):
        require_document_run_identity(accepted_document(built), run)


def test_require_document_run_identity_rejects_an_enriched_document() -> None:
    built = artifacts()

    with pytest.raises(RuntimeError, match="no longer at the raw stage"):
        require_document_run_identity(
            with_ready_stage(accepted_document(built)), accepted_run(built)
        )


def test_require_document_run_identity_rejects_another_runs_note() -> None:
    built = artifacts()

    with pytest.raises(RuntimeError, match="does not match its ingestion run"):
        require_document_run_identity(
            accepted_document(built), accepted_run(artifacts(engine_version="1.17.0"))
        )


def test_canonical_db_checksum_normalizes_the_prefix() -> None:
    bare = "e" * 64
    assert canonical_db_checksum(bare) == f"sha256:{bare}"
    assert canonical_db_checksum(f"sha256:{bare}") == f"sha256:{bare}"

    with pytest.raises(RuntimeError, match="not canonical"):
        canonical_db_checksum("sha256:short")


# --- Runtime shell ---


class RecordingSourceResolver:
    def __init__(self, events: list[str], *, entities: list[DocumentSourceEntity]) -> None:
        self.events = events
        self.entities = entities

    async def resolve(self, file_path: str) -> DocumentSourceEntity:
        assert file_path == "research/report.pdf"
        self.events.append("resolve")
        return self.entities.pop(0)


class RecordingSourceReader:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def read(
        self,
        entity: DocumentSourceEntity,
        *,
        observed_etag: str | None,
    ) -> DocumentSourceSnapshot:
        assert entity == source_entity()
        assert observed_etag is None
        self.events.append("read")
        return source_snapshot()

    async def require_current(self, snapshot: DocumentSourceSnapshot) -> None:
        assert snapshot == source_snapshot()
        self.events.append("require_current")


class RecordingPdfInspector(PdfInspector):
    events: list[str] = []

    @override
    async def extract(self, pdf_bytes: bytes) -> PdfInspectorOutput:
        assert pdf_bytes == source_snapshot().content
        self.events.append("extract")
        return extraction_output()


EXPECTED_RESULT = RawDocumentWriteResult(
    document_external_id=UUID("33333333-3333-3333-3333-333333333333"),
    document_file_path="research/report.pdf.md",
    document_db_checksum="sha256:" + "c" * 64,
    run_id=UUID("44444444-4444-4444-4444-444444444444"),
    run_file_path="document-ingestion-runs/run.md",
    document_created=True,
    run_created=True,
)


class RecordingWriter:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def write(self, artifacts: RawDocumentArtifacts) -> RawDocumentWriteResult:
        assert artifacts.document_file_path == "research/report.pdf.md"
        self.events.append("write")
        return EXPECTED_RESULT


def runtime(events: list[str], *, entities: list[DocumentSourceEntity]) -> RawPdfDocumentRuntime:
    extractor = RecordingPdfInspector(limits=PdfInspectorLimits())
    extractor.events = events
    return RawPdfDocumentRuntime(
        source_resolver=RecordingSourceResolver(events, entities=entities),
        source_reader=RecordingSourceReader(events),
        extractor=extractor,
        writer=RecordingWriter(events),
    )


@pytest.mark.asyncio
async def test_raw_runtime_revalidates_source_immediately_before_write() -> None:
    events: list[str] = []

    result = await runtime(events, entities=[source_entity(), source_entity()]).ingest(
        file_path="research/report.pdf", observed_etag=None
    )

    assert result == EXPECTED_RESULT
    assert events == ["resolve", "read", "extract", "resolve", "require_current", "write"]


@pytest.mark.asyncio
async def test_raw_runtime_rejects_a_source_replaced_during_extraction() -> None:
    events: list[str] = []
    replaced = DocumentSourceEntity(
        entity_id=8,
        external_id=UUID("55555555-5555-5555-5555-555555555555"),
        file_path="research/report.pdf",
        media_type="application/pdf",
    )

    with pytest.raises(DocumentSourceChangedError, match="changed during extraction"):
        await runtime(events, entities=[source_entity(), replaced]).ingest(
            file_path="research/report.pdf", observed_etag=None
        )

    assert events == ["resolve", "read", "extract", "resolve"]
