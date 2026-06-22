"""Local event-index watcher parity for regular file edge cases."""

from pathlib import Path

import pytest
from watchfiles import Change

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.index import (
    LocalProjectIndexRuntimeFactory,
    LocalWatchEventIndexRuntimeFactory,
    WatchService,
    run_local_project_index_for_project,
)
from basic_memory.models.knowledge import Entity
from basic_memory.schemas.search import SearchItemType


async def create_test_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_local_event_index_moves_regular_file_over_deleted_path(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    entity_repository,
) -> None:
    """Regular file move-with-delete parity should not keep the replaced target entity."""
    target_path = project_config.home / "doc.pdf"
    source_path = project_config.home / "other" / "doc-1.pdf"
    await create_test_file(target_path, "target content")
    await create_test_file(source_path, "source content")

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert first.enqueued_files == 2

    async with db.scoped_session(session_maker) as session:
        target_before = await entity_repository.get_by_file_path(session, "doc.pdf")
        source_before = await entity_repository.get_by_file_path(session, "other/doc-1.pdf")
    assert target_before is not None
    assert source_before is not None
    assert target_before.content_type == "application/pdf"
    assert source_before.content_type == "application/pdf"

    target_path.unlink()
    source_path.rename(target_path)

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(
        test_project,
        {
            (Change.deleted, str(target_path)),
            (Change.deleted, str(source_path)),
            (Change.added, str(target_path)),
        },
    )

    async with db.scoped_session(session_maker) as session:
        replaced_target = await session.get(Entity, target_before.id)
        old_source = await entity_repository.get_by_file_path(session, "other/doc-1.pdf")
        moved_source = await entity_repository.get_by_file_path(session, "doc.pdf")

    assert replaced_target is None
    assert old_source is None
    assert moved_source is not None
    assert moved_source.id == source_before.id
    assert moved_source.permalink is None
    assert moved_source.content_type == "application/pdf"
    assert target_path.read_text(encoding="utf-8") == "source content"
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"


@pytest.mark.asyncio
async def test_local_event_index_resolves_regular_file_relation_and_writes_source_permalink(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    entity_repository,
) -> None:
    """Regular-file relation parity should use the event-index path end to end."""
    asset_path = project_config.home / "asset.pdf"
    source_path = project_config.home / "note.md"
    await create_test_file(asset_path, "pdf-ish")
    await create_test_file(
        source_path,
        """---
title: a note
type: note
tags: []
---

- relates_to [[asset.pdf]]
""",
    )

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(
        test_project,
        {
            (Change.added, str(asset_path)),
            (Change.added, str(source_path)),
        },
    )

    expected_permalink = f"{test_project.permalink}/note"
    source_content = source_path.read_text(encoding="utf-8")
    assert f"permalink: {expected_permalink}" in source_content

    async with db.scoped_session(session_maker) as session:
        source_entity = await entity_repository.get_by_file_path(session, "note.md")
        target_entity = await entity_repository.get_by_file_path(session, "asset.pdf")

    assert source_entity is not None
    assert source_entity.permalink == expected_permalink
    assert target_entity is not None
    assert target_entity.permalink is None
    assert target_entity.content_type == "application/pdf"
    assert len(source_entity.outgoing_relations) == 1
    relation = source_entity.outgoing_relations[0]
    assert relation.to_id == target_entity.id
    assert watch_service.state.indexed_files == 2
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"


@pytest.mark.asyncio
async def test_local_event_index_deletes_regular_file_relation_target_and_repairs_search(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    entity_repository,
    search_service,
) -> None:
    """Deleting a regular-file relation target should repair the surviving note search rows."""
    asset_path = project_config.home / "asset.pdf"
    source_path = project_config.home / "source.md"
    await create_test_file(asset_path, "pdf-ish")
    await create_test_file(
        source_path,
        """---
type: note
title: Source
---
# Source

- relates_to [[asset.pdf]]
""",
    )

    first = await run_local_project_index_for_project(
        test_project,
        runtime_factory=LocalProjectIndexRuntimeFactory(batch_size=10),
        force_full=True,
    )
    assert first.enqueued_files == 2

    async with db.scoped_session(session_maker) as session:
        source_before = await entity_repository.get_by_file_path(session, "source.md")
        target_before = await entity_repository.get_by_file_path(session, "asset.pdf")
        assert source_before is not None
        assert target_before is not None
        assert len(source_before.outgoing_relations) == 1
        relation_before = source_before.outgoing_relations[0]
        assert relation_before.to_id == target_before.id
        assert relation_before.permalink is not None
        relation_search_rows = await search_service.repository.search(
            permalink=relation_before.permalink,
            search_item_types=[SearchItemType.RELATION],
            session=session,
        )
        assert len(relation_search_rows) == 1

        source_entity_id = source_before.id
        target_entity_id = target_before.id
        relation_permalink = relation_before.permalink

    asset_path.unlink()

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(test_project, {(Change.deleted, str(asset_path))})

    async with db.scoped_session(session_maker) as session:
        deleted_target = await session.get(Entity, target_entity_id)
        source_after = await entity_repository.get_by_file_path(session, "source.md")
        stale_relation_rows = await search_service.repository.search(
            permalink=relation_permalink,
            search_item_types=[SearchItemType.RELATION],
            session=session,
        )

    assert deleted_target is None
    assert source_after is not None
    assert source_after.id == source_entity_id
    assert source_after.outgoing_relations == []
    assert stale_relation_rows == []
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"
