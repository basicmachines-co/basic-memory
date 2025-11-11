"""Command module for basic-memory project management."""

import asyncio
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import get_project_info
from basic_memory.config import ConfigManager
import json
from datetime import datetime

from rich.panel import Panel
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.tools.utils import call_get
from basic_memory.schemas.project_info import ProjectList
from basic_memory.mcp.tools.utils import call_post
from basic_memory.schemas.project_info import ProjectStatusResponse
from basic_memory.mcp.tools.utils import call_delete
from basic_memory.mcp.tools.utils import call_put
from basic_memory.utils import generate_permalink, normalize_project_path
from basic_memory.mcp.tools.utils import call_patch

# Import rclone commands for project sync
from basic_memory.cli.commands.cloud.rclone_commands import (
    SyncProject,
    RcloneError,
    project_sync,
    project_bisync,
    project_check,
    project_ls,
)
from basic_memory.cli.commands.cloud.bisync_commands import get_mount_info

console = Console()

# Create a project subcommand
project_app = typer.Typer(help="Manage multiple Basic Memory projects")
app.add_typer(project_app, name="project")


def format_path(path: str) -> str:
    """Format a path for display, using ~ for home directory."""
    home = str(Path.home())
    if path.startswith(home):
        return path.replace(home, "~", 1)  # pragma: no cover
    return path


