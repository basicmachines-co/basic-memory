"""Tests for `bm reindex` CLI wiring."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory.cli.commands.db as db_cmd  # noqa: F401


runner = CliRunner()


def _stub_app_config(*, semantic_search_enabled: bool = True) -> SimpleNamespace:
    """Build the minimal config surface the CLI reindex path expects."""
    return SimpleNamespace(
        semantic_search_enabled=semantic_search_enabled,
        database_path=Path("/tmp/basic-memory.db"),
        get_project_mode=lambda project_name: None,
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
async def test_reindex_project_full_passes_force_full_to_sync_and_reports_mode(monkeypatch):
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", path="/tmp/foo")
    session_maker = object()
    sync_service = SimpleNamespace(sync=AsyncMock())
    printed_lines: list[str] = []

    class StubProjectRepository:
        def __init__(self, _session_maker):
            self._session_maker = _session_maker

        async def get_active_projects(self):
            return [project]

    class SilentProgress:
        def __init__(self, *args, **kwargs):
            self.tasks: dict[int, SimpleNamespace] = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add_task(self, description, total=1):
            self.tasks[1] = SimpleNamespace(total=total, description=description)
            return 1

        def update(self, task_id, **kwargs):
            if "total" in kwargs:
                self.tasks[task_id].total = kwargs["total"]

    monkeypatch.setattr(db_cmd, "reconcile_projects_with_config", AsyncMock())
    monkeypatch.setattr(
        db_cmd.db,
        "get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr(db_cmd.db, "shutdown_db", AsyncMock())
    monkeypatch.setattr(db_cmd, "ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(db_cmd, "get_sync_service", AsyncMock(return_value=sync_service))
    monkeypatch.setattr(db_cmd, "Progress", SilentProgress)
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

    sync_service.sync.assert_awaited_once()
    sync_call = sync_service.sync.await_args
    assert sync_call.args[0] == Path("/tmp/foo")
    assert sync_call.kwargs["project_name"] == "foo"
    assert sync_call.kwargs["force_full"] is True
    assert sync_call.kwargs["sync_embeddings"] is False
    assert callable(sync_call.kwargs["progress_callback"])
    assert any("full scan" in line for line in printed_lines)


@pytest.mark.asyncio
async def test_reindex_embeddings_only_full_passes_force_full_to_vector_reindex(monkeypatch):
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", path="/tmp/foo")
    session_maker = object()
    printed_lines: list[str] = []
    vector_reindex_calls: list[dict[str, object]] = []

    class StubProjectRepository:
        def __init__(self, _session_maker):
            self._session_maker = _session_maker

        async def get_active_projects(self):
            return [project]

    class StubSearchService:
        def __init__(self, search_repository, entity_repository, file_service):
            self.search_repository = search_repository
            self.entity_repository = entity_repository
            self.file_service = file_service

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

    monkeypatch.setattr(db_cmd, "reconcile_projects_with_config", AsyncMock())
    monkeypatch.setattr(
        db_cmd.db,
        "get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr(db_cmd.db, "shutdown_db", AsyncMock())
    monkeypatch.setattr(db_cmd, "ProjectRepository", StubProjectRepository)
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
