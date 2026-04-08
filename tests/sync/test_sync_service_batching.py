"""Targeted tests for batched sync indexing behavior."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from sqlalchemy import text

from basic_memory.file_utils import compute_checksum
from basic_memory.indexing import IndexFileMetadata, IndexProgress
from basic_memory.sync.sync_service import MAX_CONSECUTIVE_FAILURES


async def _create_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.asyncio
async def test_sync_batches_changed_files_emits_typed_progress_and_resolves_forward_refs(
    app_config,
    sync_service,
    search_repository,
    entity_repository,
    project_config,
):
    app_config.index_batch_size = 1
    app_config.index_batch_max_bytes = 1_024

    source_path = project_config.home / "notes/source.md"
    target_path = project_config.home / "notes/target.md"

    await _create_file(
        source_path,
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
        target_path,
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

    progress_updates: list[IndexProgress] = []
    original_get_permalink_map = entity_repository.get_file_path_to_permalink_map
    permalink_map_calls = 0

    async def on_progress(update: IndexProgress) -> None:
        progress_updates.append(update)

    async def spy_get_permalink_map() -> dict[str, str]:
        nonlocal permalink_map_calls
        permalink_map_calls += 1
        return await original_get_permalink_map()

    entity_repository.get_file_path_to_permalink_map = spy_get_permalink_map
    try:
        await sync_service.sync(
            project_config.home,
            project_name=project_config.name,
            progress_callback=on_progress,
        )
    finally:
        entity_repository.get_file_path_to_permalink_map = original_get_permalink_map

    assert progress_updates
    assert all(isinstance(update, IndexProgress) for update in progress_updates)
    assert progress_updates[-1].files_total == 2
    assert progress_updates[-1].files_processed == 2
    assert progress_updates[-1].batches_total == 2
    assert progress_updates[-1].batches_completed == 2
    assert permalink_map_calls == 1

    source = await entity_repository.get_by_file_path("notes/source.md")
    target = await entity_repository.get_by_file_path("notes/target.md")
    assert source is not None
    assert target is not None
    assert len(source.outgoing_relations) == 1
    assert source.outgoing_relations[0].to_id == target.id

    relation_rows = await search_repository.execute_query(
        text(
            "SELECT COUNT(*) FROM search_index "
            "WHERE entity_id = :entity_id AND type = 'relation' AND to_id IS NOT NULL"
        ),
        {"entity_id": source.id},
    )
    assert relation_rows.scalar_one() == 1


@pytest.mark.asyncio
async def test_index_changed_files_returns_empty_result_and_zero_progress(sync_service):
    progress_updates: list[IndexProgress] = []

    async def on_progress(update: IndexProgress) -> None:
        progress_updates.append(update)

    indexed_entities, skipped_files = await sync_service._index_changed_files(
        [],
        {},
        progress_callback=on_progress,
    )

    assert indexed_entities == []
    assert skipped_files == []
    assert len(progress_updates) == 1
    assert progress_updates[0] == IndexProgress(
        files_total=0,
        files_processed=0,
        batches_total=0,
        batches_completed=0,
    )


@pytest.mark.asyncio
async def test_index_changed_files_skips_paths_blocked_by_circuit_breaker(
    sync_service,
    project_config,
):
    skipped_path = "notes/skipped.md"
    indexed_path = "notes/indexed.md"
    await _create_file(project_config.home / skipped_path, "# Skipped\n")
    await _create_file(project_config.home / indexed_path, "# Indexed\n")

    for attempt in range(MAX_CONSECUTIVE_FAILURES):
        await sync_service._record_failure(skipped_path, f"failure {attempt}")

    indexed_entities, skipped_files = await sync_service._index_changed_files(
        [skipped_path, indexed_path],
        {
            skipped_path: await sync_service.file_service.compute_checksum(skipped_path),
            indexed_path: await sync_service.file_service.compute_checksum(indexed_path),
        },
    )

    assert [indexed.path for indexed in indexed_entities] == [indexed_path]
    assert [skipped.path for skipped in skipped_files] == [skipped_path]


@pytest.mark.asyncio
async def test_load_index_file_metadata_tracks_missing_and_error_paths(
    sync_service,
    project_config,
    monkeypatch,
):
    error_path = "notes/error.md"
    missing_path = "notes/missing.md"
    await _create_file(project_config.home / error_path, "# Error\n")

    deleted_paths: list[str] = []
    original_get_file_metadata = sync_service.file_service.get_file_metadata

    async def spy_handle_delete(path: str) -> None:
        deleted_paths.append(path)

    async def fake_get_file_metadata(path: str):
        if path == error_path:
            raise ValueError("metadata boom")
        return await original_get_file_metadata(path)

    monkeypatch.setattr(sync_service, "handle_delete", spy_handle_delete)
    monkeypatch.setattr(sync_service.file_service, "get_file_metadata", fake_get_file_metadata)

    metadata_by_path, errors, missing_paths = await sync_service._load_index_file_metadata(
        [missing_path, error_path],
        {},
    )

    assert metadata_by_path == {}
    assert errors == [(error_path, "metadata boom")]
    assert missing_paths == [missing_path]
    assert deleted_paths == [missing_path]


@pytest.mark.asyncio
async def test_load_index_batch_files_recomputes_checksum_from_loaded_bytes_and_tracks_errors(
    sync_service,
    project_config,
    monkeypatch,
):
    good_path = "notes/good.md"
    error_path = "notes/error.md"
    missing_path = "notes/missing.md"
    await _create_file(project_config.home / good_path, "# Good\n")
    await _create_file(project_config.home / error_path, "# Error\n")

    good_metadata = await sync_service.file_service.get_file_metadata(good_path)
    error_metadata = await sync_service.file_service.get_file_metadata(error_path)
    metadata_by_path = {
        good_path: IndexFileMetadata(
            path=good_path,
            size=good_metadata.size,
            checksum="stale-checksum",
            content_type=sync_service.file_service.content_type(good_path),
            last_modified=good_metadata.modified_at,
            created_at=good_metadata.created_at,
        ),
        error_path: IndexFileMetadata(
            path=error_path,
            size=error_metadata.size,
            checksum="ignored",
            content_type=sync_service.file_service.content_type(error_path),
            last_modified=error_metadata.modified_at,
            created_at=error_metadata.created_at,
        ),
        missing_path: IndexFileMetadata(
            path=missing_path,
            size=0,
            checksum="missing",
            content_type="text/markdown",
        ),
    }

    deleted_paths: list[str] = []
    original_read_file_bytes = sync_service.file_service.read_file_bytes

    async def spy_handle_delete(path: str) -> None:
        deleted_paths.append(path)

    async def fake_read_file_bytes(path: str) -> bytes:
        if path == good_path:
            return b"# Loaded\n"
        if path == error_path:
            raise ValueError("load boom")
        return await original_read_file_bytes(path)

    monkeypatch.setattr(sync_service, "handle_delete", spy_handle_delete)
    monkeypatch.setattr(sync_service.file_service, "read_file_bytes", fake_read_file_bytes)

    files, errors = await sync_service._load_index_batch_files(
        [good_path, error_path, missing_path],
        metadata_by_path,
    )

    assert files[good_path].checksum == await compute_checksum(b"# Loaded\n")
    assert files[good_path].checksum != "stale-checksum"
    assert errors == [(error_path, "load boom")]
    assert deleted_paths == [missing_path]
