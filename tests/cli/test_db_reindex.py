"""Tests for `bm reindex` CLI wiring."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.config import DatabaseBackend
import basic_memory.cli.commands.db as db_cmd  # noqa: F401


runner = CliRunner()


def _stub_app_config(*, semantic_search_enabled: bool = True) -> SimpleNamespace:
    """Build the minimal config surface the CLI reindex path expects."""
    return SimpleNamespace(
        semantic_search_enabled=semantic_search_enabled,
        database_path=Path("/tmp/basic-memory.db"),
        get_project_mode=lambda project_name: None,
        # app_callback reads this to decide whether to install the uvloop policy.
        database_backend=DatabaseBackend.SQLITE,
    )


def _configure_reindex_cli(monkeypatch, app_config: SimpleNamespace) -> None:
    """Keep CLI tests focused on reindex wiring instead of full app startup."""
    monkeypatch.setattr("basic_memory.cli.app.init_cli_logging", lambda: None)
    monkeypatch.setattr("basic_memory.cli.app.maybe_show_init_line", lambda *_args: None)
    monkeypatch.setattr("basic_memory.cli.app.maybe_show_cloud_promo", lambda *_args: None)
    monkeypatch.setattr("basic_memory.cli.app.maybe_run_periodic_auto_update", lambda *_args: None)
    monkeypatch.setattr(
        "basic_memory.cli.app.CliContainer.create",
        lambda: SimpleNamespace(config=app_config, mode=SimpleNamespace(is_cloud=False)),
    )
    monkeypatch.setattr(
        db_cmd,
        "ConfigManager",
        lambda: SimpleNamespace(config=app_config),
    )


def test_reindex_defaults_to_incremental_search_and_embeddings(monkeypatch):
    app_config = _stub_app_config()
    _configure_reindex_cli(monkeypatch, app_config)
    captured: dict[str, object] = {}

    async def _stub_reindex(app_config, *, search: bool, embeddings: bool, full: bool, project):
        captured.update(
            {
                "app_config": app_config,
                "search": search,
                "embeddings": embeddings,
                "full": full,
                "project": project,
            }
        )

    monkeypatch.setattr(db_cmd, "_reindex", _stub_reindex)
    monkeypatch.setattr(db_cmd, "run_with_cleanup", lambda coro: asyncio.run(coro))

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert captured == {
        "app_config": app_config,
        "search": True,
        "embeddings": True,
        "full": False,
        "project": None,
    }


def test_reindex_full_runs_full_search_and_embeddings(monkeypatch):
    app_config = _stub_app_config()
    _configure_reindex_cli(monkeypatch, app_config)
    captured: dict[str, object] = {}

    async def _stub_reindex(app_config, *, search: bool, embeddings: bool, full: bool, project):
        captured.update(
            {
                "search": search,
                "embeddings": embeddings,
                "full": full,
                "project": project,
            }
        )

    monkeypatch.setattr(db_cmd, "_reindex", _stub_reindex)
    monkeypatch.setattr(db_cmd, "run_with_cleanup", lambda coro: asyncio.run(coro))

    result = runner.invoke(app, ["reindex", "--full"])

    assert result.exit_code == 0
    assert captured == {
        "search": True,
        "embeddings": True,
        "full": True,
        "project": None,
    }


def test_reindex_full_search_runs_search_only(monkeypatch):
    app_config = _stub_app_config()
    _configure_reindex_cli(monkeypatch, app_config)
    captured: dict[str, object] = {}

    async def _stub_reindex(app_config, *, search: bool, embeddings: bool, full: bool, project):
        captured.update(
            {
                "search": search,
                "embeddings": embeddings,
                "full": full,
                "project": project,
            }
        )

    monkeypatch.setattr(db_cmd, "_reindex", _stub_reindex)
    monkeypatch.setattr(db_cmd, "run_with_cleanup", lambda coro: asyncio.run(coro))

    result = runner.invoke(app, ["reindex", "--full", "--search"])

    assert result.exit_code == 0
    assert captured == {
        "search": True,
        "embeddings": False,
        "full": True,
        "project": None,
    }


def test_reindex_embeddings_only_preserves_incremental_default(monkeypatch):
    app_config = _stub_app_config()
    _configure_reindex_cli(monkeypatch, app_config)
    captured: dict[str, object] = {}

    async def _stub_reindex(app_config, *, search: bool, embeddings: bool, full: bool, project):
        captured.update(
            {
                "search": search,
                "embeddings": embeddings,
                "full": full,
                "project": project,
            }
        )

    monkeypatch.setattr(db_cmd, "_reindex", _stub_reindex)
    monkeypatch.setattr(db_cmd, "run_with_cleanup", lambda coro: asyncio.run(coro))

    result = runner.invoke(app, ["reindex", "--embeddings"])

    assert result.exit_code == 0
    assert captured == {
        "search": False,
        "embeddings": True,
        "full": False,
        "project": None,
    }


@pytest.mark.asyncio
async def test_reindex_project_full_uses_core_project_index_and_reports_summary(
    monkeypatch,
    session_maker,
):
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", path="/tmp/foo")
    project_index = AsyncMock(
        return_value=SimpleNamespace(
            total_files=3,
            enqueued_files=2,
            enqueued_batches=1,
            deleted_files=1,
        )
    )
    printed_lines: list[str] = []

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return [project]

    # _reindex imports its database/index dependencies at call time (#886),
    # so stubs target the source modules instead of db_cmd attributes.
    monkeypatch.setattr(
        "basic_memory.services.initialization.reconcile_projects_with_config", AsyncMock()
    )
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.sync.sync_service.get_sync_service",
        AsyncMock(side_effect=AssertionError("search reindex should not use SyncService")),
    )
    monkeypatch.setattr(
        "basic_memory.index.run_local_project_index_for_project",
        project_index,
    )
    monkeypatch.setattr(
        db_cmd.console,
        "print",
        lambda message="", *args, **kwargs: printed_lines.append(str(message)),
    )

    await db_cmd._reindex(
        app_config,
        search=True,
        embeddings=False,
        full=True,
        project="foo",
    )

    project_index.assert_awaited_once()
    index_call = project_index.await_args
    assert index_call is not None
    assert index_call.args[0] == project
    assert index_call.kwargs["force_full"] is True
    assert any("project index" in line for line in printed_lines)
    assert any("3 observed, 2 indexed, 1 deleted" in line for line in printed_lines)


@pytest.mark.asyncio
async def test_reindex_embeddings_only_full_passes_force_full_to_vector_reindex(
    monkeypatch,
    session_maker,
):
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", path="/tmp/foo")
    printed_lines: list[str] = []
    vector_reindex_calls: list[dict[str, object]] = []

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return [project]

    class StubSearchService:
        def __init__(self, search_repository, entity_repository, file_service, *, session_maker):
            self.search_repository = search_repository
            self.entity_repository = entity_repository
            self.file_service = file_service
            self.session_maker = session_maker

        async def reindex_vectors(self, *, progress_callback=None, force_full: bool = False):
            vector_reindex_calls.append(
                {
                    "progress_callback": progress_callback,
                    "force_full": force_full,
                }
            )
            return {"total_entities": 2, "embedded": 2, "skipped": 0, "errors": 0}

    class SilentProgress:
        def __init__(self, *args, **kwargs):
            self.tasks: dict[int, SimpleNamespace] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add_task(self, description, total=None):
            self.tasks[1] = SimpleNamespace(total=total, description=description)
            return 1

        def update(self, task_id, **kwargs):
            if "total" in kwargs:
                self.tasks[task_id].total = kwargs["total"]

    # _reindex imports its database/sync dependencies at call time (#886),
    # so stubs target the source modules instead of db_cmd attributes.
    monkeypatch.setattr(
        "basic_memory.services.initialization.reconcile_projects_with_config", AsyncMock()
    )
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.repository.search_repository.create_search_repository",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "basic_memory.repository.EntityRepository", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(
        "basic_memory.markdown.entity_parser.EntityParser",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "basic_memory.markdown.markdown_processor.MarkdownProcessor",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        "basic_memory.services.file_service.FileService", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr("basic_memory.services.search_service.SearchService", StubSearchService)
    monkeypatch.setattr(db_cmd, "Progress", SilentProgress)
    monkeypatch.setattr(
        db_cmd.console,
        "print",
        lambda message="", *args, **kwargs: printed_lines.append(str(message)),
    )

    await db_cmd._reindex(
        app_config,
        search=False,
        embeddings=True,
        full=True,
        project="foo",
    )

    assert len(vector_reindex_calls) == 1
    assert vector_reindex_calls[0]["force_full"] is True
    assert callable(vector_reindex_calls[0]["progress_callback"])
    assert any("full rebuild" in line for line in printed_lines)
