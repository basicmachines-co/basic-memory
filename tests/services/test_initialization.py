"""Integration-style tests for the initialization service.

Goal: avoid brittle deep mocking; assert real behavior using the existing
test config + dual-backend fixtures.
"""

from __future__ import annotations

import pytest

from basic_memory import db
from basic_memory.config import BasicMemoryConfig, DatabaseBackend
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.services.initialization import (
    ensure_initialization,
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

        updated = app_config.model_copy(
            update={
                "projects": {"proj-a": str(proj_a), "proj-b": str(proj_b)},
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
