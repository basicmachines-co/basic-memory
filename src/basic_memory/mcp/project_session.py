"""Project session management for Basic Memory MCP server.

Provides simple in-memory project context for MCP tools, allowing users to switch
between projects during a conversation without restarting the server.
"""

from dataclasses import dataclass
from typing import Optional
from loguru import logger

from basic_memory.config import ProjectConfig, get_project_config


@dataclass
class ProjectSession:
    """Simple in-memory project context for MCP session.

    This class manages the current project context that tools use when no explicit
    project is specified. It's initialized with the default project from config
    and can be changed during the conversation.
    """

    current_project: Optional[str] = None
    default_project: Optional[str] = None

    def initialize(self, default_project: str) -> None:
        """Set the default project from config on startup.

        Args:
            default_project: The project name from configuration
        """
        self.default_project = default_project
        self.current_project = default_project
        logger.info(f"Initialized project session with default project: {default_project}")

    def get_current_project(self) -> str:
        """Get the currently active project name.

        Returns:
            The current project name, falling back to default, then 'main'
        """
        return self.current_project or self.default_project or "main"

    def set_current_project(self, project_name: str) -> None:
        """Set the current project context.

        Args:
            project_name: The project to switch to
        """
        previous = self.current_project
        self.current_project = project_name
        logger.info(f"Switched project context: {previous} -> {project_name}")

    def get_default_project(self) -> str:
        """Get the default project name from startup.

        Returns:
            The default project name, or 'main' if not set
        """
        return self.default_project or "main"  # pragma: no cover

    def reset_to_default(self) -> None:  # pragma: no cover
        """Reset current project back to the default project."""
        self.current_project = self.default_project  # pragma: no cover
        logger.info(f"Reset project context to default: {self.default_project}")  # pragma: no cover


# Global session instance
session = ProjectSession()


def get_active_project(project_override: Optional[str] = None) -> ProjectConfig:
    """Get the active project name for a tool call.

    This is the main function tools should use to determine which project
    to operate on.

    Args:
        project_override: Optional explicit project name from tool parameter

    Returns:
        The project name to use (override takes precedence over session context)
    """
    if project_override:  # pragma: no cover
        project = get_project_config(project_override)
        session.set_current_project(project_override)
        return project

    current_project = session.get_current_project()
    return get_project_config(current_project)


def add_project_metadata(result: str, project_name: str) -> str:
    """Add project context as metadata footer for LLM awareness.

    Args:
        result: The tool result string
        project_name: The project name that was used

    Returns:
        Result with project metadata footer
    """
    return f"{result}\n\n<!-- Project: {project_name} -->"  # pragma: no cover
