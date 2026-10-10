"""Tests for `bm reindex` CLI wiring."""

import asyncio
from collections.abc import Mapping
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console
from rich.progress import Progress
from sqlalchemy import text
from typer.testing import CliRunner

from basic_memory import db
from basic_memory.cli.app import app
from basic_memory.config import DatabaseBackend
import basic_memory.cli.commands.db as db_cmd  # noqa: F401


runner = CliRunner()


def _vector_stats(
    *,
    total_entities: int,
    embedded: int,
    skipped: int,
    errors: int,
    deferred: int = 0,
    sample_errors: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "total_entities": total_entities,
        "embedded": embedded,
        "skipped": skipped,
        "errors": errors,
        "deferred": deferred,
        "sample_errors": sample_errors,
        "vector_index": "milvus",
        "embedding_model": "FastEmbedEmbeddingProvider:BAAI/bge-small-en-v1.5",
    }


def _stub_app_config() -> SimpleNamespace:
    """Build the minimal config surface the CLI reindex path expects."""
    return SimpleNamespace(
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


def _configure_embedding_runtime(
    monkeypatch,
    session_maker,
    stats: Mapping[str, object],
) -> tuple[SimpleNamespace, AsyncMock, list[str]]:
    """Install the runtime boundaries needed to exercise the real reindex command."""
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", permalink="foo", path="/tmp/foo")
    printed_lines: list[str] = []
    project_index = AsyncMock(
        return_value=SimpleNamespace(
            total_files=1,
            enqueued_files=1,
            enqueued_batches=1,
            deleted_files=0,
        )
    )

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return [project]

    class StubSearchService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def reindex_vectors(
            self, *, progress_callback=None, force_full: bool = False, started_callback=None
        ):
            return dict(stats)

    class SilentProgress:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

        def add_task(self, *args, **kwargs) -> int:
            return 1

        def update(self, *args, **kwargs) -> None:
            pass

    _configure_reindex_cli(monkeypatch, app_config)
    monkeypatch.setattr(db_cmd, "run_with_cleanup", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(
        "basic_memory.services.initialization.reconcile_projects_with_config",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "basic_memory.services.initialization.recover_project_materializations",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.index.local_project.run_local_project_index_for_project",
        project_index,
    )
    monkeypatch.setattr(
        "basic_memory.repository.search_repository.create_search_repository",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("basic_memory.repository.EntityRepository", lambda *a, **k: object())
    monkeypatch.setattr(
        "basic_memory.markdown.entity_parser.EntityParser",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr(
        "basic_memory.markdown.markdown_processor.MarkdownProcessor",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr(
        "basic_memory.services.file_service.FileService",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr("basic_memory.services.search_service.SearchService", StubSearchService)
    monkeypatch.setattr(db_cmd, "Progress", SilentProgress)
    monkeypatch.setattr(
        db_cmd.console,
        "print",
        lambda message="", *args, **kwargs: printed_lines.append(str(message)),
    )
    return app_config, project_index, printed_lines


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
    project = SimpleNamespace(id=1, name="foo", permalink="foo", path="/tmp/foo")
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
        "basic_memory.index.local_project.run_local_project_index_for_project",
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
    # Search-only reindex must not run the embedding provider via the project index.
    assert index_call.kwargs["embeddings"] is False
    assert any("project index" in line for line in printed_lines)
    assert any("3 observed, 2 indexed, 1 deleted" in line for line in printed_lines)


@pytest.mark.asyncio
async def test_reindex_embeddings_only_full_passes_force_full_to_vector_reindex(
    monkeypatch,
    session_maker,
):
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", permalink="foo", path="/tmp/foo")
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

        async def reindex_vectors(
            self, *, progress_callback=None, force_full: bool = False, started_callback=None
        ):
            vector_reindex_calls.append(
                {
                    "progress_callback": progress_callback,
                    "force_full": force_full,
                    "started_callback": started_callback,
                }
            )
            return _vector_stats(total_entities=2, embedded=2, skipped=0, errors=0)

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
    assert callable(vector_reindex_calls[0]["started_callback"])
    assert any("full rebuild" in line for line in printed_lines)


@pytest.mark.asyncio
async def test_reindex_embeddings_only_warns_when_project_has_no_indexed_entities(
    monkeypatch,
    session_maker,
):
    """Embeddings-only mode explains that it cannot discover project files."""
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", permalink="foo", path="/tmp/foo")
    printed_lines: list[str] = []

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return [project]

    class StubSearchService:
        def __init__(self, search_repository, entity_repository, file_service, *, session_maker):
            pass

        async def reindex_vectors(
            self, *, progress_callback=None, force_full: bool = False, started_callback=None
        ):
            return _vector_stats(total_entities=0, embedded=0, skipped=0, errors=0)

    class SilentProgress:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add_task(self, description, total=None):
            return 1

        def update(self, task_id, **kwargs):
            pass

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
        full=False,
        project="foo",
    )

    output = "\n".join(printed_lines)
    assert "0 entities embedded" in output
    assert "No indexed entities found" in output
    assert "--embeddings" in output
    assert "bm reindex" in output


@pytest.mark.asyncio
async def test_reindex_recovers_stuck_materializations_before_scan(monkeypatch, session_maker):
    """The scan reconciles deletes against the filesystem, so a note whose accepted
    file write a crash left stuck ('writing'/'pending'/'failed') would be destroyed
    as a missing file. Recovery must re-drive stuck rows before each project scan."""
    app_config = _stub_app_config()
    projects = [
        SimpleNamespace(id=1, name="foo", permalink="foo", path="/tmp/foo"),
        SimpleNamespace(id=2, name="bar", permalink="bar", path="/tmp/bar"),
    ]
    call_order: list[str] = []

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return projects

    async def record_recover(project, session_maker_arg):
        assert session_maker_arg is session_maker
        call_order.append(f"recover:{project.name}")

    async def record_project_index(project, *, runtime_factory, force_full, embeddings):
        call_order.append(f"index:{project.name}")
        return SimpleNamespace(total_files=0, enqueued_files=0, enqueued_batches=0, deleted_files=0)

    # _reindex imports its database/index dependencies at call time (#886),
    # so stubs target the source modules instead of db_cmd attributes.
    monkeypatch.setattr(
        "basic_memory.services.initialization.reconcile_projects_with_config", AsyncMock()
    )
    monkeypatch.setattr(
        "basic_memory.services.initialization.recover_project_materializations",
        record_recover,
    )
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.index.local_project.run_local_project_index_for_project",
        record_project_index,
    )
    monkeypatch.setattr(db_cmd.console, "print", lambda *args, **kwargs: None)

    await db_cmd._reindex(app_config, search=True, embeddings=False, full=False, project=None)

    # Recovery runs before the delete-reconciling scan, per project.
    assert call_order == ["recover:foo", "index:foo", "recover:bar", "index:bar"]


@pytest.mark.asyncio
async def test_reindex_search_converges_seeded_db_fts_divergence(
    monkeypatch,
    session_maker,
    app_config,
    test_project,
    test_graph,
):
    """An unchanged file's orphaned FTS row converges through reindex --search alone."""
    printed_lines: list[str] = []

    async with db.scoped_session(session_maker) as session:
        orphaned_observation_id = await session.scalar(
            text(
                "SELECT id FROM search_index "
                "WHERE project_id = :project_id AND type = 'observation' "
                "ORDER BY id LIMIT 1"
            ),
            {"project_id": test_project.id},
        )
        assert orphaned_observation_id is not None
        await session.execute(
            text("DELETE FROM observation WHERE project_id = :project_id AND id = :row_id"),
            {"project_id": test_project.id, "row_id": orphaned_observation_id},
        )

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return [test_project]

    project_index = AsyncMock(
        return_value=SimpleNamespace(
            total_files=0,
            enqueued_files=0,
            enqueued_batches=0,
            deleted_files=0,
        )
    )
    monkeypatch.setattr(
        "basic_memory.services.initialization.reconcile_projects_with_config", AsyncMock()
    )
    monkeypatch.setattr(
        "basic_memory.services.initialization.recover_project_materializations", AsyncMock()
    )
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.index.local_project.run_local_project_index_for_project",
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
        full=False,
        project=test_project.name,
    )

    project_index.assert_awaited_once()
    async with db.scoped_session(session_maker) as session:
        orphaned_search_row_count = await session.scalar(
            text(
                "SELECT COUNT(*) FROM search_index "
                "WHERE project_id = :project_id AND type = 'observation' AND id = :row_id"
            ),
            {"project_id": test_project.id, "row_id": orphaned_observation_id},
        )
    assert orphaned_search_row_count == 0
    assert any("1 stale search rows purged" in line for line in printed_lines)


@pytest.mark.asyncio
async def test_reindex_search_wires_stale_row_purge(
    monkeypatch,
    session_maker,
    app_config,
    test_project,
):
    """The search-only CLI path calls the lightweight repository reconciliation seam."""
    printed_lines: list[str] = []

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return [test_project]

    project_index = AsyncMock(
        return_value=SimpleNamespace(
            total_files=0,
            enqueued_files=0,
            enqueued_batches=0,
            deleted_files=0,
        )
    )
    purge_stale_rows = AsyncMock(return_value=3)
    monkeypatch.setattr(
        "basic_memory.services.initialization.reconcile_projects_with_config", AsyncMock()
    )
    monkeypatch.setattr(
        "basic_memory.services.initialization.recover_project_materializations", AsyncMock()
    )
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db",
        AsyncMock(return_value=(None, session_maker)),
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.index.local_project.run_local_project_index_for_project",
        project_index,
    )
    monkeypatch.setattr(
        "basic_memory.repository.search_repository_base.purge_stale_search_index_rows",
        purge_stale_rows,
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
        full=False,
        project=test_project.name,
    )

    purge_stale_rows.assert_awaited_once_with(session_maker, test_project.id)
    assert any("3 stale search rows purged" in line for line in printed_lines)


@pytest.mark.asyncio
async def test_reindex_full_does_not_double_embed(monkeypatch, session_maker):
    """A full reindex (search + embeddings) must embed once: the FTS rebuild runs
    with embeddings=False so only the explicit vector phase calls the provider."""
    app_config = _stub_app_config()
    project = SimpleNamespace(id=1, name="foo", permalink="foo", path="/tmp/foo")
    vector_reindex_calls: list[dict[str, object]] = []
    project_index = AsyncMock(
        return_value=SimpleNamespace(
            total_files=1, enqueued_files=1, enqueued_batches=1, deleted_files=0
        )
    )

    class StubProjectRepository:
        async def get_active_projects(self, session):
            return [project]

    class StubSearchService:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def reindex_vectors(
            self, *, progress_callback=None, force_full: bool = False, started_callback=None
        ):
            vector_reindex_calls.append({"force_full": force_full})
            return _vector_stats(total_entities=1, embedded=1, skipped=0, errors=0)

    class SilentProgress:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> bool:
            return False

        def add_task(self, *args, **kwargs) -> int:
            return 1

        def update(self, *args, **kwargs) -> None:
            pass

    monkeypatch.setattr(
        "basic_memory.services.initialization.reconcile_projects_with_config", AsyncMock()
    )
    monkeypatch.setattr(
        "basic_memory.db.get_or_create_db", AsyncMock(return_value=(None, session_maker))
    )
    monkeypatch.setattr("basic_memory.db.shutdown_db", AsyncMock())
    monkeypatch.setattr("basic_memory.repository.ProjectRepository", StubProjectRepository)
    monkeypatch.setattr(
        "basic_memory.index.local_project.run_local_project_index_for_project", project_index
    )
    monkeypatch.setattr(
        "basic_memory.repository.search_repository.create_search_repository",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("basic_memory.repository.EntityRepository", lambda *a, **k: object())
    monkeypatch.setattr(
        "basic_memory.markdown.entity_parser.EntityParser", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        "basic_memory.markdown.markdown_processor.MarkdownProcessor", lambda *a, **k: object()
    )
    monkeypatch.setattr("basic_memory.services.file_service.FileService", lambda *a, **k: object())
    monkeypatch.setattr("basic_memory.services.search_service.SearchService", StubSearchService)
    monkeypatch.setattr(db_cmd, "Progress", SilentProgress)
    monkeypatch.setattr(db_cmd.console, "print", lambda message="", *a, **k: None)

    await db_cmd._reindex(app_config, search=True, embeddings=True, full=True, project="foo")

    # FTS rebuild ran without embeddings; only the explicit phase embedded (once).
    project_index.assert_awaited_once()
    index_call = project_index.await_args
    assert index_call is not None
    assert index_call.kwargs["embeddings"] is False
    assert len(vector_reindex_calls) == 1
    assert vector_reindex_calls[0]["force_full"] is True


def test_reindex_total_embedding_failure_surfaces_error_and_exits_one(
    monkeypatch,
    session_maker,
):
    representative_error = "first line\nsecond line " + ("x" * 300)
    stats = _vector_stats(
        total_entities=3,
        embedded=0,
        skipped=0,
        errors=3,
        sample_errors=(representative_error,),
    )
    _app_config, project_index, printed_lines = _configure_embedding_runtime(
        monkeypatch,
        session_maker,
        stats,
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 1
    project_index.assert_awaited_once()
    output = "\n".join(printed_lines)
    assert (
        "Embeddings incomplete[/yellow] ([cyan]index=milvus[/cyan], "
        "[cyan]model=FastEmbedEmbeddingProvider:BAAI/bge-small-en-v1.5[/cyan]): "
        "0 entities embedded, 0 skipped, 3 errors"
    ) in output
    representative_line = next(line for line in printed_lines if "Representative error:" in line)
    assert "first line second line" in representative_line
    assert representative_line.endswith("...")
    assert "Reindex failed: all vector embedding attempts failed." in output
    assert "Reindex complete!" not in output


def test_reindex_partial_embedding_failure_surfaces_error_and_exits_one(
    monkeypatch,
    session_maker,
):
    stats = _vector_stats(
        total_entities=3,
        embedded=2,
        skipped=0,
        errors=1,
        sample_errors=("Milvus is unavailable",),
    )
    _app_config, _project_index, printed_lines = _configure_embedding_runtime(
        monkeypatch,
        session_maker,
        stats,
    )

    result = runner.invoke(app, ["reindex", "--embeddings"])

    assert result.exit_code == 1
    output = "\n".join(printed_lines)
    assert "2 entities embedded, 0 skipped, 1 errors" in output
    assert "Representative error:[/yellow] Milvus is unavailable" in output
    assert "Embeddings incomplete" in output
    assert "Reindex incomplete:" in output
    assert "Embeddings complete" not in output
    assert "Reindex complete!" not in output


def test_reindex_embedding_success_reports_index_and_model_and_exits_zero(
    monkeypatch,
    session_maker,
):
    stats = _vector_stats(total_entities=2, embedded=2, skipped=0, errors=0)
    _app_config, _project_index, printed_lines = _configure_embedding_runtime(
        monkeypatch,
        session_maker,
        stats,
    )

    result = runner.invoke(app, ["reindex", "--embeddings"])

    assert result.exit_code == 0
    output = "\n".join(printed_lines)
    assert (
        "Embeddings complete ([cyan]index=milvus[/cyan], "
        "[cyan]model=FastEmbedEmbeddingProvider:BAAI/bge-small-en-v1.5[/cyan]): "
        "2 entities embedded, 0 skipped, 0 errors"
    ) in output
    assert "Representative error:" not in output
    assert "Reindex complete!" in output


# --- `bm project add` indexing (#1635) ---


@pytest.mark.asyncio
async def test_project_add_indexing_runs_the_reindex_pass_then_reports_readiness(monkeypatch):
    """`project add` reuses the reindex pass, so its embedding phase shows the progress bar.

    The old path made one foreground API request that embedded inline and printed
    nothing until it returned. Semantic search is always on (#1696), so the add
    always runs the embedding pass.
    """
    app_config = _stub_app_config()
    monkeypatch.setattr(db_cmd, "ConfigManager", lambda: SimpleNamespace(config=app_config))
    steps: list[tuple[str, object]] = []

    async def fake_reindex(config, **kwargs):
        assert config is app_config
        steps.append(("reindex", kwargs))

    async def fake_report(project: str) -> None:
        steps.append(("readiness", project))

    monkeypatch.setattr(db_cmd, "_reindex", fake_reindex)
    monkeypatch.setattr(db_cmd, "report_project_readiness", fake_report)

    await db_cmd.index_project_and_report_readiness("research")

    assert steps == [
        (
            "reindex",
            {
                "search": True,
                "embeddings": True,
                "full": False,
                "project": "research",
            },
        ),
        ("readiness", "research"),
    ]


@pytest.mark.asyncio
async def test_reindex_matches_the_project_by_permalink(monkeypatch, session_maker):
    """`new_default` names the project reconciliation stored as `new-default`.

    Exact name equality missed it, so `bm project add new_default` reported its
    own index pass as "Project 'new_default' not found".
    """
    stats = _vector_stats(total_entities=1, embedded=1, skipped=0, errors=0)
    _, project_index, _ = _configure_embedding_runtime(monkeypatch, session_maker, stats)
    stored = SimpleNamespace(id=1, name="new-default", permalink="new-default", path="/tmp/n")

    class NormalizedProjectRepository:
        async def get_active_projects(self, session):
            return [stored]

    monkeypatch.setattr("basic_memory.repository.ProjectRepository", NormalizedProjectRepository)

    await db_cmd._reindex(
        _stub_app_config(),
        search=True,
        embeddings=False,
        full=False,
        project="new_default",
    )

    project_index.assert_awaited_once()
    [indexed_call] = project_index.await_args_list
    assert indexed_call.args[0] is stored


@pytest.mark.parametrize(
    "args",
    [["reindex", "--embeddings"], ["reindex"], ["project", "index", "foo"]],
)
@pytest.mark.parametrize("errors,deferred", [(0, 2), (1, 1)])
def test_reindex_incomplete_embeddings_never_report_full_progress(
    monkeypatch, session_maker, args: list[str], errors: int, deferred: int
) -> None:
    stats = _vector_stats(total_entities=3, embedded=1, skipped=0, errors=errors, deferred=deferred)
    _, _, printed_lines = _configure_embedding_runtime(monkeypatch, session_maker, stats)
    progress_factory = MagicMock()
    progress = progress_factory.return_value.__enter__.return_value
    monkeypatch.setattr(db_cmd, "Progress", progress_factory)

    result = runner.invoke(app, args)

    assert result.exit_code == 1, result.output
    output = "\n".join(printed_lines)
    assert "Embeddings incomplete" in output
    assert f"{errors} errors, {deferred} deferred" in output
    assert "Reindex incomplete:" in output
    assert "Embeddings complete" not in output
    assert "Reindex complete!" not in output
    progress.update.assert_called_once_with(progress.add_task.return_value, total=3, completed=1)


def test_reindex_finishes_other_projects_before_reporting_incomplete(
    monkeypatch, session_maker
) -> None:
    incomplete = _vector_stats(total_entities=2, embedded=1, skipped=0, errors=0, deferred=1)
    complete = _vector_stats(total_entities=1, embedded=1, skipped=0, errors=0)
    _, project_index, printed_lines = _configure_embedding_runtime(
        monkeypatch, session_maker, incomplete
    )
    projects = [
        SimpleNamespace(id=1, name="foo", permalink="foo", path="/tmp/foo"),
        SimpleNamespace(id=2, name="bar", permalink="bar", path="/tmp/bar"),
    ]
    monkeypatch.setattr(
        "basic_memory.repository.ProjectRepository",
        lambda: SimpleNamespace(get_active_projects=AsyncMock(return_value=projects)),
    )
    reindex_vectors = AsyncMock(side_effect=[incomplete, complete])
    monkeypatch.setattr(
        "basic_memory.services.search_service.SearchService",
        lambda *args, **kwargs: SimpleNamespace(reindex_vectors=reindex_vectors),
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 1, result.output
    assert project_index.await_count == 2
    assert reindex_vectors.await_count == 2
    output = "\n".join(printed_lines)
    assert output.count("Embeddings incomplete") == 1
    assert output.count("Embeddings complete") == 1
    assert "Reindex incomplete: 0 embedding errors, 1 entities deferred." in output
    assert "Reindex complete!" not in output


def test_reindex_unavailable_vectors_reports_skips_without_crashing(
    monkeypatch, session_maker
) -> None:
    stats = _vector_stats(total_entities=2, embedded=0, skipped=2, errors=0)
    stats.update(vector_index=None, embedding_model=None)
    _, _, printed_lines = _configure_embedding_runtime(monkeypatch, session_maker, stats)

    result = runner.invoke(app, ["reindex", "--embeddings"])

    assert result.exit_code == 0, result.output
    output = "\n".join(printed_lines)
    assert "0 entities embedded, 2 skipped, 0 errors, 0 deferred" in output
    assert "index=unavailable" in output
    assert "Reindex complete!" in output


@pytest.mark.asyncio
@pytest.mark.parametrize("full", [False, True])
async def test_reindex_renders_percentages_before_vector_reindex_returns(
    monkeypatch: pytest.MonkeyPatch, session_maker, full: bool
) -> None:
    """Render real Rich frames while the service is still doing embedding work."""
    stats = _vector_stats(total_entities=2, embedded=2, skipped=0, errors=0)
    app_config, _, _ = _configure_embedding_runtime(monkeypatch, session_maker, stats)
    monkeypatch.setenv("TERM", "xterm")
    output = StringIO()
    console = Console(file=output, force_terminal=True, color_system=None, width=100)
    monkeypatch.setattr(db_cmd, "console", console)
    # No timer can rescue missing callback refreshes in this test.
    monkeypatch.setattr(
        db_cmd, "Progress", lambda *args, **kwargs: Progress(*args, auto_refresh=False, **kwargs)
    )

    async def reindex_vectors(*, progress_callback, force_full, started_callback):
        assert force_full is full
        started_callback(2)
        assert "  0%" in output.getvalue()
        assert "100%" not in output.getvalue()
        output.seek(0)
        output.truncate()
        progress_callback(10, 1, 2)
        assert " 50%" in output.getvalue()
        assert "100%" not in output.getvalue()
        progress_callback(20, 2, 2)
        return stats

    monkeypatch.setattr(
        "basic_memory.services.search_service.SearchService",
        lambda *args, **kwargs: SimpleNamespace(reindex_vectors=reindex_vectors),
    )
    await db_cmd._reindex(app_config, search=False, embeddings=True, full=full, project="foo")
    assert "100%" in output.getvalue()
