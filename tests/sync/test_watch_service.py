"""Tests for watch service."""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from watchfiles import Change

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, ProjectMode, WATCH_STATUS_JSON
from basic_memory.index import StorageEventIndexRuntime
from basic_memory.index import LocalWatchEventIndexRuntimeFactory
from basic_memory.models.project import Project
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeStorageEventOperation,
)
from basic_memory.sync.watch_service import WatchService, WatchServiceState


async def create_test_file(path: Path, content: str = "test content") -> None:
    """Create a test file with given content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# Note: watch_service fixture is defined in conftest.py with sync_service_factory


def test_watch_service_init(watch_service, project_config):
    """Test watch service initialization."""
    assert watch_service.status_path.parent.exists()


def test_watch_service_status_path_honors_basic_memory_config_dir(
    tmp_path, monkeypatch, session_maker
):
    """Regression guard for #742: watch-status.json follows BASIC_MEMORY_CONFIG_DIR.

    WatchService previously hardcoded ``Path.home() / ".basic-memory"`` which
    split state across instances running under an isolated config dir. Ensure
    the status path now lives under the configured data dir.
    """
    custom_dir = tmp_path / "instance-z" / "state"
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom_dir))

    app_config = BasicMemoryConfig(projects={"main": {"path": str(tmp_path / "project")}})
    service = WatchService(
        app_config=app_config,
        project_repository=MagicMock(),
        session_maker=session_maker,
    )

    assert service.status_path == custom_dir / WATCH_STATUS_JSON
    assert service.status_path.parent.exists()


async def _register_local_projects(
    app_config: BasicMemoryConfig, project_repository, session_maker, specs
) -> None:
    """Register projects as local in both the DB and app_config.

    Projects that aren't present in ``app_config.projects`` are treated as
    cloud-only by ``get_project_mode`` and get filtered out of the watch
    cycle, so tests that exercise ``_select_projects_to_watch`` need them
    added to both sides.
    """
    from basic_memory.config import ProjectEntry

    for spec in specs:
        async with db.scoped_session(session_maker) as session:
            await project_repository.create(
                session,
                {
                    "name": spec["name"],
                    "description": spec["name"],
                    "path": spec["path"],
                    "is_active": True,
                    "is_default": False,
                },
            )
        app_config.projects[spec["name"]] = ProjectEntry(path=spec["path"], mode=ProjectMode.LOCAL)


@pytest.mark.asyncio
async def test_select_projects_to_watch_returns_all_when_unconstrained(
    app_config: BasicMemoryConfig, project_repository, session_maker, tmp_path
):
    """Without a --project constraint, every active project is watched."""
    # Use tmp_path so the project paths are OS-absolute on Windows too — a
    # POSIX-style "/tmp/alpha" is not absolute on Windows (no drive letter),
    # and _select_projects_to_watch now skips non-absolute paths (issue #949).
    await _register_local_projects(
        app_config,
        project_repository,
        session_maker,
        [
            {"name": "project-alpha", "path": str(tmp_path / "alpha")},
            {"name": "project-beta", "path": str(tmp_path / "beta")},
        ],
    )

    service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
    )

    projects = await service._select_projects_to_watch()
    names = {p.name for p in projects}

    assert "project-alpha" in names
    assert "project-beta" in names


@pytest.mark.asyncio
async def test_select_projects_to_watch_filters_to_constrained_project(
    app_config: BasicMemoryConfig, project_repository, session_maker, tmp_path
):
    """With ``constrained_project`` set, only that project is returned.

    Regression: multiple ``basic-memory mcp --project X`` processes each spawned
    a watch service over every project, producing duplicate change handlers
    that raced on file writes and cascaded deletes.
    """
    await _register_local_projects(
        app_config,
        project_repository,
        session_maker,
        [
            {"name": "project-alpha", "path": str(tmp_path / "alpha")},
            {"name": "project-beta", "path": str(tmp_path / "beta")},
        ],
    )

    service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        constrained_project="project-beta",
    )

    projects = await service._select_projects_to_watch()

    assert [p.name for p in projects] == ["project-beta"]


@pytest.mark.asyncio
async def test_select_projects_to_watch_empty_when_constrained_project_missing(
    app_config: BasicMemoryConfig, project_repository, session_maker, tmp_path
):
    """An unknown constraint yields an empty watch set rather than watching everything."""
    await _register_local_projects(
        app_config,
        project_repository,
        session_maker,
        [{"name": "project-alpha", "path": str(tmp_path / "alpha")}],
    )

    service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        constrained_project="does-not-exist",
    )

    projects = await service._select_projects_to_watch()

    assert projects == []


def test_state_add_event():
    """Test adding events to state."""
    state = WatchServiceState()
    event = state.add_event(path="test.md", action="new", status="success", checksum="abcd1234")

    assert len(state.recent_events) == 1
    assert state.recent_events[0] == event
    assert event.path == "test.md"
    assert event.action == "new"
    assert event.checksum == "abcd1234"

    # Test event limit
    for i in range(110):
        state.add_event(f"test{i}.md", "new", "success")
    assert len(state.recent_events) == 100


def test_state_record_error():
    """Test error recording in state."""
    state = WatchServiceState()
    state.record_error("test error")

    assert state.error_count == 1
    assert state.last_error is not None
    assert len(state.recent_events) == 1
    assert state.recent_events[0].action == "sync"
    assert state.recent_events[0].status == "error"
    assert state.recent_events[0].error == "test error"


@pytest.mark.asyncio
async def test_write_status(watch_service):
    """Test writing status file."""
    await watch_service.write_status()

    assert watch_service.status_path.exists()
    data = json.loads(watch_service.status_path.read_text(encoding="utf-8"))
    assert not data["running"]
    assert data["error_count"] == 0


@pytest.mark.asyncio
async def test_handle_changes_defaults_to_local_event_index_runtime(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    entity_repository,
    monkeypatch,
):
    """A default watcher routes file events through basic_memory.index, not SyncService."""

    file_path = project_config.home / "default-event-index.md"
    await create_test_file(file_path, "# Default Event Index\n\nIndexed by default.\n")

    async def fail_legacy_sync_service(_project):
        raise AssertionError("default watcher should not build legacy SyncService")

    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        fail_legacy_sync_service,
    )

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
    )

    await watch_service.handle_changes(test_project, {(Change.added, str(file_path))})

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "default-event-index.md")
    assert entity is not None
    assert entity.title == "default-event-index"
    assert watch_service.state.synced_files == 1
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_changes_can_route_through_event_index_runtime(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
):
    """An injected event-index runtime bypasses the legacy sync path."""

    file_path = project_config.home / "event-index.md"
    await create_test_file(file_path, "# Event Index\n")
    project_prefix = Path(test_project.path).resolve().name
    processor_calls: list[tuple[str, str]] = []

    async def sync_service_factory(_project):
        raise AssertionError("legacy sync factory should not be used")

    class ProjectResolver:
        async def resolve_project(self, project_path: str) -> ProjectRuntimeReference | None:
            if project_path != project_prefix:
                return None
            return ProjectRuntimeReference(
                project_id=test_project.id,
                project_external_id=str(test_project.external_id),
                project_path=project_prefix,
                project_name=test_project.name,
                project_permalink=test_project.permalink,
            )

    class OperationProcessor:
        async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
            processor_calls.append(("skip", operation.relative_path or ""))

        async def index_file(self, operation: RuntimeStorageEventOperation) -> None:
            processor_calls.append(("index", operation.require_relative_path()))

        async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
            processor_calls.append(("delete", operation.require_relative_path()))

        async def event_failed(
            self,
            operation: RuntimeStorageEventOperation,
            exc: Exception,
        ) -> None:
            processor_calls.append(("failed", str(exc)))

    class OperationProcessorFactory:
        def processor_for_project(self, project: ProjectRuntimeReference) -> OperationProcessor:
            return OperationProcessor()

    class EventIndexRuntimeFactory:
        async def runtime_for_project(self, project: Project) -> StorageEventIndexRuntime:
            assert project == test_project
            return StorageEventIndexRuntime(
                project_resolver=ProjectResolver(),
                operation_processor_factory=OperationProcessorFactory(),
            )

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        sync_service_factory=sync_service_factory,
        event_index_runtime_factory=EventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(test_project, {(Change.added, str(file_path))})

    assert processor_calls == [("index", "event-index.md")]
    assert watch_service.state.synced_files == 1
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_changes_with_event_index_runtime_records_failed_counts(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
):
    """Event-index watcher failures should surface in watch status."""

    file_path = project_config.home / "event-index-fails.md"
    await create_test_file(file_path, "# Event Index Failure\n")
    project_prefix = Path(test_project.path).resolve().name

    async def sync_service_factory(_project):
        raise AssertionError("legacy sync factory should not be used")

    class ProjectResolver:
        async def resolve_project(self, project_path: str) -> ProjectRuntimeReference | None:
            if project_path != project_prefix:
                return None
            return ProjectRuntimeReference(
                project_id=test_project.id,
                project_external_id=str(test_project.external_id),
                project_path=project_prefix,
                project_name=test_project.name,
                project_permalink=test_project.permalink,
            )

    class FailingOperationProcessor:
        async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
            raise AssertionError(f"unexpected skip: {operation}")

        async def index_file(self, operation: RuntimeStorageEventOperation) -> None:
            raise RuntimeError(f"index failed: {operation.require_relative_path()}")

        async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
            raise AssertionError(f"unexpected delete: {operation}")

        async def event_failed(
            self,
            operation: RuntimeStorageEventOperation,
            exc: Exception,
        ) -> None:
            assert operation.require_relative_path() == "event-index-fails.md"
            assert "index failed" in str(exc)

    class OperationProcessorFactory:
        def processor_for_project(
            self,
            project: ProjectRuntimeReference,
        ) -> FailingOperationProcessor:
            return FailingOperationProcessor()

    class EventIndexRuntimeFactory:
        async def runtime_for_project(self, project: Project) -> StorageEventIndexRuntime:
            assert project == test_project
            return StorageEventIndexRuntime(
                project_resolver=ProjectResolver(),
                operation_processor_factory=OperationProcessorFactory(),
            )

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        sync_service_factory=sync_service_factory,
        event_index_runtime_factory=EventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(test_project, {(Change.added, str(file_path))})

    assert watch_service.state.synced_files == 0
    assert watch_service.state.error_count == 1
    assert watch_service.state.last_error is not None
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "error"
    assert watch_service.state.recent_events[0].path == project_prefix
    assert watch_service.state.recent_events[0].error == (
        "event-index processed=0 failed=1 skipped=0"
    )


@pytest.mark.asyncio
async def test_handle_changes_with_local_event_index_runtime_indexes_markdown_file(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    sync_service,
    entity_repository,
):
    """The concrete local event-index runtime can index watcher-created markdown files."""

    file_path = project_config.home / "local-event-index.md"
    await create_test_file(file_path, "# Local Event Index\n\nIndexed from watcher event.\n")

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(test_project, {(Change.added, str(file_path))})

    async with db.scoped_session(session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "local-event-index.md")
    assert entity is not None
    assert entity.title == "local-event-index"
    assert watch_service.state.synced_files == 1
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_changes_with_local_event_index_runtime_deletes_missing_markdown_file(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    sync_service,
    entity_repository,
):
    """The concrete local event-index runtime reconciles watcher delete events."""

    file_path = project_config.home / "local-event-delete.md"
    await create_test_file(file_path, "# Local Event Delete\n\nDelete me.\n")
    await sync_service.sync(project_config.home)

    async with db.scoped_session(session_maker) as session:
        before = await entity_repository.get_by_file_path(session, "local-event-delete.md")
    assert before is not None

    file_path.unlink()

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(test_project, {(Change.deleted, str(file_path))})

    async with db.scoped_session(session_maker) as session:
        after = await entity_repository.get_by_file_path(session, "local-event-delete.md")
    assert after is None
    assert watch_service.state.synced_files == 1
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_changes_with_local_event_index_runtime_processes_move_as_delete_and_index(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    sync_service,
    entity_repository,
):
    """The event-index watcher models moves as cloud-shaped delete plus index work."""

    old_path = project_config.home / "old" / "local-event-move.md"
    old_path.parent.mkdir(parents=True)
    await create_test_file(old_path, "# Local Event Move\n\nMove me.\n")
    await sync_service.sync(project_config.home)

    async with db.scoped_session(session_maker) as session:
        before = await entity_repository.get_by_file_path(session, "old/local-event-move.md")
    assert before is not None

    new_path = project_config.home / "new" / "local-event-move.md"
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)

    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(
        test_project,
        {
            (Change.added, str(new_path)),
            (Change.deleted, str(old_path)),
        },
    )

    async with db.scoped_session(session_maker) as session:
        old_entity = await entity_repository.get_by_file_path(session, "old/local-event-move.md")
        moved_entity = await entity_repository.get_by_file_path(session, "new/local-event-move.md")
    assert old_entity is None
    assert moved_entity is not None
    assert watch_service.state.synced_files == 2
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"
    assert not [event for event in watch_service.state.recent_events if event.action == "moved"]


@pytest.mark.asyncio
async def test_handle_changes_with_local_event_index_runtime_resolves_relations_after_index(
    app_config: BasicMemoryConfig,
    project_repository,
    session_maker,
    test_project,
    project_config,
    sync_service,
    relation_repository,
):
    """Local event-index updates run the same post-index relation repair as cloud jobs."""

    source_path = project_config.home / "source.md"
    await create_test_file(
        source_path,
        """---
