"""Tests for the project-directory document ingestion runtime."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from basic_memory import file_utils
from basic_memory.document_ingestion.csv_extractor import CSV_MEDIA_TYPE
from basic_memory.document_ingestion.local_runtime import (
    ApiDocumentSourceEntityResolver,
    DocumentSidecarConflictError,
    DocumentSourceResolutionError,
    LocalDocumentSourceReader,
    LocalRawDocumentWriter,
    default_document_extractors,
)
from basic_memory.document_ingestion.markitdown_extractor import DOCX_MEDIA_TYPE, PPTX_MEDIA_TYPE
from basic_memory.document_ingestion.raw_document import (
    PDF_MEDIA_TYPE,
    DocumentSourceChangedError,
    DocumentSourceEntity,
    DocumentSourceSnapshot,
    ExtractedDocument,
    RawDocumentArtifacts,
    build_raw_document_artifacts_from_extracted,
    build_raw_ingestion_run_markdown,
)
from basic_memory.schemas.document import (
    DocumentExtractionStatus,
    DocumentExtractionV1,
    DocumentIngestionStage,
    DocumentIngestionV1,
    assemble_document_markdown,
    document_markdown_checksum,
    parse_document_ingestion_run_markdown,
    parse_document_markdown,
)
from basic_memory.schemas.v2.entity import EntityResolveResponse, EntityResponseV2
from basic_memory.services.file_service import FileService

SOURCE_EXTERNAL_ID = UUID("11111111-1111-1111-1111-111111111111")
NOW = datetime(2026, 9, 7, 3, 0, tzinfo=UTC)


class FakeKnowledgeApi:
    """Scripted stand-in for the three KnowledgeClient calls the runtime makes."""

    def __init__(self, *, resolved: EntityResolveResponse, entity: EntityResponseV2) -> None:
        self.resolved = resolved
        self.entity = entity
        self.indexed: list[str] = []

    async def resolve_entity_response(
        self, identifier: str, *, strict: bool = False
    ) -> EntityResolveResponse:
        assert strict, "source resolution must never fall back to fuzzy search"
        return self.resolved

    async def get_entity(self, entity_id: str) -> EntityResponseV2:
        assert entity_id == self.resolved.external_id
        return self.entity

    async def index_file(self, file_path: str) -> None:
        self.indexed.append(file_path)


def resolved(file_path: str) -> EntityResolveResponse:
    return EntityResolveResponse(
        external_id=str(SOURCE_EXTERNAL_ID),
        entity_id=7,
        project_external_id=str(uuid4()),
        permalink=None,
        file_path=file_path,
        title="riders.csv",
        resolution_method="path",
    )


def entity_response(file_path: str, content_type: str) -> EntityResponseV2:
    return EntityResponseV2(
        external_id=str(SOURCE_EXTERNAL_ID),
        id=7,
        title="riders.csv",
        note_type="file",
        content_type=content_type,
        file_path=file_path,
        created_at=NOW,
        updated_at=NOW,
    )


def csv_entity() -> DocumentSourceEntity:
    return DocumentSourceEntity(
        entity_id=7,
        external_id=SOURCE_EXTERNAL_ID,
        file_path="data/riders.csv",
        media_type=CSV_MEDIA_TYPE,
    )


def sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def knowledge_api(*, resolved_path: str = "data/riders.csv") -> FakeKnowledgeApi:
    return FakeKnowledgeApi(
        resolved=resolved(resolved_path),
        entity=entity_response("data/riders.csv", CSV_MEDIA_TYPE),
    )


# --- Resolver ---


@pytest.mark.asyncio
async def test_resolver_maps_the_indexed_source_entity() -> None:
    resolver = ApiDocumentSourceEntityResolver(knowledge_api())

    assert await resolver.resolve("data/riders.csv") == csv_entity()


@pytest.mark.asyncio
async def test_resolver_refuses_a_path_that_resolved_to_its_own_sidecar() -> None:
    resolver = ApiDocumentSourceEntityResolver(knowledge_api(resolved_path="data/riders.csv.md"))

    with pytest.raises(DocumentSourceResolutionError, match="not indexed"):
        await resolver.resolve("data/riders.csv")


# --- Reader ---


def write_source(project_home: Path, content: bytes = b"team,name\nAST,Ana\n") -> Path:
    source = project_home / "data" / "riders.csv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    return source


@pytest.mark.asyncio
async def test_reader_snapshots_bytes_with_the_checksum_as_the_generation(tmp_path: Path) -> None:
    content = b"team,name\nAST,Ana\n"
    write_source(tmp_path, content)
    reader = LocalDocumentSourceReader(tmp_path)

    snapshot = await reader.read(csv_entity(), observed_etag=None)

    assert snapshot == DocumentSourceSnapshot(
        entity=csv_entity(),
        content=content,
        checksum=sha256(content),
        size_bytes=len(content),
        storage_etag=sha256(content),
    )
    await reader.require_current(snapshot)


@pytest.mark.asyncio
async def test_reader_rejects_a_stale_observed_etag(tmp_path: Path) -> None:
    write_source(tmp_path)
    reader = LocalDocumentSourceReader(tmp_path)

    with pytest.raises(DocumentSourceChangedError, match="changed since it was observed"):
        await reader.read(csv_entity(), observed_etag="sha256:" + "0" * 64)


@pytest.mark.asyncio
async def test_require_current_detects_a_file_changed_during_extraction(tmp_path: Path) -> None:
    source = write_source(tmp_path)
    reader = LocalDocumentSourceReader(tmp_path)
    snapshot = await reader.read(csv_entity(), observed_etag=None)

    source.write_bytes(b"team,name\nAST,Someone Else\n")

    with pytest.raises(DocumentSourceChangedError, match="changed on disk"):
        await reader.require_current(snapshot)


# --- Writer ---


def extracted() -> ExtractedDocument:
    return ExtractedDocument(
        extraction=DocumentExtractionV1(
            engine="basic-memory/csv",
            engine_version="0.23.2",
            profile="csv-preview-v1",
            options_hash="sha256:" + "b" * 64,
            classification="csv",
            status=DocumentExtractionStatus.complete,
            extracted_at=NOW,
            duration_ms=1,
            page_count=0,
            extracted_page_count=0,
            requires_ocr=False,
            ocr_page_count=0,
            has_tables=True,
        ),
        markdown="| team | name |\n| --- | --- |\n| AST | Ana |\n",
        kind="csv",
        pipeline_version="csv-raw-v1",
    )


def artifacts(*, checksum_char: str = "a") -> RawDocumentArtifacts:
    snapshot = DocumentSourceSnapshot(
        entity=csv_entity(),
        content=b"team,name\nAST,Ana\n",
        checksum="sha256:" + checksum_char * 64,
        size_bytes=18,
        storage_etag="sha256:" + checksum_char * 64,
    )
    return build_raw_document_artifacts_from_extracted(snapshot, extracted(), started_at=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
async def test_writer_creates_the_sidecar_and_run_note_and_indexes_both(
    file_service: FileService,
    monkeypatch: pytest.MonkeyPatch,
    newline: str,
) -> None:
    # Exercise Windows persistence on every host so checksum drift fails locally.
    async def write_with_newlines(path: Path, content: str) -> None:
        await file_utils.write_file_atomic_bytes(
            path, content.replace("\r\n", "\n").replace("\n", newline).encode("utf-8")
        )

    monkeypatch.setattr(file_utils, "write_file_atomic", write_with_newlines)
    knowledge = knowledge_api()
    writer = LocalRawDocumentWriter(file_service, knowledge)
    built = artifacts()

    result = await writer.write(built)

    assert result.document_created is True
    assert result.run_created is True
    assert result.document_file_path == "data/riders.csv.md"
    assert knowledge.indexed == [built.document_file_path, built.run_file_path]
    sidecar_bytes = (file_service.base_path / built.document_file_path).read_bytes()
    assert result.document_db_checksum == sha256(sidecar_bytes)
    assert parse_document_markdown(sidecar_bytes.decode("utf-8")).frontmatter.document.kind == "csv"
    run_note = parse_document_ingestion_run_markdown(
        (file_service.base_path / built.run_file_path).read_text(encoding="utf-8")
    )
    assert run_note.frontmatter.output is not None
    assert run_note.frontmatter.output.raw is not None
    assert run_note.frontmatter.output.raw.checksum == document_markdown_checksum(
        built.document_markdown
    )

    reused = await writer.write(built)
    assert reused.document_db_checksum == result.document_db_checksum
    assert reused.run_created is False
    rebuilt = await writer.write(artifacts(checksum_char="b"))
    assert rebuilt.document_created is True


@pytest.mark.asyncio
async def test_writer_reuses_an_identical_raw_projection(file_service: FileService) -> None:
    knowledge = knowledge_api()
    writer = LocalRawDocumentWriter(file_service, knowledge)
    first = await writer.write(artifacts())

    second = await writer.write(artifacts())

    assert second.document_created is False
    assert second.run_created is False
    assert second.document_db_checksum == first.document_db_checksum
    assert len(knowledge.indexed) == 2


@pytest.mark.asyncio
async def test_writer_rebuilds_the_raw_projection_when_the_source_changed(
    file_service: FileService,
) -> None:
    knowledge = knowledge_api()
    writer = LocalRawDocumentWriter(file_service, knowledge)
    first = await writer.write(artifacts(checksum_char="a"))

    second = await writer.write(artifacts(checksum_char="b"))

    assert second.document_created is True
    assert second.run_created is True
    assert second.run_file_path != first.run_file_path
    sidecar = parse_document_markdown(
        (file_service.base_path / second.document_file_path).read_text(encoding="utf-8")
    )
    assert sidecar.frontmatter.source.checksum == "sha256:" + "b" * 64
    assert knowledge.indexed.count(second.document_file_path) == 2


def with_permalink(markdown: str, permalink: str) -> str:
    """Reproduce the indexer adding a permalink to a freshly written sidecar."""
    document = parse_document_markdown(markdown)
    frontmatter = document.frontmatter.model_copy(update={"permalink": permalink})
    return assemble_document_markdown(document.model_copy(update={"frontmatter": frontmatter}))


@pytest.mark.asyncio
async def test_writer_rebuilds_an_untouched_sidecar_that_the_indexer_annotated(
    file_service: FileService,
) -> None:
    writer = LocalRawDocumentWriter(file_service, knowledge_api())
    first = artifacts(checksum_char="a")
    await writer.write(first)
    sidecar = file_service.base_path / first.document_file_path
    sidecar.write_text(
        with_permalink(sidecar.read_text(encoding="utf-8"), "main/data/riders.csv"),
        encoding="utf-8",
    )

    second = await writer.write(artifacts(checksum_char="b"))

    assert second.document_created is True
    rebuilt = parse_document_markdown(sidecar.read_text(encoding="utf-8"))
    assert rebuilt.frontmatter.source.checksum == "sha256:" + "b" * 64


@pytest.mark.asyncio
async def test_unchanged_import_after_indexer_annotation_keeps_provenance_consistent(
    file_service: FileService,
) -> None:
    """The permalink the indexer adds must not leak into recorded checksums.

    Otherwise an unchanged re-import rewrites the run note with the annotated
    checksum and a later source change is refused as an edit.
    """
    knowledge = knowledge_api()
    writer = LocalRawDocumentWriter(file_service, knowledge)
    first = artifacts(checksum_char="a")
    await writer.write(first)
    sidecar = file_service.base_path / first.document_file_path
    sidecar.write_bytes(
        with_permalink(sidecar.read_text(encoding="utf-8"), "main/data/riders.csv").encode()
    )

    unchanged = await writer.write(artifacts(checksum_char="a"))
    assert unchanged.document_db_checksum == sha256(sidecar.read_bytes())
    rebuilt = await writer.write(artifacts(checksum_char="b"))

    assert unchanged.document_created is False
    assert unchanged.run_created is False
    assert rebuilt.document_created is True
    assert knowledge.indexed.count(first.run_file_path) == 1


@pytest.mark.asyncio
async def test_writer_refuses_to_replace_an_edited_raw_sidecar(file_service: FileService) -> None:
    writer = LocalRawDocumentWriter(file_service, knowledge_api())
    first = artifacts(checksum_char="a")
    await writer.write(first)
    sidecar = file_service.base_path / first.document_file_path
    sidecar.write_text(
        sidecar.read_text(encoding="utf-8") + "\nMy annotation about row three.\n",
        encoding="utf-8",
    )

    with pytest.raises(DocumentSidecarConflictError, match="edited since"):
        await writer.write(artifacts(checksum_char="b"))

    assert "My annotation about row three." in sidecar.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_writer_refuses_to_replace_a_raw_sidecar_without_its_run_note(
    file_service: FileService,
) -> None:
    writer = LocalRawDocumentWriter(file_service, knowledge_api())
    first = artifacts(checksum_char="a")
    await writer.write(first)
    (file_service.base_path / first.run_file_path).unlink()

    with pytest.raises(DocumentSidecarConflictError, match="run note is missing"):
        await writer.write(artifacts(checksum_char="b"))


@pytest.mark.asyncio
async def test_writer_refuses_to_replace_a_raw_sidecar_whose_run_note_is_not_generated(
    file_service: FileService,
) -> None:
    writer = LocalRawDocumentWriter(file_service, knowledge_api())
    first = artifacts(checksum_char="a")
    await writer.write(first)
    (file_service.base_path / first.run_file_path).write_text(
        "# Someone's note\n", encoding="utf-8"
    )

    with pytest.raises(DocumentSidecarConflictError, match="not a generated ingestion run note"):
        await writer.write(artifacts(checksum_char="b"))


@pytest.mark.asyncio
async def test_writer_refuses_when_the_sidecar_changes_before_the_replacing_write(
    file_service: FileService, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = LocalRawDocumentWriter(file_service, knowledge_api())
    first = artifacts(checksum_char="a")
    await writer.write(first)
    sidecar = file_service.base_path / first.document_file_path
    real_compute_checksum = file_service.compute_checksum
    calls = 0

    async def edit_between_checks(path: Path | str) -> str:
        # The second checksum read is the compare-and-swap guard: mutate the file
        # right before it so the guard sees bytes that differ from the decision.
        nonlocal calls
        calls += 1
        if calls == 2:
            sidecar.write_text(
                sidecar.read_text(encoding="utf-8") + "\nLate edit.\n", encoding="utf-8"
            )
        return await real_compute_checksum(path)

    monkeypatch.setattr(file_service, "compute_checksum", edit_between_checks)

    with pytest.raises(DocumentSidecarConflictError, match="changed while"):
        await writer.write(artifacts(checksum_char="b"))

    assert "Late edit." in sidecar.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_writer_rewrites_a_run_note_that_names_other_sidecar_bytes(
    file_service: FileService,
) -> None:
    knowledge = knowledge_api()
    writer = LocalRawDocumentWriter(file_service, knowledge)
    built = artifacts()
    await writer.write(built)
    # Simulate an overlapping import that replaced the sidecar after this run
    # note was written: the note now names bytes that are no longer on disk.
    stale_note = build_raw_ingestion_run_markdown(
        built, raw_checksum="sha256:" + "d" * 64, raw_created_at=NOW
    )
    await file_service.write_file(built.run_file_path, stale_note)

    second = await writer.write(built)

    assert second.document_created is False
    assert second.run_created is True
    run_note = parse_document_ingestion_run_markdown(
        (file_service.base_path / built.run_file_path).read_text(encoding="utf-8")
    )
    assert run_note.frontmatter.output is not None
    assert run_note.frontmatter.output.raw is not None
    assert run_note.frontmatter.output.raw.checksum == document_markdown_checksum(
        built.document_markdown
    )
    assert knowledge.indexed.count(built.run_file_path) == 2


@pytest.mark.asyncio
async def test_writer_refuses_a_hand_written_run_note(file_service: FileService) -> None:
    knowledge = knowledge_api()
    writer = LocalRawDocumentWriter(file_service, knowledge)
    built = artifacts()
    await file_service.write_file(built.run_file_path, "# Not a run note\n")

    with pytest.raises(DocumentSidecarConflictError, match="not a generated ingestion run note"):
        await writer.write(built)


@pytest.mark.asyncio
async def test_writer_refuses_a_hand_written_sidecar(file_service: FileService) -> None:
    built = artifacts()
    await file_service.write_file(built.document_file_path, "# My own notes about riders\n")
    writer = LocalRawDocumentWriter(file_service, knowledge_api())

    with pytest.raises(DocumentSidecarConflictError, match="not a generated document note"):
        await writer.write(built)


@pytest.mark.asyncio
async def test_writer_refuses_an_enriched_sidecar(file_service: FileService) -> None:
    built = artifacts()
    document = parse_document_markdown(built.document_markdown)
    ingestion = document.frontmatter.ingestion
    enriched_frontmatter = document.frontmatter.model_copy(
        update={
            "ingestion": DocumentIngestionV1(
                stage=DocumentIngestionStage.ready,
                pipeline_version=ingestion.pipeline_version,
                run_id=ingestion.run_id,
                input_checksum=ingestion.input_checksum,
                base_checksum="sha256:" + "c" * 64,
            ),
            "bm_parse_semantics": True,
        }
    )
    enriched = document.model_copy(update={"frontmatter": enriched_frontmatter})
    await file_service.write_file(built.document_file_path, assemble_document_markdown(enriched))
    writer = LocalRawDocumentWriter(file_service, knowledge_api())

    with pytest.raises(DocumentSidecarConflictError, match="enriched past the raw stage"):
        await writer.write(built)


def test_default_document_extractors_cover_pdf_office_and_csv() -> None:
    extractors = default_document_extractors(python_executable="python-x")

    assert set(extractors) == {PDF_MEDIA_TYPE, CSV_MEDIA_TYPE, DOCX_MEDIA_TYPE, PPTX_MEDIA_TYPE}
