"""The once-per-process project reconciliation used by the CLI's API path (#1334)."""

import pytest

from basic_memory.services import initialization


@pytest.mark.asyncio
async def test_reconcile_projects_with_config_once_runs_once_per_database(app_config, monkeypatch):
    """Every local ASGI request hits the dependency; only the first may reconcile."""
    calls = []

    async def fake_reconcile(config):
        calls.append(config)

    monkeypatch.setattr(initialization, "reconcile_projects_with_config", fake_reconcile)
    monkeypatch.setattr(initialization, "_reconciled_database_paths", set())

    await initialization.reconcile_projects_with_config_once(app_config)
    await initialization.reconcile_projects_with_config_once(app_config)

    assert calls == [app_config]


@pytest.mark.asyncio
async def test_reconcile_projects_with_config_once_skips_cloud_deployments(app_config, monkeypatch):
    """Cloud/stateless deployments own their project rows; reconciling would delete them."""
    calls = []

    async def fake_reconcile(config):
        calls.append(config)

    monkeypatch.setattr(initialization, "reconcile_projects_with_config", fake_reconcile)
    monkeypatch.setattr(initialization, "_reconciled_database_paths", set())
    monkeypatch.setenv("BASIC_MEMORY_CLOUD_MODE", "true")
    assert app_config.skip_local_initialization

    await initialization.reconcile_projects_with_config_once(app_config)

    assert calls == []
