"""Tests for ProjectResolver - unified project resolution logic."""

import os
import pytest
from basic_memory.project_resolver import (
    ProjectResolver,
    ResolvedProject,
    ResolutionMode,
)


class TestProjectResolver:
    """Test ProjectResolver class."""

    def test_cloud_mode_requires_project(self):
        """In cloud mode, project is required."""
        resolver = ProjectResolver(cloud_mode=True)
        with pytest.raises(ValueError, match="Project is required for cloud mode"):
            resolver.resolve(project=None)

    def test_cloud_mode_with_explicit_project(self):
        """In cloud mode, explicit project is accepted."""
        resolver = ProjectResolver(cloud_mode=True)
        result = resolver.resolve(project="my-project")

        assert result.project == "my-project"
        assert result.mode == ResolutionMode.CLOUD_EXPLICIT
        assert result.is_resolved is True
        assert result.is_discovery_mode is False

    def test_cloud_mode_discovery_allowed(self):
        """In cloud mode with allow_discovery, None is acceptable."""
        resolver = ProjectResolver(cloud_mode=True)
        result = resolver.resolve(project=None, allow_discovery=True)

        assert result.project is None
        assert result.mode == ResolutionMode.CLOUD_DISCOVERY
        assert result.is_resolved is False
        assert result.is_discovery_mode is True

    def test_local_mode_env_constraint_priority(self, monkeypatch):
        """Env constraint has highest priority in local mode."""
        monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "constrained-project")
        resolver = ProjectResolver.from_env(
            cloud_mode=False,
            default_project_mode=True,
            default_project="default-project",
        )

        # Even with explicit project and default, env constraint wins
        result = resolver.resolve(project="explicit-project")

        assert result.project == "constrained-project"
        assert result.mode == ResolutionMode.ENV_CONSTRAINT
        assert result.is_resolved is True

    def test_local_mode_explicit_project(self):
        """Explicit project parameter has second priority."""
        resolver = ProjectResolver(
            cloud_mode=False,
            default_project_mode=True,
            default_project="default-project",
        )

        result = resolver.resolve(project="explicit-project")

        assert result.project == "explicit-project"
        assert result.mode == ResolutionMode.EXPLICIT

    def test_local_mode_default_project(self):
        """Default project is used when default_project_mode is true."""
        resolver = ProjectResolver(
            cloud_mode=False,
            default_project_mode=True,
            default_project="my-default",
        )

        result = resolver.resolve(project=None)

        assert result.project == "my-default"
        assert result.mode == ResolutionMode.DEFAULT

    def test_local_mode_no_default_when_mode_disabled(self):
        """Default project is NOT used when default_project_mode is false."""
        resolver = ProjectResolver(
            cloud_mode=False,
            default_project_mode=False,
            default_project="my-default",
        )

        result = resolver.resolve(project=None)

        assert result.project is None
        assert result.mode == ResolutionMode.NONE
        assert result.is_resolved is False

    def test_local_mode_no_resolution_possible(self):
        """When nothing is configured, resolution returns None."""
        resolver = ProjectResolver(cloud_mode=False)
        result = resolver.resolve(project=None)

        assert result.project is None
        assert result.mode == ResolutionMode.NONE
        assert "default_project_mode is disabled" in result.reason

    def test_require_project_success(self):
        """require_project returns result when project resolved."""
        resolver = ProjectResolver(
            cloud_mode=False,
            default_project_mode=True,
            default_project="required-project",
        )

        result = resolver.require_project()

        assert result.project == "required-project"
        assert result.is_resolved is True

    def test_require_project_raises_on_failure(self):
        """require_project raises ValueError when not resolved."""
        resolver = ProjectResolver(cloud_mode=False, default_project_mode=False)

        with pytest.raises(ValueError, match="No project specified"):
            resolver.require_project()

    def test_require_project_custom_error_message(self):
        """require_project uses custom error message."""
        resolver = ProjectResolver(cloud_mode=False, default_project_mode=False)

        with pytest.raises(ValueError, match="Custom error message"):
            resolver.require_project(error_message="Custom error message")

    def test_from_env_without_env_var(self, monkeypatch):
        """from_env without BASIC_MEMORY_MCP_PROJECT set."""
        monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
        resolver = ProjectResolver.from_env(
            cloud_mode=False,
            default_project_mode=True,
            default_project="test",
        )

        assert resolver.constrained_project is None
        result = resolver.resolve(project="explicit")
        assert result.mode == ResolutionMode.EXPLICIT

    def test_from_env_with_env_var(self, monkeypatch):
        """from_env with BASIC_MEMORY_MCP_PROJECT set."""
        monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "env-project")
        resolver = ProjectResolver.from_env()

        assert resolver.constrained_project == "env-project"


class TestResolvedProject:
    """Test ResolvedProject dataclass."""

    def test_is_resolved_true(self):
        """is_resolved returns True when project is set."""
        result = ResolvedProject(
            project="test",
            mode=ResolutionMode.EXPLICIT,
            reason="test",
        )
        assert result.is_resolved is True

    def test_is_resolved_false(self):
        """is_resolved returns False when project is None."""
        result = ResolvedProject(
            project=None,
            mode=ResolutionMode.NONE,
            reason="test",
        )
        assert result.is_resolved is False

    def test_is_discovery_mode_cloud(self):
        """is_discovery_mode is True for CLOUD_DISCOVERY."""
        result = ResolvedProject(
            project=None,
            mode=ResolutionMode.CLOUD_DISCOVERY,
            reason="test",
        )
        assert result.is_discovery_mode is True

    def test_is_discovery_mode_none(self):
        """is_discovery_mode is True for NONE with no project."""
        result = ResolvedProject(
            project=None,
            mode=ResolutionMode.NONE,
            reason="test",
        )
        assert result.is_discovery_mode is True

    def test_is_discovery_mode_false(self):
        """is_discovery_mode is False when project is resolved."""
        result = ResolvedProject(
            project="test",
            mode=ResolutionMode.EXPLICIT,
            reason="test",
        )
        assert result.is_discovery_mode is False

    def test_frozen_dataclass(self):
        """ResolvedProject is immutable."""
        result = ResolvedProject(
            project="test",
            mode=ResolutionMode.EXPLICIT,
            reason="test",
        )
        with pytest.raises(AttributeError):
            result.project = "changed"  # type: ignore
