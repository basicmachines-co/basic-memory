"""Local project-directory runtime for raw document ingestion.

Cloud reads sources from object storage and accepts notes through a queue. The
local runtime reads the file under the project directory, writes the sidecar
note back into it, and asks the API to index the new Markdown so the knowledge
graph sees it without waiting for the watcher. The file bytes' SHA-256 stands in
for a storage ETag: a local file has no version id, so the checksum is the only
generation marker available.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from basic_memory.document_ingestion.csv_extractor import CSV_MEDIA_TYPE, CsvExtractor
from basic_memory.document_ingestion.markitdown_extractor import markitdown_extractors
from basic_memory.document_ingestion.pdf_inspector import PdfInspector
from basic_memory.document_ingestion.raw_document import (
    PDF_MEDIA_TYPE,
    DocumentExtractor,
    DocumentSourceChangedError,
    DocumentSourceEntity,
    DocumentSourceSnapshot,
    PdfDocumentExtractor,
    RawDocumentArtifacts,
    RawDocumentWriteResult,
    build_raw_ingestion_run_markdown,
    canonical_db_checksum,
    raw_document_matches,
)
from basic_memory.file_utils import ParseError
from basic_memory.schemas.document import (
    DocumentIngestionStage,
    DocumentMarkdownV1,
    assemble_document_markdown,
    derive_document_ingestion_run_path,
    document_markdown_checksum,
    parse_document_ingestion_run_markdown,
    parse_document_markdown,
)
from basic_memory.schemas.v2.entity import EntityResolveResponse, EntityResponseV2
from basic_memory.services.file_service import FileService


class DocumentSourceResolutionError(RuntimeError):
    """The requested path did not resolve to its own indexed file entity."""


class DocumentSidecarConflictError(RuntimeError):
    """The sidecar path holds content this runtime must not overwrite."""


class DocumentKnowledgeApi(Protocol):
    """The three knowledge-API calls the local runtime needs (satisfied by KnowledgeClient)."""

    async def resolve_entity_response(
        self, identifier: str, *, strict: bool = False
    ) -> EntityResolveResponse: ...

    async def get_entity(self, entity_id: str) -> EntityResponseV2: ...

    async def index_file(self, file_path: str) -> object:
        """Index one Markdown file; only the side effect matters here."""


def default_document_extractors(
    *, python_executable: str = sys.executable
) -> dict[str, DocumentExtractor]:
    """Every extractor the local runtime can dispatch to, keyed by media type."""
    extractors: dict[str, DocumentExtractor] = {
        PDF_MEDIA_TYPE: PdfDocumentExtractor(PdfInspector(python_executable=python_executable)),
        CSV_MEDIA_TYPE: CsvExtractor(),
    }
    extractors.update(markitdown_extractors(python_executable=python_executable))
    return extractors


def sha256_checksum(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ApiDocumentSourceEntityResolver:
    """Resolve a project-relative path to its indexed file entity through the API."""

    knowledge: DocumentKnowledgeApi

    async def resolve(self, file_path: str) -> DocumentSourceEntity:
        resolved = await self.knowledge.resolve_entity_response(file_path, strict=True)
        # Strict resolution still tries ``<path>.md`` when the path itself is not
        # indexed, so an existing sidecar would be mistaken for its own source.
        if resolved.file_path != file_path:
            raise DocumentSourceResolutionError(
                f"{file_path!r} resolved to {resolved.file_path!r}; "
                "the source file itself is not indexed"
            )
        entity = await self.knowledge.get_entity(resolved.external_id)
        return DocumentSourceEntity(
            entity_id=entity.id,
            external_id=UUID(entity.external_id),
            file_path=entity.file_path,
            media_type=entity.content_type,
        )


@dataclass(frozen=True, slots=True)
class LocalDocumentSourceReader:
    """Read source bytes from the project directory; the checksum is the generation."""

    project_home: Path

    async def read(
        self,
        entity: DocumentSourceEntity,
        *,
        observed_etag: str | None,
    ) -> DocumentSourceSnapshot:
        content = await asyncio.to_thread((self.project_home / entity.file_path).read_bytes)
        checksum = sha256_checksum(content)
        if observed_etag is not None and observed_etag != checksum:
            raise DocumentSourceChangedError(
                f"{entity.file_path} changed since it was observed ({observed_etag} -> {checksum})"
            )
        return DocumentSourceSnapshot(
            entity=entity,
            content=content,
            checksum=checksum,
            size_bytes=len(content),
            storage_etag=checksum,
        )

    async def require_current(self, snapshot: DocumentSourceSnapshot) -> None:
        current = await asyncio.to_thread(
            (self.project_home / snapshot.entity.file_path).read_bytes
        )
        if sha256_checksum(current) != snapshot.checksum:
            raise DocumentSourceChangedError(
                f"{snapshot.entity.file_path} changed on disk during extraction"
            )


@dataclass(frozen=True, slots=True)
class AcceptedDocumentNote:
    """Keep physical file identity separate from normalized run provenance."""

    file_checksum: str
    projection_checksum: str
    written: bool


@dataclass(frozen=True, slots=True)
class LocalRawDocumentWriter:
    """Write the sidecar and run notes into the project directory and index them."""

    file_service: FileService
    knowledge: DocumentKnowledgeApi

    async def write(self, artifacts: RawDocumentArtifacts) -> RawDocumentWriteResult:
        document = await accept_document_note(self.file_service, self.knowledge, artifacts)
        run_created = await accept_run_note(
            self.file_service, self.knowledge, artifacts, raw_checksum=document.projection_checksum
        )
        return RawDocumentWriteResult(
            document_external_id=artifacts.document_external_id,
            document_file_path=artifacts.document_file_path,
            document_db_checksum=document.file_checksum,
            run_id=artifacts.run_id,
            run_file_path=artifacts.run_file_path,
            document_created=document.written,
            run_created=run_created,
        )


async def accept_document_note(
    file_service: FileService,
    knowledge: DocumentKnowledgeApi,
    artifacts: RawDocumentArtifacts,
) -> AcceptedDocumentNote:
    """Write the sidecar note unless an identical raw projection already exists.

    Return physical and normalized projection checksums with the write status.
    """
    path = artifacts.document_file_path
    if await file_service.exists(path):
        existing_markdown = await file_service.read_file_content(path)
        on_disk_checksum = canonical_db_checksum(await file_service.compute_checksum(path))
        try:
            existing = parse_document_markdown(existing_markdown)
        except (ParseError, ValueError) as error:
            # No frontmatter (ParseError) or frontmatter that fails the contract
            # (pydantic's ValidationError is a ValueError): the file at the sidecar
            # path is a hand-written note, not a generated projection.
            raise DocumentSidecarConflictError(
                f"{path} exists and is not a generated document note; "
                f"move it aside to ingest {artifacts.source.file_path}"
            ) from error
        if existing.frontmatter.ingestion.stage is not DocumentIngestionStage.raw:
            raise DocumentSidecarConflictError(
                f"{path} has been enriched past the raw stage; refusing to overwrite it"
            )
        if raw_document_matches(existing, artifacts):
            return AcceptedDocumentNote(on_disk_checksum, raw_projection_checksum(existing), False)
        # Same source path, different bytes or engine: the raw projection is
        # rebuilt from the new run, but only while it is still the projection an
        # earlier run wrote. Note content is canonical, and a raw note a person
        # has annotated must not be replaced silently.
        await require_untouched_raw_projection(file_service, existing, path=path)
        # Compare-and-swap: the note must still be the bytes judged untouched above.
        if canonical_db_checksum(await file_service.compute_checksum(path)) != on_disk_checksum:
            raise DocumentSidecarConflictError(
                f"{path} changed while it was being replaced; re-run the import"
            )
    checksum = await file_service.write_file(path, artifacts.document_markdown)
    await knowledge.index_file(path)
    return AcceptedDocumentNote(
        canonical_db_checksum(checksum),
        document_markdown_checksum(artifacts.document_markdown),
        True,
    )


def raw_projection_checksum(document: DocumentMarkdownV1) -> str:
    """Checksum of a raw sidecar as its run wrote it, before the indexer's permalink.

    Run provenance uses LF serialization on creation, reuse and rebuild. Physical
    file checksums retain native line endings for filesystem change detection.
    """
    pristine_frontmatter = document.frontmatter.model_copy(update={"permalink": None})
    pristine = assemble_document_markdown(
        document.model_copy(update={"frontmatter": pristine_frontmatter})
    )
    return document_markdown_checksum(pristine)


async def require_untouched_raw_projection(
    file_service: FileService,
    existing: DocumentMarkdownV1,
    *,
    path: str,
) -> None:
    """Fail unless the raw sidecar on disk is still what its own run note recorded.

    The indexer adds a ``permalink`` to a freshly written note; that is the one
    change a pristine projection is allowed to carry. Anything else means a person
    edited the note, and the rebuild must stop instead of discarding their words.
    """
    run_id = existing.frontmatter.ingestion.run_id
    run_path = derive_document_ingestion_run_path(run_id)
    if not await file_service.exists(run_path):
        raise DocumentSidecarConflictError(
            f"{path} was written by run {run_id} but that run note is missing; "
            "move the note aside to rebuild it"
        )
    try:
        run = parse_document_ingestion_run_markdown(await file_service.read_file_content(run_path))
    except (ParseError, ValueError) as error:
        raise DocumentSidecarConflictError(
            f"{run_path} exists and is not a generated ingestion run note"
        ) from error
    output = run.frontmatter.output
    recorded_checksum = output.raw.checksum if output and output.raw else None
    if recorded_checksum != raw_projection_checksum(existing):
        raise DocumentSidecarConflictError(
            f"{path} has been edited since run {run_id} wrote it; "
            "move it aside or finish enriching it before importing the source again"
        )


async def accept_run_note(
    file_service: FileService,
    knowledge: DocumentKnowledgeApi,
    artifacts: RawDocumentArtifacts,
    *,
    raw_checksum: str,
) -> bool:
    """Write the run note for this run id unless it already names the accepted sidecar bytes."""
    path = artifacts.run_file_path
    if await file_service.exists(path):
        try:
            existing = parse_document_ingestion_run_markdown(
                await file_service.read_file_content(path)
            )
        except (ParseError, ValueError) as error:
            raise DocumentSidecarConflictError(
                f"{path} exists and is not a generated ingestion run note"
            ) from error
        output = existing.frontmatter.output
        recorded_checksum = output.raw.checksum if output and output.raw else None
        if recorded_checksum == raw_checksum:
            return False
        # Trigger: the run note names different sidecar bytes than the note on disk.
        # Why: two imports of the same unchanged source can interleave (same run id,
        #      different extracted_at), leaving one run's sidecar beside the other
        #      run's note; reusing the stale note would freeze that mismatch.
        # Outcome: rewrite the run note so provenance converges on this pass.
    markdown = build_raw_ingestion_run_markdown(
        artifacts, raw_checksum=raw_checksum, raw_created_at=datetime.now(tz=UTC)
    )
    await file_service.write_file(path, markdown)
    await knowledge.index_file(path)
    return True
