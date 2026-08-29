"""Portable contracts for parser-neutral document ingestion.

The original binary remains a file entity. These schemas describe the trusted Markdown
projection created from that source and the optional ingestion-run note that records its
lifecycle. Parser execution, storage versions, queues, and agent providers belong to the
runtime that composes these contracts.
"""

import hashlib
import json
import re
from datetime import date, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from frontmatter import Post
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from basic_memory.file_utils import dump_frontmatter, parse_frontmatter, remove_frontmatter

if TYPE_CHECKING:  # pragma: no cover - static import only
    from basic_memory.markdown.entity_parser import EntityContent


type NonEmptyText = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1),
]
# Paths must never be silently rewritten: strip_whitespace would let the model
# accept " a/b " while derive_document_note_path (which validates the raw value)
# rejects it, so a padded path would resolve to a different object (#1178 review).
type ProjectRelativePathText = Annotated[
    StrictStr,
    StringConstraints(min_length=1),
]
type Sha256Checksum = Annotated[
    StrictStr,
    StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$"),
]
type AgentTag = Annotated[
    StrictStr,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]

_DOCUMENT_INGESTION_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://basicmemory.com/schemas/document-ingestion/v1",
)
_SHA256_CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class _DocumentContractModel(BaseModel):
    """Fail-fast base for trusted ingestion boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentExtractionStatus(StrEnum):
    """Outcome reported by a parser-neutral extraction provider."""

    complete = "complete"
    partial = "partial"
    needs_ocr = "needs_ocr"
    failed = "failed"


class DocumentIngestionStage(StrEnum):
    """Lifecycle stage of the stable derived document note."""

    raw = "raw"
    ready = "ready"
    needs_review = "needs_review"
    failed = "failed"


class DocumentRevisionKind(StrEnum):
    """Meaning of a version referenced by an ingestion-run note."""

    raw_extraction = "raw_extraction"
    agent_enriched = "agent_enriched"
    human_edit = "human_edit"


class DocumentSourceV1(_DocumentContractModel):
    """Stable provenance for the original binary file entity."""

    kind: Literal["file"] = "file"
    media_type: Annotated[
        StrictStr,
        StringConstraints(pattern=r"^[a-z0-9][a-z0-9!#$&^_.+\-]*/[a-z0-9][a-z0-9!#$&^_.+\-]*$"),
    ]
    entity_external_id: UUID
    file_path: ProjectRelativePathText
    checksum: Sha256Checksum
    size_bytes: int = Field(ge=0, strict=True)
    storage_version_id: NonEmptyText | None = None
    storage_etag: NonEmptyText | None = None

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, value: str) -> str:
        return _validate_project_relative_path(value)


class DocumentExtractionV1(_DocumentContractModel):
    """Parser-neutral extraction diagnostics stored on the document note."""

    engine: NonEmptyText
    engine_version: NonEmptyText
    profile: NonEmptyText
    options_hash: Sha256Checksum
    classification: NonEmptyText | None = None
    status: DocumentExtractionStatus
    extracted_at: datetime
    duration_ms: int | None = Field(default=None, ge=0, strict=True)
    page_count: int = Field(ge=0, strict=True)
    extracted_page_count: int = Field(ge=0, strict=True)
    requires_ocr: StrictBool
    ocr_page_count: int = Field(ge=0, strict=True)
    pages_needing_ocr: tuple[StrictInt, ...] = ()
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, strict=True)
    has_encoding_issues: StrictBool = False
    has_tables: StrictBool = False
    has_columns: StrictBool = False

    @field_validator("extracted_at")
    @classmethod
    def require_aware_extracted_at(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, field_name="extracted_at")

    @model_validator(mode="after")
    def validate_page_diagnostics(self) -> "DocumentExtractionV1":
        if self.extracted_page_count > self.page_count:
            raise ValueError("extracted_page_count cannot exceed page_count")
        if self.ocr_page_count != len(self.pages_needing_ocr):
            raise ValueError("ocr_page_count must equal the number of pages_needing_ocr")
        if self.pages_needing_ocr != tuple(sorted(set(self.pages_needing_ocr))):
            raise ValueError("pages_needing_ocr must be unique and sorted")
        if any(page < 1 or page > self.page_count for page in self.pages_needing_ocr):
            raise ValueError("pages_needing_ocr must contain valid one-based page numbers")
        if self.requires_ocr != bool(self.pages_needing_ocr):
            raise ValueError("requires_ocr must match whether pages_needing_ocr is non-empty")
        if self.status is DocumentExtractionStatus.needs_ocr and not self.requires_ocr:
            raise ValueError("needs_ocr extraction status requires at least one OCR page")
        if self.status is DocumentExtractionStatus.complete:
            if self.requires_ocr or self.extracted_page_count != self.page_count:
                raise ValueError("complete extraction must cover every page without OCR gaps")
        return self


class DocumentIngestionV1(_DocumentContractModel):
    """Trusted processing state for one version of the derived document note."""

    stage: DocumentIngestionStage
    pipeline_version: NonEmptyText
    prompt_version: NonEmptyText | None = None
    run_id: UUID
    input_checksum: Sha256Checksum
    base_checksum: Sha256Checksum | None = None

    @model_validator(mode="after")
    def validate_base_checksum(self) -> "DocumentIngestionV1":
        if self.stage is DocumentIngestionStage.raw and self.base_checksum is not None:
            raise ValueError("raw ingestion stage cannot declare a base_checksum")
        if self.stage is not DocumentIngestionStage.raw and self.base_checksum is None:
            raise ValueError("non-raw ingestion stages require the raw base_checksum")
        return self


class DocumentMetadataV1(_DocumentContractModel):
    """Descriptive document fields that an enrichment pass may supply."""

    kind: NonEmptyText | None = None
    language: NonEmptyText | None = None
    authors: tuple[NonEmptyText, ...] = ()
    published_at: date | None = None

    @field_validator("authors")
    @classmethod
    def require_unique_authors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("authors must be unique")
        return value


class DocumentNoteFrontmatterV1(_DocumentContractModel):
    """Authoritative nested frontmatter for a ``type: document`` note."""

    schema_version: Literal["1"] = "1"
    title: NonEmptyText
    type: Literal["document"] = "document"
    schema_ref: Literal["schema/document-extraction"] = Field(
        default="schema/document-extraction",
        validation_alias=AliasChoices("schema", "schema_ref"),
        serialization_alias="schema",
    )
    tags: tuple[NonEmptyText, ...] = ("document",)
    permalink: NonEmptyText | None = None
    created: datetime | None = None
    modified: datetime | None = None
    source: DocumentSourceV1
    extraction: DocumentExtractionV1
    ingestion: DocumentIngestionV1
    document: DocumentMetadataV1 = Field(default_factory=DocumentMetadataV1)
    bm_parse_semantics: StrictBool

    @field_validator("tags")
    @classmethod
    def require_unique_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("tags must be unique")
        return value

    @field_validator("created", "modified")
    @classmethod
    def require_aware_canonical_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_aware_datetime(value, field_name="canonical note timestamp")

    @model_validator(mode="after")
    def validate_trusted_envelope(self) -> "DocumentNoteFrontmatterV1":
        if self.source.checksum != self.ingestion.input_checksum:
            raise ValueError("ingestion input_checksum must match the source checksum")
        _require_deterministic_run_id(
            source=self.source,
            extraction=self.extraction,
            run_id=self.ingestion.run_id,
            pipeline_version=self.ingestion.pipeline_version,
            prompt_version=self.ingestion.prompt_version,
        )

        parses_semantics = self.ingestion.stage in {
            DocumentIngestionStage.ready,
            DocumentIngestionStage.needs_review,
        }
        if self.bm_parse_semantics != parses_semantics:
            raise ValueError(
                "bm_parse_semantics must be false for raw/failed notes and true after enrichment"
            )
        return self


class DocumentMarkdownV1(_DocumentContractModel):
    """Validated document frontmatter paired with its Markdown body."""

    frontmatter: DocumentNoteFrontmatterV1
    body: StrictStr

    @field_validator("body")
    @classmethod
    def reject_nul_bytes(cls, value: str) -> str:
        # A raw extractor can emit NUL; assemble_document_markdown preserves it and
        # the hosted path writes it to Postgres note_content.markdown_content (TEXT),
        # which rejects NUL before EntityParser's later cleanup runs (#1178 review).
        if "\x00" in value:
            raise ValueError("markdown body must not contain NUL bytes")
        return value


class DocumentRevisionReferenceV1(_DocumentContractModel):
    """One exact Markdown revision referenced by an ingestion-run note."""

    kind: DocumentRevisionKind
    checksum: Sha256Checksum
    storage_version_id: NonEmptyText | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        return _require_aware_datetime(value, field_name="created_at")


class DocumentIngestionFailureV1(_DocumentContractModel):
    """Structured terminal failure details for an ingestion run."""

    code: NonEmptyText
    message: NonEmptyText
    retryable: StrictBool


class DocumentIngestionRunStateV1(_DocumentContractModel):
    """Lifecycle metadata for a queryable ingestion-run history note."""

    run_id: UUID
    stage: DocumentIngestionStage
    pipeline_version: NonEmptyText
    prompt_version: NonEmptyText | None = None
    input_checksum: Sha256Checksum
    started_at: datetime
    completed_at: datetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_aware_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_aware_datetime(value, field_name="ingestion run timestamp")

    @model_validator(mode="after")
    def validate_completion(self) -> "DocumentIngestionRunStateV1":
        if self.stage is DocumentIngestionStage.raw and self.completed_at is not None:
            raise ValueError("raw ingestion run cannot be completed")
        if self.stage is not DocumentIngestionStage.raw and self.completed_at is None:
            raise ValueError("terminal ingestion run requires completed_at")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        return self


class DocumentIngestionRunOutputV1(_DocumentContractModel):
    """Stable document identity and exact revisions produced by an ingestion run."""

    document_entity_external_id: UUID
    document_file_path: ProjectRelativePathText
    raw: DocumentRevisionReferenceV1
    current: DocumentRevisionReferenceV1 | None = None

    @field_validator("document_file_path")
    @classmethod
    def validate_document_file_path(cls, value: str) -> str:
        return _validate_project_relative_path(value)

    @model_validator(mode="after")
    def require_raw_extraction_revision(self) -> "DocumentIngestionRunOutputV1":
        if self.raw.kind is not DocumentRevisionKind.raw_extraction:
            raise ValueError("raw ingestion output must reference a raw_extraction revision")
        return self


class DocumentIngestionRunFrontmatterV1(_DocumentContractModel):
    """Authoritative frontmatter for a ``type: document_ingestion_run`` note."""

    schema_version: Literal["1"] = "1"
    title: NonEmptyText
    type: Literal["document_ingestion_run"] = "document_ingestion_run"
    schema_ref: Literal["schema/document-ingestion-run"] = Field(
        default="schema/document-ingestion-run",
        validation_alias=AliasChoices("schema", "schema_ref"),
        serialization_alias="schema",
    )
    tags: tuple[NonEmptyText, ...] = ("document", "ingestion-run")
    permalink: NonEmptyText | None = None
    created: datetime | None = None
    modified: datetime | None = None
    source: DocumentSourceV1
    extraction: DocumentExtractionV1 | None = None
    ingestion: DocumentIngestionRunStateV1
    output: DocumentIngestionRunOutputV1 | None = None
    failure: DocumentIngestionFailureV1 | None = None
    bm_parse_semantics: Literal[False] = False

    @field_validator("created", "modified")
    @classmethod
    def require_aware_canonical_timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _require_aware_datetime(value, field_name="canonical note timestamp")

    @model_validator(mode="after")
    def validate_run_result(self) -> "DocumentIngestionRunFrontmatterV1":
        if self.source.checksum != self.ingestion.input_checksum:
            raise ValueError("ingestion input_checksum must match the source checksum")
        if self.extraction is not None:
            _require_deterministic_run_id(
                source=self.source,
                extraction=self.extraction,
                run_id=self.ingestion.run_id,
                pipeline_version=self.ingestion.pipeline_version,
                prompt_version=self.ingestion.prompt_version,
            )
        if self.output is not None:
            expected_document_external_id = UUID(
                derive_document_note_external_id(self.source.entity_external_id)
            )
            if self.output.document_entity_external_id != expected_document_external_id:
                raise ValueError("output document identity must match the source entity")

            expected_document_path = derive_document_note_path(self.source.file_path)
            if self.output.document_file_path != expected_document_path:
                raise ValueError("output document path must be derived from the source path")

        if self.ingestion.stage is DocumentIngestionStage.raw:
            if self.extraction is None:
                raise ValueError("raw ingestion run requires extraction metadata")
            if self.output is None:
                raise ValueError("raw ingestion run requires its raw document revision")
            if self.output.current is not None:
                raise ValueError("raw ingestion run cannot declare a current document revision")
            if self.failure is not None:
                raise ValueError("raw ingestion run cannot declare a failure")
        elif self.ingestion.stage is DocumentIngestionStage.failed:
            if self.failure is None:
                raise ValueError("failed ingestion run requires structured failure details")
        else:
            if self.extraction is None:
                raise ValueError("successful ingestion run requires extraction metadata")
            if self.output is None or self.output.current is None:
                raise ValueError("successful ingestion run requires its current document revision")
            if self.output.current.kind is DocumentRevisionKind.raw_extraction:
                raise ValueError(
                    "successful ingestion run current revision must be enriched or human-edited"
                )
            if self.failure is not None:
                raise ValueError("successful ingestion run cannot declare a failure")
        return self


class DocumentIngestionRunMarkdownV1(_DocumentContractModel):
    """Validated ingestion-run frontmatter paired with its Markdown body."""

    frontmatter: DocumentIngestionRunFrontmatterV1
    body: StrictStr


def _parse_agent_semantics(markdown: str) -> "EntityContent":
    # Importing the schema package is part of lightweight CLI registration.
    # The Markdown parser is only needed when validating actual agent output.
    from basic_memory.markdown.entity_parser import parse

    return parse(markdown)


class DocumentAgentObservationV1(_DocumentContractModel):
    """One bounded observation proposed by an untrusted enrichment agent."""

    category: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\[\]()\r\n]+$"),
    ]
    content: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\r\n]+$"),
    ]
    tags: tuple[AgentTag, ...] = ()
    context: (
        Annotated[
            StrictStr,
            StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\r\n()]+$"),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def require_exact_parser_semantics(self) -> "DocumentAgentObservationV1":
        parsed = _parse_agent_semantics(_format_agent_observation(self))
        if parsed.relations or len(parsed.observations) != 1:
            raise ValueError("agent observation must produce exactly one observation")

        parsed_observation = parsed.observations[0]
        expected_content = self.content
        if self.tags:
            expected_content += " " + " ".join(f"#{tag}" for tag in self.tags)
        if (
            parsed_observation.category != self.category
            or parsed_observation.content != expected_content
            or tuple(parsed_observation.tags or ()) != self.tags
            or parsed_observation.context != self.context
        ):
            raise ValueError("agent observation fields must match parsed Markdown semantics")
        return self


class DocumentAgentRelationV1(_DocumentContractModel):
    """One bounded relation proposed by an untrusted enrichment agent."""

    relation_type: Annotated[
        StrictStr,
        StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$"),
    ]
    target: Annotated[
        StrictStr,
        StringConstraints(strip_whitespace=True, min_length=1),
    ]
    context: (
        Annotated[
            StrictStr,
            StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\r\n()]+$"),
        ]
        | None
    ) = None

    @field_validator("target")
    @classmethod
    def require_single_line_target(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("relation target must fit on one line")
        if "[[" in value or "]]" in value:
            raise ValueError("relation target cannot contain nested wikilink delimiters")
        return value

    @model_validator(mode="after")
    def require_exact_parser_semantics(self) -> "DocumentAgentRelationV1":
        parsed = _parse_agent_semantics(_format_agent_relation(self))
        if parsed.observations or len(parsed.relations) != 1:
            raise ValueError("agent relation must produce exactly one relation")

        parsed_relation = parsed.relations[0]
        if (
            parsed_relation.type != self.relation_type
            or parsed_relation.target != self.target
            or parsed_relation.context != self.context
        ):
            raise ValueError("agent relation fields must match parsed Markdown semantics")
        return self


class DocumentAgentOutputV1(_DocumentContractModel):
    """Allowed output from a no-tools enrichment agent.

    This model deliberately has no source, extraction, ingestion, checksum, or identity fields.
    Trusted code reconstructs that envelope after validating this bounded semantic payload.
    """

    schema_version: Literal["1"] = "1"
    title: NonEmptyText
    body: StrictStr
    tags: tuple[AgentTag, ...] = ()
    document: DocumentMetadataV1 = Field(default_factory=DocumentMetadataV1)
    observations: tuple[DocumentAgentObservationV1, ...] = ()
    relations: tuple[DocumentAgentRelationV1, ...] = ()

    @field_validator("body")
    @classmethod
    def reject_unstructured_semantics(cls, value: str) -> str:
        parsed = _parse_agent_semantics(value)
        if parsed.observations or parsed.relations:
            raise ValueError(
                "agent body cannot contain observations or relations; use structured fields"
            )
        return value

    @model_validator(mode="after")
    def require_exact_assembled_semantics(self) -> "DocumentAgentOutputV1":
        parsed = _parse_agent_semantics(_assemble_agent_body(self))
        expected_observations = [
            _parse_agent_semantics(_format_agent_observation(observation)).observations[0]
            for observation in self.observations
        ]
        expected_relations = [
            _parse_agent_semantics(_format_agent_relation(relation)).relations[0]
            for relation in self.relations
        ]
        if parsed.observations != expected_observations or parsed.relations != expected_relations:
            raise ValueError(
                "assembled agent Markdown must preserve exactly the declared semantics"
            )
        return self


_ALLOWED_DOCUMENT_INGESTION_TRANSITIONS: dict[
    DocumentIngestionStage,
    frozenset[DocumentIngestionStage],
] = {
    DocumentIngestionStage.raw: frozenset(
        {
            DocumentIngestionStage.ready,
            DocumentIngestionStage.needs_review,
            DocumentIngestionStage.failed,
        }
    ),
    DocumentIngestionStage.needs_review: frozenset(
        {DocumentIngestionStage.ready, DocumentIngestionStage.failed}
    ),
    DocumentIngestionStage.ready: frozenset(),
    DocumentIngestionStage.failed: frozenset(),
}


def require_document_ingestion_transition(
    current: DocumentIngestionStage,
    target: DocumentIngestionStage,
) -> None:
    """Fail unless ``target`` is an explicit forward ingestion transition."""
    if target not in _ALLOWED_DOCUMENT_INGESTION_TRANSITIONS[current]:
        raise ValueError(
            f"invalid document ingestion transition: {current.value} -> {target.value}"
        )


def derive_document_note_external_id(source_entity_external_id: str | UUID) -> str:
    """Derive one stable note identity from the original file entity identity."""
    source_id = UUID(str(source_entity_external_id))
    return str(uuid5(_DOCUMENT_INGESTION_NAMESPACE, f"document-note:{source_id}"))


def derive_document_ingestion_run_id(
    *,
    source_entity_external_id: str | UUID,
    source_checksum: str,
    pipeline_version: str,
    extractor_engine: str,
    extractor_version: str,
    extraction_profile: str,
    extraction_options_hash: str,
    prompt_version: str | None,
) -> str:
    """Derive one idempotency identity from every extraction-shaping input."""
    source_id = UUID(str(source_entity_external_id))
    checksum = _validate_sha256_checksum(source_checksum)
    options_hash = _validate_sha256_checksum(extraction_options_hash)
    normalized_prompt_version = (
        _normalize_run_identity_text(prompt_version, field_name="prompt_version")
        if prompt_version is not None
        else None
    )
    identity = json.dumps(
        {
            "extraction_options_hash": options_hash,
            "extractor_engine": _normalize_run_identity_text(
                extractor_engine,
                field_name="extractor_engine",
            ),
            "extractor_version": _normalize_run_identity_text(
                extractor_version,
                field_name="extractor_version",
            ),
            "extraction_profile": _normalize_run_identity_text(
                extraction_profile,
                field_name="extraction_profile",
            ),
            "pipeline_version": _normalize_run_identity_text(
                pipeline_version,
                field_name="pipeline_version",
            ),
            "prompt_version": normalized_prompt_version,
            "source_checksum": checksum,
            "source_entity_external_id": str(source_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return str(uuid5(_DOCUMENT_INGESTION_NAMESPACE, identity))


def derive_document_note_path(source_file_path: str) -> str:
    """Return the portable sidecar path for one source file (for example ``report.pdf.md``)."""
    source_path = _validate_project_relative_path(source_file_path)
    return f"{source_path}.md"


def derive_document_ingestion_run_path(run_id: str | UUID) -> str:
    """Return the stable, path-independent note location for one ingestion run."""
    return f"document-ingestion-runs/{UUID(str(run_id))}.md"


def document_markdown_checksum(markdown: str) -> str:
    """Return the canonical SHA-256 identity for exact Markdown bytes."""
    return f"sha256:{hashlib.sha256(markdown.encode('utf-8')).hexdigest()}"


def assemble_document_markdown(document: DocumentMarkdownV1) -> str:
    """Serialize a validated document note into deterministic canonical Markdown."""
    return _assemble_markdown(document.frontmatter, document.body)


def parse_document_markdown(markdown: str) -> DocumentMarkdownV1:
    """Parse and strictly validate one canonical document note."""
    return DocumentMarkdownV1(
        frontmatter=DocumentNoteFrontmatterV1.model_validate(parse_frontmatter(markdown)),
        body=_extract_markdown_body(markdown),
    )


def assemble_document_ingestion_run_markdown(run: DocumentIngestionRunMarkdownV1) -> str:
    """Serialize a validated ingestion-run note into deterministic canonical Markdown."""
    return _assemble_markdown(run.frontmatter, run.body)


def parse_document_ingestion_run_markdown(markdown: str) -> DocumentIngestionRunMarkdownV1:
    """Parse and strictly validate one canonical ingestion-run note."""
    return DocumentIngestionRunMarkdownV1(
        frontmatter=DocumentIngestionRunFrontmatterV1.model_validate(parse_frontmatter(markdown)),
        body=_extract_markdown_body(markdown),
    )


def enrich_document_markdown(
    raw_document: DocumentMarkdownV1,
    agent_output: DocumentAgentOutputV1,
    target_ingestion: DocumentIngestionV1,
) -> DocumentMarkdownV1:
    """Build an enriched note while preserving the trusted raw provenance envelope."""
    raw_frontmatter = raw_document.frontmatter
    if raw_frontmatter.ingestion.stage is not DocumentIngestionStage.raw:
        raise ValueError("agent enrichment requires a raw document")
    require_document_ingestion_transition(
        raw_frontmatter.ingestion.stage,
        target_ingestion.stage,
    )
    if target_ingestion.stage not in {
        DocumentIngestionStage.ready,
        DocumentIngestionStage.needs_review,
    }:
        raise ValueError("agent enrichment can only produce ready or needs_review documents")

    expected_base_checksum = document_markdown_checksum(assemble_document_markdown(raw_document))
    if target_ingestion.base_checksum != expected_base_checksum:
        raise ValueError("target ingestion base_checksum does not match the raw document")
    if (
        target_ingestion.run_id != raw_frontmatter.ingestion.run_id
        or target_ingestion.input_checksum != raw_frontmatter.ingestion.input_checksum
        or target_ingestion.pipeline_version != raw_frontmatter.ingestion.pipeline_version
        or target_ingestion.prompt_version != raw_frontmatter.ingestion.prompt_version
    ):
        raise ValueError("enrichment cannot replace trusted ingestion identity or version fields")

    tags = tuple(dict.fromkeys((*raw_frontmatter.tags, *agent_output.tags)))
    return DocumentMarkdownV1(
        frontmatter=DocumentNoteFrontmatterV1(
            title=agent_output.title,
            tags=tags,
            permalink=raw_frontmatter.permalink,
            created=raw_frontmatter.created,
            modified=raw_frontmatter.modified,
            source=raw_frontmatter.source,
            extraction=raw_frontmatter.extraction,
            ingestion=target_ingestion,
            document=agent_output.document,
            bm_parse_semantics=True,
        ),
        body=_assemble_agent_body(agent_output),
    )


def _assemble_agent_body(agent_output: DocumentAgentOutputV1) -> str:
    sections: list[str] = []
    if normalized_body := _normalize_markdown_body(agent_output.body):
        sections.append(normalized_body.rstrip("\n"))
    if agent_output.observations:
        observations = [
            _format_agent_observation(observation) for observation in agent_output.observations
        ]
        sections.append("## Observations\n\n" + "\n".join(observations))
    if agent_output.relations:
        relations = [_format_agent_relation(relation) for relation in agent_output.relations]
        sections.append("## Relations\n\n" + "\n".join(relations))
    return "\n\n".join(sections) + ("\n" if sections else "")


def _format_agent_observation(observation: DocumentAgentObservationV1) -> str:
    line = f"- [{observation.category}] {observation.content}"
    if observation.tags:
        line += " " + " ".join(f"#{tag}" for tag in observation.tags)
    if observation.context:
        line += f" ({observation.context})"
    return line


def _format_agent_relation(relation: DocumentAgentRelationV1) -> str:
    line = f"- {relation.relation_type} [[{relation.target}]]"
    if relation.context:
        line += f" ({relation.context})"
    return line


def _assemble_markdown(frontmatter: _DocumentContractModel, body: str) -> str:
    metadata = frontmatter.model_dump(mode="json", exclude_none=True, by_alias=True)
    return dump_frontmatter(Post(_normalize_markdown_body(body), **metadata))


def _extract_markdown_body(markdown: str) -> str:
    body = remove_frontmatter(markdown, strip=False).lstrip("\r\n")
    return _normalize_markdown_body(body)


def _normalize_markdown_body(body: str) -> str:
    normalized = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    return f"{normalized}\n" if normalized else ""


def _require_aware_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


def _validate_sha256_checksum(value: str) -> str:
    if _SHA256_CHECKSUM_PATTERN.fullmatch(value) is None:
        raise ValueError("checksum must use canonical sha256:<lowercase hex> form")
    return value


def _require_deterministic_run_id(
    *,
    source: DocumentSourceV1,
    extraction: DocumentExtractionV1,
    run_id: UUID,
    pipeline_version: str,
    prompt_version: str | None,
) -> None:
    expected_run_id = UUID(
        derive_document_ingestion_run_id(
            source_entity_external_id=source.entity_external_id,
            source_checksum=source.checksum,
            pipeline_version=pipeline_version,
            extractor_engine=extraction.engine,
            extractor_version=extraction.engine_version,
            extraction_profile=extraction.profile,
            extraction_options_hash=extraction.options_hash,
            prompt_version=prompt_version,
        )
    )
    if run_id != expected_run_id:
        raise ValueError(
            "ingestion run_id must match its deterministic source and pipeline identity"
        )


def _normalize_run_identity_text(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    return normalized


_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)


def _validate_project_relative_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ValueError("path must be a non-empty POSIX project-relative path")
    if value != value.strip():
        raise ValueError("path must not have leading or trailing whitespace")
    path = PurePosixPath(value)
    # A Windows drive or rooted path ("C:/x", "C:x", "\\x") passes PurePosixPath's
    # is_absolute() yet escapes the project root when FileService joins it with
    # base_path, so reject it the way note_move validation does (#1178 review).
    windows_path = PureWindowsPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or windows_path.drive
        or windows_path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
        or value.endswith("/")
    ):
        raise ValueError("path must be a canonical POSIX project-relative path")
    # A component that is a Windows reserved device name (CON, NUL, COM1 ...) or
    # carries an NTFS ":stream" suffix never materializes as a portable sidecar on
    # Windows; no retry, reindex, or sweep can make it converge (#1178 review).
    for component in path.parts:
        if ":" in component:
            raise ValueError("path components must not contain ':'")
        stem = component.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise ValueError(f"path component '{component}' is a reserved device name")
    return value
