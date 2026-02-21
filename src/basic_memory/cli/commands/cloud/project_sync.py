"""Cloud sync commands for Basic Memory projects.

Commands for syncing, bisyncing, and checking integrity between local and cloud
project instances. These were previously in project.py but belong here since
they are cloud-specific operations.
"""

from datetime import datetime

import typer
from rich.console import Console

from basic_memory.cli.app import cloud_app
from basic_memory.cli.auth import CLIAuth
from basic_memory.cli.commands.cloud.bisync_commands import get_mount_info
from basic_memory.cli.commands.cloud.rclone_commands import (
    RcloneError,
    SyncProject,
    get_project_bisync_state,
    project_bisync,
    project_check,
    project_sync,
)
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing
from basic_memory.config import ConfigManager, ProjectEntry
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.tools.utils import call_get, call_post
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.utils import generate_permalink, normalize_project_path

console = Console()


# --- Shared helpers ---


def _has_cloud_credentials(config) -> bool:
    """Return whether cloud credentials are available (API key or OAuth token)."""
    if config.cloud_api_key:
        return True

    auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
    return auth.load_tokens() is not None


def _require_cloud_credentials(config) -> None:
    """Exit with actionable guidance when cloud credentials are missing."""
    if _has_cloud_credentials(config):
        return

    console.print("[red]Error: cloud credentials are required for this command[/red]")
    console.print("[dim]Run 'bm cloud login' or 'bm cloud api-key save <key>' first[/dim]")
    raise typer.Exit(1)


async def _get_cloud_project(name: str) -> ProjectItem | None:
    """Fetch a project by name from the cloud API."""
    async with get_client() as client:
        response = await call_get(client, "/v2/projects/")
        projects_list = ProjectList.model_validate(response.json())
        for proj in projects_list.projects:
            if generate_permalink(proj.name) == generate_permalink(name):
                return proj
        return None


def _get_sync_project(
    name: str, config, project_data: ProjectItem
) -> tuple[SyncProject, str | None]:
    """Build a SyncProject and resolve local_sync_path from config.

    Returns (sync_project, local_sync_path). Exits if no local_sync_path configured.
    """
    sync_entry = config.projects.get(name)
    local_sync_path = sync_entry.local_sync_path if sync_entry else None

    if not local_sync_path:
        console.print(f"[red]Error: Project '{name}' has no local_sync_path configured[/red]")
        console.print(f"\nConfigure sync with: bm cloud sync-setup {name} ~/path/to/local")
        raise typer.Exit(1)

    sync_project = SyncProject(
        name=project_data.name,
        path=normalize_project_path(project_data.path),
        local_sync_path=local_sync_path,
    )
    return sync_project, local_sync_path


# --- Commands ---


