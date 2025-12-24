"""Format command for basic-memory CLI."""

import asyncio
from pathlib import Path
from typing import Annotated, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from basic_memory.cli.app import app
from basic_memory.config import ConfigManager, get_project_config
from basic_memory.file_utils import format_file

console = Console()


def is_markdown_extension(path: Path) -> bool:
    """Check if file has a markdown extension."""
    return path.suffix.lower() in (".md", ".markdown")


async def format_single_file(file_path: Path, app_config) -> tuple[Path, bool, Optional[str]]:
    """Format a single file.

    Returns:
        Tuple of (path, success, error_message)
    """
    try:
        result = await format_file(
            file_path, app_config, is_markdown=is_markdown_extension(file_path)
        )
        if result is not None:
            return (file_path, True, None)
        else:
            return (file_path, False, "No formatter configured or formatting skipped")
    except Exception as e:
        return (file_path, False, str(e))


async def format_files(
    paths: list[Path], app_config, show_progress: bool = True
) -> tuple[int, int, list[tuple[Path, str]]]:
    """Format multiple files.

    Returns:
        Tuple of (formatted_count, skipped_count, errors)
    """
    formatted = 0
    skipped = 0
    errors: list[tuple[Path, str]] = []

    if show_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Formatting files...", total=len(paths))

            for file_path in paths:
                path, success, error = await format_single_file(file_path, app_config)
                if success:
                    formatted += 1
                elif error and "No formatter configured" not in error:
                    errors.append((path, error))
                else:
                    skipped += 1
                progress.update(task, advance=1)
    else:
        for file_path in paths:
            path, success, error = await format_single_file(file_path, app_config)
            if success:
                formatted += 1
            elif error and "No formatter configured" not in error:
                errors.append((path, error))
            else:
                skipped += 1

    return formatted, skipped, errors


async def run_format(
    path: Optional[Path] = None,
    project: Optional[str] = None,
) -> None:
    """Run the format command."""
    app_config = ConfigManager().config

    # Check if formatting is enabled
    if (
        not app_config.format_on_save
        and not app_config.formatter_command
        and not app_config.formatters
    ):
        console.print(
            "[yellow]No formatters configured. Set format_on_save=true and "
            "formatter_command or formatters in your config.[/yellow]"
        )
        console.print(
            "\nExample config (~/.basic-memory/config.json):\n"
            '  "format_on_save": true,\n'
            '  "formatter_command": "prettier --write {file}"\n'
        )
        raise typer.Exit(1)

    # Temporarily enable format_on_save for this command
    # (so format_file actually runs the formatter)
    original_format_on_save = app_config.format_on_save
    app_config.format_on_save = True

    try:
        # Determine which files to format
        if path:
            # Format specific file or directory
            if path.is_file():
                files = [path]
            elif path.is_dir():
                # Find all markdown and json files
                files = (
                    list(path.rglob("*.md"))
                    + list(path.rglob("*.json"))
                    + list(path.rglob("*.canvas"))
                )
            else:
                console.print(f"[red]Path not found: {path}[/red]")
                raise typer.Exit(1)
        else:
            # Format all files in project
            project_config = get_project_config(project)
            project_path = Path(project_config.home)

            if not project_path.exists():
                console.print(f"[red]Project path not found: {project_path}[/red]")
                raise typer.Exit(1)

            # Find all markdown and json files
            files = (
                list(project_path.rglob("*.md"))
                + list(project_path.rglob("*.json"))
                + list(project_path.rglob("*.canvas"))
            )

        if not files:
            console.print("[yellow]No files found to format.[/yellow]")
            return

        console.print(f"Found {len(files)} file(s) to format...")

        formatted, skipped, errors = await format_files(files, app_config)

        # Print summary
        console.print()
        if formatted > 0:
            console.print(f"[green]Formatted: {formatted} file(s)[/green]")
        if skipped > 0:
            console.print(f"[dim]Skipped: {skipped} file(s) (no formatter for extension)[/dim]")
        if errors:
            console.print(f"[red]Errors: {len(errors)} file(s)[/red]")
            for path, error in errors:
                console.print(f"  [red]{path}[/red]: {error}")

    finally:
        # Restore original setting
        app_config.format_on_save = original_format_on_save


@app.command()
def format(
    path: Annotated[
        Optional[Path],
        typer.Argument(help="File or directory to format. Defaults to current project."),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option("--project", "-p", help="Project name to format."),
    ] = None,
) -> None:
    """Format files using configured formatters.

    Uses the formatter_command or formatters settings from your config.
    By default, formats all .md, .json, and .canvas files in the current project.

    Examples:
        basic-memory format                    # Format all files in current project
        basic-memory format --project research # Format files in specific project
        basic-memory format notes/meeting.md   # Format a specific file
        basic-memory format notes/             # Format all files in directory
    """
    try:
        asyncio.run(run_format(path, project))
    except Exception as e:
        if not isinstance(e, typer.Exit):
            logger.error(f"Error formatting files: {e}")
            console.print(f"[red]Error formatting files: {e}[/red]")
            raise typer.Exit(code=1)
        raise
