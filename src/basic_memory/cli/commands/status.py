"""Status command for basic-memory CLI."""

import asyncio
from typing import Set, Dict

import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

from basic_memory import db
from basic_memory.cli.app import app
from basic_memory.cli.commands.sync import get_sync_service
from basic_memory.config import config, app_config
from basic_memory.repository import ProjectRepository
from basic_memory.sync.sync_service import SyncReport

# Create rich console
console = Console()


def add_files_to_tree(
    tree: Tree, paths: Set[str], style: str, checksums: Dict[str, str] | None = None
):
    """Add files to tree, grouped by directory."""
    # Group by directory
    by_dir = {}
    for path in sorted(paths):
        parts = path.split("/", 1)
        dir_name = parts[0] if len(parts) > 1 else ""
        file_name = parts[1] if len(parts) > 1 else parts[0]
        by_dir.setdefault(dir_name, []).append((file_name, path))

    # Add to tree
    for dir_name, files in sorted(by_dir.items()):
        if dir_name:
            branch = tree.add(f"[bold]{dir_name}/[/bold]")
        else:
            branch = tree

        for file_name, full_path in sorted(files):
            if checksums and full_path in checksums:
                checksum_short = checksums[full_path][:8]
                branch.add(f"[{style}]{file_name}[/{style}] ({checksum_short})")
            else:
                branch.add(f"[{style}]{file_name}[/{style}]")


def group_changes_by_directory(changes: SyncReport) -> Dict[str, Dict[str, int]]:
    """Group changes by directory for summary view."""
    by_dir = {}
    for change_type, paths in [
        ("new", changes.new),
        ("modified", changes.modified),
        ("deleted", changes.deleted),
    ]:
        for path in paths:
            dir_name = path.split("/", 1)[0]
            by_dir.setdefault(dir_name, {"new": 0, "modified": 0, "deleted": 0, "moved": 0})
            by_dir[dir_name][change_type] += 1

    # Handle moves - count in both source and destination directories
    for old_path, new_path in changes.moves.items():
        old_dir = old_path.split("/", 1)[0]
        new_dir = new_path.split("/", 1)[0]
        by_dir.setdefault(old_dir, {"new": 0, "modified": 0, "deleted": 0, "moved": 0})
        by_dir.setdefault(new_dir, {"new": 0, "modified": 0, "deleted": 0, "moved": 0})
        by_dir[old_dir]["moved"] += 1
        if old_dir != new_dir:
            by_dir[new_dir]["moved"] += 1

    return by_dir


def build_directory_summary(counts: Dict[str, int]) -> str:
    """Build summary string for directory changes."""
    parts = []
    if counts["new"]:
        parts.append(f"[green]+{counts['new']} new[/green]")
    if counts["modified"]:
        parts.append(f"[yellow]~{counts['modified']} modified[/yellow]")
    if counts["moved"]:
        parts.append(f"[blue]↔{counts['moved']} moved[/blue]")
    if counts["deleted"]:
        parts.append(f"[red]-{counts['deleted']} deleted[/red]")
    return " ".join(parts)


def display_changes(project_name: str, title: str, changes: SyncReport, verbose: bool = False):
    """Display changes using Rich for better visualization."""
    tree = Tree(f"{project_name}: {title}")

    if changes.total == 0:
        tree.add("No changes")
        console.print(Panel(tree, expand=False))
        return

    if verbose:
        # Full file listing with checksums
        if changes.new:
            new_branch = tree.add("[green]New Files[/green]")
            add_files_to_tree(new_branch, changes.new, "green", changes.checksums)
        if changes.modified:
            mod_branch = tree.add("[yellow]Modified[/yellow]")
            add_files_to_tree(mod_branch, changes.modified, "yellow", changes.checksums)
        if changes.moves:
            move_branch = tree.add("[blue]Moved[/blue]")
            for old_path, new_path in sorted(changes.moves.items()):
                move_branch.add(f"[blue]{old_path}[/blue] → [blue]{new_path}[/blue]")
        if changes.deleted:
            del_branch = tree.add("[red]Deleted[/red]")
            add_files_to_tree(del_branch, changes.deleted, "red")
    else:
        # Show directory summaries
        by_dir = group_changes_by_directory(changes)
        for dir_name, counts in sorted(by_dir.items()):
            summary = build_directory_summary(counts)
            if summary:  # Only show directories with changes
                tree.add(f"[bold]{dir_name}/[/bold] {summary}")

    console.print(Panel(tree, expand=False))


async def run_status(verbose: bool = False):
    """Check sync status of files vs database."""
    # Check knowledge/ directory

    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path, db_type=db.DatabaseType.FILESYSTEM
    )
    project_repository = ProjectRepository(session_maker)
    project = await project_repository.get_by_name(config.project)
    if not project:  # pragma: no cover
        raise Exception(f"Project '{config.project}' not found")

    sync_service = await get_sync_service(project)
    knowledge_changes = await sync_service.scan(config.home)
    display_changes(project.name, "Status", knowledge_changes, verbose)


@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed file information"),
):
    """Show sync status between files and database."""
    try:
        asyncio.run(run_status(verbose))  # pragma: no cover
    except Exception as e:
        logger.error(f"Error checking status: {e}")
        typer.echo(f"Error checking status: {e}", err=True)
        raise typer.Exit(code=1)  # pragma: no cover
