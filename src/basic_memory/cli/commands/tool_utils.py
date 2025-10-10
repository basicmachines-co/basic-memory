"""Shared utilities for CLI tool commands."""

import asyncio
from typing import Any, Callable, Optional

import typer

from basic_memory.config import ConfigManager


def resolve_project(project: Optional[str] = None) -> str:
    """Resolve project name from parameter or default.

    Args:
        project: Optional project name to resolve. If None, uses default project.

    Returns:
        The resolved project name.

    Raises:
        typer.Exit: If the specified project is not found in the configuration.
    """
    config_manager = ConfigManager()

    if project is not None:
        project_name, _ = config_manager.get_project(project)
        if not project_name:
            typer.echo(f"No project found named: {project}", err=True)
            raise typer.Exit(1)
        return project_name

    return config_manager.default_project


def run_async_tool(tool_func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run an async MCP tool function with proper error handling.

    Args:
        tool_func: The async function to execute.
        *args: Positional arguments to pass to the function.
        **kwargs: Keyword arguments to pass to the function.

    Returns:
        The result from the async function.

    Raises:
        typer.Exit: If an error occurs during execution.
    """
    try:
        return asyncio.run(tool_func(*args, **kwargs))
    except Exception as e:
        if not isinstance(e, typer.Exit):
            # Get function name for better error messages
            func_name = getattr(tool_func, "__name__", "tool")
            typer.echo(f"Error during {func_name}: {e}", err=True)
            raise typer.Exit(1)
        raise
