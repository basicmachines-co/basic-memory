"""Integration-style tests for the initialization service.

Goal: avoid brittle deep mocking; assert real behavior using the existing
test config + dual-backend fixtures.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.index import ProjectIndexCoordinatorResult
from basic_memory.index import (
    LOCAL_EVENT_INDEX_TENANT_ID,
    LocalWatchEventIndexRuntimeFactory,
)
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.services.initialization import (
    ensure_initialization,
    initialize_app,
    initialize_database,
    initialize_file_sync,
    reconcile_projects_with_config,
)


@pytest.mark.asyncio
async def test_initialize_database_creates_engine_and_allows_queries(app_config: BasicMemoryConfig):
    await db.shutdown_db()
    try:
        await initialize_database(app_config)

        engine, session_maker = await db.get_or_create_db(app_config.database_path)
        assert engine is not None
        assert session_maker is not None

        # Smoke query on the initialized DB
        async with db.scoped_session(session_maker) as session:
            result = await session.execute(db.text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_initialize_database_raises_on_invalid_postgres_config(
    app_config: BasicMemoryConfig, config_manager
):
    """If config selects Postgres but has no DATABASE_URL, initialization should fail."""
    await db.shutdown_db()
    try:
        bad_config = app_config.model_copy(
            update={"database_backend": DatabaseBackend.POSTGRES, "database_url": None}
        )
        config_manager.save_config(bad_config)

        with pytest.raises(ValueError):
            await initialize_database(bad_config)
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_reconcile_projects_with_config_creates_projects_and_default(
    app_config: BasicMemoryConfig, config_manager, config_home
):
    await db.shutdown_db()
    try:
        # Ensure the configured paths exist
        proj_a = config_home / "proj-a"
        proj_b = config_home / "proj-b"
        proj_a.mkdir(parents=True, exist_ok=True)
        proj_b.mkdir(parents=True, exist_ok=True)

        from basic_memory.config import ProjectEntry

        updated = app_config.model_copy(
            update={
                "projects": {
                    "proj-a": ProjectEntry(path=str(proj_a)),
                    "proj-b": ProjectEntry(path=str(proj_b)),
                },
                "default_project": "proj-b",
            }
        )
        config_manager.save_config(updated)

        # Real DB init + reconcile
        await initialize_database(updated)
        await reconcile_projects_with_config(updated)

        _, session_maker = await db.get_or_create_db(
            updated.database_path, db_type=db.DatabaseType.FILESYSTEM
        )
        repo = ProjectRepository()

        async with db.scoped_session(session_maker) as session:
            active = await repo.get_active_projects(session)
            names = {p.name for p in active}
            assert names.issuperset({"proj-a", "proj-b"})

            default = await repo.get_default_project(session)
            assert default is not None
            assert default.name == "proj-b"
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_reconcile_projects_with_config_swallow_errors(
    monkeypatch, app_config: BasicMemoryConfig
):
    """reconcile_projects_with_config should not raise if ProjectService sync fails."""
    await db.shutdown_db()
    try:
        await initialize_database(app_config)

        async def boom(self):  # noqa: ANN001
            raise ValueError("Project synchronization error")

        monkeypatch.setattr(
            "basic_memory.services.project_service.ProjectService.synchronize_projects",
            boom,
        )

        # Should not raise
        await reconcile_projects_with_config(app_config)
    finally:
        await db.shutdown_db()


def test_ensure_initialization_runs_and_cleans_up(app_config: BasicMemoryConfig, config_manager):
    # ensure_initialization uses asyncio.run; keep this test synchronous.
    ensure_initialization(app_config)

    # Must be cleaned up to avoid hanging processes.
    assert db._engine is None  # pyright: ignore [reportPrivateUsage]
    assert db._session_maker is None  # pyright: ignore [reportPrivateUsage]


@pytest.mark.asyncio
async def test_initialize_app_warns_on_frontmatter_permalink_precedence(
    app_config: BasicMemoryConfig, monkeypatch
):
    app_config.database_backend = DatabaseBackend.SQLITE
    app_config.ensure_frontmatter_on_sync = True
    app_config.disable_permalinks = True

    init_db_mock = AsyncMock()
    reconcile_mock = AsyncMock()
    monkeypatch.setattr("basic_memory.services.initialization.initialize_database", init_db_mock)
    monkeypatch.setattr(
        "basic_memory.services.initialization.reconcile_projects_with_config",
        reconcile_mock,
    )

    warnings: list[str] = []

    def capture_warning(message: str) -> None:
        warnings.append(message)

    monkeypatch.setattr("basic_memory.services.initialization.logger.warning", capture_warning)

    await initialize_app(app_config)

    assert init_db_mock.await_count == 1
    assert reconcile_mock.await_count == 1
    assert any(
        "ensure_frontmatter_on_sync=True overrides disable_permalinks=True" in message
        for message in warnings
    )


class _FakeWatchService:
    """Captures init kwargs so tests can assert what the real service receives."""

    last_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs):
        _FakeWatchService.last_kwargs = kwargs

    async def run(self):
        return None


def _disable_test_env_short_circuit(monkeypatch) -> None:
    """Bypass ``is_test_env`` so ``initialize_file_sync`` actually runs.

    ``is_test_env`` returns True whenever pytest is running, which would cause
    ``initialize_file_sync`` to return before constructing a WatchService.
    """
    monkeypatch.setattr(BasicMemoryConfig, "is_test_env", property(lambda self: False))


@pytest.mark.asyncio
async def test_initialize_file_sync_passes_constrained_project_to_watch_service(
    app_config: BasicMemoryConfig, monkeypatch
):
    """``BASIC_MEMORY_MCP_PROJECT`` must reach the watch service, not just the
    one-shot background sync. Otherwise multiple ``basic-memory mcp --project X``
    processes each spawn a watcher over every project and race on file writes.
    """
    _disable_test_env_short_circuit(monkeypatch)
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "target-project")
    monkeypatch.setattr("basic_memory.sync.WatchService", _FakeWatchService)
    _FakeWatchService.last_kwargs = {}

    await initialize_file_sync(app_config, quiet=True)

    assert _FakeWatchService.last_kwargs.get("constrained_project") == "target-project"


@pytest.mark.asyncio
async def test_initialize_file_sync_no_constraint_when_env_unset(
    app_config: BasicMemoryConfig, monkeypatch
):
    """With no env var set, the watch service is unconstrained."""
    _disable_test_env_short_circuit(monkeypatch)
    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    monkeypatch.setattr("basic_memory.sync.WatchService", _FakeWatchService)
    _FakeWatchService.last_kwargs = {}

    await initialize_file_sync(app_config, quiet=True)

    assert _FakeWatchService.last_kwargs.get("constrained_project") is None


@pytest.mark.asyncio
async def test_initialize_file_sync_wires_event_index_runtime_by_default(
    app_config: BasicMemoryConfig, monkeypatch
):
    """Watcher event indexing is the default startup path."""
    _disable_test_env_short_circuit(monkeypatch)
    monkeypatch.setattr("basic_memory.sync.WatchService", _FakeWatchService)
    _FakeWatchService.last_kwargs = {}

    await initialize_file_sync(app_config, quiet=True)

    assert isinstance(
        _FakeWatchService.last_kwargs.get("event_index_runtime_factory"),
        LocalWatchEventIndexRuntimeFactory,
    )


@pytest.mark.asyncio
async def test_initialize_file_sync_uses_project_index_runtime_for_initial_sync_by_default(
    app_config: BasicMemoryConfig, config_manager, config_home, monkeypatch
):
    """Default startup indexing uses project fanout instead of the legacy sync scan."""
    await db.shutdown_db()
    try:
        from basic_memory.config import ProjectEntry

        project_dir = config_home / "event-startup"
        project_dir.mkdir(parents=True, exist_ok=True)
        updated = app_config.model_copy(
            update={
                "projects": {"event-startup": ProjectEntry(path=str(project_dir))},
                "default_project": "event-startup",
            }
        )
        config_manager.save_config(updated)

        await initialize_database(updated)
        await reconcile_projects_with_config(updated)

        _disable_test_env_short_circuit(monkeypatch)
        monkeypatch.setattr("basic_memory.sync.WatchService", _FakeWatchService)

        created_coroutines = []

        def capture_task(coro):
            created_coroutines.append(coro)
            return object()

        async def get_sync_service(project):  # noqa: ANN001
            raise AssertionError("event-index startup should not build a legacy SyncService")

        class RecordingProjectIndexRuntimeFactory:
            tenant_id = LOCAL_EVENT_INDEX_TENANT_ID

            async def runtime_for_project(self, project):  # noqa: ANN001
                return f"runtime:{project.name}"

        project_index_calls = []

        async def run_project_index(request, *, runtime):  # noqa: ANN001
            project_index_calls.append((request, runtime))
            return ProjectIndexCoordinatorResult(
                total_files=0,
                enqueued_files=0,
                enqueued_batches=0,
                deleted_files=0,
            )

        monkeypatch.setattr(
            "basic_memory.services.initialization.asyncio.create_task", capture_task
        )
        monkeypatch.setattr("basic_memory.sync.sync_service.get_sync_service", get_sync_service)
        monkeypatch.setattr(
            "basic_memory.index.LocalProjectIndexRuntimeFactory",
            RecordingProjectIndexRuntimeFactory,
        )
        monkeypatch.setattr("basic_memory.index.run_local_project_index", run_project_index)

        await initialize_file_sync(updated, quiet=True)

        assert len(created_coroutines) == 1
        await created_coroutines[0]

        assert len(project_index_calls) == 1
        request, runtime = project_index_calls[0]
        assert request.project.project_name == "event-startup"
        assert request.search is True
        assert request.embeddings is True
        assert runtime == "runtime:event-startup"
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_initialize_file_sync_uses_legacy_sync_when_event_index_disabled(
    app_config: BasicMemoryConfig, config_manager, config_home, monkeypatch
):
    """The old SyncService path remains callable as the regression oracle."""
    await db.shutdown_db()
    try:
        from basic_memory.config import ProjectEntry

        project_dir = config_home / "legacy-startup"
        project_dir.mkdir(parents=True, exist_ok=True)
        updated = app_config.model_copy(
            update={
                "projects": {"legacy-startup": ProjectEntry(path=str(project_dir))},
                "default_project": "legacy-startup",
                "watch_event_index": False,
            }
        )
        config_manager.save_config(updated)

        await initialize_database(updated)
        await reconcile_projects_with_config(updated)

        _disable_test_env_short_circuit(monkeypatch)
        monkeypatch.setattr("basic_memory.sync.WatchService", _FakeWatchService)

        created_coroutines = []

        def capture_task(coro):
            created_coroutines.append(coro)
            return object()

        sync_calls = []

        class RecordingLegacySyncService:
            async def sync(self, sync_dir, project_name=None):  # noqa: ANN001
                sync_calls.append((sync_dir, project_name))

        async def get_sync_service(project):  # noqa: ANN001
            assert project.name == "legacy-startup"
            return RecordingLegacySyncService()

        async def run_project_index(request, *, runtime):  # noqa: ANN001
            raise AssertionError("legacy startup should not run project-index fanout")

        monkeypatch.setattr(
            "basic_memory.services.initialization.asyncio.create_task", capture_task
        )
        monkeypatch.setattr("basic_memory.sync.sync_service.get_sync_service", get_sync_service)
        monkeypatch.setattr("basic_memory.index.run_local_project_index", run_project_index)

        await initialize_file_sync(updated, quiet=True)

        assert len(created_coroutines) == 1
        await created_coroutines[0]

        assert sync_calls == [(project_dir, "legacy-startup")]
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_initialize_file_sync_skips_project_with_non_absolute_path(
    app_config: BasicMemoryConfig, config_manager, config_home, monkeypatch
):
    """Projects without an absolute local path are excluded from background sync (issue #949).

    A config entry of ``{"path": ""}`` defaults to LOCAL mode and is not
    recognized as cloud, yet Path("") resolves to the process cwd. Syncing it
    would inject frontmatter into unrelated files, so it must be skipped.
    """
    await db.shutdown_db()
    try:
        from basic_memory.config import ProjectEntry

        good = config_home / "good"
        good.mkdir(parents=True, exist_ok=True)

        updated = app_config.model_copy(
            update={
                "projects": {
                    "good": ProjectEntry(path=str(good)),
                    # No mode -> defaults to LOCAL, empty (cwd-relative) path.
                    "empty-path": ProjectEntry(path=""),
                },
                "default_project": "good",
            }
        )
        config_manager.save_config(updated)

        await initialize_database(updated)
        await reconcile_projects_with_config(updated)

        _disable_test_env_short_circuit(monkeypatch)
        monkeypatch.setattr("basic_memory.sync.WatchService", _FakeWatchService)

        infos: list[str] = []
        monkeypatch.setattr(
            "basic_memory.services.initialization.logger.info",
            lambda message, *args, **kwargs: infos.append(message),
        )

        await initialize_file_sync(updated, quiet=True)

        skip_logs = [m for m in infos if "not locally syncable" in m]
        assert skip_logs, "expected a skip log for the empty-path project"
        assert "empty-path" in skip_logs[0]
        assert "good" not in skip_logs[0]
    finally:
        await db.shutdown_db()


@pytest.mark.asyncio
async def test_initialize_app_no_precedence_warning_when_not_conflicting(
    app_config: BasicMemoryConfig, monkeypatch
):
    app_config.ensure_frontmatter_on_sync = False
    app_config.disable_permalinks = True

    monkeypatch.setattr(
        "basic_memory.services.initialization.initialize_database",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "basic_memory.services.initialization.reconcile_projects_with_config",
        AsyncMock(),
    )

    warnings: list[str] = []

    def capture_warning(message: str) -> None:
        warnings.append(message)

    monkeypatch.setattr("basic_memory.services.initialization.logger.warning", capture_warning)

    await initialize_app(app_config)

    assert not any(
        "ensure_frontmatter_on_sync=True overrides disable_permalinks=True" in message
        for message in warnings
    )
