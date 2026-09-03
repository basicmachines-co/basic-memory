"""Tests for the reusable batch indexing executor."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from basic_memory import db
from basic_memory.file_utils import remove_frontmatter
from basic_memory.indexing.batch_indexer import BatchIndexer
from basic_memory.indexing.models import (
    IndexingBatchResult,
    IndexInputFile,
    RelationGenerationBatchResult,
    StorageIndexFileWriter,
)
from basic_memory.indexing.note_content_reconciler import NoteContentReconciler
from basic_memory.repository import NoteContentRepository
from basic_memory.repository.semantic_errors import SemanticDependenciesMissingError
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.schemas.search import SearchItemType, SearchQuery
from basic_memory.services.exceptions import SyncFatalError


async def _create_file(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)


async def _load_input(file_service, path: str) -> IndexInputFile:
    metadata = await file_service.get_file_metadata(path)
    return IndexInputFile(
        path=path,
        size=metadata.size,
        checksum=await file_service.compute_checksum(path),
        content_type=file_service.content_type(path),
        last_modified=metadata.modified_at,
        created_at=metadata.created_at,
        content=await file_service.read_file_bytes(path),
    )


def _make_batch_indexer(
    app_config, entity_service, entity_repository, relation_repository, search_service, file_service
) -> BatchIndexer:
    return BatchIndexer(
        project_id=relation_repository.project_id,
        app_config=app_config,
        entity_service=entity_service,
        entity_repository=entity_repository,
        observation_repository=entity_service.observation_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        file_writer=StorageIndexFileWriter(storage=file_service),
        session_maker=search_service.session_maker,
    )


async def _claim_and_publish_relations(
    batch_indexer: BatchIndexer,
    result: IndexingBatchResult,
    *,
    entity_repository,
    relation_repository,
    session_maker,
    max_concurrent: int,
) -> RelationGenerationBatchResult:
    """Exercise the post-index generation claim and relation publication boundary."""
    markdown_entities = [
        indexed for indexed in result.indexed if indexed.markdown_content is not None
    ]
    async with db.scoped_session(session_maker) as session:
        entities = await entity_repository.find_by_ids(
            session,
            [indexed.entity_id for indexed in markdown_entities],
        )
    entity_by_id = {entity.id: entity for entity in entities}
    reconciler = NoteContentReconciler(
        note_content_repository=NoteContentRepository(
            project_id=relation_repository.project_id,
        ),
        session_maker=session_maker,
    )
    generation_by_entity_id: dict[int, int] = {}
    for indexed in markdown_entities:
        reconciliation = await reconciler.reconcile(
            entity=entity_by_id[indexed.entity_id],
            markdown_content=indexed.markdown_content or "",
            observed_at=datetime.now(tz=UTC),
            source="test",
        )
        assert reconciliation.generation is not None
        generation_by_entity_id[indexed.entity_id] = reconciliation.generation

    publication = await batch_indexer.publish_relation_generations(
        result.indexed,
        generation_by_entity_id=generation_by_entity_id,
        max_concurrent=max_concurrent,
    )
    result.errors.extend(publication.errors)
    result.relations_resolved = publication.relations_resolved
    result.relations_unresolved = publication.relations_unresolved
    return publication


@pytest.mark.asyncio
async def test_batch_indexer_parses_markdown_with_parallel_path(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
):
    path_one = "notes/one.md"
    path_two = "notes/two.md"
    await _create_file(
        project_config.home / path_one,
        dedent(
            """
            ---
            title: One
            type: note
            ---
            # One
            """
        ).strip(),
    )
    await _create_file(
        project_config.home / path_two,
        dedent(
            """
            ---
            title: Two
            type: note
            ---
            # Two
            """
        ).strip(),
    )

    files = {
        path_one: await _load_input(file_service, path_one),
        path_two: await _load_input(file_service, path_two),
    }
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    original_parse = entity_service.entity_parser.parse_markdown_content
    in_flight = 0
    max_in_flight = 0

    async def spy_parse(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        try:
            return await original_parse(*args, **kwargs)
        finally:
            in_flight -= 1

    entity_service.entity_parser.parse_markdown_content = spy_parse
    try:
        result = await batch_indexer.index_files(
            files,
            max_concurrent=2,
            parse_max_concurrent=2,
        )
    finally:
        entity_service.entity_parser.parse_markdown_content = original_parse

    assert max_in_flight >= 2
    assert len(result.indexed) == 2
    assert result.errors == []


@pytest.mark.asyncio
async def test_batch_indexer_honors_explicit_search_refresh_concurrency(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    paths = ("notes/one.md", "notes/two.md")
    for path in paths:
        await _create_file(project_config.home / path, f"# {Path(path).stem}\n")

    files = {path: await _load_input(file_service, path) for path in paths}
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )
    original_index_entity_data = search_service.index_entity_data
    in_flight = 0
    max_in_flight = 0

    async def spy_index_entity_data(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        try:
            return await original_index_entity_data(*args, **kwargs)
        finally:
            in_flight -= 1

    monkeypatch.setattr(search_service, "index_entity_data", spy_index_entity_data)

    result = await batch_indexer.index_files(
        files,
        max_concurrent=2,
        parse_max_concurrent=2,
        metadata_update_max_concurrent=1,
    )

    assert result.errors == []
    assert len(result.indexed) == 2
    assert max_in_flight == 1


@pytest.mark.asyncio
async def test_batch_indexer_creates_entities_with_real_db_session(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
):
    path_one = "notes/alpha.md"
    path_two = "notes/beta.md"
    await _create_file(
        project_config.home / path_one,
        dedent(
            """
            ---
            title: Alpha
            type: note
            ---
            # Alpha
            """
        ).strip(),
    )
    await _create_file(
        project_config.home / path_two,
        dedent(
            """
            ---
            title: Beta
            type: note
            ---
            # Beta
            """
        ).strip(),
    )

    files = {
        path_one: await _load_input(file_service, path_one),
        path_two: await _load_input(file_service, path_two),
    }
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    result = await batch_indexer.index_files(
        files,
        max_concurrent=2,
        parse_max_concurrent=2,
    )

    assert len(result.indexed) == 2
    assert result.errors == []

    async with db.scoped_session(search_service.session_maker) as session:
        alpha = await entity_repository.get_by_file_path(session, path_one)
        beta = await entity_repository.get_by_file_path(session, path_two)

    assert alpha is not None
    assert alpha.title == "Alpha"
    assert beta is not None
    assert beta.title == "Beta"


@pytest.mark.asyncio
async def test_batch_indexer_preserves_markdown_semantic_timestamps_on_reindex(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
):
    path = "notes/canonical-timestamps.md"
    absolute_path = project_config.home / path
    canonical_created = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    canonical_modified = datetime.fromisoformat("2024-01-16T11:45:00+05:30")
    frontmatter = dedent(
        """
        ---
        title: Canonical Timestamps
        type: note
        created: 2024-01-15T10:30:00Z
        modified: 2024-01-16T11:45:00+05:30
        ---
        """
    ).lstrip()
    await _create_file(absolute_path, f"{frontmatter}First body\n")
    os.utime(absolute_path, (1_730_000_000, 1_730_000_000))

    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )
    first_input = await _load_input(file_service, path)
    first_result = await batch_indexer.index_files({path: first_input}, max_concurrent=1)

    assert first_result.errors == []
    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, path)

    assert entity is not None
    assert first_input.last_modified is not None
    assert entity.created_at == canonical_created
    assert entity.updated_at == canonical_modified
    assert entity.mtime == first_input.last_modified.timestamp()

    await _create_file(absolute_path, f"{frontmatter}Second body\n")
    os.utime(absolute_path, (1_740_000_000, 1_740_000_000))
    second_input = await _load_input(file_service, path)
    second_result = await batch_indexer.index_files({path: second_input}, max_concurrent=1)

    assert second_result.errors == []
    async with db.scoped_session(search_service.session_maker) as session:
        reindexed = await entity_repository.get_by_file_path(session, path)

    assert reindexed is not None
    assert second_input.last_modified is not None
    assert reindexed.created_at == canonical_created
    assert reindexed.updated_at == canonical_modified
    assert reindexed.mtime == second_input.last_modified.timestamp()
    assert reindexed.mtime != entity.mtime

    included = await search_service.search(
        SearchQuery(
            after_date="2024-01-16T06:00:00Z",
            entity_types=[SearchItemType.ENTITY],
        )
    )
    excluded = await search_service.search(
        SearchQuery(
            after_date="2024-01-16T06:30:00Z",
            entity_types=[SearchItemType.ENTITY],
        )
    )

    assert [row.file_path for row in included] == [path]
    assert included[0].updated_at == canonical_modified
    assert excluded == []


@pytest.mark.asyncio
async def test_batch_indexer_returns_original_markdown_content_when_no_frontmatter_rewrite(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
):
    app_config.disable_permalinks = True

    path = "notes/original.md"
    original_content = dedent(
        """
        ---
        title: Original
        type: note
        ---
        # Original
        """
    ).strip()
    await _create_file(project_config.home / path, original_content)

    files = {path: await _load_input(file_service, path)}
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    result = await batch_indexer.index_files(
        files,
        max_concurrent=1,
        parse_max_concurrent=1,
    )

    # Trigger: Windows persists CRLF for text writes even when the test literal uses LF.
    # Why: this assertion cares about "no rewrite happened", not about forcing one newline
    #      convention across platforms.
    # Outcome: compare against the exact markdown text stored on disk for this file.
    persisted_content = (project_config.home / path).read_bytes().decode("utf-8")

    assert result.errors == []
    assert len(result.indexed) == 1
    assert result.indexed[0].markdown_content == persisted_content


@pytest.mark.asyncio
async def test_batch_indexer_indexes_non_markdown_files(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    pdf_path = "assets/doc.pdf"
    image_path = "assets/image.png"
    await _create_file(project_config.home / pdf_path, b"%PDF-1.4 test")
    await _create_file(project_config.home / image_path, b"\x89PNG\r\n\x1a\nrest")

    files = {
        pdf_path: await _load_input(file_service, pdf_path),
        image_path: await _load_input(file_service, image_path),
    }
    resolve_permalink = AsyncMock(
        side_effect=AssertionError("non-Markdown files must not resolve permalinks")
    )
    monkeypatch.setattr(entity_service, "resolve_permalink", resolve_permalink)
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    result = await batch_indexer.index_files(
        files,
        max_concurrent=2,
        parse_max_concurrent=2,
    )

    assert {indexed.path for indexed in result.indexed} == {pdf_path, image_path}
    assert all(indexed.markdown_content is None for indexed in result.indexed)

    async with db.scoped_session(search_service.session_maker) as session:
        pdf_entity = await entity_repository.get_by_file_path(session, pdf_path)
        image_entity = await entity_repository.get_by_file_path(session, image_path)
    assert pdf_entity is not None
    assert pdf_entity.content_type == "application/pdf"
    assert pdf_entity.permalink is None
    assert image_entity is not None
    assert image_entity.content_type == "image/png"
    assert image_entity.permalink is None
    resolve_permalink.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_indexer_resolves_relations_and_refreshes_search(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    search_repository,
    file_service,
    project_config,
):
    source_path = "notes/source.md"
    target_path = "notes/target.md"
    await _create_file(
        project_config.home / source_path,
        dedent(
            """
            ---
            title: Source
            type: note
            ---
            # Source

            - depends_on [[Target]]
            """
        ).strip(),
    )
    await _create_file(
        project_config.home / target_path,
        dedent(
            """
            ---
            title: Target
            type: note
            ---
            # Target
            """
        ).strip(),
    )

    files = {
        source_path: await _load_input(file_service, source_path),
        target_path: await _load_input(file_service, target_path),
    }
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    result = await batch_indexer.index_files(
        files,
        max_concurrent=2,
        parse_max_concurrent=2,
    )
    async with db.scoped_session(search_service.session_maker) as session:
        source_before_claim = await entity_repository.get_by_file_path(session, source_path)
    assert source_before_claim is not None
    assert source_before_claim.outgoing_relations == []
    indexed_source = next(indexed for indexed in result.indexed if indexed.path == source_path)
    assert [
        (relation.relation_type, relation.target_name) for relation in indexed_source.relations
    ] == [("depends_on", "Target")]
    await _claim_and_publish_relations(
        batch_indexer,
        result,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        session_maker=search_service.session_maker,
        max_concurrent=2,
    )

    async with db.scoped_session(search_service.session_maker) as session:
        source = await entity_repository.get_by_file_path(session, source_path)
        target = await entity_repository.get_by_file_path(session, target_path)
    assert source is not None
    assert target is not None
    assert len(source.outgoing_relations) == 1
    assert source.outgoing_relations[0].to_id == target.id
    assert result.relations_unresolved == 0
    assert result.search_indexed == 2

    relation_rows = await search_repository.execute_query(
        text(
            "SELECT COUNT(*) FROM search_index "
            "WHERE entity_id = :entity_id AND type = 'relation' AND to_id IS NOT NULL"
        ),
        {"entity_id": source.id},
    )
    assert relation_rows.scalar_one() == 1


@pytest.mark.asyncio
async def test_batch_indexer_keeps_file_indexed_when_semantic_dependencies_are_missing(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    path = "notes/semantic.md"
    await _create_file(
        project_config.home / path,
        dedent(
            """
            ---
            title: Semantic
            type: note
            ---
            # Semantic
            """
        ).strip(),
    )

    missing_semantic_dependencies = SemanticDependenciesMissingError(
        "semantic dependencies unavailable"
    )
    index_entity_data = AsyncMock(side_effect=missing_semantic_dependencies)
    monkeypatch.setattr(search_service, "index_entity_data", index_entity_data)
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    result = await batch_indexer.index_files(
        {path: await _load_input(file_service, path)},
        max_concurrent=1,
        parse_max_concurrent=1,
    )

    assert result.errors == []
    assert len(result.indexed) == 1
    assert result.indexed[0].path == path
    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, path)
    assert entity is not None
    assert entity.checksum == result.indexed[0].checksum
    index_entity_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_indexer_assigns_unique_permalinks_for_batch_local_conflicts(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
):
    path_one = "notes/basic memory bug.md"
    path_two = "notes/basic-memory-bug.md"
    await _create_file(
        project_config.home / path_one,
        dedent(
            """
            ---
            title: Basic Memory Bug
            type: note
            ---
            # Basic Memory Bug
            """
        ).strip(),
    )
    await _create_file(
        project_config.home / path_two,
        dedent(
            """
            ---
            title: Basic Memory Bug Report
            type: note
            ---
            # Basic Memory Bug Report
            """
        ).strip(),
    )

    files = {
        path_one: await _load_input(file_service, path_one),
        path_two: await _load_input(file_service, path_two),
    }
    original_contents = {
        path: file.content.decode("utf-8")
        for path, file in files.items()
        if file.content is not None
    }
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    result = await batch_indexer.index_files(
        files,
        max_concurrent=2,
        parse_max_concurrent=2,
    )

    assert result.errors == []
    indexed_by_path = {indexed.path: indexed for indexed in result.indexed}
    assert indexed_by_path[path_one].markdown_content is not None
    assert indexed_by_path[path_two].markdown_content is not None
    assert indexed_by_path[path_one].markdown_content != original_contents[path_one]
    assert indexed_by_path[path_two].markdown_content != original_contents[path_two]
    assert indexed_by_path[path_one].markdown_content == (
        project_config.home / path_one
    ).read_bytes().decode("utf-8")
    assert indexed_by_path[path_two].markdown_content == (
        project_config.home / path_two
    ).read_bytes().decode("utf-8")

    async with db.scoped_session(search_service.session_maker) as session:
        entities = await entity_repository.find_all(session)
    assert len(entities) == 2
    permalinks = [entity.permalink for entity in entities if entity.permalink]
    assert len(set(permalinks)) == 2


@pytest.mark.asyncio
async def test_batch_indexer_uses_parsed_markdown_body_for_malformed_frontmatter_delimiters(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
):
    app_config.disable_permalinks = True
    app_config.ensure_frontmatter_on_sync = False

    path = "notes/malformed.md"
    malformed_content = dedent(
        """
        ---
        this is not valid frontmatter
        # Malformed Frontmatter

        The parser should still index this file.
        """
    ).strip()
    await _create_file(project_config.home / path, malformed_content)

    files = {path: await _load_input(file_service, path)}
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    result = await batch_indexer.index_files(
        files,
        max_concurrent=1,
        parse_max_concurrent=1,
    )
    await _claim_and_publish_relations(
        batch_indexer,
        result,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        session_maker=search_service.session_maker,
        max_concurrent=1,
    )

    # Trigger: malformed frontmatter should pass through without normalization.
    # Why: Windows can still surface that unchanged file with CRLF line endings.
    # Outcome: compare the indexed markdown to the persisted file content, not the LF
    #          test literal used to create it.
    persisted_content = (project_config.home / path).read_bytes().decode("utf-8")

    assert result.errors == []
    assert len(result.indexed) == 1
    assert result.indexed[0].markdown_content == persisted_content

    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, path)
    assert entity is not None


@pytest.mark.asyncio
async def test_batch_indexer_indexes_malformed_yaml_without_rewriting_source(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
):
    path = "notes/malformed-yaml.md"
    original_content = dedent(
        """\
        ---
        title: Important
        description: Agent context: urgent: keep
        tags: [critical]
        ---

        # Important

        The source file must remain authoritative.
        """
    )
    await _create_file(project_config.home / path, original_content)
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    result = await batch_indexer.index_files(
        {path: await _load_input(file_service, path)},
        max_concurrent=1,
        parse_max_concurrent=1,
    )

    # The source stays byte-identical: a fenced block that is not YAML cannot be
    # rewritten field by field. Before #1451 the indexer also dropped the file
    # ("Refusing to update malformed frontmatter"), so the note was invisible to
    # every retrieval tool while readiness reported idle.
    assert result.errors == []
    assert len(result.indexed) == 1
    assert (project_config.home / path).read_text(encoding="utf-8") == original_content
    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, path)
    assert entity is not None
    indexed_content = result.indexed[0].markdown_content
    assert indexed_content is not None
    assert "The source file must remain authoritative." in indexed_content


@pytest.mark.asyncio
async def test_batch_indexer_indexes_a_letterhead_between_rules_as_body(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
):
    """A letter that opens with a horizontal-rule-delimited letterhead has fences
    but no YAML (`**Bold**` reads as an alias). With permalinks and frontmatter
    enforcement on, the default configuration, the file must be indexed as-is
    and never rewritten (#1451, found by the xAFS eval's seed guard)."""
    path = "pleadings/demand-letter.md"
    original_content = dedent(
        """\
        ---
        **OSTROWSKI LEGAL PLLC**
        Carmen Ostrowski, Esq.
        280 Garfield Place, Brooklyn NY 11215
        ---

        February 19, 2026

        Re: Demand for repair costs, HIC #0892461.
        """
    )
    await _create_file(project_config.home / path, original_content)
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )
    result = await batch_indexer.index_files(
        {path: await _load_input(file_service, path)},
        max_concurrent=1,
        parse_max_concurrent=1,
    )

    assert result.errors == []
    assert len(result.indexed) == 1
    assert (project_config.home / path).read_text(encoding="utf-8") == original_content
    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, path)
    assert entity is not None
    assert entity.title == "demand-letter"
    indexed_content = result.indexed[0].markdown_content
    assert indexed_content is not None
    assert "HIC #0892461" in indexed_content