type: note
title: Source
---
# Source

- relates_to [[Target Note]]
""",
    )
    await sync_service.sync(project_config.home)

    async with db.scoped_session(session_maker) as session:
        unresolved_before = await relation_repository.find_unresolved_relations(session)
    assert len(unresolved_before) == 1

    target_path = project_config.home / "target-note.md"
    await create_test_file(
        target_path,
        """---
type: note
title: Target Note
---
# Target Note
""",
    )
    watch_service = WatchService(
        app_config=app_config,
        project_repository=project_repository,
        session_maker=session_maker,
        event_index_runtime_factory=LocalWatchEventIndexRuntimeFactory(),
    )

    await watch_service.handle_changes(test_project, {(Change.added, str(target_path))})

    async with db.scoped_session(session_maker) as session:
        unresolved_after = await relation_repository.find_unresolved_relations(session)
    assert unresolved_after == []
    assert watch_service.state.synced_files == 1
    assert watch_service.state.recent_events[0].action == "index"
    assert watch_service.state.recent_events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_file_add(watch_service, project_config, test_project, entity_repository):
    """Test handling new file creation."""
    project_dir = project_config.home

    # empty dir is ignored
    empty_dir = project_dir / "empty_dir"
    empty_dir.mkdir()

    # Setup changes
    new_file = project_dir / "new_note.md"
    changes = {(Change.added, str(empty_dir)), (Change.added, str(new_file))}

    # Create the file
    content = """---
