"""Portable raw PDF ingestion: stable source snapshot -> extraction -> document artifacts.

The uploaded PDF stays its own file entity. This module maps one stable source
snapshot plus the bounded extractor's output into Core's document contract (a
``type: document`` note and its ``document_ingestion_run`` note) and orchestrates
the read / extract / revalidate / write sequence behind narrow protocols.

Storage reads and accepted-note writes are runtime-specific (Tigris and PGQueuer
in Cloud, the project directory locally) and are supplied by the caller. The
identity checks at the bottom let any writer verify that an already-accepted
document or run note still matches the deterministic inputs before reusing it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID

from basic_memory.document_ingestion.pdf_inspector import (
    PdfInspector,
    PdfInspectorLimits,
    PdfInspectorOutput,
)
from basic_memory.schemas.document import (
    DocumentExtractionStatus,
    DocumentExtractionV1,
    DocumentIngestionRunFrontmatterV1,
    DocumentIngestionRunMarkdownV1,
    DocumentIngestionRunOutputV1,
    DocumentIngestionRunStateV1,
    DocumentIngestionStage,
    DocumentIngestionV1,
    DocumentMarkdownV1,
    DocumentMetadataV1,
    DocumentNoteFrontmatterV1,
    DocumentRevisionKind,
    DocumentRevisionReferenceV1,
    DocumentSourceV1,
    assemble_document_ingestion_run_markdown,
    assemble_document_markdown,
    derive_document_ingestion_run_id,
    derive_document_ingestion_run_path,
    derive_document_note_external_id,
    derive_document_note_path,
)

# Actor source recorded on generated notes; see VALID_NOTE_OBJECT_SOURCES.
DOCUMENT_INGESTION_SOURCE = "document_ingestion"
PDF_RAW_PIPELINE_VERSION = "pdf-inspector-raw-v1"
PDF_RAW_EXTRACTION_PROFILE = "pdf-inspector-v1"


class DocumentSourceChangedError(RuntimeError):
    """The source object changed while its bytes were being read."""


# --- Values ---


@dataclass(frozen=True, slots=True)
class DocumentSourceEntity:
    """Indexed identity of the PDF source file entity."""

    entity_id: int
    external_id: UUID
    file_path: str
    media_type: str


@dataclass(frozen=True, slots=True)
class DocumentSourceSnapshot:
    """Stable source bytes and their trusted storage provenance."""

    entity: DocumentSourceEntity
    content: bytes
    checksum: str
    size_bytes: int
    storage_etag: str
    storage_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class RawDocumentArtifacts:
    """Deterministic raw document values before accepted-note timestamps."""

    source: DocumentSourceV1
    extraction: DocumentExtractionV1
    ingestion: DocumentIngestionV1
    document_external_id: UUID
    document_file_path: str
    document_markdown: str
    run_id: UUID
    run_file_path: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class RawDocumentWriteResult:
    """Accepted identities for one idempotent raw ingestion run."""

    document_external_id: UUID
    document_file_path: str
    document_db_checksum: str
    run_id: UUID
    run_file_path: str
    document_created: bool
    run_created: bool


# --- Runtime-supplied capabilities ---


class DocumentSourceEntityResolver(Protocol):
    """Resolve the indexed source entity after file indexing completes."""

    async def resolve(self, file_path: str) -> DocumentSourceEntity: ...


class DocumentSourceReader(Protocol):
    """Read one stable storage generation for extraction."""

    async def read(
        self,
        entity: DocumentSourceEntity,
        *,
        observed_etag: str | None,
    ) -> DocumentSourceSnapshot: ...

    async def require_current(self, snapshot: DocumentSourceSnapshot) -> None:
        """Fail unless the extracted storage generation is still current."""


class RawDocumentWriter(Protocol):
    """Accept raw document and ingestion-run notes idempotently."""

    async def write(self, artifacts: RawDocumentArtifacts) -> RawDocumentWriteResult: ...


# --- Orchestration ---


@dataclass(frozen=True, slots=True)
class RawPdfDocumentRuntime:
    """Orchestrate one stable source read, extraction, and accepted write."""

    source_resolver: DocumentSourceEntityResolver
    source_reader: DocumentSourceReader
    extractor: PdfInspector
    writer: RawDocumentWriter

    async def ingest(
        self,
        *,
        file_path: str,
        observed_etag: str | None,
    ) -> RawDocumentWriteResult:
        started_at = datetime.now(tz=UTC)
        source_entity = await self.source_resolver.resolve(file_path)
        source = await self.source_reader.read(
            source_entity,
            observed_etag=observed_etag,
        )
        extracted = await self.extractor.extract(source.content)
        artifacts = build_raw_document_artifacts(
            source,
            extracted,
            limits=self.extractor.limits,
            started_at=started_at,
            extracted_at=datetime.now(tz=UTC),
        )
        # Extraction can take seconds; re-resolve so a source replaced or moved
        # meanwhile is rejected instead of being written under stale provenance.
        current_source_entity = await self.source_resolver.resolve(file_path)
        if current_source_entity != source.entity:
            raise DocumentSourceChangedError(
                "Indexed PDF source identity changed during extraction"
            )
        await self.source_reader.require_current(source)
        return await self.writer.write(artifacts)


# --- Contract mapping ---


def build_raw_document_artifacts(
    source: DocumentSourceSnapshot,
    extracted: PdfInspectorOutput,
    *,
    limits: PdfInspectorLimits,
    started_at: datetime,
    extracted_at: datetime,
) -> RawDocumentArtifacts:
    """Map native extraction output into Core's portable document contract."""
    options_hash = extraction_options_checksum(limits)
    run_id = UUID(
        derive_document_ingestion_run_id(
            source_entity_external_id=source.entity.external_id,
            source_checksum=source.checksum,
            pipeline_version=PDF_RAW_PIPELINE_VERSION,
            extractor_engine=extracted.engine,
            extractor_version=extracted.engine_version,
            extraction_profile=PDF_RAW_EXTRACTION_PROFILE,
            extraction_options_hash=options_hash,
            prompt_version=None,
        )
    )
    document_external_id = UUID(derive_document_note_external_id(source.entity.external_id))
    source_contract = DocumentSourceV1(
        media_type=source.entity.media_type,
        entity_external_id=source.entity.external_id,
        file_path=source.entity.file_path,
        checksum=source.checksum,
        size_bytes=source.size_bytes,
        storage_version_id=source.storage_version_id,
        storage_etag=source.storage_etag,
    )
    extraction = DocumentExtractionV1(
        engine=extracted.engine,
        engine_version=extracted.engine_version,
        profile=PDF_RAW_EXTRACTION_PROFILE,
        options_hash=options_hash,
        classification=extracted.pdf_type.value,
        status=(
            DocumentExtractionStatus.needs_ocr
            if extracted.pages_needing_ocr
            else DocumentExtractionStatus.complete
        ),
        extracted_at=extracted_at,
        duration_ms=extracted.processing_time_ms,
        page_count=extracted.page_count,
        extracted_page_count=extracted.extracted_page_count,
        requires_ocr=bool(extracted.pages_needing_ocr),
        ocr_page_count=len(extracted.pages_needing_ocr),
        pages_needing_ocr=extracted.pages_needing_ocr,
        confidence=extracted.confidence,
        has_encoding_issues=extracted.has_encoding_issues,
        has_tables=bool(extracted.pages_with_tables),
        has_columns=bool(extracted.pages_with_columns),
    )
    ingestion = DocumentIngestionV1(
        stage=DocumentIngestionStage.raw,
        pipeline_version=PDF_RAW_PIPELINE_VERSION,
        run_id=run_id,
        input_checksum=source.checksum,
    )
    # Raw extraction is untrusted semantic input: keep the body searchable but
    # opt out of observation/relation parsing until a bounded enrichment pass.
    document = DocumentMarkdownV1(
        frontmatter=DocumentNoteFrontmatterV1(
            title=PurePosixPath(source.entity.file_path).name,
            tags=("document", "pdf", "generated"),
            source=source_contract,
            extraction=extraction,
            ingestion=ingestion,
            document=DocumentMetadataV1(kind="pdf"),
            bm_parse_semantics=False,
        ),
        body=extracted.markdown,
    )
    return RawDocumentArtifacts(
        source=source_contract,
        extraction=extraction,
        ingestion=ingestion,
        document_external_id=document_external_id,
        document_file_path=derive_document_note_path(source.entity.file_path),
        document_markdown=assemble_document_markdown(document),
        run_id=run_id,
        run_file_path=derive_document_ingestion_run_path(run_id),
        started_at=started_at,
    )


