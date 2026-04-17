"""Focused tests for the one-file markdown sync primitive."""

from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock

import pytest

from basic_memory.file_utils import compute_checksum, remove_frontmatter
from basic_memory.schemas import Entity as EntitySchema


def _write_markdown(project_root: Path, relative_path: str, content: str) -> Path:
    """Create one markdown file under the test project."""
    file_path = project_root / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.mark.asyncio
async def test_sync_one_markdown_file_writes_missing_frontmatter_and_returns_canonical_content(
    sync_service,
    test_project,
    app_config,
    monkeypatch,
):
    """Missing frontmatter is written once and returned exactly as stored on disk."""
    app_config.ensure_frontmatter_on_sync = True
    file_path = _write_markdown(
        Path(test_project.path),
        "notes/frontmatterless.md",
        "# Frontmatterless\n\nBody content.\n",
    )

    index_entity_data = AsyncMock()
    monkeypatch.setattr(sync_service.search_service, "index_entity_data", index_entity_data)

    result = await sync_service.sync_one_markdown_file("notes/frontmatterless.md")

    final_content = file_path.read_text(encoding="utf-8")
    assert result.markdown_content == final_content
    assert result.entity.permalink == f"{test_project.name}/notes/frontmatterless"
    assert f"permalink: {result.entity.permalink}" in final_content
    assert result.checksum == await sync_service.file_service.compute_checksum(
        "notes/frontmatterless.md"
    )
    assert result.size == file_path.stat().st_size
    index_entity_data.assert_awaited_once_with(
        result.entity,
        content=remove_frontmatter(final_content),
    )