type: knowledge
---
# New Note
Test content
"""
    await create_test_file(new_file, content)

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify
    async with db.scoped_session(watch_service.session_maker) as session:
        entity = await entity_repository.get_by_file_path(session, "new_note.md")
    assert entity is not None
    assert entity.title == "new_note"

    # Check event was recorded
    events = [e for e in watch_service.state.recent_events if e.action == "new"]
    assert len(events) == 1
    assert events[0].path == "new_note.md"
    assert events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_file_modify(watch_service, project_config, sync_service, test_project):
    """Test handling file modifications."""
    project_dir = project_config.home

    # empty dir is ignored
    empty_dir = project_dir / "empty_dir"
    empty_dir.mkdir()

    # Create initial file
    test_file = project_dir / "test_note.md"
    initial_content = """---
type: knowledge
---
# Test Note
Initial content
"""
    await create_test_file(test_file, initial_content)

    # Initial sync
    await sync_service.sync(project_dir)

    # Modify file
    modified_content = """---
type: knowledge
---
# Test Note
Modified content
"""
    await create_test_file(test_file, modified_content)

    # Setup changes
    changes = {(Change.modified, str(empty_dir)), (Change.modified, str(test_file))}

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify
    async with db.scoped_session(sync_service.session_maker) as session:
        entity = await sync_service.entity_repository.get_by_file_path(session, "test_note.md")
    assert entity is not None

    # Check event was recorded
    events = [e for e in watch_service.state.recent_events if e.action == "modified"]
    assert len(events) == 1
    assert events[0].path == "test_note.md"
    assert events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_file_delete(watch_service, project_config, test_project, sync_service):
    """Test handling file deletion."""
    project_dir = project_config.home

    # Create initial file
    test_file = project_dir / "to_delete.md"
    content = """---
