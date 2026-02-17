"""Tests for ProjectResolver - unified project resolution logic."""

import pytest

from basic_memory.project_resolver import (
    ProjectResolver,
    ResolvedProject,
    ResolutionMode,
)


class TestProjectResolver:
    """Test ProjectResolver class."""

    def test_env_constraint_has_highest_priority(self, monkeypatch):
        """Environment constraint should win over explicit/default."""
        monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "constrained-project")
        resolver = ProjectResolver.from_env(default_project="default-project")

        result = resolver.resolve(project="explicit-project")

        assert result.project == "constrained-project"
        assert result.mode == ResolutionMode.ENV_CONSTRAINT
        assert result.is_resolved is True

    def test_explicit_project_has_second_priority(self):
        """Explicit project parameter should override default."""
        resolver = ProjectResolver(default_project="default-project")

        result = resolver.resolve(project="explicit-project")

        assert result.project == "explicit-project"
        assert result.mode == ResolutionMode.EXPLICIT

    def test_default_project_is_used_as_fallback(self):
        """Default project should be used when explicit is missing."""
        resolver = ProjectResolver(default_project="my-default")

        result = resolver.resolve(project=None)

        assert result.project == "my-default"
        assert result.mode == ResolutionMode.DEFAULT

    def test_no_resolution_when_no_default_and_no_discovery(self):
        """Without explicit/default/discovery, resolution should return NONE."""
        resolver = ProjectResolver(default_project=None)

        result = resolver.resolve(project=None)

        assert result.project is None
        assert result.mode == ResolutionMode.NONE
        assert result.is_resolved is False

    def test_discovery_resolution_when_allowed(self):
        """Discovery mode should return DISCOVERY when allowed."""
        resolver = ProjectResolver(default_project=None)

        result = resolver.resolve(project=None, allow_discovery=True)

        assert result.project is None
        assert result.mode == ResolutionMode.DISCOVERY
        assert result.is_discovery_mode is True

    def test_require_project_success(self):
        """require_project returns result when project resolves."""
        resolver = ProjectResolver(default_project="required-project")

        result = resolver.require_project()

        assert result.project == "required-project"
        assert result.is_resolved is True

    def test_require_project_raises_on_failure(self):
        """require_project raises ValueError when project cannot resolve."""
        resolver = ProjectResolver(default_project=None)

        with pytest.raises(ValueError, match="No project specified"):
            resolver.require_project()

    def test_require_project_custom_error_message(self):
        """require_project uses custom error message."""
        resolver = ProjectResolver(default_project=None)

        with pytest.raises(ValueError, match="Custom error message"):
            resolver.require_project(error_message="Custom error message")

    def test_from_env_without_env_var(self, monkeypatch):
        """from_env without BASIC_MEMORY_MCP_PROJECT set."""
        monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
        resolver = ProjectResolver.from_env(default_project="test")

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

    def test_is_discovery_mode_discovery(self):
        """is_discovery_mode is True for DISCOVERY."""
        result = ResolvedProject(
            project=None,
            mode=ResolutionMode.DISCOVERY,
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