@cloud_app.command("sync")
def sync_project_command(
    name: str = typer.Option(..., "--name", help="Project name to sync"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without syncing"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
) -> None:
    """One-way sync: local -> cloud (make cloud identical to local).

    Example:
      bm cloud sync --name research
      bm cloud sync --name research --dry-run
    """
    config = ConfigManager().config
    _require_cloud_credentials(config)

    try:
        # Get tenant info for bucket name
        tenant_info = run_with_cleanup(get_mount_info())
        bucket_name = tenant_info.bucket_name

        # Get project info
        with force_routing(cloud=True):
            project_data = run_with_cleanup(_get_cloud_project(name))
        if not project_data:
            console.print(f"[red]Error: Project '{name}' not found[/red]")
            raise typer.Exit(1)

        sync_project, local_sync_path = _get_sync_project(name, config, project_data)

        # Run sync
        console.print(f"[blue]Syncing {name} (local -> cloud)...[/blue]")
        success = project_sync(sync_project, bucket_name, dry_run=dry_run, verbose=verbose)

        if success:
            console.print(f"[green]{name} synced successfully[/green]")

            # Trigger database sync if not a dry run
            if not dry_run:

                async def _trigger_db_sync():
                    async with get_client() as client:
                        response = await call_post(
                            client,
                            f"/v2/projects/{project_data.external_id}/sync?force_full=true",
                            json={},
                        )
                        return response.json()

                try:
                    with force_routing(cloud=True):
                        result = run_with_cleanup(_trigger_db_sync())
                    console.print(f"[dim]Database sync initiated: {result.get('message')}[/dim]")
                except Exception as e:
                    console.print(f"[yellow]Warning: Could not trigger database sync: {e}[/yellow]")
        else:
            console.print(f"[red]{name} sync failed[/red]")
            raise typer.Exit(1)

    except RcloneError as e:
        console.print(f"[red]Sync error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@cloud_app.command("bisync")
def bisync_project_command(
    name: str = typer.Option(..., "--name", help="Project name to bisync"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without syncing"),
    resync: bool = typer.Option(False, "--resync", help="Force new baseline"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
) -> None:
    """Two-way sync: local <-> cloud (bidirectional sync).

    Examples:
      bm cloud bisync --name research --resync  # First time
      bm cloud bisync --name research           # Subsequent syncs
      bm cloud bisync --name research --dry-run # Preview changes
    """
    config = ConfigManager().config
    _require_cloud_credentials(config)

    try:
        # Get tenant info for bucket name
        tenant_info = run_with_cleanup(get_mount_info())
        bucket_name = tenant_info.bucket_name

        # Get project info
        with force_routing(cloud=True):
            project_data = run_with_cleanup(_get_cloud_project(name))
        if not project_data:
            console.print(f"[red]Error: Project '{name}' not found[/red]")
            raise typer.Exit(1)

        sync_project, local_sync_path = _get_sync_project(name, config, project_data)

        # Run bisync
        console.print(f"[blue]Bisync {name} (local <-> cloud)...[/blue]")
        success = project_bisync(
            sync_project, bucket_name, dry_run=dry_run, resync=resync, verbose=verbose
        )

        if success:
            console.print(f"[green]{name} bisync completed successfully[/green]")

            # Update config — sync_entry is guaranteed non-None because
            # _get_sync_project validated local_sync_path (which comes from sync_entry)
            sync_entry = config.projects.get(name)
            assert sync_entry is not None
            sync_entry.last_sync = datetime.now()
            sync_entry.bisync_initialized = True
            ConfigManager().save_config(config)

            # Trigger database sync if not a dry run
            if not dry_run:

                async def _trigger_db_sync():
                    async with get_client() as client:
                        response = await call_post(
                            client,
                            f"/v2/projects/{project_data.external_id}/sync?force_full=true",
                            json={},
                        )
                        return response.json()

                try:
                    with force_routing(cloud=True):
                        result = run_with_cleanup(_trigger_db_sync())
                    console.print(f"[dim]Database sync initiated: {result.get('message')}[/dim]")
                except Exception as e:
                    console.print(f"[yellow]Warning: Could not trigger database sync: {e}[/yellow]")
        else:
            console.print(f"[red]{name} bisync failed[/red]")
            raise typer.Exit(1)

    except RcloneError as e:
        console.print(f"[red]Bisync error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@cloud_app.command("check")
def check_project_command(
    name: str = typer.Option(..., "--name", help="Project name to check"),
    one_way: bool = typer.Option(False, "--one-way", help="Check one direction only (faster)"),
) -> None:
    """Verify file integrity between local and cloud.

    Example:
      bm cloud check --name research
    """
    config = ConfigManager().config
    _require_cloud_credentials(config)

    try:
        # Get tenant info for bucket name
        tenant_info = run_with_cleanup(get_mount_info())
        bucket_name = tenant_info.bucket_name

        # Get project info
        with force_routing(cloud=True):
            project_data = run_with_cleanup(_get_cloud_project(name))
        if not project_data:
            console.print(f"[red]Error: Project '{name}' not found[/red]")
            raise typer.Exit(1)

        sync_project, local_sync_path = _get_sync_project(name, config, project_data)

        # Run check
        console.print(f"[blue]Checking {name} integrity...[/blue]")
        match = project_check(sync_project, bucket_name, one_way=one_way)

        if match:
            console.print(f"[green]{name} files match[/green]")
        else:
            console.print(f"[yellow]!{name} has differences[/yellow]")

    except RcloneError as e:
        console.print(f"[red]Check error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@cloud_app.command("bisync-reset")
def bisync_reset(
    name: str = typer.Argument(..., help="Project name to reset bisync state for"),
) -> None:
    """Clear bisync state for a project.

    This removes the bisync metadata files, forcing a fresh --resync on next bisync.
    Useful when bisync gets into an inconsistent state or when remote path changes.
    """
    import shutil

    try:
        state_path = get_project_bisync_state(name)

        if not state_path.exists():
            console.print(f"[yellow]No bisync state found for project '{name}'[/yellow]")
            return

        # Remove the entire state directory
        shutil.rmtree(state_path)
        console.print(f"[green]Cleared bisync state for project '{name}'[/green]")
        console.print("\nNext steps:")
        console.print(f"  1. Preview: bm cloud bisync --name {name} --resync --dry-run")
        console.print(f"  2. Sync: bm cloud bisync --name {name} --resync")

    except Exception as e:
        console.print(f"[red]Error clearing bisync state: {str(e)}[/red]")
        raise typer.Exit(1)


@cloud_app.command("sync-setup")
def setup_project_sync(
    name: str = typer.Argument(..., help="Project name"),
    local_path: str = typer.Argument(..., help="Local sync directory"),
) -> None:
    """Configure local sync for an existing cloud project.

    Example:
      bm cloud sync-setup research ~/Documents/research
    """
    import os
    from pathlib import Path

    config_manager = ConfigManager()
    config = config_manager.config
    _require_cloud_credentials(config)

    async def _verify_project_exists():
        """Verify the project exists on cloud by listing all projects."""
        async with get_client() as client:
            response = await call_get(client, "/v2/projects/")
            project_list = response.json()
            project_names = [p["name"] for p in project_list["projects"]]
            if name not in project_names:
                raise ValueError(f"Project '{name}' not found on cloud")
            return True

    try:
        # Verify project exists on cloud
        with force_routing(cloud=True):
            run_with_cleanup(_verify_project_exists())

        # Resolve and create local path
        resolved_path = Path(os.path.abspath(os.path.expanduser(local_path)))
        resolved_path.mkdir(parents=True, exist_ok=True)

        # Update project entry with sync path
        entry = config.projects.get(name)
        if entry:
            entry.local_sync_path = resolved_path.as_posix()
            entry.bisync_initialized = False
            entry.last_sync = None
        else:
            config.projects[name] = ProjectEntry(
                path=resolved_path.as_posix(),
                local_sync_path=resolved_path.as_posix(),
            )
        config_manager.save_config(config)

        console.print(f"[green]Sync configured for project '{name}'[/green]")
        console.print(f"\nLocal sync path: {resolved_path}")
        console.print("\nNext steps:")
        console.print(f"  1. Preview: bm cloud bisync --name {name} --resync --dry-run")
        console.print(f"  2. Sync: bm cloud bisync --name {name} --resync")
    except Exception as e:
        console.print(f"[red]Error configuring sync: {str(e)}[/red]")
        raise typer.Exit(1)
