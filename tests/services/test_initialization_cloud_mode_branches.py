import pytest

from basic_memory.config import DatabaseBackend
from basic_memory.services import initialization
from basic_memory.services.initialization import (
    ensure_initialization,
    initialize_app,
    initialize_file_sync,
)


@pytest.mark.asyncio
async def test_initialize_app_noop_for_stateless_cloud(app_config):
    # Stateless/cloud deployments (skip_initialization_sync) manage their own
    # schema + per-tenant projects from the DB, so local init is a no-op.
    app_config.database_backend = DatabaseBackend.POSTGRES
    app_config.skip_initialization_sync = True
    await initialize_app(app_config)


def test_ensure_initialization_noop_for_stateless_cloud(app_config):
    app_config.database_backend = DatabaseBackend.POSTGRES
    app_config.skip_initialization_sync = True
    ensure_initialization(app_config)


@pytest.mark.asyncio
async def test_initialize_app_runs_for_local_postgres(app_config, monkeypatch):
    """A LOCAL Postgres backend (not stateless) still initializes — migrate +
    reconcile — so the seeded default project gets a row in the projects table.

    Gating the skip on the Postgres backend (instead of skip_initialization_sync)
    left the seeded default in config only, so /v2/projects/resolve rejected it.
    """
    app_config.database_backend = DatabaseBackend.POSTGRES
    app_config.skip_initialization_sync = False

    calls: list[str] = []

    async def fake_initialize_database(cfg):
        calls.append("initialize_database")

    async def fake_reconcile(cfg):
        calls.append("reconcile_projects_with_config")

    monkeypatch.setattr(initialization, "initialize_database", fake_initialize_database)
    monkeypatch.setattr(initialization, "reconcile_projects_with_config", fake_reconcile)

    await initialize_app(app_config)

    assert calls == ["initialize_database", "reconcile_projects_with_config"]


@pytest.mark.asyncio
async def test_initialize_file_sync_skips_in_test_env(app_config):
    # app_config fixture uses env="test"
    assert app_config.is_test_env is True
    await initialize_file_sync(app_config)