@pytest.mark.asyncio
async def test_sync_one_markdown_file_rewrites_permalink_once_after_repository_conflict(
    sync_service,
    entity_service,
    test_project,
    monkeypatch,
):
    """Late DB conflict resolution updates the file exactly once with the accepted permalink."""
    existing = await entity_service.create_entity_with_content(
        EntitySchema(
            title="Existing Note",
            directory="notes",
            content="# Existing Note\n\nOriginal content.\n",
        )
    )
    conflicting_permalink = existing.entity.permalink
    assert conflicting_permalink is not None

    file_path = _write_markdown(
        Path(test_project.path),
        "notes/race.md",
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

    original_writer = sync_service.file_service.update_frontmatter_with_result
    frontmatter_writer = AsyncMock(side_effect=original_writer)
    monkeypatch.setattr(sync_service.entity_service, "resolve_permalink", stale_permalink)
    monkeypatch.setattr(
        sync_service.file_service,
        "update_frontmatter_with_result",
        frontmatter_writer,
    )

    result = await sync_service.sync_one_markdown_file("notes/race.md", index_search=False)

    final_content = file_path.read_text(encoding="utf-8")
    assert frontmatter_writer.await_count == 1
    assert result.entity.permalink == f"{conflicting_permalink}-1"
    assert result.markdown_content == final_content
    assert f"permalink: {result.entity.permalink}" in final_content
    assert result.checksum == await sync_service.file_service.compute_checksum("notes/race.md")


@pytest.mark.asyncio
async def test_sync_one_markdown_file_returns_original_content_when_no_rewrite_needed(
    sync_service,
    test_project,
    monkeypatch,
):
    """Canonical markdown is returned as-read when frontmatter already matches."""
    original_content = dedent(
        f"""\
        ---
        title: No Rewrite
        type: note
        permalink: {test_project.name}/notes/no-rewrite
        ---

        # No Rewrite

        Body content.
        """
    )
    file_path = _write_markdown(
        Path(test_project.path),
        "notes/no-rewrite.md",
        original_content,
    )

    original_writer = sync_service.file_service.update_frontmatter_with_result
    frontmatter_writer = AsyncMock(side_effect=original_writer)
    monkeypatch.setattr(
        sync_service.file_service,
        "update_frontmatter_with_result",
        frontmatter_writer,
    )

    result = await sync_service.sync_one_markdown_file("notes/no-rewrite.md", index_search=False)

    # Trigger: Windows persists CRLF for text files even when the test literal uses LF.
    # Why: this assertion cares about "no rewrite happened", not about pinning one newline style.
    # Outcome: compare against the exact markdown bytes stored on disk.
    persisted_content = file_path.read_bytes().decode("utf-8")

    assert frontmatter_writer.await_count == 0
    assert result.markdown_content == persisted_content
    assert file_path.read_bytes().decode("utf-8") == persisted_content
    assert result.checksum == await sync_service.file_service.compute_checksum(
        "notes/no-rewrite.md"
    )


@pytest.mark.asyncio
async def test_sync_one_markdown_file_does_not_reread_for_initial_checksum_when_no_rewrite(
    sync_service,
    test_project,
    monkeypatch,
):
    """Initial checksum comes from the loaded file bytes, not a second storage read."""
    original_content = dedent(
        f"""\
        ---
        title: No Rewrite
        type: note
        permalink: {test_project.name}/notes/no-rewrite
        ---

        # No Rewrite

        Body content.
        """
    )
    file_path = _write_markdown(
        Path(test_project.path),
        "notes/no-rewrite.md",
        original_content,
    )

    checksum_spy = AsyncMock()
    monkeypatch.setattr(sync_service.file_service, "compute_checksum", checksum_spy)

    result = await sync_service.sync_one_markdown_file("notes/no-rewrite.md", index_search=False)

    checksum_spy.assert_not_awaited()
    assert result.checksum == await compute_checksum(file_path.read_bytes())


@pytest.mark.asyncio
async def test_sync_one_markdown_file_skips_indexing_when_checksum_matches(
    sync_service,
    test_project,
    monkeypatch,
):
    """A matching DB checksum is the consistency boundary for derived indexes."""
    original_content = dedent(
        f"""\
        ---
        title: Already Current
        type: note
        permalink: {test_project.name}/notes/already-current
        ---

        # Already Current

        - [note] Derived indexes are assumed current when the file checksum matches.
        """
    )
    file_path = _write_markdown(
        Path(test_project.path),
        "notes/already-current.md",
        original_content,
    )

    initial = await sync_service.sync_one_markdown_file(
        "notes/already-current.md",
        index_search=False,
    )
    assert initial.checksum == await compute_checksum(file_path.read_bytes())

    index_markdown_file = AsyncMock(side_effect=AssertionError("indexer should not run"))
    index_entity_data = AsyncMock(side_effect=AssertionError("search should not refresh"))
    monkeypatch.setattr(sync_service.batch_indexer, "index_markdown_file", index_markdown_file)
    monkeypatch.setattr(sync_service.search_service, "index_entity_data", index_entity_data)

    result = await sync_service.sync_one_markdown_file("notes/already-current.md")

    index_markdown_file.assert_not_awaited()
    index_entity_data.assert_not_awaited()
    assert result.entity.id == initial.entity.id
    assert len(result.entity.observations) == 1
    assert result.markdown_content == file_path.read_bytes().decode("utf-8")
    assert result.checksum == initial.checksum


@pytest.mark.asyncio
async def test_sync_one_markdown_file_indexes_when_checksum_differs(
    sync_service,
    test_project,
    monkeypatch,
):
    """A DB checksum mismatch still takes the full indexing path."""
    initial_content = dedent(
        f"""\
        ---
        title: Changed
        type: note
        permalink: {test_project.name}/notes/changed
        ---

        # Changed

        Original body.
        """
    )
    file_path = _write_markdown(
        Path(test_project.path),
        "notes/changed.md",
        initial_content,
    )
    initial = await sync_service.sync_one_markdown_file("notes/changed.md", index_search=False)

    updated_content = initial_content.replace("Original body.", "Updated body.")
    file_path.write_text(updated_content, encoding="utf-8")

    original_index_markdown_file = sync_service.batch_indexer.index_markdown_file

    async def index_markdown_file_spy(*args, **kwargs):
        return await original_index_markdown_file(*args, **kwargs)

    index_markdown_file = AsyncMock(side_effect=index_markdown_file_spy)
    index_entity_data = AsyncMock()
    monkeypatch.setattr(sync_service.batch_indexer, "index_markdown_file", index_markdown_file)
    monkeypatch.setattr(sync_service.search_service, "index_entity_data", index_entity_data)

    result = await sync_service.sync_one_markdown_file("notes/changed.md")

    index_markdown_file.assert_awaited_once()
    index_entity_data.assert_awaited_once()
    assert result.entity.id == initial.entity.id
    assert result.markdown_content == file_path.read_bytes().decode("utf-8")
    assert result.checksum == await compute_checksum(file_path.read_bytes())
    assert result.checksum != initial.checksum


@pytest.mark.asyncio
async def test_sync_one_markdown_file_can_defer_relation_resolution(
    sync_service,
    entity_service,
    test_project,
    monkeypatch,
):
    """Cloud callers can keep one-file sync cheap and repair relations later."""
    await entity_service.create_entity_with_content(
        EntitySchema(
            title="Deferred Target",
            directory="notes",
            content="# Deferred Target\n",
        )
    )
    _write_markdown(
        Path(test_project.path),
        "notes/deferred-source.md",
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
    monkeypatch.setattr(sync_service.entity_service.link_resolver, "resolve_link", resolve_link)

    result = await sync_service.sync_one_markdown_file(
        "notes/deferred-source.md",
        index_search=False,
        resolve_relations=False,
    )

    resolve_link.assert_not_awaited()
    assert len(result.entity.outgoing_relations) == 1
    assert result.entity.outgoing_relations[0].to_id is None
    assert result.entity.outgoing_relations[0].to_name == "Deferred Target"


@pytest.mark.asyncio
async def test_sync_markdown_file_remains_tuple_compatible(sync_service, test_project):
    """The legacy tuple-returning API still works for existing callers."""
    _write_markdown(
        Path(test_project.path),
        "notes/compat.md",
        dedent(
            f"""\
            ---
            title: Compat Note
            type: note
            permalink: {test_project.name}/notes/compat
            ---

            # Compat Note

            Body content.
            """
        ),
    )

    entity, checksum = await sync_service.sync_markdown_file("notes/compat.md")

    assert entity is not None
    assert entity.file_path == "notes/compat.md"
    assert entity.permalink == f"{test_project.name}/notes/compat"
    assert checksum == await sync_service.file_service.compute_checksum("notes/compat.md")


@pytest.mark.asyncio
async def test_sync_one_markdown_file_indexes_thematic_break_content_without_frontmatter(
    sync_service,
    test_project,
    app_config,
    monkeypatch,
):
    """Leading thematic-break markdown should index as raw content when frontmatter is absent."""
    app_config.ensure_frontmatter_on_sync = False

    original_content = "---\nBody content after a thematic break.\n"
    file_path = _write_markdown(
        Path(test_project.path),
        "notes/thematic-break.md",
        original_content,
    )

    index_entity_data = AsyncMock()
    monkeypatch.setattr(sync_service.search_service, "index_entity_data", index_entity_data)

    result = await sync_service.sync_one_markdown_file("notes/thematic-break.md")

    persisted_content = file_path.read_bytes().decode("utf-8")

    assert result.markdown_content == persisted_content
    assert file_path.read_bytes().decode("utf-8") == persisted_content
    index_entity_data.assert_awaited_once_with(
        result.entity,
        content=persisted_content,
    )
