"""Unified project resolution across MCP, API, and CLI."""

import os
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from loguru import logger


class ResolutionMode(Enum):
    """How the project was resolved."""

    ENV_CONSTRAINT = auto()  # BASIC_MEMORY_MCP_PROJECT env var
    EXPLICIT = auto()  # Explicit project parameter
    DEFAULT = auto()  # default_project from config
    DISCOVERY = auto()  # Discovery mode allowed (no project)
    NONE = auto()  # No resolution possible


@dataclass(frozen=True)
class ResolvedProject:
    """Result of project resolution."""

    project: Optional[str]
    mode: ResolutionMode
    reason: str

    @property
    def is_resolved(self) -> bool:
        return self.project is not None

    @property
    def is_discovery_mode(self) -> bool:
        return self.mode in {ResolutionMode.DISCOVERY, ResolutionMode.NONE} and self.project is None


@dataclass
class ProjectResolver:
    """Unified project resolution logic."""

    default_project: Optional[str] = None
    constrained_project: Optional[str] = None

    @classmethod
    def from_env(
        cls,
        default_project: Optional[str] = None,
    ) -> "ProjectResolver":
        """Create resolver with constrained_project from environment."""
        return cls(
            default_project=default_project,
            constrained_project=os.environ.get("BASIC_MEMORY_MCP_PROJECT"),
        )

    def resolve(
        self,
        project: Optional[str] = None,
        allow_discovery: bool = False,
    ) -> ResolvedProject:
        """Resolve project using a unified linear priority chain."""
        if self.constrained_project:
            logger.debug(f"Using constrained project from env: {self.constrained_project}")
            return ResolvedProject(
                project=self.constrained_project,
                mode=ResolutionMode.ENV_CONSTRAINT,
                reason=f"Environment constraint: BASIC_MEMORY_MCP_PROJECT={self.constrained_project}",
            )

        if project:
            logger.debug(f"Using explicit project parameter: {project}")
            return ResolvedProject(
                project=project,
                mode=ResolutionMode.EXPLICIT,
                reason=f"Explicit parameter: {project}",
            )

        if self.default_project:
            logger.debug(f"Using default project from config: {self.default_project}")
            return ResolvedProject(
                project=self.default_project,
                mode=ResolutionMode.DEFAULT,
                reason=f"Default project: {self.default_project}",
            )

        if allow_discovery:
            logger.debug("No project resolved, using discovery mode")
            return ResolvedProject(
                project=None,
                mode=ResolutionMode.DISCOVERY,
                reason="Discovery mode enabled",
            )

        logger.debug("No project resolution possible")
        return ResolvedProject(
            project=None,
            mode=ResolutionMode.NONE,
            reason="No project specified and no default project configured",
        )

    def require_project(
        self,
        project: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> ResolvedProject:
        """Resolve project, raising an error if not resolved."""
        result = self.resolve(project, allow_discovery=False)
        if not result.is_resolved:
            msg = error_message or (
                "No project specified. Either set 'default_project' in config, "
                "or provide a 'project' argument."
            )
            raise ValueError(msg)
        return result


__all__ = [
    "ProjectResolver",
    "ResolvedProject",
    "ResolutionMode",
]
