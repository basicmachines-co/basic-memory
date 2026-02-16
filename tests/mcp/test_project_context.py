"""Tests for project context utilities (no standard-library mock usage).

These functions are config/env driven, so we use the real ConfigManager-backed
test config file and pytest monkeypatch for environment variables.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_cloud_mode_requires_project_when_no_default(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.cloud_mode = True
    # Clear default_project to test the "no default available" path
    cfg.default_project = None
    config_manager.save_config(cfg)

    with pytest.raises(ValueError) as exc_info:
        await resolve_project_parameter(project=None, allow_discovery=False)

    assert "No project specified" in str(exc_info.value)
    assert "Project is required" in str(exc_info.value)


@pytest.mark.asyncio
async def test_cloud_mode_allows_discovery_when_enabled(config_manager):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.cloud_mode = True
    # Clear default_project so discovery fallback is reached
    cfg.default_project = None
    config_manager.save_config(cfg)

    assert await resolve_project_parameter(project=None, allow_discovery=True) is None


@pytest.mark.asyncio
async def test_cloud_mode_returns_project_when_specified(config_manager):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.cloud_mode = True
    config_manager.save_config(cfg)

    assert await resolve_project_parameter(project="my-project") == "my-project"


@pytest.mark.asyncio
async def test_local_mode_uses_env_var_priority(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "env-project")
    assert await resolve_project_parameter(project="explicit-project") == "env-project"


@pytest.mark.asyncio
async def test_local_mode_uses_explicit_project(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project="explicit-project") == "explicit-project"


@pytest.mark.asyncio
async def test_local_mode_uses_default_project(config_manager, config_home, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    from basic_memory.config import ProjectEntry

    (config_home / "default-project").mkdir(parents=True, exist_ok=True)
    cfg.projects["default-project"] = ProjectEntry(path=str(config_home / "default-project"))
    cfg.default_project = "default-project"
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project=None) == "default-project"


@pytest.mark.asyncio
async def test_local_mode_returns_none_when_no_default(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.cloud_mode = False
    cfg.default_project = None
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project=None) is None


@pytest.mark.asyncio
async def test_cloud_mode_uses_default_project(config_manager, config_home, monkeypatch):
    """In cloud mode with default_project set, default project is resolved."""
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    cfg.cloud_mode = True
    from basic_memory.config import ProjectEntry

    (config_home / "cloud-default").mkdir(parents=True, exist_ok=True)
    cfg.projects["cloud-default"] = ProjectEntry(path=str(config_home / "cloud-default"))
    cfg.default_project = "cloud-default"
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project=None) == "cloud-default"
