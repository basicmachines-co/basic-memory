"""Local event-index watcher parity for Basic Memory ignore rules."""

from pathlib import Path

import pytest
from watchfiles import Change

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.index.local_runtime import LocalWatchEventIndexRuntimeFactory
from basic_memory.index.watch_service import WatchService


async def create_test_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_local_event_index_respects_basic_memory_ignore_rules(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    entity_repository,
) -> None:
    """Local event-index should filter watcher batches through .bmignore/.gitignore."""
    await create_test_file(project_config.home / ".gitignore", "ignored/\n*.swp\n*~\n")
    valid_path = project_config.home / "notes" / "keep.md"
    ignored_dir_path = project_config.home / "ignored" / "skip.md"
    swap_path = project_config.home / "notes" / "draft.swp"
    backup_path = project_config.home / "notes" / "draft.md~"
    await create_test_file(valid_path, "# Keep\n")
    await create_test_file(ignored_dir_path, "# Skip\n")
    await create_test_file(swap_path, "swap")
    await create_test_file(backup_path, "backup")

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(
        test_project,
        {
            (Change.added, str(valid_path)),
            (Change.added, str(ignored_dir_path)),
            (Change.added, str(swap_path)),
            (Change.added, str(backup_path)),
        },
    )

    async with db.scoped_session(session_maker) as session:
        valid = await entity_repository.get_by_file_path(session, "notes/keep.md")
        ignored_dir = await entity_repository.get_by_file_path(session, "ignored/skip.md")
        swap = await entity_repository.get_by_file_path(session, "notes/draft.swp")
        backup = await entity_repository.get_by_file_path(session, "notes/draft.md~")

    assert valid is not None
    assert ignored_dir is None
    assert swap is None
    assert backup is None
    assert watch_service.state.indexed_files == 1
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"