type: knowledge
---
# Delete Test
Test content
"""
    await create_test_file(test_file, content)

    # Initial sync
    await sync_service.sync(project_dir)

    # Delete file
    test_file.unlink()

    # Setup changes
    changes = {(Change.deleted, str(test_file))}

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify
    async with db.scoped_session(sync_service.session_maker) as session:
        entity = await sync_service.entity_repository.get_by_file_path(session, "to_delete.md")
    assert entity is None

    # Check event was recorded
    events = [e for e in watch_service.state.recent_events if e.action == "deleted"]
    assert len(events) == 1
    assert events[0].path == "to_delete.md"
    assert events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_file_move(watch_service, project_config, test_project, sync_service):
    """Test handling file moves."""
    project_dir = project_config.home

    # Create initial file
    old_path = project_dir / "old" / "test_move.md"
    content = """---
type: knowledge
---
# Move Test
Test content
"""
    await create_test_file(old_path, content)

    # Initial sync
    await sync_service.sync(project_dir)
    async with db.scoped_session(sync_service.session_maker) as session:
        initial_entity = await sync_service.entity_repository.get_by_file_path(
            session, "old/test_move.md"
        )
    assert initial_entity is not None

    # Move file
    new_path = project_dir / "new" / "moved_file.md"
    new_path.parent.mkdir(parents=True)
    old_path.rename(new_path)

    # Setup changes
    changes = {(Change.deleted, str(old_path)), (Change.added, str(new_path))}

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify
    async with db.scoped_session(sync_service.session_maker) as session:
        moved_entity = await sync_service.entity_repository.get_by_file_path(
            session, "new/moved_file.md"
        )
    assert moved_entity is not None
    assert moved_entity.id == initial_entity.id  # Same entity, new path

    # Original path should no longer exist
    async with db.scoped_session(sync_service.session_maker) as session:
        old_entity = await sync_service.entity_repository.get_by_file_path(
            session, "old/test_move.md"
        )
    assert old_entity is None

    # Check event was recorded
    events = [e for e in watch_service.state.recent_events if e.action == "moved"]
    assert len(events) == 1
    assert events[0].path == "old/test_move.md -> new/moved_file.md"
    assert events[0].status == "success"


@pytest.mark.asyncio
async def test_handle_concurrent_changes(watch_service, project_config, test_project, sync_service):
    """Test handling multiple file changes happening close together."""
    project_dir = project_config.home

    # Create multiple files with small delays to simulate concurrent changes
    async def create_files():
        # Create first file
        file1 = project_dir / "note1.md"
        await create_test_file(file1, "First note")
        await asyncio.sleep(0.1)

        # Create second file
        file2 = project_dir / "note2.md"
        await create_test_file(file2, "Second note")
        await asyncio.sleep(0.1)

        # Modify first file
        await create_test_file(file1, "Modified first note")

        return file1, file2

    # Create files and collect changes
    file1, file2 = await create_files()

    # Setup combined changes
    changes = {
        (Change.added, str(file1)),
        (Change.modified, str(file1)),
        (Change.added, str(file2)),
    }

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify both files were processed
    async with db.scoped_session(sync_service.session_maker) as session:
        entity1 = await sync_service.entity_repository.get_by_file_path(session, "note1.md")
        entity2 = await sync_service.entity_repository.get_by_file_path(session, "note2.md")

    assert entity1 is not None
    assert entity2 is not None

    # Check events were recorded in correct order
    events = watch_service.state.recent_events
    actions = [e.action for e in events]
    assert "new" in actions
    assert "modified" not in actions  # only process file once


@pytest.mark.asyncio
async def test_handle_rapid_move(watch_service, project_config, test_project, sync_service):
    """Test handling rapid move operations."""
    project_dir = project_config.home

    # Create initial file
    original_path = project_dir / "original.md"
    content = """---
