"""Command module for basic-memory project management."""

import json
import os
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from basic_memory.cli.app import app
from basic_memory.cli.auth import CLIAuth
from basic_memory.cli.commands.cloud.bisync_commands import get_mount_info
from basic_memory.cli.commands.cloud.project_sync import (
    _has_cloud_credentials,
    _require_cloud_credentials,
)
from basic_memory.cli.commands.cloud.rclone_commands import (
    SyncProject,
    project_ls,
)
from basic_memory.cli.commands.command_utils import get_project_info, run_with_cleanup
from basic_memory.cli.commands.routing import force_routing, validate_routing_flags
from basic_memory.config import ConfigManager, ProjectEntry, ProjectMode
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import ProjectClient
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.utils import generate_permalink, normalize_project_path

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
def list_projects(
    local: bool = typer.Option(False, "--local", help="Force local routing for this command"),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
    workspace: str = typer.Option(None, "--workspace", help="Cloud workspace name or tenant_id"),
) -> None:
    """List Basic Memory projects from local and (when available) cloud."""
    try:
        validate_routing_flags(local, cloud)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    async def _list_projects(ws: str | None = None):
        async with get_client(workspace=ws) as client:
            return await ProjectClient(client).list_projects()

    try:
        config = ConfigManager().config
        # Use explicit workspace, fall back to config default
        effective_workspace = workspace or config.default_workspace

        local_result: ProjectList | None = None
        cloud_result: ProjectList | None = None
        cloud_error: Exception | None = None

        if cloud:
            with force_routing(cloud=True):
                cloud_result = run_with_cleanup(_list_projects(effective_workspace))
        elif local:
            with force_routing(local=True):
                local_result = run_with_cleanup(_list_projects())
        else:
            # Default behavior: always show local projects first.
            with force_routing(local=True):
                local_result = run_with_cleanup(_list_projects())

            if _has_cloud_credentials(config):
                try:
                    with force_routing(cloud=True):
                        cloud_result = run_with_cleanup(_list_projects(effective_workspace))
                except Exception as exc:  # pragma: no cover
                    cloud_error = exc

        # Resolve workspace name for cloud projects (best-effort)
        cloud_ws_name: str | None = None
        cloud_ws_type: str | None = None
        if cloud_result and effective_workspace:
            try:
                from basic_memory.mcp.project_context import get_available_workspaces

                workspaces = run_with_cleanup(get_available_workspaces())
                matched = next(
                    (ws for ws in workspaces if ws.tenant_id == effective_workspace),
                    None,
                )
                if matched:
                    cloud_ws_name = matched.name
                    cloud_ws_type = matched.workspace_type
            except Exception:
                pass

        table = Table(title="Basic Memory Projects")
        table.add_column("Name", style="cyan")
        table.add_column("Local Path", style="yellow", no_wrap=True, overflow="fold")
        table.add_column("Cloud Path", style="green")
        table.add_column("Workspace", style="green")
        table.add_column("CLI Route", style="blue")
        table.add_column("MCP (stdio)", style="blue")
        table.add_column("Sync", style="green")
        table.add_column("Default", style="magenta")

        project_names_by_permalink: dict[str, str] = {}
        local_projects_by_permalink: dict[str, ProjectItem] = {}
        cloud_projects_by_permalink: dict[str, ProjectItem] = {}

        if local_result:
            for project in local_result.projects:
                permalink = generate_permalink(project.name)
                project_names_by_permalink[permalink] = project.name
                local_projects_by_permalink[permalink] = project

        if cloud_result:
            for project in cloud_result.projects:
                permalink = generate_permalink(project.name)
                project_names_by_permalink[permalink] = project.name
                cloud_projects_by_permalink[permalink] = project

        for permalink in sorted(project_names_by_permalink):
            project_name = project_names_by_permalink[permalink]
            local_project = local_projects_by_permalink.get(permalink)
            cloud_project = cloud_projects_by_permalink.get(permalink)
            entry = config.projects.get(project_name)

            local_path = ""
            if local_project is not None:
                local_path = format_path(normalize_project_path(local_project.path))
            elif entry and entry.local_sync_path:
                local_path = format_path(entry.local_sync_path)
            elif entry and entry.mode == ProjectMode.LOCAL and entry.path:
                local_path = format_path(normalize_project_path(entry.path))

            cloud_path = ""
            if cloud_project is not None:
                cloud_path = normalize_project_path(cloud_project.path)

            if local:
                cli_route = "local (flag)"
            elif cloud:
                cli_route = "cloud (flag)"
            elif entry:
                cli_route = entry.mode.value
            elif cloud_project is not None and local_project is None:
                cli_route = ProjectMode.CLOUD.value
            else:
                cli_route = ProjectMode.LOCAL.value

            is_default = "[X]" if config.default_project == project_name else ""

            has_sync = "[X]" if entry and entry.local_sync_path else ""
            mcp_stdio_target = "local" if local_project is not None else "n/a"

            # Show workspace name (type) for cloud-sourced projects
            ws_label = ""
            if cloud_project is not None and cloud_ws_name:
                ws_label = f"{cloud_ws_name} ({cloud_ws_type})" if cloud_ws_type else cloud_ws_name

            row = [
                project_name,
                local_path,
                cloud_path,
                ws_label,
                cli_route,
                mcp_stdio_target,
                has_sync,
                is_default,
            ]

            table.add_row(*row)

        console.print(table)
        if cloud_error is not None:
            console.print(
                "[yellow]Cloud project discovery failed. "
                "Showing local projects only. Run 'bm cloud login' or 'bm cloud api-key save <key>'.[/yellow]"
            )
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
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
) -> None:
    """Add a new project.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.

    Cloud mode examples:\n
        bm project add research                           # No local sync\n
        bm project add research --local-path ~/docs       # With local sync\n

    Local mode example:\n
        bm project add research ~/Documents/research
    """
    try:
        validate_routing_flags(local, cloud)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    config = ConfigManager().config

    # Determine effective mode: default local, cloud only when explicitly requested.
    effective_cloud_mode = cloud and not local

    # Resolve local sync path early (needed for both cloud and local mode)
    local_sync_path: str | None = None
    if local_path:
        local_sync_path = Path(os.path.abspath(os.path.expanduser(local_path))).as_posix()

    if effective_cloud_mode:
        _require_cloud_credentials(config)
        # Cloud mode: path auto-generated from name, local sync is optional

        async def _add_project():
            async with get_client() as client:
                data = {
                    "name": name,
                    "path": generate_permalink(name),
                    "local_sync_path": local_sync_path,
                    "set_default": set_default,
                }
                return await ProjectClient(client).create_project(data)
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
                return await ProjectClient(client).create_project(data)

    try:
        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(_add_project())
        console.print(f"[green]{result.message}[/green]")

        # Save local sync path to config if in cloud mode
        if effective_cloud_mode and local_sync_path:
            # Create local directory if it doesn't exist
            local_dir = Path(local_sync_path)
            local_dir.mkdir(parents=True, exist_ok=True)

            # Update project entry with sync path
            entry = config.projects.get(name)
            if entry:
                entry.local_sync_path = local_sync_path
            else:
                # Project may not be in local config yet (cloud-only add)
                config.projects[name] = ProjectEntry(
                    path=local_sync_path,
                    local_sync_path=local_sync_path,
                )
            ConfigManager().save_config(config)

            console.print(f"\n[green]Local sync path configured: {local_sync_path}[/green]")
            console.print("\nNext steps:")
            console.print(f"  1. Preview: bm cloud bisync --name {name} --resync --dry-run")
            console.print(f"  2. Sync: bm cloud bisync --name {name} --resync")
    except Exception as e:
        console.print(f"[red]Error adding project: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("remove")
def remove_project(
    name: str = typer.Argument(..., help="Name of the project to remove"),
    delete_notes: bool = typer.Option(
        False, "--delete-notes", help="Delete project files from disk"
    ),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
) -> None:
    """Remove a project.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    try:
        validate_routing_flags(local, cloud)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    async def _remove_project():
        # Resolve workspace so cloud-only projects auto-route without --cloud
        config = ConfigManager().config
        entry = config.projects.get(name)
        ws = None
        if entry and entry.workspace_id:
            ws = entry.workspace_id
        elif config.default_workspace:
            ws = config.default_workspace

        async with get_client(project_name=name, workspace=ws) as client:
            project_client = ProjectClient(client)
            # Convert name to permalink for efficient resolution
            project_permalink = generate_permalink(name)
            target_project = await project_client.resolve_project(project_permalink)
            return await project_client.delete_project(
                target_project.external_id, delete_notes=delete_notes
            )

    try:
        # Get config to check for local sync path and bisync state
        config = ConfigManager().config
        local_path_config = None
        has_bisync_state = False

        entry = config.projects.get(name)
        if cloud and entry and entry.local_sync_path:
            local_path_config = entry.local_sync_path

            # Check for bisync state
            from basic_memory.cli.commands.cloud.rclone_commands import get_project_bisync_state

            bisync_state_path = get_project_bisync_state(name)
            has_bisync_state = bisync_state_path.exists()

        # Remove project from cloud/API
        with force_routing(local=local, cloud=cloud):
            result = run_with_cleanup(_remove_project())
        console.print(f"[green]{result.message}[/green]")

        # Clean up local sync directory if it exists and delete_notes is True
        if delete_notes and local_path_config:
            local_dir = Path(local_path_config)
            if local_dir.exists():
                import shutil

                shutil.rmtree(local_dir)
                console.print(f"[green]Removed local sync directory: {local_path_config}[/green]")

        # Clean up bisync state if it exists
        if has_bisync_state:
            from basic_memory.cli.commands.cloud.rclone_commands import get_project_bisync_state
            import shutil

            bisync_state_path = get_project_bisync_state(name)
            if bisync_state_path.exists():
                shutil.rmtree(bisync_state_path)
                console.print("[green]Removed bisync state[/green]")

        # Clean up cloud sync fields on the project entry
        if cloud and entry and entry.local_sync_path:
            entry.local_sync_path = None
            entry.bisync_initialized = False
            entry.last_sync = None
            ConfigManager().save_config(config)

        # Show informative message if files were not deleted
        if not delete_notes:
            if local_path_config:
                console.print(f"[yellow]Note: Local files remain at {local_path_config}[/yellow]")

    except Exception as e:
        console.print(f"[red]Error removing project: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("default")
def set_default_project(
    name: str = typer.Argument(..., help="Name of the project to set as CLI default"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (required in cloud mode)"
    ),
) -> None:
    """Set the default project used as fallback when no project is specified.

    In cloud mode, use --local to modify the local configuration.
    """

    async def _set_default():
        # Resolve workspace so cloud-only projects auto-route without flags
        config = ConfigManager().config
        entry = config.projects.get(name)
        ws = None
        if entry and entry.workspace_id:
            ws = entry.workspace_id
        elif config.default_workspace:
            ws = config.default_workspace

        async with get_client(project_name=name, workspace=ws) as client:
            project_client = ProjectClient(client)
            # Convert name to permalink for efficient resolution
            project_permalink = generate_permalink(name)
            target_project = await project_client.resolve_project(project_permalink)
            return await project_client.set_default(target_project.external_id)

    try:
        with force_routing(local=local):
            result = run_with_cleanup(_set_default())
        console.print(f"[green]{result.message}[/green]")
    except Exception as e:
        console.print(f"[red]Error setting default project: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("move")
def move_project(
    name: str = typer.Argument(..., help="Name of the project to move"),
    new_path: str = typer.Argument(..., help="New absolute path for the project"),
) -> None:
    """Move a local project to a new filesystem location.

    This command only applies to local projects — it updates the project's
    configured path in the local database.
    """
    # Resolve to absolute path
    resolved_path = Path(os.path.abspath(os.path.expanduser(new_path))).as_posix()

    async def _move_project():
        async with get_client() as client:
            project_client = ProjectClient(client)
            project_info = await project_client.resolve_project(name)
            return await project_client.update_project(
                project_info.external_id, {"path": resolved_path}
            )

    try:
        with force_routing(local=True):
            result = run_with_cleanup(_move_project())
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


@project_app.command("set-cloud")
def set_cloud(
    name: str = typer.Argument(..., help="Name of the project to route through cloud"),
    workspace: str = typer.Option(
        None,
        "--workspace",
        help="Cloud workspace name or tenant_id to associate with this project",
    ),
) -> None:
    """Set a project to cloud mode (route through cloud API).

    Requires either an API key or an active OAuth session.

    Use --workspace to associate a specific workspace with this project.
    If omitted, uses the default workspace (if set) or auto-selects when
    only one workspace is available.

    Examples:
      bm project set-cloud research --workspace Personal
      bm project set-cloud research --workspace 11111111-...
      bm project set-cloud research   # uses default workspace
    """

    config_manager = ConfigManager()
    config = config_manager.config

    # Validate project exists in config
    if name not in config.projects:
        console.print(f"[red]Error: Project '{name}' not found in config[/red]")
        raise typer.Exit(1)

    # Validate credentials: API key or OAuth session
    has_api_key = bool(config.cloud_api_key)
    has_oauth = False
    if not has_api_key:
        auth = CLIAuth(client_id=config.cloud_client_id, authkit_domain=config.cloud_domain)
        has_oauth = auth.load_tokens() is not None

    if not has_api_key and not has_oauth:
        console.print("[red]Error: No cloud credentials found[/red]")
        console.print("[dim]Run 'bm cloud api-key save <key>' or 'bm cloud login' first[/dim]")
        raise typer.Exit(1)

    # --- Resolve workspace to tenant_id ---
    resolved_workspace_id: str | None = None

    if workspace is not None:
        # Explicit --workspace: resolve to tenant_id via cloud lookup
        from basic_memory.mcp.project_context import (
            get_available_workspaces,
            _workspace_matches_identifier,
            _workspace_choices,
        )

        workspaces = run_with_cleanup(get_available_workspaces())
        matches = [ws for ws in workspaces if _workspace_matches_identifier(ws, workspace)]
        if not matches:
            console.print(f"[red]Error: Workspace '{workspace}' not found[/red]")
            if workspaces:
                console.print(f"[dim]Available:\n{_workspace_choices(workspaces)}[/dim]")
            raise typer.Exit(1)
        if len(matches) > 1:
            console.print(
                f"[red]Error: Workspace name '{workspace}' matches multiple workspaces. "
                f"Use tenant_id instead.[/red]"
            )
            console.print(f"[dim]Available:\n{_workspace_choices(workspaces)}[/dim]")
            raise typer.Exit(1)
        resolved_workspace_id = matches[0].tenant_id
    elif config.default_workspace:
        # Fall back to global default
        resolved_workspace_id = config.default_workspace
    else:
        # Try auto-select if single workspace
        try:
            from basic_memory.mcp.project_context import get_available_workspaces

            workspaces = run_with_cleanup(get_available_workspaces())
            if len(workspaces) == 1:
                resolved_workspace_id = workspaces[0].tenant_id
        except Exception:
            pass  # Workspace resolution is optional at set-cloud time

    config.set_project_mode(name, ProjectMode.CLOUD)
    if resolved_workspace_id:
        config.projects[name].workspace_id = resolved_workspace_id
    config_manager.save_config(config)

    console.print(f"[green]Project '{name}' set to cloud mode[/green]")
    if resolved_workspace_id:
        console.print(f"[dim]Workspace: {resolved_workspace_id}[/dim]")
    console.print("[dim]MCP tools and CLI commands for this project will route through cloud[/dim]")


@project_app.command("set-local")
def set_local(
    name: str = typer.Argument(..., help="Name of the project to revert to local mode"),
) -> None:
    """Revert a project to local mode (use in-process ASGI transport).

    Clears any associated cloud workspace.

    Example:
      bm project set-local research
    """
    config_manager = ConfigManager()
    config = config_manager.config

    # Validate project exists in config
    if name not in config.projects:
        console.print(f"[red]Error: Project '{name}' not found in config[/red]")
        raise typer.Exit(1)

    config.set_project_mode(name, ProjectMode.LOCAL)
    config.projects[name].workspace_id = None
    config_manager.save_config(config)

    console.print(f"[green]Project '{name}' set to local mode[/green]")
    console.print("[dim]MCP tools and CLI commands for this project will use local transport[/dim]")


@project_app.command("ls")
def ls_project_command(
    name: str = typer.Option(..., "--name", help="Project name to list files from"),
    path: str = typer.Argument(None, help="Path within project (optional)"),
    local: bool = typer.Option(False, "--local", help="List files from local project instance"),
    cloud: bool = typer.Option(False, "--cloud", help="List files from cloud project instance"),
) -> None:
    """List files in a project.

    Examples:
      bm project ls --name research
      bm project ls --name research --local
      bm project ls --name research --cloud
      bm project ls --name research subfolder
    """
    try:
        validate_routing_flags(local, cloud)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    # Determine routing: explicit flags take precedence, otherwise check project mode
    if cloud or local:
        use_cloud_route = cloud and not local
    else:
        config = ConfigManager().config
        project_mode = config.get_project_mode(name)
        use_cloud_route = project_mode == ProjectMode.CLOUD

    def _list_local_files(project_path: str, subpath: str | None = None) -> list[str]:
        project_root = Path(normalize_project_path(project_path)).expanduser().resolve()
        target_dir = project_root

        if subpath:
            requested = Path(subpath)
            if requested.is_absolute():
                raise ValueError("Path must be relative to the project root")
            target_dir = (project_root / requested).resolve()
            if not target_dir.is_relative_to(project_root):
                raise ValueError("Path must stay within the project root")

        if not target_dir.exists():
            raise ValueError(f"Path not found: {target_dir}")
        if not target_dir.is_dir():
            raise ValueError(f"Path is not a directory: {target_dir}")

        files: list[str] = []
        for file_path in sorted(target_dir.rglob("*")):
            if file_path.is_file():
                size = file_path.stat().st_size
                relative = file_path.relative_to(project_root).as_posix()
                files.append(f"{size:10d} {relative}")

        return files

    try:
        # Get project info
        async def _get_project():
            async with get_client() as client:
                projects_list = await ProjectClient(client).list_projects()
                for proj in projects_list.projects:
                    if generate_permalink(proj.name) == generate_permalink(name):
                        return proj
                return None

        if use_cloud_route:
            config = ConfigManager().config
            _require_cloud_credentials(config)

            tenant_info = run_with_cleanup(get_mount_info())
            bucket_name = tenant_info.bucket_name

            with force_routing(cloud=True):
                project_data = run_with_cleanup(_get_project())
            if not project_data:
                console.print(f"[red]Error: Project '{name}' not found[/red]")
                raise typer.Exit(1)

            sync_project = SyncProject(
                name=project_data.name,
                path=normalize_project_path(project_data.path),
            )
            files = project_ls(sync_project, bucket_name, path=path)
            target_label = "CLOUD"
        else:
            with force_routing(local=True):
                project_data = run_with_cleanup(_get_project())
            if not project_data:
                console.print(f"[red]Error: Project '{name}' not found[/red]")
                raise typer.Exit(1)

            # For cloud-mode projects accessed with --local, use local_sync_path
            # (the actual local directory) instead of project_data.path from the API
            local_dir = project_data.path
            if local:
                entry = ConfigManager().config.projects.get(name)
                if entry and entry.local_sync_path:
                    local_dir = entry.local_sync_path

            files = _list_local_files(local_dir, path)
            target_label = "LOCAL"

        if files:
            heading = f"\n[bold]Files in {name} ({target_label})"
            if path:
                heading += f"/{path}"
            heading += ":[/bold]"
            console.print(heading)
            for file in files:
                console.print(f"  {file}")
            console.print(f"\n[dim]Total: {len(files)} files[/dim]")
        else:
            prefix = f"[yellow]No files found in {name} ({target_label})"
            console.print(prefix + (f"/{path}" if path else "") + "[/yellow]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@project_app.command("info")
def display_project_info(
    name: str = typer.Argument(..., help="Name of the project"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
    local: bool = typer.Option(
        False, "--local", help="Force local API routing (ignore cloud mode)"
    ),
    cloud: bool = typer.Option(False, "--cloud", help="Force cloud API routing"),
):
    """Display detailed information and statistics about the current project.

    Use --local to force local routing when cloud mode is enabled.
    Use --cloud to force cloud routing when cloud mode is disabled.
    """
    try:
        validate_routing_flags(local, cloud)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    try:
        # Get project info
        with force_routing(local=local, cloud=cloud):
            info = run_with_cleanup(get_project_info(name))

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

            # Note types
            if info.statistics.note_types:
                note_types_table = Table(title="Note Types")
                note_types_table.add_column("Type", style="blue")
                note_types_table.add_column("Count", style="green")

                for note_type, count in info.statistics.note_types.items():
                    note_types_table.add_row(note_type, str(count))

                console.print(note_types_table)

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
                        entity["note_type"],
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

    except typer.Exit:
        raise
    except Exception as e:  # pragma: no cover
        typer.echo(f"Error getting project info: {e}", err=True)
        raise typer.Exit(1)