def build_raw_ingestion_run_markdown(
    artifacts: RawDocumentArtifacts,
    *,
    raw_checksum: str,
    raw_created_at: datetime,
) -> str:
    """Build the compact queryable history note for an accepted raw revision."""
    raw_revision = DocumentRevisionReferenceV1(
        kind=DocumentRevisionKind.raw_extraction,
        checksum=raw_checksum,
        storage_version_id=None,
        created_at=raw_created_at,
    )
    run = DocumentIngestionRunMarkdownV1(
        frontmatter=DocumentIngestionRunFrontmatterV1(
            title=str(artifacts.run_id),
            source=artifacts.source,
            extraction=artifacts.extraction,
            ingestion=DocumentIngestionRunStateV1(
                run_id=artifacts.run_id,
                stage=DocumentIngestionStage.raw,
                pipeline_version=artifacts.ingestion.pipeline_version,
                prompt_version=artifacts.ingestion.prompt_version,
                input_checksum=artifacts.ingestion.input_checksum,
                started_at=artifacts.started_at,
            ),
            output=DocumentIngestionRunOutputV1(
                document_entity_external_id=artifacts.document_external_id,
                document_file_path=artifacts.document_file_path,
                raw=raw_revision,
            ),
        ),
        body="Raw extraction accepted; exact storage materialization is pending.\n",
    )
    return assemble_document_ingestion_run_markdown(run)


