"""Tests for project context utilities."""

import os
from unittest.mock import patch, MagicMock

import pytest


class TestResolveProjectParameter:
    """Tests for resolve_project_parameter function."""

    @pytest.mark.asyncio
    async def test_cloud_mode_requires_project_by_default(self):
        """In cloud mode, project is required when allow_discovery=False."""
        from basic_memory.mcp.project_context import resolve_project_parameter

        mock_config = MagicMock()
        mock_config.cloud_mode = True

        with patch("basic_memory.mcp.project_context.ConfigManager") as mock_config_manager:
            mock_config_manager.return_value.config = mock_config

            with pytest.raises(ValueError) as exc_info:
                await resolve_project_parameter(project=None, allow_discovery=False)

            assert "No project specified" in str(exc_info.value)
            assert "Project is required for cloud mode" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_cloud_mode_allows_discovery_when_enabled(self):
        """In cloud mode with allow_discovery=True, returns None instead of error."""
        from basic_memory.mcp.project_context import resolve_project_parameter

        mock_config = MagicMock()
        mock_config.cloud_mode = True

        with patch("basic_memory.mcp.project_context.ConfigManager") as mock_config_manager:
            mock_config_manager.return_value.config = mock_config

            result = await resolve_project_parameter(project=None, allow_discovery=True)

            assert result is None

    @pytest.mark.asyncio
    async def test_cloud_mode_returns_project_when_specified(self):
        """In cloud mode, returns the specified project."""
        from basic_memory.mcp.project_context import resolve_project_parameter

        mock_config = MagicMock()
        mock_config.cloud_mode = True

        with patch("basic_memory.mcp.project_context.ConfigManager") as mock_config_manager:
            mock_config_manager.return_value.config = mock_config

            result = await resolve_project_parameter(project="my-project")

            assert result == "my-project"

    @pytest.mark.asyncio
    async def test_local_mode_uses_env_var_priority(self):
        """In local mode, BASIC_MEMORY_MCP_PROJECT env var takes priority."""
        from basic_memory.mcp.project_context import resolve_project_parameter

        mock_config = MagicMock()
        mock_config.cloud_mode = False

        with patch("basic_memory.mcp.project_context.ConfigManager") as mock_config_manager:
            mock_config_manager.return_value.config = mock_config

            with patch.dict(os.environ, {"BASIC_MEMORY_MCP_PROJECT": "env-project"}):
                result = await resolve_project_parameter(project="explicit-project")

            # Env var should take priority over explicit project
            assert result == "env-project"

    @pytest.mark.asyncio
    async def test_local_mode_uses_explicit_project(self):
        """In local mode without env var, uses explicit project parameter."""
        from basic_memory.mcp.project_context import resolve_project_parameter

        mock_config = MagicMock()
        mock_config.cloud_mode = False
        mock_config.default_project_mode = False

        with patch("basic_memory.mcp.project_context.ConfigManager") as mock_config_manager:
            mock_config_manager.return_value.config = mock_config

            with patch.dict(os.environ, {}, clear=True):
                # Remove the env var if it exists
                os.environ.pop("BASIC_MEMORY_MCP_PROJECT", None)
                result = await resolve_project_parameter(project="explicit-project")

            assert result == "explicit-project"

    @pytest.mark.asyncio
    async def test_local_mode_uses_default_project(self):
        """In local mode with default_project_mode, uses default project."""
        from basic_memory.mcp.project_context import resolve_project_parameter

        mock_config = MagicMock()
        mock_config.cloud_mode = False
        mock_config.default_project_mode = True
        mock_config.default_project = "default-project"

        with patch("basic_memory.mcp.project_context.ConfigManager") as mock_config_manager:
            mock_config_manager.return_value.config = mock_config

            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("BASIC_MEMORY_MCP_PROJECT", None)
                result = await resolve_project_parameter(project=None)

            assert result == "default-project"

    @pytest.mark.asyncio
    async def test_local_mode_returns_none_when_no_resolution(self):
        """In local mode without any project source, returns None."""
        from basic_memory.mcp.project_context import resolve_project_parameter

        mock_config = MagicMock()
        mock_config.cloud_mode = False
        mock_config.default_project_mode = False

        with patch("basic_memory.mcp.project_context.ConfigManager") as mock_config_manager:
            mock_config_manager.return_value.config = mock_config

            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("BASIC_MEMORY_MCP_PROJECT", None)
                result = await resolve_project_parameter(project=None)

            assert result is None