type: knowledge
---
# Move Test
Test content for rapid moves
"""
    await create_test_file(original_path, content)
    await sync_service.sync(project_dir)

    # Perform rapid moves
    temp_path = project_dir / "temp.md"
    final_path = project_dir / "final.md"

    original_path.rename(temp_path)
    await asyncio.sleep(0.1)
    temp_path.rename(final_path)

    # Setup changes that might come in various orders
    changes = {
        (Change.deleted, str(original_path)),
        (Change.added, str(temp_path)),
        (Change.deleted, str(temp_path)),
        (Change.added, str(final_path)),
    }

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify final state
    async with db.scoped_session(sync_service.session_maker) as session:
        final_entity = await sync_service.entity_repository.get_by_file_path(session, "final.md")
    assert final_entity is not None

    # Intermediate paths should not exist
    async with db.scoped_session(sync_service.session_maker) as session:
        original_entity = await sync_service.entity_repository.get_by_file_path(
            session, "original.md"
        )
        temp_entity = await sync_service.entity_repository.get_by_file_path(session, "temp.md")
    assert original_entity is None
    assert temp_entity is None


@pytest.mark.asyncio
async def test_handle_delete_then_add(watch_service, project_config, test_project, sync_service):
    """Test handling rapid move operations."""
    project_dir = project_config.home

    # Create initial file
    original_path = project_dir / "original.md"
    content = """---
