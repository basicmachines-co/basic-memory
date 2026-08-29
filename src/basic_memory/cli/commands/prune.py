"""Prune index entries for files the ignore patterns now exclude."""

from typing import Optional

import typer
from rich.console import Console

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.config import ConfigManager, ProjectMode

console = Console()


@app.command("prune")
def prune(
    project: Optional[str] = typer.Option(
        None, "--project", "-p", help="Project to prune (defaults to the default project)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List what would be removed without deleting anything"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Delete without a confirmation prompt"),
) -> None:
    """Remove index entries for files your ignore patterns now exclude.

    Indexing skips files matched by ~/.basic-memory/.bmignore and the project's
    .gitignore, but entries indexed before a pattern was added stay in the index:
    the files are still on disk, so the scan's delete guard keeps them. Like
    `git rm --cached`, prune removes the index entries (entity, relations, search
    rows, vectors) and leaves the files untouched. The cloud counterpart is
    `bm cloud prune`.
    """
    app_config = ConfigManager().config
    run_with_cleanup(_prune(app_config, project=project, dry_run=dry_run, yes=yes))


async def _prune(app_config, *, project: str | None, dry_run: bool, yes: bool) -> None:
    # Deferred: SQLAlchemy, repositories, and the indexing stack load only when a
    # prune actually runs, not on every CLI start (#886).
    from basic_memory import db
    from basic_memory.index.local_project import LocalProjectIndexRuntimeFactory
    from basic_memory.index.local_prune import list_ignored_indexed_paths, prune_ignored_entities
    from basic_memory.repository import ProjectRepository
    from basic_memory.services.initialization import reconcile_projects_with_config

    project_name = project or app_config.default_project
    # default_project may be unset on purpose (automatic project resolution
    # disabled); get_project_mode(None) would report it as an unknown cloud
    # project, which is the wrong message.
    if not project_name:
        console.print("[red]No project given and no default project is set; pass --project.[/red]")
        raise typer.Exit(1)
    if app_config.get_project_mode(project_name) == ProjectMode.CLOUD:
        console.print(
            f"[yellow]Project '{project_name}' is a cloud project.[/yellow]\n"
            "Prune is a local operation — use `bm cloud prune` for cloud projects."
        )
        raise typer.Exit(1)

    await reconcile_projects_with_config(app_config)
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )
    async with db.scoped_session(session_maker) as session:
        projects = await ProjectRepository().get_active_projects(session)
    matches = [candidate for candidate in projects if candidate.name == project_name]
    if not matches:
        console.print(f"[red]Project '{project_name}' not found.[/red]")
        raise typer.Exit(1)
    target = matches[0]

    dependencies = await LocalProjectIndexRuntimeFactory().dependencies_for_project(target)
    console.print(
        f"[blue]Scanning {target.name} for indexed files matching ignore patterns...[/blue]"
    )
    paths = await list_ignored_indexed_paths(dependencies)
    if not paths:
        console.print(f"[green]No indexed files in {target.name} match the ignore patterns[/green]")
        return

    console.print(f"[yellow]{len(paths)} indexed file(s) match the ignore patterns:[/yellow]")
    for path in paths:
        console.print(f"  [yellow]-[/yellow] {path}")
    if dry_run:
        console.print("\n[dim]Dry run: nothing removed. Re-run without --dry-run to prune.[/dim]")
        return
    # Trigger: no --yes flag.
    # Why: prune drops index entries that only a re-index of the files brings back;
    #      require an explicit confirmation, as `bm cloud prune` does.
    # Outcome: abort cleanly unless the user confirms.
    if not yes and not typer.confirm(
        f"\nRemove these {len(paths)} index entr{'y' if len(paths) == 1 else 'ies'}? "
        "Files on disk are not touched."
    ):
        console.print("[yellow]Prune cancelled - nothing removed[/yellow]")
        raise typer.Exit(0)

    result = await prune_ignored_entities(dependencies, paths)
    console.print(
        f"[green]Removed {result.deleted_entities} index entr{'y' if result.deleted_entities == 1 else 'ies'}[/green]"
    )
    if result.refreshed_entity_ids:
        console.print(
            f"[dim]Refreshed search rows for {len(result.refreshed_entity_ids)} linking note(s)[/dim]"
        )