@pytest.mark.asyncio
async def test_batch_indexer_keeps_a_scalar_preamble_in_search_content(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    """`---\nACME LEGAL PLLC\n---` is valid YAML but not a mapping. It must be indexed
    (the writer would refuse it as "must be a YAML dictionary"), and its text must
    reach the search row on the initial write and survive the deferred refresh,
    which strips frontmatter through the same `remove_frontmatter` (#1451)."""
    path = "pleadings/letter.md"
    original_content = "---\nACME LEGAL PLLC\n123 Main St\n---\n\nDear counsel, HIC #0892461.\n"
    await _create_file(project_config.home / path, original_content)
    index_entity_data = AsyncMock()
    monkeypatch.setattr(search_service, "index_entity_data", index_entity_data)
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    result = await batch_indexer.index_files(
        {path: await _load_input(file_service, path)},
        max_concurrent=1,
        parse_max_concurrent=1,
    )
    await _claim_and_publish_relations(
        batch_indexer,
        result,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        session_maker=search_service.session_maker,
        max_concurrent=1,
    )

    assert result.errors == []
    assert (project_config.home / path).read_text(encoding="utf-8") == original_content
    # The initial write and the deferred refresh both pass explicit content; the
    # relation-generation step passes None and lets the writer read the stored
    # body. Every explicit content must still carry the preamble.
    contents = [
        call.kwargs["content"]
        for call in index_entity_data.await_args_list
        if call.kwargs["content"] is not None
    ]
    assert len(contents) >= 2
    for content in contents:
        assert "ACME LEGAL PLLC" in content
        assert "HIC #0892461" in content


@pytest.mark.asyncio
async def test_batch_indexer_re_raises_fatal_sync_errors(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
):
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    async def fatal_worker(path: str) -> str:
        raise SyncFatalError(f"fatal batch failure for {path}")

    with pytest.raises(SyncFatalError, match="fatal batch failure"):
        await batch_indexer._run_bounded(
            ["notes/fatal.md"],
            limit=1,
            worker=fatal_worker,
        )


@pytest.mark.asyncio
async def test_batch_indexer_index_markdown_file_rewrites_permalink_after_repository_conflict(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    existing = await entity_service.create_entity_with_content(
        EntitySchema(
            title="Existing Note",
            directory="notes",
            content="# Existing Note\n\nOriginal content.\n",
        )
    )
    conflicting_permalink = existing.entity.permalink
    assert conflicting_permalink is not None

    path = "notes/race.md"
    await _create_file(
        project_config.home / path,
        dedent(
            f"""\
            ---
            title: Race Note
            type: note
            permalink: {conflicting_permalink}
            ---

            # Race Note

            Body content.
            """
        ),
    )

    async def stale_permalink(*args, **kwargs) -> str:
        return conflicting_permalink

    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    monkeypatch.setattr(entity_service, "resolve_permalink", stale_permalink)
    indexed = await batch_indexer.index_markdown_file(
        await _load_input(file_service, path),
        index_search=False,
    )

    persisted_content = (project_config.home / path).read_bytes().decode("utf-8")
    assert indexed.permalink == f"{conflicting_permalink}-1"
    assert indexed.markdown_content == persisted_content


@pytest.mark.asyncio
async def test_batch_indexer_index_markdown_file_can_defer_relation_resolution(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    await entity_service.create_entity_with_content(
        EntitySchema(
            title="Deferred Target",
            directory="notes",
            content="# Deferred Target\n",
        )
    )
    path = "notes/deferred-source.md"
    await _create_file(
        project_config.home / path,
        dedent(
            """
            ---
            title: Deferred Source
            type: note
            ---

            # Deferred Source

            - links_to [[Deferred Target]]
            """
        ),
    )

    resolve_link = AsyncMock(side_effect=AssertionError("relation lookup should be deferred"))
    monkeypatch.setattr(entity_service.link_resolver, "resolve_link", resolve_link)
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    indexed = await batch_indexer.index_markdown_file(
        await _load_input(file_service, path),
        index_search=False,
        resolve_relations=False,
    )
    await _claim_and_publish_relations(
        batch_indexer,
        IndexingBatchResult(indexed=[indexed]),
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        session_maker=search_service.session_maker,
        max_concurrent=1,
    )

    resolve_link.assert_not_awaited()
    async with db.scoped_session(search_service.session_maker) as session:
        source = await entity_repository.get_by_file_path(session, path)
    assert source is not None
    assert len(source.outgoing_relations) == 1
    assert source.outgoing_relations[0].to_id is None
    assert source.outgoing_relations[0].to_name == "Deferred Target"


@pytest.mark.asyncio
async def test_relation_publication_search_failure_leaves_retry_marker(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    """A failed post-publication search write remains discoverable after this batch exits."""
    path = "notes/retry-relation-search.md"
    await _create_file(
        project_config.home / path,
        "# Retry Relation Search\n\n- [note] Exact observation snapshot\n",
    )
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )
    indexed = await batch_indexer.index_markdown_file(
        await _load_input(file_service, path),
        index_search=False,
        resolve_relations=False,
    )
    original_index_entity_data = search_service.index_entity_data

    async def fail_search_refresh(*args, **kwargs):
        del args, kwargs
        raise OSError("relation search refresh failed")

    monkeypatch.setattr(search_service, "index_entity_data", fail_search_refresh)
    publication = await _claim_and_publish_relations(
        batch_indexer,
        IndexingBatchResult(indexed=[indexed]),
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        session_maker=search_service.session_maker,
        max_concurrent=1,
    )

    assert [(error_path, str(error)) for error_path, error in publication.errors] == [
        (path, "relation search refresh failed")
    ]
    async with db.scoped_session(search_service.session_maker) as session:
        pending_after_failure = await relation_repository.list_pending_search_refreshes(
            session,
            entity_id=indexed.entity_id,
        )
    assert [refresh.entity_id for refresh in pending_after_failure] == [indexed.entity_id]
    async with db.scoped_session(search_service.session_maker) as session:
        note_content = await NoteContentRepository(
            project_id=relation_repository.project_id
        ).get_by_entity_id(session, indexed.entity_id)
    assert note_content is not None

    monkeypatch.setattr(search_service, "index_entity_data", original_index_entity_data)
    await batch_indexer.refresh_indexed_entity_search(
        indexed,
        generation=note_content.db_version,
    )

    async with db.scoped_session(search_service.session_maker) as session:
        pending_after_retry = await relation_repository.list_pending_search_refreshes(
            session,
            entity_id=indexed.entity_id,
        )
    assert pending_after_retry == []


@pytest.mark.asyncio
async def test_relation_search_refresh_skips_superseded_publication_generation(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    """Generation N must not refresh search from its payload after N+1 is accepted."""
    path = "notes/superseded-relation-search.md"
    await _create_file(project_config.home / path, "# Generation N\n")
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )
    indexed = await batch_indexer.index_markdown_file(
        await _load_input(file_service, path),
        index_search=False,
        resolve_relations=False,
    )
    async with db.scoped_session(search_service.session_maker) as session:
        entities = await entity_repository.find_by_ids(session, [indexed.entity_id])
    assert len(entities) == 1

    reconciler = NoteContentReconciler(
        note_content_repository=NoteContentRepository(
            project_id=relation_repository.project_id,
        ),
        session_maker=search_service.session_maker,
    )
    generation_n = await reconciler.reconcile(
        entity=entities[0],
        markdown_content=indexed.markdown_content or "",
        observed_at=datetime.now(tz=UTC),
        source="test",
    )
    assert generation_n.generation is not None
    generation_n_value = generation_n.generation
    assert await batch_indexer.publish_relation_generation(
        indexed,
        generation=generation_n_value,
    )

    generation_n_plus_one = await reconciler.reconcile(
        entity=entities[0],
        markdown_content="# Generation N+1\n",
        observed_at=datetime.now(tz=UTC),
        source="test",
    )
    assert generation_n_plus_one.generation == generation_n_value + 1

    search_write = AsyncMock(side_effect=AssertionError("superseded refresh must be terminal"))
    monkeypatch.setattr(search_service, "index_entity_data", search_write)

    refreshed = await batch_indexer.refresh_indexed_entity_search(
        indexed,
        generation=generation_n_value,
    )

    assert refreshed is indexed
    search_write.assert_not_awaited()
    async with db.scoped_session(search_service.session_maker) as session:
        pending = await relation_repository.list_pending_search_refreshes(
            session,
            entity_id=indexed.entity_id,
        )
    assert [refresh.entity_id for refresh in pending] == [indexed.entity_id]


@pytest.mark.asyncio
async def test_relation_search_refresh_requeues_generation_lost_during_write(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    """A late N write cannot consume the final repair signal after N+1 wins."""
    path = "notes/search-generation-race.md"
    await _create_file(project_config.home / path, "# Generation N\n")
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )
    indexed = await batch_indexer.index_markdown_file(
        await _load_input(file_service, path),
        index_search=False,
        resolve_relations=False,
    )
    async with db.scoped_session(search_service.session_maker) as session:
        entities = await entity_repository.find_by_ids(session, [indexed.entity_id])
    assert len(entities) == 1

    reconciler = NoteContentReconciler(
        note_content_repository=NoteContentRepository(
            project_id=relation_repository.project_id,
        ),
        session_maker=search_service.session_maker,
    )
    generation_n = await reconciler.reconcile(
        entity=entities[0],
        markdown_content=indexed.markdown_content or "",
        observed_at=datetime.now(tz=UTC),
        source="test",
    )
    assert generation_n.generation is not None
    generation_n_value = generation_n.generation
    assert await batch_indexer.publish_relation_generation(
        indexed,
        generation=generation_n_value,
    )

    async def accept_newer_generation_during_search(*args, **kwargs) -> None:
        del args, kwargs
        generation_n_plus_one = await reconciler.reconcile(
            entity=entities[0],
            markdown_content="# Generation N+1\n",
            observed_at=datetime.now(tz=UTC),
            source="test",
        )
        assert generation_n_plus_one.generation == generation_n_value + 1

        # Model N+1 completing its own refresh before N returns from the external
        # search writer. N's post-write guard must create later repair work.
        async with db.scoped_session(search_service.session_maker) as session:
            observed = await relation_repository.list_pending_search_refreshes(
                session,
                entity_id=indexed.entity_id,
            )
            await relation_repository.clear_pending_search_refreshes(
                session,
                [refresh.id for refresh in observed],
            )

    monkeypatch.setattr(
        search_service,
        "index_entity_data",
        accept_newer_generation_during_search,
    )

    await batch_indexer.refresh_indexed_entity_search(
        indexed,
        generation=generation_n_value,
    )

    async with db.scoped_session(search_service.session_maker) as session:
        pending = await relation_repository.list_pending_search_refreshes(
            session,
            entity_id=indexed.entity_id,
        )
    assert [refresh.entity_id for refresh in pending] == [indexed.entity_id]


@pytest.mark.asyncio
async def test_batch_indexer_publishes_only_ambiguity_safe_self_relations(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
):
    """File indexing preserves safe self-links without guessing ambiguous title targets."""
    path = "notes/self-links.md"
    content = dedent(
        """
        ---
        title: Self Links
        type: note
        ---

        # Self Links

        - links_to [[Self Links]]
        - links_to [[notes/self-links.md]]
        - mentions [[Self Links]]
        """
    ).strip()
    await _create_file(project_config.home / path, content)
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    indexed = await batch_indexer.index_markdown_file(
        await _load_input(file_service, path),
        index_search=False,
        resolve_relations=False,
    )
    assert {relation.target_id for relation in indexed.relations} == {indexed.entity_id}

    await _claim_and_publish_relations(
        batch_indexer,
        IndexingBatchResult(indexed=[indexed]),
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        session_maker=search_service.session_maker,
        max_concurrent=1,
    )
    async with db.scoped_session(search_service.session_maker) as session:
        source = await entity_repository.get_by_file_path(session, path)
    assert source is not None
    assert sorted(
        (relation.relation_type, relation.to_name, relation.to_id)
        for relation in source.outgoing_relations
    ) == [
        ("links_to", "Self Links", source.id),
        ("mentions", "Self Links", source.id),
    ]

    await entity_service.create_entity_with_content(
        EntitySchema(
            title="Self Links",
            directory="duplicates",
            content="# Self Links\n\nA different note with the same title.\n",
        )
    )
    await _create_file(project_config.home / path, f"{content}\n\nUpdated source bytes.\n")

    ambiguous = await batch_indexer.index_markdown_file(
        await _load_input(file_service, path),
        index_search=False,
        resolve_relations=False,
    )
    target_ids = {
        (relation.relation_type, relation.target_name): relation.target_id
        for relation in ambiguous.relations
    }
    assert target_ids == {
        ("links_to", "Self Links"): None,
        ("links_to", "notes/self-links.md"): source.id,
        ("mentions", "Self Links"): None,
    }

    await _claim_and_publish_relations(
        batch_indexer,
        IndexingBatchResult(indexed=[ambiguous]),
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        session_maker=search_service.session_maker,
        max_concurrent=1,
    )
    async with db.scoped_session(search_service.session_maker) as session:
        reloaded = await entity_repository.get_by_file_path(session, path)
    assert reloaded is not None
    assert sorted(
        (relation.relation_type, relation.to_name, relation.to_id)
        for relation in reloaded.outgoing_relations
    ) == [
        ("links_to", "Self Links", None),
        ("links_to", "notes/self-links.md", source.id),
        ("mentions", "Self Links", None),
    ]


@pytest.mark.asyncio
async def test_batch_indexer_uses_exact_bulk_resolution_for_deferred_relations(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    """Deferred relations use the shared exact-match bulk resolver."""
    path = "notes/source.md"
    await _create_file(
        project_config.home / path,
        dedent(
            """
            ---
            title: Source
            type: note
            ---

            # Source

            - links_to [[never-resolves-target]]
            """
        ),
    )

    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    target_resolver_type = type(batch_indexer.relation_resolution.target_resolver)
    original_resolve_targets = target_resolver_type.resolve_relation_targets
    seen_target_batches: list[tuple[str, ...]] = []

    async def spy_resolve_targets(self, link_texts, *, session):
        seen_target_batches.append(tuple(link_texts))
        return await original_resolve_targets(self, link_texts, session=session)

    monkeypatch.setattr(target_resolver_type, "resolve_relation_targets", spy_resolve_targets)

    result = await batch_indexer.index_files(
        {path: await _load_input(file_service, path)},
        max_concurrent=1,
    )
    await _claim_and_publish_relations(
        batch_indexer,
        result,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        session_maker=search_service.session_maker,
        max_concurrent=1,
    )

    assert seen_target_batches == [("never-resolves-target",)]

    # The unresolvable relation stayed unresolved.
    async with db.scoped_session(search_service.session_maker) as session:
        source = await entity_repository.get_by_file_path(session, path)
    assert source is not None
    assert len(source.outgoing_relations) == 1
    assert source.outgoing_relations[0].to_id is None
    assert source.outgoing_relations[0].to_name == "never-resolves-target"


@pytest.mark.asyncio
async def test_batch_indexer_strips_frontmatter_from_search_content_when_body_is_empty(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    path = "notes/frontmatter-only.md"
    await _create_file(
        project_config.home / path,
        dedent(
            """
            ---
            title: Frontmatter Only
            type: note
            status: draft
            ---
            """
        ).strip(),
    )

    index_entity_data = AsyncMock()
    monkeypatch.setattr(search_service, "index_entity_data", index_entity_data)
    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    await batch_indexer.index_markdown_file(
        await _load_input(file_service, path), index_search=True
    )

    persisted_content = await file_service.read_file_content(path)
    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, path)
    assert entity is not None
    index_entity_data.assert_awaited_once()
    await_args = index_entity_data.await_args
    assert await_args is not None
    args, kwargs = await_args
    assert args[0].id == entity.id
    assert kwargs["content"] == remove_frontmatter(persisted_content)


@pytest.mark.asyncio
async def test_batch_indexer_does_not_inject_frontmatter_when_sync_enforcement_is_disabled(
    app_config,
    entity_service,
    entity_repository,
    relation_repository,
    search_service,
    file_service,
    project_config,
    monkeypatch,
):
    app_config.ensure_frontmatter_on_sync = False

    created = await entity_service.create_entity_with_content(
        EntitySchema(
            title="Frontmatterless",
            directory="notes",
            content="# Frontmatterless\n\nOriginal content.\n",
        )
    )
    path = created.entity.file_path
    assert path is not None
    existing_permalink = created.entity.permalink
    assert existing_permalink is not None

    original_content = "# Frontmatterless\n\nBody content.\n"
    await _create_file(project_config.home / path, original_content)

    original_writer = file_service.update_frontmatter_with_result
    frontmatter_writer = AsyncMock(side_effect=original_writer)
    monkeypatch.setattr(file_service, "update_frontmatter_with_result", frontmatter_writer)

    batch_indexer = _make_batch_indexer(
        app_config,
        entity_service,
        entity_repository,
        relation_repository,
        search_service,
        file_service,
    )

    indexed = await batch_indexer.index_markdown_file(
        await _load_input(file_service, path),
        index_search=False,
    )

    # Trigger: Windows persists CRLF for text files even when the test literal uses LF.
    # Why: this assertion cares about preserving a frontmatterless file, not about newline style.
    # Outcome: compare against the exact content stored on disk after sync.
    persisted_content = (project_config.home / path).read_bytes().decode("utf-8")
    async with db.scoped_session(search_service.session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, path)
    assert entity is not None
    assert entity.permalink == existing_permalink
    assert frontmatter_writer.await_count == 0
    assert indexed.markdown_content == persisted_content
    assert (await file_service.read_file_bytes(path)).decode("utf-8") == persisted_content
