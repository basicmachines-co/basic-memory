"""Local event-index watcher parity for larger mixed change batches."""

from pathlib import Path

import pytest
from watchfiles import Change

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.index import LocalWatchEventIndexRuntimeFactory, WatchService
from basic_memory.repository.note_content_repository import NoteContentRepository


async def create_test_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_local_event_index_empty_batches_do_not_build_runtime(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
) -> None:
    """Empty watcher batches should remain a cheap no-op in the event-index path."""

    class FailingEventIndexRuntimeFactory:
        async def runtime_for_project(self, project):
            raise AssertionError("empty event-index batches should not build runtime dependencies")

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=FailingEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(test_project, set())

    assert watch_service.state.last_scan is not None
    assert watch_service.state.indexed_files == 0
    assert watch_service.state.error_count == 0
    assert watch_service.state.recent_events == []


@pytest.mark.asyncio
async def test_local_event_index_handles_large_batch_of_file_adds(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    entity_repository,
) -> None:
    """Large watcher batches should index every file through the event-index path."""
    file_count = 50
    changes = set()
    for index in range(file_count):
        path = project_config.home / f"event_batch_note_{index:03d}.md"
        await create_test_file(
            path,
            f"""---
type: note
---
# Event Batch Note {index}

Content for event batch note {index}.
""",
        )
        changes.add((Change.added, str(path)))

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(test_project, changes)

    async with db.scoped_session(session_maker) as session:
        indexed_count = 0
        for index in range(file_count):
            entity = await entity_repository.get_by_file_path(
                session,
                f"event_batch_note_{index:03d}.md",
            )
            if entity is not None:
                indexed_count += 1

    assert indexed_count == file_count
    assert watch_service.state.indexed_files == file_count
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"


@pytest.mark.asyncio
async def test_local_event_index_handles_mixed_operations_batch(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    entity_repository,
) -> None:
    """Mixed add/modify/delete watcher batches should settle to current file state."""
    initial_paths: list[Path] = []
    for index in range(10):
        path = project_config.home / f"event_mixed_note_{index:03d}.md"
        await create_test_file(
            path,
            f"""---
type: note
---
# Event Mixed Note {index}

Initial content for note {index}.
""",
        )
        initial_paths.append(path)

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(
        test_project,
        {(Change.added, str(path)) for path in initial_paths},
    )

    mixed_changes = set()
    for index in range(3):
        path = initial_paths[index]
        await create_test_file(
            path,
            f"""---
type: note
---
# Event Mixed Note {index}

Modified content for note {index}.
""",
        )
        mixed_changes.add((Change.modified, str(path)))

    for index in range(3, 6):
        path = initial_paths[index]
        path.unlink()
        mixed_changes.add((Change.deleted, str(path)))

    for index in range(10, 15):
        path = project_config.home / f"event_mixed_note_{index:03d}.md"
        await create_test_file(
            path,
            f"""---
type: note
---
# Event Mixed Note {index}

New content for note {index}.
""",
        )
        mixed_changes.add((Change.added, str(path)))

    await watch_service.handle_changes(test_project, mixed_changes)

    async with db.scoped_session(session_maker) as session:
        modified_entities = [
            await entity_repository.get_by_file_path(
                session,
                f"event_mixed_note_{index:03d}.md",
            )
            for index in range(3)
        ]
        deleted_entities = [
            await entity_repository.get_by_file_path(
                session,
                f"event_mixed_note_{index:03d}.md",
            )
            for index in range(3, 6)
        ]
        new_entities = [
            await entity_repository.get_by_file_path(
                session,
                f"event_mixed_note_{index:03d}.md",
            )
            for index in range(10, 15)
        ]

    assert all(entity is not None for entity in modified_entities)
    assert all(entity is None for entity in deleted_entities)
    assert all(entity is not None for entity in new_entities)
    assert watch_service.state.indexed_files == 21
    assert watch_service.state.recent_events[0].status == "success"
    assert watch_service.state.recent_events[1].status == "success"


@pytest.mark.asyncio
async def test_local_event_index_handles_rapid_atomic_writes_to_same_file(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    entity_repository,
) -> None:
    """Rapid atomic-write temp noise should settle to the current destination file."""
    tmp1_path = project_config.home / "document.1.tmp"
    tmp2_path = project_config.home / "document.2.tmp"
    final_path = project_config.home / "document.md"
    await create_test_file(tmp1_path, "# First Version\n")
    await create_test_file(tmp2_path, "# Second Version\n")

    tmp1_path.replace(final_path)
    tmp2_path.replace(final_path)

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(
        test_project,
        {
            (Change.added, str(tmp1_path)),
            (Change.deleted, str(tmp1_path)),
            (Change.added, str(tmp2_path)),
            (Change.deleted, str(tmp2_path)),
            (Change.added, str(final_path)),
            (Change.modified, str(final_path)),
        },
    )

    async with db.scoped_session(session_maker) as session:
        final_entity = await entity_repository.get_by_file_path(session, "document.md")
        final_content = await NoteContentRepository(test_project.id).get_by_file_path(
            session,
            "document.md",
        )
        tmp1_entity = await entity_repository.get_by_file_path(session, "document.1.tmp")
        tmp2_entity = await entity_repository.get_by_file_path(session, "document.2.tmp")

    assert final_entity is not None
    assert final_entity.title == "document"
    assert final_content is not None
    assert "# Second Version" in final_content.markdown_content
    assert tmp1_entity is None
    assert tmp2_entity is None
    assert watch_service.state.indexed_files == 1
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"
