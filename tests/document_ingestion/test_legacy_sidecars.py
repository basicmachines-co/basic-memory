"""Sidecars written before the extracted_text rename and the page-map move keep working.

The fixtures were serialized by the previous contract (``type: document``, with
the full page map in the sidecar frontmatter). Accepted bytes and their checksums
were computed from that exact text, so it must parse, reassemble byte for byte,
match its run note, enrich, and be reused on re-import without a rewrite.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from basic_memory.document_ingestion.local_runtime import LocalRawDocumentWriter
from basic_memory.document_ingestion.raw_document import (
    PDF_RAW_PIPELINE_VERSION,
    DocumentSourceEntity,
    DocumentSourceSnapshot,
    ExtractedDocument,
    RawDocumentArtifacts,
    build_raw_document_artifacts_from_extracted,
    build_raw_ingestion_run_markdown,
    require_document_run_identity,
)
from basic_memory.schemas.document import (
    DocumentAgentObservationV1,
    DocumentAgentOutputV1,
    DocumentExtractionSummaryV1,
    DocumentExtractionV1,
    DocumentIngestionStage,
    DocumentIngestionV1,
    DocumentPageLocatorV1,
    assemble_document_markdown,
    document_markdown_checksum,
    enrich_document_markdown,
    parse_document_ingestion_run_markdown,
    parse_document_markdown,
)
from basic_memory.services.file_service import FileService
from tests.document_ingestion.test_local_runtime import FakeKnowledgeApi, resolved, entity_response

FIXTURES = Path(__file__).parent / "fixtures"
MAPPED = "legacy-document-sidecar-mapped.md"
UNMAPPED = "legacy-document-sidecar-unmapped.md"
NOW = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)


def legacy_markdown(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "extraction_type"),
    [(MAPPED, DocumentExtractionV1), (UNMAPPED, DocumentExtractionSummaryV1)],
)
def test_legacy_document_sidecar_round_trips_byte_identically(
    name: str, extraction_type: type
) -> None:
    markdown = legacy_markdown(name)

    parsed = parse_document_markdown(markdown)

    assert parsed.frontmatter.type == "document"
    assert type(parsed.frontmatter.extraction) is extraction_type
    assert assemble_document_markdown(parsed) == markdown


def legacy_artifacts() -> RawDocumentArtifacts:
    """Rebuild the deterministic inputs that produced the mapped legacy fixture."""
    legacy = parse_document_markdown(legacy_markdown(MAPPED))
    extraction = legacy.frontmatter.extraction
    assert isinstance(extraction, DocumentExtractionV1)
    source = legacy.frontmatter.source
    snapshot = DocumentSourceSnapshot(
        entity=DocumentSourceEntity(
            entity_id=7,
            external_id=source.entity_external_id,
            file_path=source.file_path,
            media_type=source.media_type,
        ),
        content=b"%PDF-test",
        checksum=source.checksum,
        size_bytes=source.size_bytes,
        storage_etag="etag-1",
    )
    return build_raw_document_artifacts_from_extracted(
        snapshot,
        ExtractedDocument(
            extraction=extraction,
            markdown=legacy.body,
            kind="pdf",
            pipeline_version=PDF_RAW_PIPELINE_VERSION,
        ),
        started_at=NOW,
    )


def test_legacy_mapped_sidecar_matches_its_run_note() -> None:
    built = legacy_artifacts()
    legacy = parse_document_markdown(legacy_markdown(MAPPED))
    run = parse_document_ingestion_run_markdown(
        build_raw_ingestion_run_markdown(
            built, raw_checksum="sha256:" + "b" * 64, raw_created_at=NOW
        )
    )

    # Same deterministic inputs as before the rename: the run id is unchanged.
    assert built.run_id == legacy.frontmatter.ingestion.run_id
    require_document_run_identity(legacy, run)

    # A legacy sidecar's own map must still agree with the run's map.
    assert run.frontmatter.extraction is not None
    run_map = run.frontmatter.extraction.page_map
    assert run_map is not None
    shifted = run_map.model_copy(
        update={
            "pages": (
                run_map.pages[0].model_copy(update={"end": 29}),
                run_map.pages[1].model_copy(update={"start": 29}),
            )
        }
    )
    other_run = run.model_copy(
        update={
            "frontmatter": run.frontmatter.model_copy(
                update={
                    "extraction": run.frontmatter.extraction.model_copy(
                        update={"page_map": shifted}
                    )
                }
            )
        }
    )
    with pytest.raises(RuntimeError, match="does not match its ingestion run"):
        require_document_run_identity(legacy, other_run)


def test_legacy_sidecar_enriches_without_changing_its_type() -> None:
    legacy = parse_document_markdown(legacy_markdown(MAPPED))
    ingestion = legacy.frontmatter.ingestion
    target = DocumentIngestionV1(
        stage=DocumentIngestionStage.ready,
        pipeline_version=ingestion.pipeline_version,
        run_id=ingestion.run_id,
        input_checksum=ingestion.input_checksum,
        base_checksum=document_markdown_checksum(legacy_markdown(MAPPED)),
    )

    enriched = enrich_document_markdown(
        legacy,
        DocumentAgentOutputV1(
            title="Report",
            body="A summary.",
            observations=(
                DocumentAgentObservationV1(
                    category="fact",
                    content="Page two exists.",
                    locator=DocumentPageLocatorV1(page=2),
                ),
            ),
        ),
        target,
    )

    assert enriched.frontmatter.type == "document"
    assert enriched.frontmatter.extraction == legacy.frontmatter.extraction
    reparsed = parse_document_markdown(assemble_document_markdown(enriched))
    assert reparsed == enriched


@pytest.mark.asyncio
async def test_writer_reuses_a_legacy_document_typed_sidecar(file_service: FileService) -> None:
    built = legacy_artifacts()
    markdown = legacy_markdown(MAPPED)
    sidecar = file_service.base_path / built.document_file_path
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(markdown, encoding="utf-8")
    file_checksum = f"sha256:{hashlib.sha256(sidecar.read_bytes()).hexdigest()}"
    # The run note an earlier import recorded for exactly these sidecar bytes.
    run_path = file_service.base_path / built.run_file_path
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(
        build_raw_ingestion_run_markdown(
            built,
            raw_checksum=document_markdown_checksum(markdown),
            raw_created_at=NOW,
            raw_storage_version_id=file_checksum,
        ),
        encoding="utf-8",
    )
    knowledge = FakeKnowledgeApi(
        resolved=resolved("docs/report.pdf"),
        entity=entity_response("docs/report.pdf", "application/pdf"),
    )

    result = await LocalRawDocumentWriter(file_service, knowledge).write(built)

    assert result.document_created is False
    assert result.run_created is False
    assert result.document_db_checksum == file_checksum
    assert sidecar.read_text(encoding="utf-8") == markdown
    assert knowledge.indexed == []


def test_new_artifacts_for_the_legacy_input_write_a_slim_extracted_text_sidecar() -> None:
    built = legacy_artifacts()
    document = parse_document_markdown(built.document_markdown)

    assert document.frontmatter.type == "extracted_text"
    assert isinstance(document.frontmatter.extraction, DocumentExtractionSummaryV1)
    assert built.extraction.page_map is not None
    assert UUID(str(document.frontmatter.ingestion.run_id)) == built.run_id