type: knowledge
---
# Move Test
Test content for rapid moves
"""
    await create_test_file(original_path, content)

    # Setup changes that might come in various orders
    changes = {
        (Change.deleted, str(original_path)),
        (Change.added, str(original_path)),
    }

    # Handle changes
    await watch_service.handle_changes(test_project, changes)

    # Verify final state
    async with db.scoped_session(sync_service.session_maker) as session:
        original_entity = await sync_service.entity_repository.get_by_file_path(
            session, "original.md"
        )
    assert original_entity is None  # delete event is handled


@pytest.mark.asyncio
async def test_handle_directory_rename(watch_service, project_config, test_project, sync_service):
    """Test handling directory rename operations - regression test for the bug where directories
    were being processed as files, causing errors."""
    project_dir = project_config.home

    # Create a directory with a file inside
    old_dir_path = project_dir / "old_dir"
    old_dir_path.mkdir(parents=True, exist_ok=True)

    file_in_dir = old_dir_path / "test_file.md"
    content = """---
type: knowledge
---
# Test File
This is a test file in a directory
"""
    await create_test_file(file_in_dir, content)

    # Initial sync to add the file to the database
    await sync_service.sync(project_dir)

    # Rename the directory
    new_dir_path = project_dir / "new_dir"
    old_dir_path.rename(new_dir_path)

    # Setup changes that simulate directory rename
    # When a directory is renamed, watchfiles reports it as deleted and added
    changes = {
        (Change.deleted, str(old_dir_path)),
        (Change.added, str(new_dir_path)),
    }

    # Spy on sync_file calls without using stdlib mocks.
    original_sync_file = sync_service.sync_file
    calls: list[tuple[tuple, dict]] = []

    async def spy_sync_file(*args, **kwargs):
        calls.append((args, kwargs))
        return await original_sync_file(*args, **kwargs)

    sync_service.sync_file = spy_sync_file

    # Handle changes - this should not throw an exception
    await watch_service.handle_changes(test_project, changes)

    # Check if sync_file was called with any directory paths
    for args, kwargs in calls:
        path = args[0]
        full_path = project_dir / path
        assert not full_path.is_dir(), f"sync_file should not be called with directory path: {path}"

    # The file path should be untouched since we're ignoring directory events
    # We'd need a separate event for the file itself to be updated
    async with db.scoped_session(sync_service.session_maker) as session:
        old_entity = await sync_service.entity_repository.get_by_file_path(
            session, "old_dir/test_file.md"
        )

    # The original entity should still exist since we only renamed the directory
    # but didn't process updates to the file itself
    assert old_entity is not None


def test_is_project_path(watch_service, tmp_path):
    """Test the is_project_path method to ensure it correctly identifies paths within a project."""
    # Create a project at a specific path
    project_path = tmp_path / "project"
    project_path.mkdir(parents=True, exist_ok=True)

    # Create a file inside the project
    file_in_project = project_path / "subdirectory" / "file.md"
    file_in_project.parent.mkdir(parents=True, exist_ok=True)
    file_in_project.touch()

    # Create a file outside the project
    file_outside_project = tmp_path / "outside" / "file.md"
    file_outside_project.parent.mkdir(parents=True, exist_ok=True)
    file_outside_project.touch()

    # Create Project object with our path
    project = Project(id=1, name="test", path=str(project_path), permalink="test")

    # Test a file inside the project
    assert watch_service.is_project_path(project, file_in_project) is True

    # Test a file outside the project
    assert watch_service.is_project_path(project, file_outside_project) is False

    # Test the project path itself
    assert watch_service.is_project_path(project, project_path) is False


@pytest.mark.asyncio
async def test_handle_changes_skips_deleted_project(
    watch_service, project_config, test_project, sync_service, project_service, tmp_path
):
    """Test that handle_changes skips processing changes for projects that have been deleted.

    This is a regression test for issue #193 where deleted projects were being recreated
    by background sync because the directory still existed on disk.
    """
    from textwrap import dedent

    project_dir = project_config.home

    # Create a test file in the project
    test_file = project_dir / "test_note.md"
    content = dedent("""
        ---
        type: knowledge
        ---
        # Test Note
        Test content
    """).strip()
    await create_test_file(test_file, content)

    # Initial sync to create the entity
    await sync_service.sync(project_dir)

    # Verify entity was created
    async with db.scoped_session(sync_service.session_maker) as session:
        entity_before = await sync_service.entity_repository.get_by_file_path(
            session, "test_note.md"
        )
    assert entity_before is not None

    # Create a second project directly in the database and set it as default
    # so we can remove the first one (cannot remove default project)
    other_project_path = str(tmp_path.parent / "other-project-for-test")
    project_data = {
        "name": "other-project",
        "path": other_project_path,
        "permalink": "other-project",
        "is_active": True,
    }
    async with db.scoped_session(project_service.session_maker) as session:
        other_project = await project_service.repository.create(session, project_data)
        await project_service.repository.set_as_default(session, other_project.id)

    # Also add to config
    config = project_service.config_manager.load_config()
    from basic_memory.config import ProjectEntry

    config.projects["other-project"] = ProjectEntry(path=other_project_path)
    config.default_project = "other-project"
    project_service.config_manager.save_config(config)

    # Remove the test project from configuration (simulating project deletion)
    # This should prevent background sync from processing changes
    await project_service.remove_project(test_project.name)

    # Simulate file changes after project deletion
    # These changes should be ignored by the watch service
    modified_content = dedent("""
        ---
        type: knowledge
        ---
        # Test Note
        Modified content after project deletion
    """).strip()
    await create_test_file(test_file, modified_content)

    changes = {(Change.modified, str(test_file))}

    # Handle changes - should skip processing since project is deleted
    await watch_service.handle_changes(test_project, changes)

    # Verify that the entity was NOT re-created or updated
    # Since the project was deleted, the database should still have the old state
    # or the entity should be gone entirely if cleanup happened
    async with db.scoped_session(sync_service.session_maker) as session:
        entity_after = await sync_service.entity_repository.get_by_file_path(
            session, "test_note.md"
        )

    # The entity might be deleted or unchanged, but it should not be updated with new content
    if entity_after is not None:
        # If the entity still exists, it should have the old content, not the new content
        assert entity_after.checksum == entity_before.checksum, (
            "Entity should not be updated for deleted project"
        )
