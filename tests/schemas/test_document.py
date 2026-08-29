"""Tests for the portable document-ingestion contracts."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from basic_memory.markdown.entity_parser import EntityParser
from basic_memory.schemas.document import (
    DocumentAgentObservationV1,
    DocumentAgentOutputV1,
    DocumentAgentRelationV1,
    DocumentExtractionStatus,
    DocumentExtractionV1,
    DocumentIngestionFailureV1,
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
    document_markdown_checksum,
    enrich_document_markdown,
    parse_document_ingestion_run_markdown,
    parse_document_markdown,
    require_document_ingestion_transition,
)


SOURCE_ENTITY_ID = UUID("11111111-1111-1111-1111-111111111111")
SOURCE_CHECKSUM = f"sha256:{'a' * 64}"
EXTRACTION_OPTIONS_HASH = f"sha256:{'d' * 64}"
EXTRACTED_AT = datetime(2026, 8, 1, 1, 2, 3, tzinfo=UTC)
CANONICAL_CREATED = datetime(2026, 8, 1, 1, 3, tzinfo=UTC)
CANONICAL_MODIFIED = datetime(2026, 8, 1, 1, 4, tzinfo=UTC)
RUN_ID = UUID(
    derive_document_ingestion_run_id(
        source_entity_external_id=SOURCE_ENTITY_ID,
        source_checksum=SOURCE_CHECKSUM,
        pipeline_version="bm-document-ingest-v1",
        extractor_engine="firecrawl/pdf-inspector",
        extractor_version="0.2.6",
        extraction_profile="pdf-inspector-v1",
        extraction_options_hash=EXTRACTION_OPTIONS_HASH,
        prompt_version="document-normalize-v1",
    )
)


def source() -> DocumentSourceV1:
    return DocumentSourceV1(
        media_type="application/pdf",
        entity_external_id=SOURCE_ENTITY_ID,
        file_path="docs/report.pdf",
        checksum=SOURCE_CHECKSUM,
        size_bytes=1024,
        storage_version_id="tigris-source-v1",
        storage_etag='"source-etag-v1"',
    )


def extraction() -> DocumentExtractionV1:
    return DocumentExtractionV1(
        engine="firecrawl/pdf-inspector",
        engine_version="0.2.6",
        profile="pdf-inspector-v1",
        options_hash=EXTRACTION_OPTIONS_HASH,
        classification="mixed",
        status=DocumentExtractionStatus.needs_ocr,
        extracted_at=EXTRACTED_AT,
        duration_ms=12,
        page_count=3,
        extracted_page_count=2,
        requires_ocr=True,
        ocr_page_count=1,
        pages_needing_ocr=(3,),
        confidence=0.91,
        has_tables=True,
        has_columns=False,
    )


def raw_document() -> DocumentMarkdownV1:
    return DocumentMarkdownV1(
        frontmatter=DocumentNoteFrontmatterV1(
            title="Report",
            tags=("document", "pdf", "imported"),
            permalink="docs/report-pdf",
            created=CANONICAL_CREATED,
            modified=CANONICAL_MODIFIED,
            source=source(),
            extraction=extraction(),
            ingestion=DocumentIngestionV1(
                stage=DocumentIngestionStage.raw,
                pipeline_version="bm-document-ingest-v1",
                prompt_version="document-normalize-v1",
                run_id=RUN_ID,
                input_checksum=SOURCE_CHECKSUM,
            ),
            bm_parse_semantics=False,
        ),
        body="# Extracted report\r\n\r\n- [accidental] not an observation\r\n\r\n[[Not a relation]]\r\n",
    )


def raw_revision() -> DocumentRevisionReferenceV1:
    return DocumentRevisionReferenceV1(
        kind=DocumentRevisionKind.raw_extraction,
        checksum=document_markdown_checksum(assemble_document_markdown(raw_document())),
        storage_version_id="tigris-v1",
        created_at=EXTRACTED_AT,
    )


def test_document_markdown_round_trip_is_canonical() -> None:
    document = raw_document()

    first = assemble_document_markdown(document)
    second = assemble_document_markdown(document)

    assert first == second
    assert first.startswith("---\nschema_version: '1'\ntitle: Report\ntype: document\n")
    assert first.endswith("[[Not a relation]]\n")
    assert "\r" not in first
    assert parse_document_markdown(first) == DocumentMarkdownV1(
        frontmatter=document.frontmatter,
        body="# Extracted report\n\n- [accidental] not an observation\n\n[[Not a relation]]\n",
    )
    assert (
        DocumentNoteFrontmatterV1.model_validate(document.frontmatter.model_dump())
        == document.frontmatter
    )


def test_document_contract_rejects_invalid_nested_provenance() -> None:
    data = source().model_dump(mode="json")
    data["extra"] = "not trusted"

    with pytest.raises(ValidationError, match="extra"):
        DocumentSourceV1.model_validate(data)

    with pytest.raises(ValidationError, match="checksum"):
        DocumentSourceV1.model_validate({**source().model_dump(), "checksum": "A" * 64})

    with pytest.raises(ValidationError, match="pages_needing_ocr"):
        DocumentExtractionV1.model_validate(
            {
                **extraction().model_dump(),
                "ocr_page_count": 2,
            }
        )

    with pytest.raises(ValidationError, match="timezone"):
        DocumentExtractionV1.model_validate(
            {
                **extraction().model_dump(),
                "extracted_at": datetime(2026, 8, 1, 1, 2, 3),
            }
        )


@pytest.mark.parametrize("page", [True, 1.0, "1"])
def test_extraction_rejects_coerced_ocr_page_numbers(page: object) -> None:
    with pytest.raises(ValidationError, match="pages_needing_ocr"):
        DocumentExtractionV1.model_validate(
            {
                **extraction().model_dump(),
                "pages_needing_ocr": (page,),
            }
        )


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"extracted_page_count": 4}, "extracted_page_count"),
        ({"pages_needing_ocr": (3, 2), "ocr_page_count": 2}, "unique and sorted"),
        ({"pages_needing_ocr": (4,)}, "one-based page numbers"),
        ({"requires_ocr": False}, "requires_ocr"),
        (
            {
                "status": DocumentExtractionStatus.needs_ocr,
                "requires_ocr": False,
                "ocr_page_count": 0,
                "pages_needing_ocr": (),
            },
            "needs_ocr extraction status",
        ),
        (
            {
                "status": DocumentExtractionStatus.complete,
                "requires_ocr": False,
                "ocr_page_count": 0,
                "pages_needing_ocr": (),
            },
            "complete extraction",
        ),
    ],
)
def test_extraction_page_invariants_fail_fast(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        DocumentExtractionV1.model_validate({**extraction().model_dump(), **updates})


def test_document_frontmatter_requires_consistent_trusted_metadata() -> None:
    raw = raw_document()

    with pytest.raises(ValidationError, match="raw ingestion stage"):
        DocumentIngestionV1.model_validate(
            {
                **raw.frontmatter.ingestion.model_dump(),
                "base_checksum": f"sha256:{'b' * 64}",
            }
        )
    with pytest.raises(ValidationError, match="non-raw ingestion stages"):
        DocumentIngestionV1(
            stage=DocumentIngestionStage.ready,
            pipeline_version="pipeline-v1",
            run_id=RUN_ID,
            input_checksum=SOURCE_CHECKSUM,
        )
    with pytest.raises(ValidationError, match="authors"):
        DocumentMetadataV1(authors=("Ada", "Ada"))

    frontmatter_data = raw.frontmatter.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError, match="tags"):
        DocumentNoteFrontmatterV1.model_validate(
            {**frontmatter_data, "tags": ["document", "document"]}
        )
    with pytest.raises(ValidationError, match="input_checksum"):
        DocumentNoteFrontmatterV1.model_validate(
            {
                **frontmatter_data,
                "ingestion": {
                    **frontmatter_data["ingestion"],
                    "input_checksum": f"sha256:{'b' * 64}",
                },
            }
        )
    with pytest.raises(ValidationError, match="run_id"):
        DocumentNoteFrontmatterV1.model_validate(
            {
                **frontmatter_data,
                "ingestion": {
                    **frontmatter_data["ingestion"],
                    "run_id": "22222222-2222-2222-2222-222222222222",
                },
            }
        )
    with pytest.raises(ValidationError, match="bm_parse_semantics"):
        DocumentNoteFrontmatterV1.model_validate({**frontmatter_data, "bm_parse_semantics": True})
    without_canonical_timestamps = DocumentNoteFrontmatterV1.model_validate(
        {**frontmatter_data, "created": None, "modified": None}
    )
    assert without_canonical_timestamps.created is None

    with pytest.raises(ValidationError, match="canonical note timestamp"):
        DocumentNoteFrontmatterV1.model_validate(
            {**frontmatter_data, "created": datetime(2026, 8, 1)}
        )


def test_ingestion_stages_require_explicit_forward_transitions() -> None:
    require_document_ingestion_transition(
        DocumentIngestionStage.raw,
        DocumentIngestionStage.needs_review,
    )
    require_document_ingestion_transition(
        DocumentIngestionStage.needs_review,
        DocumentIngestionStage.ready,
    )

    with pytest.raises(ValueError, match="raw -> raw"):
        require_document_ingestion_transition(
            DocumentIngestionStage.raw,
            DocumentIngestionStage.raw,
        )
    with pytest.raises(ValueError, match="ready -> failed"):
        require_document_ingestion_transition(
            DocumentIngestionStage.ready,
            DocumentIngestionStage.failed,
        )


def test_document_identities_and_paths_are_deterministic() -> None:
    assert derive_document_note_external_id(SOURCE_ENTITY_ID) == derive_document_note_external_id(
        str(SOURCE_ENTITY_ID)
    )
    assert derive_document_ingestion_run_id(
        source_entity_external_id=SOURCE_ENTITY_ID,
        source_checksum=SOURCE_CHECKSUM,
        pipeline_version="bm-document-ingest-v1",
        extractor_engine="firecrawl/pdf-inspector",
        extractor_version="0.2.6",
        extraction_profile="pdf-inspector-v1",
        extraction_options_hash=EXTRACTION_OPTIONS_HASH,
        prompt_version="document-normalize-v1",
    ) == str(RUN_ID)
    assert derive_document_note_path("docs/report.pdf") == "docs/report.pdf.md"
    assert derive_document_ingestion_run_path(RUN_ID) == f"document-ingestion-runs/{RUN_ID}.md"

    with pytest.raises(ValueError, match="project-relative"):
        derive_document_note_path("../report.pdf")
    with pytest.raises(ValueError, match="project-relative"):
        derive_document_note_path(".")
    with pytest.raises(ValueError, match="canonical sha256"):
        derive_document_ingestion_run_id(
            source_entity_external_id=SOURCE_ENTITY_ID,
            source_checksum="a" * 64,
            pipeline_version="pipeline-v1",
            extractor_engine="extractor",
            extractor_version="1",
            extraction_profile="profile-v1",
            extraction_options_hash=EXTRACTION_OPTIONS_HASH,
            prompt_version=None,
        )
    with pytest.raises(ValueError, match="pipeline_version"):
        derive_document_ingestion_run_id(
            source_entity_external_id=SOURCE_ENTITY_ID,
            source_checksum=SOURCE_CHECKSUM,
            pipeline_version="  ",
            extractor_engine="extractor",
            extractor_version="1",
            extraction_profile="profile-v1",
            extraction_options_hash=EXTRACTION_OPTIONS_HASH,
            prompt_version=None,
        )


@pytest.mark.parametrize(
    (
        "extractor_engine",
        "extractor_version",
        "extraction_profile",
        "extraction_options_hash",
        "prompt_version",
    ),
    [
        (
            "other-extractor",
            "0.2.6",
            "pdf-inspector-v1",
            EXTRACTION_OPTIONS_HASH,
            "document-normalize-v1",
        ),
        (
            "firecrawl/pdf-inspector",
            "0.2.7",
            "pdf-inspector-v1",
            EXTRACTION_OPTIONS_HASH,
            "document-normalize-v1",
        ),
        (
            "firecrawl/pdf-inspector",
            "0.2.6",
            "pdf-inspector-v2",
            EXTRACTION_OPTIONS_HASH,
            "document-normalize-v1",
        ),
        (
            "firecrawl/pdf-inspector",
            "0.2.6",
            "pdf-inspector-v1",
            f"sha256:{'e' * 64}",
            "document-normalize-v1",
        ),
        (
            "firecrawl/pdf-inspector",
            "0.2.6",
            "pdf-inspector-v1",
            EXTRACTION_OPTIONS_HASH,
            "document-normalize-v2",
        ),
        (
            "firecrawl/pdf-inspector",
            "0.2.6",
            "pdf-inspector-v1",
            EXTRACTION_OPTIONS_HASH,
            None,
        ),
    ],
)
def test_ingestion_run_identity_includes_every_extraction_shaping_input(
    extractor_engine: str,
    extractor_version: str,
    extraction_profile: str,
    extraction_options_hash: str,
    prompt_version: str | None,
) -> None:
    assert derive_document_ingestion_run_id(
        source_entity_external_id=SOURCE_ENTITY_ID,
        source_checksum=SOURCE_CHECKSUM,
        pipeline_version="bm-document-ingest-v1",
        extractor_engine=extractor_engine,
        extractor_version=extractor_version,
        extraction_profile=extraction_profile,
        extraction_options_hash=extraction_options_hash,
        prompt_version=prompt_version,
    ) != str(RUN_ID)


@pytest.mark.parametrize(
    (
        "pipeline_version",
        "extractor_engine",
        "extractor_version",
        "extraction_profile",
        "prompt_version",
        "message",
    ),
    [
        ("  ", "extractor", "1", "profile", "prompt", "pipeline_version"),
        ("pipeline", "  ", "1", "profile", "prompt", "extractor_engine"),
        ("pipeline", "extractor", "  ", "profile", "prompt", "extractor_version"),
        ("pipeline", "extractor", "1", "  ", "prompt", "extraction_profile"),
        ("pipeline", "extractor", "1", "profile", "  ", "prompt_version"),
    ],
)
def test_ingestion_run_identity_rejects_blank_versions(
    pipeline_version: str,
    extractor_engine: str,
    extractor_version: str,
    extraction_profile: str,
    prompt_version: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        derive_document_ingestion_run_id(
            source_entity_external_id=SOURCE_ENTITY_ID,
            source_checksum=SOURCE_CHECKSUM,
            pipeline_version=pipeline_version,
            extractor_engine=extractor_engine,
            extractor_version=extractor_version,
            extraction_profile=extraction_profile,
            extraction_options_hash=EXTRACTION_OPTIONS_HASH,
            prompt_version=prompt_version,
        )


def test_ingestion_run_identity_requires_canonical_options_hash() -> None:
    with pytest.raises(ValueError, match="canonical sha256"):
        derive_document_ingestion_run_id(
            source_entity_external_id=SOURCE_ENTITY_ID,
            source_checksum=SOURCE_CHECKSUM,
            pipeline_version="bm-document-ingest-v1",
            extractor_engine="firecrawl/pdf-inspector",
            extractor_version="0.2.6",
            extraction_profile="pdf-inspector-v1",
            extraction_options_hash="d" * 64,
            prompt_version="document-normalize-v1",
        )


def test_agent_enrichment_preserves_provenance_and_requires_raw_checksum() -> None:
    raw = raw_document()
    raw_checksum = document_markdown_checksum(assemble_document_markdown(raw))
    agent_output = DocumentAgentOutputV1(
        title="Quarterly Report",
        body="# Quarterly Report\n\nA normalized summary.",
        tags=("quarterly",),
        document=DocumentMetadataV1(
            kind="report",
            language="en",
            authors=("Ada Example",),
        ),
        observations=(
            DocumentAgentObservationV1(
                category="summary",
                content="Revenue increased.",
                tags=("finance",),
                context="reported result",
            ),
        ),
        relations=(
            DocumentAgentRelationV1(
                relation_type="references",
                target="Finance plan",
                context="supporting plan",
            ),
        ),
    )
    target_ingestion = DocumentIngestionV1(
        stage=DocumentIngestionStage.ready,
        pipeline_version=raw.frontmatter.ingestion.pipeline_version,
        prompt_version=raw.frontmatter.ingestion.prompt_version,
        run_id=raw.frontmatter.ingestion.run_id,
        input_checksum=raw.frontmatter.ingestion.input_checksum,
        base_checksum=raw_checksum,
    )

    enriched = enrich_document_markdown(raw, agent_output, target_ingestion)

    assert enriched.frontmatter.source == raw.frontmatter.source
    assert enriched.frontmatter.extraction == raw.frontmatter.extraction
    assert enriched.frontmatter.ingestion == target_ingestion
    assert enriched.frontmatter.permalink == raw.frontmatter.permalink
    assert enriched.frontmatter.created == raw.frontmatter.created
    assert enriched.frontmatter.modified == raw.frontmatter.modified
    assert enriched.frontmatter.tags == ("document", "pdf", "imported", "quarterly")
    assert enriched.frontmatter.bm_parse_semantics is True
    assert "- [summary] Revenue increased. #finance (reported result)" in enriched.body
    assert "- references [[Finance plan]] (supporting plan)" in enriched.body

    with pytest.raises(ValidationError, match="source"):
        DocumentAgentOutputV1.model_validate(
            {
                **agent_output.model_dump(mode="json"),
                "source": source().model_dump(mode="json"),
            }
        )

    wrong_base = target_ingestion.model_copy(update={"base_checksum": f"sha256:{'b' * 64}"})
    with pytest.raises(ValueError, match="base_checksum"):
        enrich_document_markdown(raw, agent_output, wrong_base)

    failed_target = target_ingestion.model_copy(update={"stage": DocumentIngestionStage.failed})
    with pytest.raises(ValueError, match="only produce ready or needs_review"):
        enrich_document_markdown(raw, agent_output, failed_target)

    different_run = target_ingestion.model_copy(
        update={"run_id": UUID("22222222-2222-2222-2222-222222222222")}
    )
    with pytest.raises(ValueError, match="trusted ingestion identity"):
        enrich_document_markdown(raw, agent_output, different_run)

    needs_review_target = target_ingestion.model_copy(
        update={"stage": DocumentIngestionStage.needs_review}
    )
    needs_review = enrich_document_markdown(raw, agent_output, needs_review_target)
    reviewed_checksum = document_markdown_checksum(assemble_document_markdown(needs_review))
    with pytest.raises(ValueError, match="requires a raw document"):
        enrich_document_markdown(
            needs_review,
            agent_output,
            target_ingestion.model_copy(update={"base_checksum": reviewed_checksum}),
        )


@pytest.mark.parametrize(
    "body",
    [
        "- [injected] Unstructured observation",
        "A summary with [[Injected relation]].",
        "A summary with #injected-tag",
    ],
)
def test_agent_body_rejects_unstructured_semantics(body: str) -> None:
    with pytest.raises(ValidationError, match="structured fields"):
        DocumentAgentOutputV1(title="Unsafe output", body=body)


@pytest.mark.parametrize("body", ["Summary.\n\n```", "Summary.\n\n<!--"])
def test_agent_body_cannot_hide_declared_semantics(body: str) -> None:
    with pytest.raises(ValidationError, match="preserve exactly the declared semantics"):
        DocumentAgentOutputV1(
            title="Unsafe output",
            body=body,
            observations=(DocumentAgentObservationV1(category="summary", content="Declared fact"),),
        )


@pytest.mark.parametrize("tag", ["finance,secret", "#hidden", "two words"])
def test_agent_output_rejects_noncanonical_frontmatter_tags(tag: str) -> None:
    with pytest.raises(ValidationError, match="tags"):
        DocumentAgentOutputV1(title="Unsafe output", body="Summary.", tags=(tag,))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("A fact with [[Injected relation]]", "exactly one observation"),
        ("A fact with #injected-tag", "fields must match"),
        ("A fact (forged context)", "fields must match"),
    ],
)
def test_agent_observation_rejects_undeclared_parser_semantics(
    content: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        DocumentAgentObservationV1(category="summary", content=content)

    with pytest.raises(ValidationError):
        DocumentAgentObservationV1(category="summary(forged)", content="A fact")


def test_agent_payload_rejects_unsafe_relation_targets() -> None:
    with pytest.raises(ValidationError, match="one line"):
        DocumentAgentRelationV1(
            relation_type="references",
            target="First line\nSecond line",
        )
    with pytest.raises(ValidationError, match="wikilink delimiters"):
        DocumentAgentRelationV1(
            relation_type="references",
            target="[[Nested target]]",
        )
    with pytest.raises(ValidationError, match="exactly one relation"):
        DocumentAgentRelationV1(
            relation_type="references",
            target="Target #injected-tag",
        )
    with pytest.raises(ValidationError, match="fields must match"):
        DocumentAgentRelationV1(
            relation_type="references",
            target="project::note",
        )

    empty = enrich_document_markdown(
        raw_document(),
        DocumentAgentOutputV1(title="Empty extraction", body=""),
        DocumentIngestionV1(
            stage=DocumentIngestionStage.needs_review,
            pipeline_version="bm-document-ingest-v1",
            prompt_version="document-normalize-v1",
            run_id=RUN_ID,
            input_checksum=SOURCE_CHECKSUM,
            base_checksum=document_markdown_checksum(assemble_document_markdown(raw_document())),
        ),
    )
    assert empty.body == ""


@pytest.mark.asyncio
async def test_raw_extraction_body_stays_out_of_graph_only_via_opt_out(tmp_path) -> None:
    """Regression for the Codex P1 on #1178.

    A serialized raw extraction whose body carries observation, hashtag, and wiki-link
    syntax must parse through the real EntityParser with zero graph semantics while the
    body text survives for search. The control parse of the same note with the opt-out
    stripped MUST mint them, proving bm_parse_semantics is the operative suppression.
    """
    parser = EntityParser(tmp_path)
    raw = DocumentMarkdownV1(
        frontmatter=raw_document().frontmatter,
        body="# Extracted\n\n- [result] something #hashtag\n\nSee [[Some Note]].\n",
    )
    markdown = assemble_document_markdown(raw)
    assert "bm_parse_semantics: false" in markdown

    parsed = await parser.parse_markdown_content(tmp_path / "raw-doc.md", markdown)
    assert parsed.observations == []
    assert parsed.relations == []
    assert parsed.content is not None
    assert "- [result] something #hashtag" in parsed.content
    assert "[[Some Note]]" in parsed.content

    without_opt_out = "\n".join(
        line for line in markdown.splitlines() if not line.startswith("bm_parse_semantics:")
    )
    control = await parser.parse_markdown_content(tmp_path / "control-doc.md", without_opt_out)
    assert [observation.content for observation in control.observations] == ["something #hashtag"]
    assert "hashtag" in (control.observations[0].tags or [])
    assert [relation.target for relation in control.relations] == ["Some Note"]


@pytest.mark.asyncio
async def test_raw_search_only_note_does_not_mint_graph_semantics(tmp_path) -> None:
    parser = EntityParser(tmp_path)
    raw = raw_document()

    parsed_raw = await parser.parse_markdown_content(
        tmp_path / derive_document_note_path(raw.frontmatter.source.file_path),
        assemble_document_markdown(raw),
    )

    assert parsed_raw.content is not None
    assert "accidental" in parsed_raw.content
    assert parsed_raw.observations == []
    assert parsed_raw.relations == []

    raw_checksum = document_markdown_checksum(assemble_document_markdown(raw))
    enriched = enrich_document_markdown(
        raw,
        DocumentAgentOutputV1(
            title="Report",
            body="A clean summary.",
            observations=(
                DocumentAgentObservationV1(category="summary", content="Intentional fact"),
            ),
            relations=(DocumentAgentRelationV1(relation_type="references", target="Source note"),),
        ),
        DocumentIngestionV1(
            stage=DocumentIngestionStage.ready,
            pipeline_version=raw.frontmatter.ingestion.pipeline_version,
            prompt_version=raw.frontmatter.ingestion.prompt_version,
            run_id=raw.frontmatter.ingestion.run_id,
            input_checksum=raw.frontmatter.ingestion.input_checksum,
            base_checksum=raw_checksum,
        ),
    )
    parsed_enriched = await parser.parse_markdown_content(
        tmp_path / derive_document_note_path(enriched.frontmatter.source.file_path),
        assemble_document_markdown(enriched),
    )

    assert [observation.content for observation in parsed_enriched.observations] == [
        "Intentional fact"
    ]
    assert [relation.target for relation in parsed_enriched.relations] == ["Source note"]


def test_ingestion_run_note_round_trip_and_failure_contract() -> None:
    document_external_id = UUID(derive_document_note_external_id(SOURCE_ENTITY_ID))
    raw_output = DocumentIngestionRunOutputV1(
        document_entity_external_id=document_external_id,
        document_file_path=derive_document_note_path(source().file_path),
        raw=raw_revision(),
    )
    raw_run = DocumentIngestionRunMarkdownV1(
        frontmatter=DocumentIngestionRunFrontmatterV1(
            title=f"Document ingestion {RUN_ID}",
            permalink=f"document-ingestion-runs/{RUN_ID}",
            created=CANONICAL_CREATED,
            modified=CANONICAL_MODIFIED,
            source=source(),
            extraction=extraction(),
            ingestion=DocumentIngestionRunStateV1(
                run_id=RUN_ID,
                stage=DocumentIngestionStage.raw,
                pipeline_version="bm-document-ingest-v1",
                prompt_version="document-normalize-v1",
                input_checksum=SOURCE_CHECKSUM,
                started_at=EXTRACTED_AT,
            ),
            output=raw_output,
        ),
        body="# Document ingestion run\n\nRaw extraction materialized.",
    )

    markdown = assemble_document_ingestion_run_markdown(raw_run)

    assert parse_document_ingestion_run_markdown(markdown) == DocumentIngestionRunMarkdownV1(
        frontmatter=raw_run.frontmatter,
        body="# Document ingestion run\n\nRaw extraction materialized.\n",
    )
    assert (
        DocumentIngestionRunFrontmatterV1.model_validate(raw_run.frontmatter.model_dump())
        == raw_run.frontmatter
    )

    with pytest.raises(ValidationError, match="failure"):
        DocumentIngestionRunFrontmatterV1(
            title=f"Document ingestion {RUN_ID}",
            source=source(),
            ingestion=DocumentIngestionRunStateV1(
                run_id=RUN_ID,
                stage=DocumentIngestionStage.failed,
                pipeline_version="bm-document-ingest-v1",
                input_checksum=SOURCE_CHECKSUM,
                started_at=EXTRACTED_AT,
                completed_at=EXTRACTED_AT,
            ),
            output=raw_output,
        )

    failed = DocumentIngestionRunFrontmatterV1(
        title=f"Document ingestion {RUN_ID}",
        created=None,
        modified=None,
        source=source(),
        ingestion=DocumentIngestionRunStateV1(
            run_id=RUN_ID,
            stage=DocumentIngestionStage.failed,
            pipeline_version="bm-document-ingest-v1",
            input_checksum=SOURCE_CHECKSUM,
            started_at=EXTRACTED_AT,
            completed_at=EXTRACTED_AT,
        ),
        output=raw_output,
        failure=DocumentIngestionFailureV1(
            code="agent_timeout",
            message="The enrichment provider timed out.",
            retryable=True,
        ),
    )
    assert failed.failure is not None


def test_ingestion_run_output_requires_raw_extraction_revision() -> None:
    with pytest.raises(ValidationError, match="raw_extraction"):
        DocumentIngestionRunOutputV1(
            document_entity_external_id=SOURCE_ENTITY_ID,
            document_file_path="docs/report.pdf.md",
            raw=raw_revision().model_copy(update={"kind": DocumentRevisionKind.human_edit}),
        )


def test_ingestion_run_requires_consistent_terminal_result() -> None:
    document_external_id = UUID(derive_document_note_external_id(SOURCE_ENTITY_ID))
    raw_output = DocumentIngestionRunOutputV1(
        document_entity_external_id=document_external_id,
        document_file_path=derive_document_note_path(source().file_path),
        raw=raw_revision(),
    )
    raw_state = DocumentIngestionRunStateV1(
        run_id=RUN_ID,
        stage=DocumentIngestionStage.raw,
        pipeline_version="bm-document-ingest-v1",
        prompt_version="document-normalize-v1",
        input_checksum=SOURCE_CHECKSUM,
        started_at=EXTRACTED_AT,
    )

    with pytest.raises(ValidationError, match="raw ingestion run cannot be completed"):
        DocumentIngestionRunStateV1.model_validate(
            {**raw_state.model_dump(), "completed_at": EXTRACTED_AT}
        )
    with pytest.raises(ValidationError, match="requires completed_at"):
        DocumentIngestionRunStateV1(
            run_id=RUN_ID,
            stage=DocumentIngestionStage.ready,
            pipeline_version="bm-document-ingest-v1",
            input_checksum=SOURCE_CHECKSUM,
            started_at=EXTRACTED_AT,
        )
    with pytest.raises(ValidationError, match="cannot precede"):
        DocumentIngestionRunStateV1(
            run_id=RUN_ID,
            stage=DocumentIngestionStage.ready,
            pipeline_version="bm-document-ingest-v1",
            input_checksum=SOURCE_CHECKSUM,
            started_at=EXTRACTED_AT,
            completed_at=datetime(2026, 7, 31, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="timezone"):
        DocumentIngestionRunStateV1(
            run_id=RUN_ID,
            stage=DocumentIngestionStage.raw,
            pipeline_version="bm-document-ingest-v1",
            input_checksum=SOURCE_CHECKSUM,
            started_at=datetime(2026, 8, 1),
        )

    base_frontmatter = {
        "title": f"Document ingestion {RUN_ID}",
        "source": source().model_dump(mode="json"),
        "extraction": extraction().model_dump(mode="json"),
        "ingestion": raw_state.model_dump(mode="json"),
        "output": raw_output.model_dump(mode="json"),
    }
    with pytest.raises(ValidationError, match="input_checksum"):
        DocumentIngestionRunFrontmatterV1.model_validate(
            {
                **base_frontmatter,
                "ingestion": {
                    **base_frontmatter["ingestion"],
                    "input_checksum": f"sha256:{'b' * 64}",
                },
            }
        )
    with pytest.raises(ValidationError, match="extraction metadata"):
        DocumentIngestionRunFrontmatterV1.model_validate({**base_frontmatter, "extraction": None})
    with pytest.raises(ValidationError, match="raw document revision"):
        DocumentIngestionRunFrontmatterV1.model_validate({**base_frontmatter, "output": None})
    with pytest.raises(ValidationError, match="cannot declare a current document revision"):
        DocumentIngestionRunFrontmatterV1.model_validate(
            {
                **base_frontmatter,
                "output": {
                    **base_frontmatter["output"],
                    "current": base_frontmatter["output"]["raw"],
                },
            }
        )
    with pytest.raises(ValidationError, match="cannot declare a failure"):
        DocumentIngestionRunFrontmatterV1.model_validate(
            {
                **base_frontmatter,
                "failure": {
                    "code": "unexpected",
                    "message": "Unexpected failure.",
                    "retryable": False,
                },
            }
        )
    with pytest.raises(ValidationError, match="run_id"):
        DocumentIngestionRunFrontmatterV1.model_validate(
            {
                **base_frontmatter,
                "ingestion": {
                    **base_frontmatter["ingestion"],
                    "run_id": "22222222-2222-2222-2222-222222222222",
                },
            }
        )
    with pytest.raises(ValidationError, match="output document identity"):
        DocumentIngestionRunFrontmatterV1.model_validate(
            {
                **base_frontmatter,
                "output": {
                    **base_frontmatter["output"],
                    "document_entity_external_id": "22222222-2222-2222-2222-222222222222",
                },
            }
        )
    with pytest.raises(ValidationError, match="output document path"):
        DocumentIngestionRunFrontmatterV1.model_validate(
            {
                **base_frontmatter,
                "output": {
                    **base_frontmatter["output"],
                    "document_file_path": "docs/other.pdf.md",
                },
            }
        )

    ready_state = DocumentIngestionRunStateV1(
        run_id=RUN_ID,
        stage=DocumentIngestionStage.ready,
        pipeline_version="bm-document-ingest-v1",
        prompt_version="document-normalize-v1",
        input_checksum=SOURCE_CHECKSUM,
        started_at=EXTRACTED_AT,
        completed_at=EXTRACTED_AT,
    )
    with pytest.raises(ValidationError, match="current document revision"):
        DocumentIngestionRunFrontmatterV1(
            title=f"Document ingestion {RUN_ID}",
            source=source(),
            extraction=extraction(),
            ingestion=ready_state,
            output=raw_output,
        )

    current_revision = DocumentRevisionReferenceV1(
        kind=DocumentRevisionKind.agent_enriched,
        checksum=f"sha256:{'c' * 64}",
        storage_version_id="tigris-v2",
        created_at=EXTRACTED_AT,
    )
    ready_output = raw_output.model_copy(update={"current": current_revision})
    with pytest.raises(ValidationError, match="must be enriched or human-edited"):
        DocumentIngestionRunFrontmatterV1(
            title=f"Document ingestion {RUN_ID}",
            source=source(),
            extraction=extraction(),
            ingestion=ready_state,
            output=raw_output.model_copy(update={"current": raw_revision()}),
        )
    with pytest.raises(ValidationError, match="extraction metadata"):
        DocumentIngestionRunFrontmatterV1(
            title=f"Document ingestion {RUN_ID}",
            source=source(),
            ingestion=ready_state,
            output=ready_output,
        )
    ready = DocumentIngestionRunFrontmatterV1(
        title=f"Document ingestion {RUN_ID}",
        source=source(),
        extraction=extraction(),
        ingestion=ready_state,
        output=ready_output,
    )
    assert ready.output == ready_output

    with pytest.raises(ValidationError, match="cannot declare a failure"):
        DocumentIngestionRunFrontmatterV1(
            title=f"Document ingestion {RUN_ID}",
            source=source(),
            extraction=extraction(),
            ingestion=ready_state,
            output=ready_output,
            failure=DocumentIngestionFailureV1(
                code="unexpected",
                message="Unexpected failure.",
                retryable=False,
            ),
        )


def test_document_markdown_body_rejects_nul_bytes() -> None:
    """A raw extractor's NUL byte must be rejected before it reaches Postgres TEXT (#1178 review)."""
    with pytest.raises(ValueError, match="NUL"):
        DocumentMarkdownV1(frontmatter=raw_document().frontmatter, body="# Doc\x00body")


@pytest.mark.parametrize(
    "invalid_path",
    [
        "/absolute/report.pdf",
        "docs//report.pdf",
        "docs\\report.pdf",
        "docs/report.pdf/",
        # Windows drive / rooted paths escape the project root when joined (#1178 review).
        "C:/outside/report.pdf",
        "C:outside/report.pdf",
        # Whitespace-padded paths must be rejected, not silently stripped (#1178 review).
        " docs/report.pdf",
        "docs/report.pdf ",
        "docs/report.pdf\t",
        # Windows reserved device names and NTFS stream suffixes never materialize
        # as portable sidecars (#1178 review).
        "docs/CON.pdf",
        "docs/nul",
        "docs/COM1.txt",
        "docs/report.pdf:stream",
        # Other Windows-invalid components (#1178 review).
        "docs/report?.pdf",
        "docs/re*port.pdf",
        "docs/re<port>.pdf",
        'docs/re"port.pdf',
        "docs/pipe|name.pdf",
        "docs/archive./report.pdf",
        "docs/trailing /report.pdf",
    ],
)
def test_document_paths_reject_noncanonical_values(invalid_path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        derive_document_note_path(invalid_path)
