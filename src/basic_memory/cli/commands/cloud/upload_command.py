"""Upload CLI commands for basic-memory projects."""

from functools import partial
from pathlib import Path

import typer
from rich.console import Console

from basic_memory.cli.app import cloud_app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.cloud.cloud_utils import (
    CloudUtilsError,
    create_cloud_project,
    index_project,
    project_exists,
)
from basic_memory.cli.commands.cloud.upload import upload_path
from basic_memory.mcp.async_client import (
    get_cloud_control_plane_client,
    resolve_configured_workspace,
)

console = Console()


@cloud_app.command("upload")
def upload(
    path: Path = typer.Argument(
        ...,
        help="Path to local file or directory to upload",
        exists=True,
        readable=True,
        resolve_path=True,
    ),
    project: str = typer.Option(
        ...,
        "--project",
        "-p",
        help="Cloud project name (destination)",
    ),
    create_project: bool = typer.Option(
        False,
        "--create-project",
        "-c",
        help="Create project if it doesn't exist",
    ),
    index: bool = typer.Option(
        True,
        "--index/--no-index",
        help="Index project after upload (default: true)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed information about file filtering and upload",
    ),
    no_gitignore: bool = typer.Option(
        False,
        "--no-gitignore",
        help="Skip .gitignore patterns (still respects .bmignore)",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be uploaded without actually uploading",
    ),
) -> None:
    """Upload local files or directories to cloud project via WebDAV.

    Examples:
      bm cloud upload ~/my-notes --project research
      bm cloud upload notes.md --project research --create-project
      bm cloud upload ~/docs --project work --no-index
      bm cloud upload ./history --project proto --verbose
      bm cloud upload ./notes --project work --no-gitignore
      bm cloud upload ./files --project test --dry-run
    """

    async def _upload():
        resolved_workspace = resolve_configured_workspace(project_name=project)

        try:
            project_already_exists = await project_exists(project, workspace=resolved_workspace)
        except CloudUtilsError as e:
            console.print(f"[red]Failed to check cloud project '{project}': {e}[/red]")
            raise typer.Exit(1)

        # Check if project exists
        if not project_already_exists:
            if create_project:
                console.print(f"[blue]Creating cloud project '{project}'...[/blue]")
                try:
                    await create_cloud_project(project, workspace=resolved_workspace)
                    console.print(f"[green]Created project '{project}'[/green]")
                except Exception as e:
                    console.print(f"[red]Failed to create project: {e}[/red]")
                    raise typer.Exit(1)
            else:
                console.print(
                    f"[red]Project '{project}' does not exist.[/red]\n"
                    f"[yellow]Options:[/yellow]\n"
                    f"  1. Create it first: bm project add {project} --cloud\n"
                    f"  2. Use --create-project flag to create automatically"
                )
                raise typer.Exit(1)

        # Perform upload (or dry run)
        if resolved_workspace:
            console.print(f"[dim]Using workspace: {resolved_workspace}[/dim]")
        if dry_run:
            console.print(
                f"[yellow]DRY RUN: Showing what would be uploaded to '{project}'[/yellow]"
            )
        else:
            console.print(f"[blue]Uploading {path} to project '{project}'...[/blue]")

        success = await upload_path(
            path,
            project,
            verbose=verbose,
            use_gitignore=not no_gitignore,
            dry_run=dry_run,
            client_cm_factory=partial(
                get_cloud_control_plane_client,
                workspace=resolved_workspace,
            ),
        )
        if not success:
            console.print("[red]Upload failed[/red]")
            raise typer.Exit(1)

        if dry_run:
            console.print("[yellow]DRY RUN complete - no files were uploaded[/yellow]")
        else:
            console.print(f"[green]Successfully uploaded to '{project}'[/green]")

        # Index project if requested (skip on dry run).
        # Trigger: upload adds new files the watcher has not observed locally.
        # Why: force_full ensures those freshly uploaded files are indexed immediately.
        # Outcome: upload keeps its eager reindex while sync/bisync stay incremental.
        if index and not dry_run:
            console.print(f"[blue]Indexing project '{project}'...[/blue]")
            try:
                await index_project(project)
            except Exception as e:
                console.print(f"[yellow]Warning: indexing failed: {e}[/yellow]")
                console.print("[dim]Files uploaded but may not be indexed yet[/dim]")

    run_with_cleanup(_upload())