def extraction_options_checksum(limits: PdfInspectorLimits) -> str:
    """Return a stable identity for every extraction-shaping limit."""
    encoded = json.dumps(
        limits.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


# --- Identity checks for reusing already-accepted notes ---


def require_matching_raw_document(
    document: DocumentMarkdownV1,
    artifacts: RawDocumentArtifacts,
) -> None:
    """Fail unless an accepted document still matches this run's deterministic inputs."""
    if document.frontmatter.ingestion.stage is not DocumentIngestionStage.raw:
        raise RuntimeError("Existing ingestion run document is no longer at the raw stage")
    if (
        document.frontmatter.source != artifacts.source
        or document.frontmatter.ingestion != artifacts.ingestion
        or document.frontmatter.extraction.engine != artifacts.extraction.engine
        or document.frontmatter.extraction.engine_version != artifacts.extraction.engine_version
    ):
        raise RuntimeError("Existing raw document does not match the deterministic run identity")


def require_matching_source_identity(
    existing: DocumentSourceV1,
    current: DocumentSourceV1,
) -> None:
    """Fail unless two source contracts describe the same bytes of the same entity."""
    if (
        existing.kind != current.kind
        or existing.media_type != current.media_type
        or existing.entity_external_id != current.entity_external_id
        or existing.checksum != current.checksum
        or existing.size_bytes != current.size_bytes
    ):
        raise RuntimeError("Existing ingestion run does not match the source identity")


def require_document_run_identity(
    document: DocumentMarkdownV1,
    run: DocumentIngestionRunMarkdownV1,
) -> None:
    """Fail unless an accepted document and its run note agree on provenance."""
    run_extraction = run.frontmatter.extraction
    if run_extraction is None:
        raise RuntimeError("Existing raw ingestion run has no extraction metadata")
    if document.frontmatter.ingestion.stage is not DocumentIngestionStage.raw:
        raise RuntimeError("Existing ingestion run document is no longer at the raw stage")
    require_matching_source_identity(document.frontmatter.source, run.frontmatter.source)
    if (
        document.frontmatter.extraction != run_extraction
        or document.frontmatter.ingestion.run_id != run.frontmatter.ingestion.run_id
        or document.frontmatter.ingestion.pipeline_version
        != run.frontmatter.ingestion.pipeline_version
        or document.frontmatter.ingestion.prompt_version != run.frontmatter.ingestion.prompt_version
        or document.frontmatter.ingestion.input_checksum != run.frontmatter.ingestion.input_checksum
    ):
        raise RuntimeError("Existing raw document does not match its ingestion run")


def canonical_db_checksum(checksum: str) -> str:
    """Return the ``sha256:``-prefixed form of an accepted note's db_checksum."""
    normalized = checksum.removeprefix("sha256:")
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise RuntimeError("Accepted document db_checksum is not canonical SHA-256")
    return f"sha256:{normalized}"