@project_app.command("list")
def list_projects() -> None:
    """List all Basic Memory projects."""

    async def _list_projects():
        async with get_client() as client:
            response = await call_get(client, "/projects/projects")
            return ProjectList.model_validate(response.json())

    try:
        result = asyncio.run(_list_projects())
        config = ConfigManager().config

        table = Table(title="Basic Memory Projects")
        table.add_column("Name", style="cyan")
        table.add_column("Path", style="green")

        # Add Local Path column if in cloud mode
        if config.cloud_mode_enabled:
            table.add_column("Local Path", style="yellow", no_wrap=True, overflow="fold")

        # Show Default column in local mode or if default_project_mode is enabled in cloud mode
        show_default_column = not config.cloud_mode_enabled or config.default_project_mode
        if show_default_column:
            table.add_column("Default", style="magenta")

        for project in result.projects:
            is_default = "[X]" if project.is_default else ""
            normalized_path = normalize_project_path(project.path)

            # Build row based on mode
            row = [project.name, format_path(normalized_path)]

            # Add local path if in cloud mode
            if config.cloud_mode_enabled:
                local_path = ""
                if project.name in config.cloud_projects:
                    local_path = config.cloud_projects[project.name].local_path or ""
                    local_path = format_path(local_path)
                row.append(local_path)

            # Add default indicator if showing default column
            if show_default_column:
                row.append(is_default)

            table.add_row(*row)

        console.print(table)
    except Exception as e:
        console.print(f"[red]Error listing projects: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("add")
def add_project(
    name: str = typer.Argument(..., help="Name of the project"),
    path: str = typer.Argument(
        None, help="Path to the project directory (required for local mode)"
    ),
    local_path: str = typer.Option(
        None, "--local-path", help="Local sync path for cloud mode (optional)"
    ),
    set_default: bool = typer.Option(False, "--default", help="Set as default project"),
) -> None:
    """Add a new project.

    Cloud mode examples:\n
        bm project add research                           # No local sync\n
        bm project add research --local-path ~/docs       # With local sync\n

    Local mode example:\n
        bm project add research ~/Documents/research
    """
    config = ConfigManager().config

    # Resolve local sync path early (needed for both cloud and local mode)
    local_sync_path: str | None = None
    if local_path:
        local_sync_path = Path(os.path.abspath(os.path.expanduser(local_path))).as_posix()

    if config.cloud_mode_enabled:
        # Cloud mode: path auto-generated from name, local sync is optional

        async def _add_project():
            async with get_client() as client:
                data = {
                    "name": name,
                    "path": generate_permalink(name),
                    "local_sync_path": local_sync_path,
                    "set_default": set_default,
                }
                response = await call_post(client, "/projects/projects", json=data)
                return ProjectStatusResponse.model_validate(response.json())
    else:
        # Local mode: path is required
        if path is None:
            console.print("[red]Error: path argument is required in local mode[/red]")
            raise typer.Exit(1)

        # Resolve to absolute path
        resolved_path = Path(os.path.abspath(os.path.expanduser(path))).as_posix()

        async def _add_project():
            async with get_client() as client:
                data = {"name": name, "path": resolved_path, "set_default": set_default}
                response = await call_post(client, "/projects/projects", json=data)
                return ProjectStatusResponse.model_validate(response.json())

    try:
        result = asyncio.run(_add_project())
        console.print(f"[green]{result.message}[/green]")

        # Save local sync path to config if in cloud mode
        if config.cloud_mode_enabled and local_sync_path:
            from basic_memory.config import CloudProjectConfig

            # Create local directory if it doesn't exist
            local_dir = Path(local_sync_path)
            local_dir.mkdir(parents=True, exist_ok=True)

            # Update config with sync path
            config.cloud_projects[name] = CloudProjectConfig(
                local_path=local_sync_path,
                last_sync=None,
                bisync_initialized=False,
            )
            ConfigManager().save_config(config)

            console.print(f"\n[green]Local sync path configured: {local_sync_path}[/green]")
            console.print("\nNext steps:")
            console.print(f"  1. Preview: bm project bisync --name {name} --resync --dry-run")
            console.print(f"  2. Sync: bm project bisync --name {name} --resync")
    except Exception as e:
        console.print(f"[red]Error adding project: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("sync-setup")
def setup_project_sync(
    name: str = typer.Argument(..., help="Project name"),
    local_path: str = typer.Argument(..., help="Local sync directory"),
) -> None:
    """Configure local sync for an existing cloud project.

    Example:
      bm project sync-setup research ~/Documents/research
    """
    config_manager = ConfigManager()
    config = config_manager.config

    if not config.cloud_mode_enabled:
        console.print("[red]Error: sync-setup only available in cloud mode[/red]")
        raise typer.Exit(1)

    async def _verify_project_exists():
        """Verify the project exists on cloud by listing all projects."""
        async with get_client() as client:
            response = await call_get(client, "/projects/projects")
            project_list = response.json()
            project_names = [p["name"] for p in project_list["projects"]]
            if name not in project_names:
                raise ValueError(f"Project '{name}' not found on cloud")
            return True

    try:
        # Verify project exists on cloud
        asyncio.run(_verify_project_exists())

        # Resolve and create local path
        resolved_path = Path(os.path.abspath(os.path.expanduser(local_path)))
        resolved_path.mkdir(parents=True, exist_ok=True)

        # Update local config with sync path
        from basic_memory.config import CloudProjectConfig

        config.cloud_projects[name] = CloudProjectConfig(
            local_path=resolved_path.as_posix(),
            last_sync=None,
            bisync_initialized=False,
        )
        config_manager.save_config(config)

        console.print(f"[green]Sync configured for project '{name}'[/green]")
        console.print(f"\nLocal sync path: {resolved_path}")
        console.print("\nNext steps:")
        console.print(f"  1. Preview: bm project bisync --name {name} --resync --dry-run")
        console.print(f"  2. Sync: bm project bisync --name {name} --resync")
    except Exception as e:
        console.print(f"[red]Error configuring sync: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("remove")
def remove_project(
    name: str = typer.Argument(..., help="Name of the project to remove"),
    delete_notes: bool = typer.Option(
        False, "--delete-notes", help="Delete project files from disk"
    ),
) -> None:
    """Remove a project."""

    async def _remove_project():
        async with get_client() as client:
            project_permalink = generate_permalink(name)
            response = await call_delete(
                client, f"/projects/{project_permalink}?delete_notes={delete_notes}"
            )
            return ProjectStatusResponse.model_validate(response.json())

    try:
        # Get config to check for local sync path and bisync state
        config = ConfigManager().config
        local_path = None
        has_bisync_state = False

        if config.cloud_mode_enabled and name in config.cloud_projects:
            local_path = config.cloud_projects[name].local_path

            # Check for bisync state
            from basic_memory.cli.commands.cloud.rclone_commands import get_project_bisync_state

            bisync_state_path = get_project_bisync_state(name)
            has_bisync_state = bisync_state_path.exists()

        # Remove project from cloud/API
        result = asyncio.run(_remove_project())
        console.print(f"[green]{result.message}[/green]")

        # Clean up local sync directory if it exists and delete_notes is True
        if delete_notes and local_path:
            local_dir = Path(local_path)
            if local_dir.exists():
                import shutil

                shutil.rmtree(local_dir)
                console.print(f"[green]Removed local sync directory: {local_path}[/green]")

        # Clean up bisync state if it exists
        if has_bisync_state:
            from basic_memory.cli.commands.cloud.rclone_commands import get_project_bisync_state
            import shutil

            bisync_state_path = get_project_bisync_state(name)
            if bisync_state_path.exists():
                shutil.rmtree(bisync_state_path)
                console.print("[green]Removed bisync state[/green]")

        # Clean up cloud_projects config entry
        if config.cloud_mode_enabled and name in config.cloud_projects:
            del config.cloud_projects[name]
            ConfigManager().save_config(config)

        # Show informative message if files were not deleted
        if not delete_notes:
            if local_path:
                console.print(f"[yellow]Note: Local files remain at {local_path}[/yellow]")

    except Exception as e:
        console.print(f"[red]Error removing project: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("default")
def set_default_project(
    name: str = typer.Argument(..., help="Name of the project to set as CLI default"),
) -> None:
    """Set the default project when 'config.default_project_mode' is set.

    Note: This command is only available in local mode.
    """
    config = ConfigManager().config

    if config.cloud_mode_enabled:
        console.print("[red]Error: 'default' command is not available in cloud mode[/red]")
        raise typer.Exit(1)

    async def _set_default():
        async with get_client() as client:
            project_permalink = generate_permalink(name)
            response = await call_put(client, f"/projects/{project_permalink}/default")
            return ProjectStatusResponse.model_validate(response.json())

    try:
        result = asyncio.run(_set_default())
        console.print(f"[green]{result.message}[/green]")
    except Exception as e:
        console.print(f"[red]Error setting default project: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("sync-config")
def synchronize_projects() -> None:
    """Synchronize project config between configuration file and database.

    Note: This command is only available in local mode.
    """
    config = ConfigManager().config

    if config.cloud_mode_enabled:
        console.print("[red]Error: 'sync-config' command is not available in cloud mode[/red]")
        raise typer.Exit(1)

    async def _sync_config():
        async with get_client() as client:
            response = await call_post(client, "/projects/config/sync")
            return ProjectStatusResponse.model_validate(response.json())

    try:
        result = asyncio.run(_sync_config())
        console.print(f"[green]{result.message}[/green]")
    except Exception as e:  # pragma: no cover
        console.print(f"[red]Error synchronizing projects: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("move")
def move_project(
    name: str = typer.Argument(..., help="Name of the project to move"),
    new_path: str = typer.Argument(..., help="New absolute path for the project"),
) -> None:
    """Move a project to a new location.

    Note: This command is only available in local mode.
    """
    config = ConfigManager().config

    if config.cloud_mode_enabled:
        console.print("[red]Error: 'move' command is not available in cloud mode[/red]")
        raise typer.Exit(1)

    # Resolve to absolute path
    resolved_path = Path(os.path.abspath(os.path.expanduser(new_path))).as_posix()

    async def _move_project():
        async with get_client() as client:
            data = {"path": resolved_path}
            project_permalink = generate_permalink(name)

            # TODO fix route to use ProjectPathDep
            response = await call_patch(client, f"/{name}/project/{project_permalink}", json=data)
            return ProjectStatusResponse.model_validate(response.json())

    try:
        result = asyncio.run(_move_project())
        console.print(f"[green]{result.message}[/green]")

        # Show important file movement reminder
        console.print()  # Empty line for spacing
        console.print(
            Panel(
                "[bold red]IMPORTANT:[/bold red] Project configuration updated successfully.\n\n"
                "[yellow]You must manually move your project files from the old location to:[/yellow]\n"
                f"[cyan]{resolved_path}[/cyan]\n\n"
                "[dim]Basic Memory has only updated the configuration - your files remain in their original location.[/dim]",
                title="Manual File Movement Required",
                border_style="yellow",
                expand=False,
            )
        )

    except Exception as e:
        console.print(f"[red]Error moving project: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("sync")
def sync_project_command(
    name: str = typer.Option(..., "--name", help="Project name to sync"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without syncing"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
) -> None:
    """One-way sync: local -> cloud (make cloud identical to local).

    Example:
      bm project sync --name research
      bm project sync --name research --dry-run
    """
    config = ConfigManager().config
    if not config.cloud_mode_enabled:
        console.print("[red]Error: sync only available in cloud mode[/red]")
        raise typer.Exit(1)

    try:
        # Get tenant info for bucket name
        tenant_info = asyncio.run(get_mount_info())
        bucket_name = tenant_info.bucket_name

        # Get project info
        async def _get_project():
            async with get_client() as client:
                response = await call_get(client, "/projects/projects")
                projects_list = ProjectList.model_validate(response.json())
                for proj in projects_list.projects:
                    if generate_permalink(proj.name) == generate_permalink(name):
                        return proj
                return None

        project_data = asyncio.run(_get_project())
        if not project_data:
            console.print(f"[red]Error: Project '{name}' not found[/red]")
            raise typer.Exit(1)

        # Get local_sync_path from cloud_projects config
        local_sync_path = None
        if name in config.cloud_projects:
            local_sync_path = config.cloud_projects[name].local_path

        if not local_sync_path:
            console.print(f"[red]Error: Project '{name}' has no local_sync_path configured[/red]")
            console.print(f"\nConfigure sync with: bm project sync-setup {name} ~/path/to/local")
            raise typer.Exit(1)

        # Create SyncProject
        sync_project = SyncProject(
            name=project_data.name,
            path=normalize_project_path(project_data.path),
            local_sync_path=local_sync_path,
        )

        # Run sync
        console.print(f"[blue]Syncing {name} (local -> cloud)...[/blue]")
        success = project_sync(sync_project, bucket_name, dry_run=dry_run, verbose=verbose)

        if success:
            console.print(f"[green]{name} synced successfully[/green]")

            # Trigger database sync if not a dry run
            if not dry_run:

                async def _trigger_db_sync():
                    async with get_client() as client:
                        permalink = generate_permalink(name)
                        response = await call_post(
                            client, f"/{permalink}/project/sync?force_full=true", json={}
                        )
                        return response.json()

                try:
                    result = asyncio.run(_trigger_db_sync())
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


@project_app.command("bisync")
def bisync_project_command(
    name: str = typer.Option(..., "--name", help="Project name to bisync"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without syncing"),
    resync: bool = typer.Option(False, "--resync", help="Force new baseline"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
) -> None:
    """Two-way sync: local <-> cloud (bidirectional sync).

    Examples:
      bm project bisync --name research --resync  # First time
      bm project bisync --name research           # Subsequent syncs
      bm project bisync --name research --dry-run # Preview changes
    """
    config = ConfigManager().config
    if not config.cloud_mode_enabled:
        console.print("[red]Error: bisync only available in cloud mode[/red]")
        raise typer.Exit(1)

    try:
        # Get tenant info for bucket name
        tenant_info = asyncio.run(get_mount_info())
        bucket_name = tenant_info.bucket_name

        # Get project info
        async def _get_project():
            async with get_client() as client:
                response = await call_get(client, "/projects/projects")
                projects_list = ProjectList.model_validate(response.json())
                for proj in projects_list.projects:
                    if generate_permalink(proj.name) == generate_permalink(name):
                        return proj
                return None

        project_data = asyncio.run(_get_project())
        if not project_data:
            console.print(f"[red]Error: Project '{name}' not found[/red]")
            raise typer.Exit(1)

        # Get local_sync_path from cloud_projects config
        local_sync_path = None
        if name in config.cloud_projects:
            local_sync_path = config.cloud_projects[name].local_path

        if not local_sync_path:
            console.print(f"[red]Error: Project '{name}' has no local_sync_path configured[/red]")
            console.print(f"\nConfigure sync with: bm project sync-setup {name} ~/path/to/local")
            raise typer.Exit(1)

        # Create SyncProject
        sync_project = SyncProject(
            name=project_data.name,
            path=normalize_project_path(project_data.path),
            local_sync_path=local_sync_path,
        )

        # Run bisync
        console.print(f"[blue]Bisync {name} (local <-> cloud)...[/blue]")
        success = project_bisync(
            sync_project, bucket_name, dry_run=dry_run, resync=resync, verbose=verbose
        )

        if success:
            console.print(f"[green]{name} bisync completed successfully[/green]")

            # Update config
            config.cloud_projects[name].last_sync = datetime.now()
            config.cloud_projects[name].bisync_initialized = True
            ConfigManager().save_config(config)

            # Trigger database sync if not a dry run
            if not dry_run:

                async def _trigger_db_sync():
                    async with get_client() as client:
                        permalink = generate_permalink(name)
                        response = await call_post(
                            client, f"/{permalink}/project/sync?force_full=true", json={}
                        )
                        return response.json()

                try:
                    result = asyncio.run(_trigger_db_sync())
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


@project_app.command("check")
def check_project_command(
    name: str = typer.Option(..., "--name", help="Project name to check"),
    one_way: bool = typer.Option(False, "--one-way", help="Check one direction only (faster)"),
) -> None:
    """Verify file integrity between local and cloud.

    Example:
      bm project check --name research
    """
    config = ConfigManager().config
    if not config.cloud_mode_enabled:
        console.print("[red]Error: check only available in cloud mode[/red]")
        raise typer.Exit(1)

    try:
        # Get tenant info for bucket name
        tenant_info = asyncio.run(get_mount_info())
        bucket_name = tenant_info.bucket_name

        # Get project info
        async def _get_project():
            async with get_client() as client:
                response = await call_get(client, "/projects/projects")
                projects_list = ProjectList.model_validate(response.json())
                for proj in projects_list.projects:
                    if generate_permalink(proj.name) == generate_permalink(name):
                        return proj
                return None

        project_data = asyncio.run(_get_project())
        if not project_data:
            console.print(f"[red]Error: Project '{name}' not found[/red]")
            raise typer.Exit(1)

        # Get local_sync_path from cloud_projects config
        local_sync_path = None
        if name in config.cloud_projects:
            local_sync_path = config.cloud_projects[name].local_path

        if not local_sync_path:
            console.print(f"[red]Error: Project '{name}' has no local_sync_path configured[/red]")
            console.print(f"\nConfigure sync with: bm project sync-setup {name} ~/path/to/local")
            raise typer.Exit(1)

        # Create SyncProject
        sync_project = SyncProject(
            name=project_data.name,
            path=normalize_project_path(project_data.path),
            local_sync_path=local_sync_path,
        )

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


@project_app.command("bisync-reset")
def bisync_reset(
    name: str = typer.Argument(..., help="Project name to reset bisync state for"),
) -> None:
    """Clear bisync state for a project.

    This removes the bisync metadata files, forcing a fresh --resync on next bisync.
    Useful when bisync gets into an inconsistent state or when remote path changes.
    """
    from basic_memory.cli.commands.cloud.rclone_commands import get_project_bisync_state
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
        console.print(f"  1. Preview: bm project bisync --name {name} --resync --dry-run")
        console.print(f"  2. Sync: bm project bisync --name {name} --resync")

    except Exception as e:
        console.print(f"[red]Error clearing bisync state: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("ls")
def ls_project_command(
    name: str = typer.Option(..., "--name", help="Project name to list files from"),
    path: str = typer.Argument(None, help="Path within project (optional)"),
) -> None:
    """List files in remote project.

    Examples:
      bm project ls --name research
      bm project ls --name research subfolder
    """
    config = ConfigManager().config
    if not config.cloud_mode_enabled:
        console.print("[red]Error: ls only available in cloud mode[/red]")
        raise typer.Exit(1)

    try:
        # Get tenant info for bucket name
        tenant_info = asyncio.run(get_mount_info())
        bucket_name = tenant_info.bucket_name

        # Get project info
        async def _get_project():
            async with get_client() as client:
                response = await call_get(client, "/projects/projects")
                projects_list = ProjectList.model_validate(response.json())
                for proj in projects_list.projects:
                    if generate_permalink(proj.name) == generate_permalink(name):
                        return proj
                return None

        project_data = asyncio.run(_get_project())
        if not project_data:
            console.print(f"[red]Error: Project '{name}' not found[/red]")
            raise typer.Exit(1)

        # Create SyncProject (local_sync_path not needed for ls)
        sync_project = SyncProject(
            name=project_data.name,
            path=normalize_project_path(project_data.path),
        )

        # List files
        files = project_ls(sync_project, bucket_name, path=path)

        if files:
            console.print(f"\n[bold]Files in {name}" + (f"/{path}" if path else "") + ":[/bold]")
            for file in files:
                console.print(f"  {file}")
            console.print(f"\n[dim]Total: {len(files)} files[/dim]")
        else:
            console.print(
                f"[yellow]No files found in {name}" + (f"/{path}" if path else "") + "[/yellow]"
            )

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@project_app.command("info")
def display_project_info(
    name: str = typer.Argument(..., help="Name of the project"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """Display detailed information and statistics about the current project."""
    try:
        # Get project info
        info = asyncio.run(get_project_info(name))

        if json_output:
            # Convert to JSON and print
            print(json.dumps(info.model_dump(), indent=2, default=str))
        else:
            # Project configuration section
            console.print(
                Panel(
                    f"Basic Memory version: [bold green]{info.system.version}[/bold green]\n"
                    f"[bold]Project:[/bold] {info.project_name}\n"
                    f"[bold]Path:[/bold] {info.project_path}\n"
                    f"[bold]Default Project:[/bold] {info.default_project}\n",
                    title="Basic Memory Project Info",
                    expand=False,
                )
            )

            # Statistics section
            stats_table = Table(title="Statistics")
            stats_table.add_column("Metric", style="cyan")
            stats_table.add_column("Count", style="green")

            stats_table.add_row("Entities", str(info.statistics.total_entities))
            stats_table.add_row("Observations", str(info.statistics.total_observations))
            stats_table.add_row("Relations", str(info.statistics.total_relations))
            stats_table.add_row(
                "Unresolved Relations", str(info.statistics.total_unresolved_relations)
            )
            stats_table.add_row("Isolated Entities", str(info.statistics.isolated_entities))

            console.print(stats_table)

            # Entity types
            if info.statistics.entity_types:
                entity_types_table = Table(title="Entity Types")
                entity_types_table.add_column("Type", style="blue")
                entity_types_table.add_column("Count", style="green")

                for entity_type, count in info.statistics.entity_types.items():
                    entity_types_table.add_row(entity_type, str(count))

                console.print(entity_types_table)

            # Most connected entities
            if info.statistics.most_connected_entities:  # pragma: no cover
                connected_table = Table(title="Most Connected Entities")
                connected_table.add_column("Title", style="blue")
                connected_table.add_column("Permalink", style="cyan")
                connected_table.add_column("Relations", style="green")

                for entity in info.statistics.most_connected_entities:
                    connected_table.add_row(
                        entity["title"], entity["permalink"], str(entity["relation_count"])
                    )

                console.print(connected_table)

            # Recent activity
            if info.activity.recently_updated:  # pragma: no cover
                recent_table = Table(title="Recent Activity")
                recent_table.add_column("Title", style="blue")
                recent_table.add_column("Type", style="cyan")
                recent_table.add_column("Last Updated", style="green")

                for entity in info.activity.recently_updated[:5]:  # Show top 5
                    updated_at = (
                        datetime.fromisoformat(entity["updated_at"])
                        if isinstance(entity["updated_at"], str)
                        else entity["updated_at"]
                    )
                    recent_table.add_row(
                        entity["title"],
                        entity["entity_type"],
                        updated_at.strftime("%Y-%m-%d %H:%M"),
                    )

                console.print(recent_table)

            # Available projects
            projects_table = Table(title="Available Projects")
            projects_table.add_column("Name", style="blue")
            projects_table.add_column("Path", style="cyan")
            projects_table.add_column("Default", style="green")

            for name, proj_info in info.available_projects.items():
                is_default = name == info.default_project
                project_path = proj_info["path"]
                projects_table.add_row(name, project_path, "[X]" if is_default else "")

            console.print(projects_table)

            # Timestamp
            current_time = (
                datetime.fromisoformat(str(info.system.timestamp))
                if isinstance(info.system.timestamp, str)
                else info.system.timestamp
            )
            console.print(f"\nTimestamp: [cyan]{current_time.strftime('%Y-%m-%d %H:%M:%S')}[/cyan]")

    except Exception as e:  # pragma: no cover
        typer.echo(f"Error getting project info: {e}", err=True)
        raise typer.Exit(1)
