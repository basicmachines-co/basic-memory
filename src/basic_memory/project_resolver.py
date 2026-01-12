"""Unified project resolution across MCP, API, and CLI.

This module provides a single canonical implementation of project resolution
logic, eliminating duplicated decision trees across the codebase.

The resolution follows a three-tier hierarchy:
1. Constrained mode: BASIC_MEMORY_MCP_PROJECT env var (highest priority)
2. Explicit parameter: Project passed directly to operation
3. Default project: Used when default_project_mode=true (lowest priority)

In cloud mode, project is required unless discovery mode is explicitly allowed.
"""

import os
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from loguru import logger


class ResolutionMode(Enum):
    """How the project was resolved."""

    CLOUD_EXPLICIT = auto()  # Explicit project in cloud mode
    CLOUD_DISCOVERY = auto()  # Discovery mode allowed in cloud (no project)
    ENV_CONSTRAINT = auto()  # BASIC_MEMORY_MCP_PROJECT env var
    EXPLICIT = auto()  # Explicit project parameter
    DEFAULT = auto()  # default_project with default_project_mode=true
    NONE = auto()  # No resolution possible


@dataclass(frozen=True)
class ResolvedProject:
    """Result of project resolution.

    Attributes:
        project: The resolved project name, or None if not resolved
        mode: How the project was resolved
        reason: Human-readable explanation of resolution
    """

    project: Optional[str]
    mode: ResolutionMode
    reason: str

    @property
    def is_resolved(self) -> bool:
        """Whether a project was successfully resolved."""
        return self.project is not None

    @property
    def is_discovery_mode(self) -> bool:
        """Whether we're in discovery mode (no specific project)."""
        return self.mode == ResolutionMode.CLOUD_DISCOVERY or (
            self.mode == ResolutionMode.NONE and self.project is None
        )


@dataclass
class ProjectResolver:
    """Unified project resolution logic.

    Resolves the effective project given requested project, environment
    constraints, and configuration settings.

    This is the single canonical implementation of project resolution,
    used by MCP tools, API routes, and CLI commands.

    Args:
        cloud_mode: Whether running in cloud mode (project required)
        default_project_mode: Whether to use default project when not specified
        default_project: The default project name
        constrained_project: Optional env-constrained project override
            (typically from BASIC_MEMORY_MCP_PROJECT)
    """

    cloud_mode: bool = False
    default_project_mode: bool = False
    default_project: Optional[str] = None
    constrained_project: Optional[str] = None

    @classmethod
    def from_env(
        cls,
        cloud_mode: bool = False,
        default_project_mode: bool = False,
        default_project: Optional[str] = None,
    ) -> "ProjectResolver":
        """Create resolver with constrained_project from environment.

        Args:
            cloud_mode: Whether running in cloud mode
            default_project_mode: Whether to use default project when not specified
            default_project: The default project name

        Returns:
            ProjectResolver configured with current environment
        """
        constrained = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
        return cls(
            cloud_mode=cloud_mode,
            default_project_mode=default_project_mode,
            default_project=default_project,
            constrained_project=constrained,
        )

    def resolve(
        self,
        project: Optional[str] = None,
        allow_discovery: bool = False,
    ) -> ResolvedProject:
        """Resolve project using the three-tier hierarchy.

        Resolution order:
        1. Cloud mode check (project required unless discovery allowed)
        2. Constrained project from env var (highest priority in local mode)
        3. Explicit project parameter
        4. Default project if default_project_mode=true

        Args:
            project: Optional explicit project parameter
            allow_discovery: If True, allows returning None in cloud mode
                for discovery operations (e.g., recent_activity across projects)

        Returns:
            ResolvedProject with project name, resolution mode, and reason

        Raises:
            ValueError: If in cloud mode and no project specified (unless discovery allowed)
        """
        # --- Cloud Mode Handling ---
        # In cloud mode, project is required unless discovery is explicitly allowed
        if self.cloud_mode:
            if project:
                logger.debug(f"Cloud mode: using explicit project '{project}'")
                return ResolvedProject(
                    project=project,
                    mode=ResolutionMode.CLOUD_EXPLICIT,
                    reason=f"Explicit project in cloud mode: {project}",
                )
            elif allow_discovery:
                logger.debug("Cloud mode: discovery mode allowed, no project required")
                return ResolvedProject(
                    project=None,
                    mode=ResolutionMode.CLOUD_DISCOVERY,
                    reason="Discovery mode enabled in cloud",
                )
            else:
                raise ValueError("No project specified. Project is required for cloud mode.")

        # --- Local Mode: Three-Tier Hierarchy ---

        # Priority 1: CLI constraint overrides everything
        if self.constrained_project:
            logger.debug(f"Using CLI constrained project: {self.constrained_project}")
            return ResolvedProject(
                project=self.constrained_project,
                mode=ResolutionMode.ENV_CONSTRAINT,
                reason=f"Environment constraint: BASIC_MEMORY_MCP_PROJECT={self.constrained_project}",
            )

        # Priority 2: Explicit project parameter
        if project:
            logger.debug(f"Using explicit project parameter: {project}")
            return ResolvedProject(
                project=project,
                mode=ResolutionMode.EXPLICIT,
                reason=f"Explicit parameter: {project}",
            )

        # Priority 3: Default project mode
        if self.default_project_mode and self.default_project:
            logger.debug(f"Using default project from config: {self.default_project}")
            return ResolvedProject(
                project=self.default_project,
                mode=ResolutionMode.DEFAULT,
                reason=f"Default project mode: {self.default_project}",
            )

        # No resolution possible
        logger.debug("No project resolution possible")
        return ResolvedProject(
            project=None,
            mode=ResolutionMode.NONE,
            reason="No project specified and default_project_mode is disabled",
        )

    def require_project(
        self,
        project: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> ResolvedProject:
        """Resolve project, raising an error if not resolved.

        Convenience method for operations that require a project.

        Args:
            project: Optional explicit project parameter
            error_message: Custom error message if project not resolved

        Returns:
            ResolvedProject (always with a non-None project)

        Raises:
            ValueError: If project could not be resolved
        """
        result = self.resolve(project, allow_discovery=False)
        if not result.is_resolved:
            msg = error_message or (
                "No project specified. Either set 'default_project_mode=true' in config, "
                "or provide a 'project' argument."
            )
            raise ValueError(msg)
        return result


__all__ = [
    "ProjectResolver",
    "ResolvedProject",
    "ResolutionMode",
]
