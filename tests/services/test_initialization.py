"""Integration-style tests for the initialization service.

Goal: avoid brittle deep mocking; assert real behavior using the existing
test config + dual-backend fixtures.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.services.initialization import (
    ensure_initialization,
    initialize_app,
    initialize_database,
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
        repo = ProjectRepository(session_maker)

        active = await repo.get_active_projects()
        names = {p.name for p in active}
        assert names.issuperset({"proj-a", "proj-b"})

        default = await repo.get_default_project()
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


@pytest.mark.asyncio
async def test_run_migrations_triggers_embedding_backfill_on_new_revision(
    monkeypatch, app_config: BasicMemoryConfig
):
    """When the trigger revision is newly applied, run automatic embedding backfill once."""

    class StubSearchRepository:
        def __init__(self, *args, **kwargs):
            pass

        async def init_search_index(self):
            return None

    original_session_maker = db._session_maker  # pyright: ignore [reportPrivateUsage]
    try:
        session_marker = object()
        db._session_maker = session_marker  # pyright: ignore [reportPrivateUsage]

        monkeypatch.setattr(
            "basic_memory.db.command.upgrade",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr("basic_memory.db.SQLiteSearchRepository", StubSearchRepository)
        monkeypatch.setattr("basic_memory.db.PostgresSearchRepository", StubSearchRepository)

        load_revisions_mock = AsyncMock(
            side_effect=[
                set(),
                {db.SEMANTIC_EMBEDDING_BACKFILL_REVISION},
            ]
        )
        backfill_mock = AsyncMock()
        monkeypatch.setattr("basic_memory.db._load_applied_alembic_revisions", load_revisions_mock)
        monkeypatch.setattr("basic_memory.db._run_semantic_embedding_backfill", backfill_mock)

        await db.run_migrations(app_config)

        assert load_revisions_mock.await_count == 2
        backfill_mock.assert_awaited_once_with(app_config, session_marker)
    finally:
        db._session_maker = original_session_maker  # pyright: ignore [reportPrivateUsage]


@pytest.mark.asyncio
async def test_run_migrations_skips_embedding_backfill_when_revision_already_applied(
    monkeypatch, app_config: BasicMemoryConfig
):
    """If the trigger revision was already present before upgrade, skip backfill."""

    class StubSearchRepository:
        def __init__(self, *args, **kwargs):
            pass

        async def init_search_index(self):
            return None

    original_session_maker = db._session_maker  # pyright: ignore [reportPrivateUsage]
    try:
        session_marker = object()
        db._session_maker = session_marker  # pyright: ignore [reportPrivateUsage]

        monkeypatch.setattr(
            "basic_memory.db.command.upgrade",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr("basic_memory.db.SQLiteSearchRepository", StubSearchRepository)
        monkeypatch.setattr("basic_memory.db.PostgresSearchRepository", StubSearchRepository)

        load_revisions_mock = AsyncMock(
            side_effect=[
                {db.SEMANTIC_EMBEDDING_BACKFILL_REVISION},
                {db.SEMANTIC_EMBEDDING_BACKFILL_REVISION},
            ]
        )
        backfill_mock = AsyncMock()
        monkeypatch.setattr("basic_memory.db._load_applied_alembic_revisions", load_revisions_mock)
        monkeypatch.setattr("basic_memory.db._run_semantic_embedding_backfill", backfill_mock)

        await db.run_migrations(app_config)

        assert load_revisions_mock.await_count == 2
        assert backfill_mock.await_count == 0
    finally:
        db._session_maker = original_session_maker  # pyright: ignore [reportPrivateUsage]


@pytest.mark.asyncio
async def test_semantic_embedding_backfill_syncs_each_entity(
    monkeypatch,
    app_config: BasicMemoryConfig,
    session_maker,
    test_project,
):
    """Automatic backfill should run sync_entity_vectors for every entity in active projects."""
    from basic_memory.repository.entity_repository import EntityRepository

    entity_repository = EntityRepository(session_maker, project_id=test_project.id)
    created_entity_ids: list[int] = []
    for i in range(3):
        entity = await entity_repository.create(
            {
                "title": f"Backfill Entity {i}",
                "note_type": "note",
                "entity_metadata": {},
                "content_type": "text/markdown",
                "file_path": f"test/backfill-{i}.md",
                "permalink": f"test/backfill-{i}",
                "project_id": test_project.id,
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            }
        )
        created_entity_ids.append(entity.id)

    synced_pairs: list[tuple[int, int]] = []

    class StubSearchRepository:
        def __init__(self, _session_maker, project_id: int, app_config=None):
            self.project_id = project_id

        async def sync_entity_vectors(self, entity_id: int) -> None:
            synced_pairs.append((self.project_id, entity_id))

    monkeypatch.setattr("basic_memory.db.SQLiteSearchRepository", StubSearchRepository)
    monkeypatch.setattr("basic_memory.db.PostgresSearchRepository", StubSearchRepository)

    app_config.semantic_search_enabled = True

    await db._run_semantic_embedding_backfill(app_config, session_maker)  # pyright: ignore [reportPrivateUsage]

    expected_pairs = {(test_project.id, entity_id) for entity_id in created_entity_ids}
    assert expected_pairs.issubset(set(synced_pairs))


@pytest.mark.asyncio
async def test_semantic_embedding_backfill_skips_when_semantic_disabled(
    monkeypatch,
    app_config: BasicMemoryConfig,
    session_maker,
):
    """Automatic backfill should no-op when semantic search is disabled."""
    called = False

    class StubSearchRepository:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True

        async def sync_entity_vectors(self, entity_id: int) -> None:  # pragma: no cover
            return None

    monkeypatch.setattr("basic_memory.db.SQLiteSearchRepository", StubSearchRepository)
    monkeypatch.setattr("basic_memory.db.PostgresSearchRepository", StubSearchRepository)

    app_config.semantic_search_enabled = False
    await db._run_semantic_embedding_backfill(app_config, session_maker)  # pyright: ignore [reportPrivateUsage]
    assert called is False
