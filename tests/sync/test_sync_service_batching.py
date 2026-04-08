"""Targeted tests for batched sync indexing behavior."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from sqlalchemy import text

from basic_memory.indexing import IndexProgress


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

    async def on_progress(update: IndexProgress) -> None:
        progress_updates.append(update)

    await sync_service.sync(
        project_config.home,
        project_name=project_config.name,
        progress_callback=on_progress,
    )

    assert progress_updates
    assert all(isinstance(update, IndexProgress) for update in progress_updates)
    assert progress_updates[-1].files_total == 2
    assert progress_updates[-1].files_processed == 2
    assert progress_updates[-1].batches_total == 2
    assert progress_updates[-1].batches_completed == 2

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
