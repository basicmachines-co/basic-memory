"""Tests for the reusable batch indexing executor."""

from __future__ import annotations

import asyncio
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from basic_memory.file_utils import remove_frontmatter
from basic_memory.indexing import (
    BatchIndexer,
    IndexFrontmatterUpdate,
    IndexFrontmatterWriteResult,
    IndexInputFile,
)
from basic_memory.schemas import Entity as EntitySchema
from basic_memory.services.exceptions import SyncFatalError


class _TestFileWriter:
    """Adapt the real FileService for batch indexer tests."""

    def __init__(self, file_service) -> None:
        self.file_service = file_service

    async def write_frontmatter(
        self, update: IndexFrontmatterUpdate
    ) -> IndexFrontmatterWriteResult:
        result = await self.file_service.update_frontmatter_with_result(
            update.path, update.metadata
        )
        return IndexFrontmatterWriteResult(checksum=result.checksum, content=result.content)


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
        app_config=app_config,
        entity_service=entity_service,
        entity_repository=entity_repository,
        relation_repository=relation_repository,
        search_service=search_service,
        file_writer=_TestFileWriter(file_service),
    )


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
async def test_batch_indexer_creates_entities_with_parallel_path(
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

    original_upsert = entity_service.upsert_entity_from_markdown
    in_flight = 0
    max_in_flight = 0

    async def spy_upsert(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        try:
            return await original_upsert(*args, **kwargs)
        finally:
            in_flight -= 1

    entity_service.upsert_entity_from_markdown = spy_upsert
    try:
        result = await batch_indexer.index_files(
            files,
            max_concurrent=2,
            parse_max_concurrent=2,
        )
    finally:
        entity_service.upsert_entity_from_markdown = original_upsert

    assert max_in_flight >= 2
    assert len(result.indexed) == 2
    assert result.errors == []


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
):
    pdf_path = "assets/doc.pdf"
    image_path = "assets/image.png"
    await _create_file(project_config.home / pdf_path, b"%PDF-1.4 test")
    await _create_file(project_config.home / image_path, b"\x89PNG\r\n\x1a\nrest")

    files = {
        pdf_path: await _load_input(file_service, pdf_path),
        image_path: await _load_input(file_service, image_path),
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

    assert {indexed.path for indexed in result.indexed} == {pdf_path, image_path}
    assert all(indexed.markdown_content is None for indexed in result.indexed)

    pdf_entity = await entity_repository.get_by_file_path(pdf_path)
    image_entity = await entity_repository.get_by_file_path(image_path)
    assert pdf_entity is not None
    assert pdf_entity.content_type == "application/pdf"
    assert image_entity is not None
    assert image_entity.content_type == "image/png"


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

    source = await entity_repository.get_by_file_path(source_path)
    target = await entity_repository.get_by_file_path(target_path)
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
    assert indexed_by_path[path_one].markdown_content == await file_service.read_file_content(
        path_one
    )
    assert indexed_by_path[path_two].markdown_content == await file_service.read_file_content(
        path_two
    )

    entities = await entity_repository.find_all()
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

    # Trigger: malformed frontmatter should pass through without normalization.
    # Why: Windows can still surface that unchanged file with CRLF line endings.
    # Outcome: compare the indexed markdown to the persisted file content, not the LF
    #          test literal used to create it.
    persisted_content = (project_config.home / path).read_bytes().decode("utf-8")

    assert result.errors == []
    assert len(result.indexed) == 1
    assert result.indexed[0].markdown_content == persisted_content

    entity = await entity_repository.get_by_file_path(path)
    assert entity is not None


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

    persisted_content = await file_service.read_file_content(path)
    assert indexed.permalink == f"{conflicting_permalink}-1"
    assert indexed.markdown_content == persisted_content


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
    entity = await entity_repository.get_by_file_path(path)
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
    entity = await entity_repository.get_by_file_path(path)
    assert entity is not None
    assert entity.permalink == existing_permalink
    assert frontmatter_writer.await_count == 0
    assert indexed.markdown_content == persisted_content
    assert (await file_service.read_file_bytes(path)).decode("utf-8") == persisted_content
