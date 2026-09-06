"""PDF extraction -> enrichment -> portable citations -> real note API regressions."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

from httpx import AsyncClient
from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from pydantic import ValidationError
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from basic_memory.document_ingestion.pdf_inspector import PdfInspectorLimits
from basic_memory.document_ingestion.pdf_inspector_worker import inspect_pdf_bytes
from basic_memory.document_ingestion.raw_document import (
    DocumentSourceEntity,
    DocumentSourceSnapshot,
    build_raw_document_artifacts,
)
from basic_memory.markdown.entity_parser import parse
from basic_memory.models import Observation, Project
from basic_memory.schemas.document import (
    DocumentAgentObservationV1,
    DocumentAgentOutputV1,
    DocumentIngestionStage,
    DocumentIngestionV1,
    DocumentMarkdownV1,
    DocumentPageLocatorV1,
    assemble_document_markdown,
    document_markdown_checksum,
    enrich_document_markdown,
    parse_document_markdown,
)


@pytest.fixture
def raw_pdf(test_project: Project) -> DocumentMarkdownV1:
    pdf_bytes = (Path(__file__).parents[1] / "tests/Non-MarkdownFileSupport.pdf").read_bytes()
    source_path = Path(test_project.path) / "docs/report #1%.pdf"
    source_path.parent.mkdir()
    source_path.write_bytes(pdf_bytes)
    source = DocumentSourceSnapshot(
        entity=DocumentSourceEntity(
            entity_id=1,
            external_id=UUID("11111111-1111-1111-1111-111111111111"),
            file_path="docs/report #1%.pdf",
            media_type="application/pdf",
        ),
        content=pdf_bytes,
        checksum=f"sha256:{hashlib.sha256(pdf_bytes).hexdigest()}",
        size_bytes=len(pdf_bytes),
        storage_etag="original-pdf-etag",
        storage_version_id="original-pdf-version",
    )
    extracted = inspect_pdf_bytes(pdf_bytes, max_pages=10, max_output_bytes=5_000_000)
    assert extracted.page_count == 3
    assert "<!-- Page 2 -->" in extracted.markdown
    now = datetime.now(UTC)
    artifacts = build_raw_document_artifacts(
        source, extracted, limits=PdfInspectorLimits(), started_at=now, extracted_at=now
    )
    return parse_document_markdown(artifacts.document_markdown)


def enrich(
    raw: DocumentMarkdownV1, observations: tuple[DocumentAgentObservationV1, ...]
) -> DocumentMarkdownV1:
    ingestion = raw.frontmatter.ingestion
    return enrich_document_markdown(
        raw,
        DocumentAgentOutputV1(
            title="PDF citations", body="Extracted evidence.", observations=observations
        ),
        DocumentIngestionV1(
            stage=DocumentIngestionStage.ready,
            pipeline_version=ingestion.pipeline_version,
            prompt_version=ingestion.prompt_version,
            run_id=ingestion.run_id,
            input_checksum=ingestion.input_checksum,
            base_checksum=document_markdown_checksum(assemble_document_markdown(raw)),
        ),
    )


@pytest.mark.asyncio
async def test_pdf_page_citation_survives_real_note_api(
    client: AsyncClient,
    test_project: Project,
    raw_pdf: DocumentMarkdownV1,
    engine_factory: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    enriched = enrich(
        raw_pdf,
        (
            DocumentAgentObservationV1(
                category="fact",
                content="The document describes non-Markdown file support.",
                tags=("pdf",),
                context="Source document",
                locator=DocumentPageLocatorV1(page=2, page_label="iv"),
            ),
        ),
    )
    response = await client.post(
        f"/v2/projects/{test_project.external_id}/knowledge/entities",
        json={
            "title": "PDF citations",
            "directory": "docs",
            "content": assemble_document_markdown(enriched),
        },
    )
    assert response.status_code == 202, response.text
    entity = response.json()
    read = await client.get(
        f"/v2/projects/{test_project.external_id}/knowledge/entities/{entity['external_id']}"
    )
    assert read.status_code == 200, read.text
    stored = parse_document_markdown(read.json()["content"])
    assert stored.frontmatter.source == raw_pdf.frontmatter.source
    assert stored.frontmatter.sources is not None
    citation = stored.frontmatter.sources[0]
    assert citation.id == "document-page-2"
    assert citation.resource == "/docs/report%20%231%25.pdf#page=2"
    assert citation.locator.page_label == "iv"
    assert citation.title.endswith("p. iv")
    target = urlsplit(citation.resource)
    assert target.fragment == "page=2"
    source_bytes = (Path(test_project.path) / unquote(target.path).lstrip("/")).read_bytes()
    assert (
        f"sha256:{hashlib.sha256(source_bytes).hexdigest()}" == stored.frontmatter.source.checksum
    )
    assert "[^document-page-2]: [PDF page 2](/docs/report%20%231%25.pdf#page=2)" in stored.body
    # The accepted-content GET returns canonical Markdown; inspect the real
    # database separately to prove the write also indexed the cited observation.
    _, session_maker = engine_factory
    async with session_maker() as session:
        observations = (
            await session.scalars(
                select(Observation).where(Observation.project_id == test_project.id)
            )
        ).all()
        assert len(observations) == 1
        assert observations[0].content.endswith("[^document-page-2] #pdf")
        assert observations[0].context == "Source document"
    assert (Path(test_project.path) / entity["file_path"]).read_text(
        encoding="utf-8"
    ) == read.json()["content"]


def test_pdf_citation_ids_survive_source_and_observation_reordering(
    raw_pdf: DocumentMarkdownV1,
) -> None:
    first = DocumentAgentObservationV1(
        category="fact", content="First.", locator=DocumentPageLocatorV1(page=1)
    )
    second = DocumentAgentObservationV1(
        category="fact", content="Second.", locator=DocumentPageLocatorV1(page=2)
    )
    original = enrich(raw_pdf, (first, second))
    reordered = enrich(raw_pdf, (second, first))
    assert original.frontmatter.sources is not None
    assert reordered.frontmatter.sources is not None
    expected = {source.id: source.resource for source in original.frontmatter.sources}
    reordered = reordered.model_copy(
        update={
            "frontmatter": reordered.frontmatter.model_copy(
                update={"sources": tuple(reversed(reordered.frontmatter.sources))}
            )
        }
    )
    parsed = parse_document_markdown(assemble_document_markdown(reordered))
    assert parsed.frontmatter.sources is not None
    assert {source.id: source.resource for source in parsed.frontmatter.sources} == expected
    assert "First. [^document-page-1]" in parsed.body
    assert "Second. [^document-page-2]" in parsed.body


def test_trailing_backslash_does_not_escape_rendered_page_citation(
    raw_pdf: DocumentMarkdownV1,
) -> None:
    enriched = enrich(
        raw_pdf,
        (
            DocumentAgentObservationV1(
                category="fact",
                content="The separator is \\",
                locator=DocumentPageLocatorV1(page=2),
            ),
        ),
    )
    rendered = MarkdownIt().use(footnote_plugin).render(enriched.body)
    assert 'class="footnote-ref"' in rendered
    assert 'href="/docs/report%20%231%25.pdf#page=2"' in rendered


def test_multiple_observations_share_one_page_citation(raw_pdf: DocumentMarkdownV1) -> None:
    locator = DocumentPageLocatorV1(page=2)
    enriched = enrich(
        raw_pdf,
        (
            DocumentAgentObservationV1(category="fact", content="First.", locator=locator),
            DocumentAgentObservationV1(category="fact", content="Second.", locator=locator),
        ),
    )
    assert enriched.frontmatter.sources is not None
    assert len(enriched.frontmatter.sources) == 1
    assert enriched.body.count("[^document-page-2]:") == 1
    assert len(parse(enriched.body).observations) == 2


def test_uncited_document_retains_legacy_markdown_shape(raw_pdf: DocumentMarkdownV1) -> None:
    enriched = enrich(
        raw_pdf, (DocumentAgentObservationV1(category="fact", content="No locator."),)
    )
    markdown = assemble_document_markdown(enriched)
    assert "sources:" not in markdown
    assert "[^document-page-" not in markdown
    assert enriched.body.endswith("- [fact] No locator.\n")
    assert parse_document_markdown(markdown) == enriched


def test_pdf_citation_rejects_page_outside_extracted_document(raw_pdf: DocumentMarkdownV1) -> None:
    with pytest.raises(ValueError, match="outside the source PDF"):
        enrich(
            raw_pdf,
            (
                DocumentAgentObservationV1(
                    category="fact", content="Missing page.", locator=DocumentPageLocatorV1(page=4)
                ),
            ),
        )


def test_pdf_citation_rejects_zero_based_page() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        DocumentAgentOutputV1.model_validate(
            {
                "title": "Bad",
                "body": "",
                "observations": [{"category": "fact", "content": "Zero.", "locator": {"page": 0}}],
            }
        )


def test_pdf_citation_does_not_coerce_printed_page_label_into_page() -> None:
    with pytest.raises(ValidationError, match="valid integer"):
        DocumentAgentOutputV1.model_validate(
            {
                "title": "Bad",
                "body": "",
                "observations": [
                    {"category": "fact", "content": "Label.", "locator": {"page": "iv"}}
                ],
            }
        )


def test_pdf_citation_rejects_conflicting_labels_for_same_page(raw_pdf: DocumentMarkdownV1) -> None:
    with pytest.raises(ValueError, match="agree on its page label"):
        enrich(
            raw_pdf,
            (
                DocumentAgentObservationV1(
                    category="fact",
                    content="First.",
                    locator=DocumentPageLocatorV1(page=2, page_label="iv"),
                ),
                DocumentAgentObservationV1(
                    category="fact",
                    content="Second.",
                    locator=DocumentPageLocatorV1(page=2, page_label="v"),
                ),
            ),
        )


def test_agent_cannot_redirect_generated_pdf_footnote() -> None:
    with pytest.raises(ValidationError, match="cannot define generated"):
        DocumentAgentOutputV1(
            title="Redirect",
            body="[^document-page-2]: https://example.com/unrelated.pdf",
            observations=(
                DocumentAgentObservationV1(
                    category="fact", content="Claim.", locator=DocumentPageLocatorV1(page=2)
                ),
            ),
        )


def test_pdf_citation_rejects_source_entry_that_disagrees_with_provenance(
    raw_pdf: DocumentMarkdownV1,
) -> None:
    enriched = enrich(
        raw_pdf,
        (
            DocumentAgentObservationV1(
                category="fact", content="Claim.", locator=DocumentPageLocatorV1(page=2)
            ),
        ),
    )
    markdown = assemble_document_markdown(enriched)
    redirected = markdown.replace(
        "resource: /docs/report%20%231%25.pdf#page=2", "resource: /other.pdf#page=2"
    )
    assert redirected != markdown
    with pytest.raises(ValidationError, match="trusted source PDF page"):
        parse_document_markdown(redirected)
