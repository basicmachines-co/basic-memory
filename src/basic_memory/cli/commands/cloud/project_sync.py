"""Cloud sync commands for Basic Memory projects.

Commands for syncing, bisyncing, and checking integrity between local and cloud
project instances. These were previously in project.py but belong here since
they are cloud-specific operations.
"""

import os
from datetime import datetime
from enum import Enum

import typer
from rich.console import Console

from basic_memory.cli.app import cloud_app
from basic_memory.cli.commands.cloud.bisync_commands import get_mount_info
from basic_memory.cli.commands.cloud.rclone_commands import (
    RcloneError,
    SyncProject,
    TransferDirection,
    TransferPlan,
    get_project_bisync_state,
    project_bisync,
    project_check,
    project_diff,
    project_sync,
    project_transfer,
)
from basic_memory.cli.commands.cloud.rclone_config import (
    DEFAULT_RCLONE_REMOTE,
    rclone_remote_exists,
    remote_name_for_workspace,
)
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.routing import force_routing
from basic_memory.config import BasicMemoryConfig, ConfigManager, ProjectEntry
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import ProjectClient
from basic_memory.mcp.project_context import get_available_workspaces
from basic_memory.schemas.cloud import (
    WorkspaceInfo,
    format_workspace_choices,
    format_workspace_selection_choices,
    workspace_matches_exact_identifier,
)
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.utils import generate_permalink, normalize_project_path

console = Console()

TEAM_WORKSPACE_BISYNC_UNSUPPORTED = (
    "The bisync operation is only supported on Personal workspaces.\n"
    "Use `bm cloud pull --name {name}` / `bm cloud push --name {name}` instead."
)

TEAM_WORKSPACE_SYNC_UNSUPPORTED = (
    "The sync operation mirrors local onto the shared bucket and can delete a "
    "teammate's files, so it is only supported on Personal workspaces.\n"
    "Use `bm cloud pull --name {name}` (fetch) / `bm cloud push --name {name}` "
    "(additive upload) instead."
)


class ConflictStrategy(str, Enum):
    """How push/pull resolves files that differ on both sides.

    Default is ``fail``: surface the conflicts and abort before transferring,
    leaving the user to re-run with an explicit resolution — like git refusing
    to clobber local changes.

    This is the Typer-facing enum; the engine in ``rclone_commands`` accepts the
    same values as a ``ConflictStrategy`` Literal. ``_run_directional_transfer``
    bridges the two by passing ``on_conflict.value``. Keep the values in sync.
    """

    fail = "fail"
    keep_local = "keep-local"
    keep_cloud = "keep-cloud"
    keep_both = "keep-both"


# --- Shared helpers ---


def _has_cloud_credentials(config: BasicMemoryConfig) -> bool:
    """Return whether cloud credentials are available (API key or OAuth token)."""
    from basic_memory.config import has_cloud_credentials

    return has_cloud_credentials(config)


def _require_cloud_credentials(config: BasicMemoryConfig) -> None:
    """Exit with actionable guidance when cloud credentials are missing."""
    if _has_cloud_credentials(config):
        return

    console.print("[red]Error: cloud credentials are required for this command[/red]")
    console.print("[dim]Run 'bm cloud login' or 'bm cloud api-key save <key>' first[/dim]")
    raise typer.Exit(1)


async def _get_workspace_for_project(
    name: str,
    config: BasicMemoryConfig,
    *,
    workspace_override: str | None = None,
) -> WorkspaceInfo:
    """Resolve the cloud workspace targeted by a project-scoped sync command.

    ``workspace_override`` (a slug, name, or tenant_id, e.g. from ``--workspace``)
    takes precedence over config, letting the user disambiguate a project name
    that exists in more than one workspace.
    """
    workspaces = await get_available_workspaces()
    if not workspaces:
        raise ValueError("No accessible cloud workspaces found for this account")

    # An explicit override wins over config — this is how the user disambiguates.
    if workspace_override is not None:
        matches = [
            item
            for item in workspaces
            if workspace_matches_exact_identifier(item, workspace_override)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(
                f"No accessible workspace matches '{workspace_override}'.\n"
                f"{format_workspace_choices(workspaces)}"
            )
        raise ValueError(
            f"'{workspace_override}' matches multiple workspaces; use a slug or tenant_id:\n"
            f"{format_workspace_selection_choices(matches)}"
        )

    entry = config.projects.get(name)
    workspace_id = entry.workspace_id if entry and entry.workspace_id else config.default_workspace
    if workspace_id:
        workspace = next(
            (item for item in workspaces if item.tenant_id == workspace_id),
            None,
        )
        if workspace is None:
            raise ValueError(
                f"Configured workspace '{workspace_id}' for project '{name}' is not accessible"
            )
        return workspace

    default_workspaces = [item for item in workspaces if item.is_default]
    if len(default_workspaces) == 1:
        return default_workspaces[0]

    if len(workspaces) == 1:
        return workspaces[0]

    raise ValueError(
        f"Project '{name}' does not have an unambiguous cloud workspace. "
        "Set a default workspace with `bm cloud workspace set-default <workspace>` "
        "or attach the project with `bm project set-cloud <name> --workspace <workspace>`."
    )


def _require_personal_workspace(
    name: str,
    config: BasicMemoryConfig,
    *,
    unsupported_message: str = TEAM_WORKSPACE_BISYNC_UNSUPPORTED,
) -> WorkspaceInfo:
    """Exit before mirror work when the target workspace is not personal.

    Used to gate the destructive mirror operations (`sync`, `bisync`) to
    Personal workspaces. ``unsupported_message`` lets each command point Team
    users at the right Team-safe alternative.
    """
    try:
        workspace = run_with_cleanup(_get_workspace_for_project(name, config))
    except Exception as exc:
        console.print(f"[red]Error resolving workspace for project '{name}': {exc}[/red]")
        raise typer.Exit(1)

    if workspace.workspace_type != "personal":
        console.print(f"[red]{unsupported_message.format(name=name)}[/red]")
        raise typer.Exit(1)

    return workspace


async def _get_cloud_project(name: str, *, workspace_id: str | None = None) -> ProjectItem | None:
    """Fetch a project by name from the cloud API.

    ``workspace_id`` routes the lookup to a specific tenant so the project
    metadata comes from the same workspace the transfer targets (otherwise
    get_client would resolve the workspace from config/default and could read a
    different tenant — see #920 review).
    """
    async with get_client(project_name=name, workspace=workspace_id) as client:
        projects_list = await ProjectClient(client).list_projects()
        for proj in projects_list.projects:
            if generate_permalink(proj.name) == generate_permalink(name):
                return proj
        return None


def _get_sync_project(
    name: str,
    config: BasicMemoryConfig,
    project_data: ProjectItem,
    *,
    remote_name: str = DEFAULT_RCLONE_REMOTE,
) -> tuple[SyncProject, str | None]:
    """Build a SyncProject and resolve local_sync_path from config.

    ``remote_name`` selects which tenant-scoped rclone remote the project routes
    through (default tenant vs a team workspace remote).

    Returns (sync_project, local_sync_path). Exits if no local_sync_path configured.
    """
    sync_entry = config.projects.get(name)
    # Support both new (path) and legacy (local_sync_path) configs
    local_sync_path = (sync_entry.local_sync_path or sync_entry.path) if sync_entry else None

    if not local_sync_path or not os.path.isabs(local_sync_path):
        console.print(f"[red]Error: Project '{name}' has no local sync path configured[/red]")
        console.print(f"\nConfigure sync with: bm cloud sync-setup {name} ~/path/to/local")
        raise typer.Exit(1)

    sync_project = SyncProject(
        name=project_data.name,
        path=normalize_project_path(project_data.path),
        local_sync_path=local_sync_path,
        remote_name=remote_name,
    )
    return sync_project, local_sync_path


# --- Commands ---


@cloud_app.command("sync")
def sync_project_command(
    name: str = typer.Option(..., "--name", "--project", help="Project name to sync"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without syncing"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
) -> None:
    """One-way mirror: local -> cloud (make cloud identical to local).

    Personal workspaces only. This deletes cloud files not present locally, so
    on Team workspaces use `bm cloud push` (additive upload) / `bm cloud pull`
    (fetch) instead.

    Example:
      bm cloud sync --name research
      bm cloud sync --name research --dry-run
    """
    config = ConfigManager().config
    _require_cloud_credentials(config)
    _require_personal_workspace(name, config, unsupported_message=TEAM_WORKSPACE_SYNC_UNSUPPORTED)

    try:
        # Get tenant info for bucket name.
        # TODO(#919): scope to the project's workspace like push/pull. Safe for now
        # because these mirror commands are gated to the (default-tenant) Personal
        # workspace, so the default mount info is correct.
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
        else:
            console.print(f"[red]{name} sync failed[/red]")
            raise typer.Exit(1)

    except RcloneError as e:
        console.print(f"[red]Sync error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


def _print_conflict_abort(name: str, direction: TransferDirection, plan: TransferPlan) -> None:
    """Explain a conflict abort and how to resolve it (git-pull style)."""
    console.print(
        f"[red]{direction.capitalize()} aborted: {len(plan.conflicts)} file(s) differ between "
        f"local and cloud.[/red]"
    )
    for path in plan.conflicts:
        console.print(f"  [yellow]*[/yellow] {path}")
    console.print("\nRe-run with one of:")
    console.print("  [dim]--on-conflict keep-cloud[/dim]  take the cloud version")
    console.print("  [dim]--on-conflict keep-local[/dim]  keep your local version")
    console.print(
        "  [dim]--on-conflict keep-both[/dim]   keep both (writes <name>.conflict-<date>)"
    )


def _run_directional_transfer(
    name: str,
    direction: TransferDirection,
    *,
    on_conflict: ConflictStrategy,
    dry_run: bool,
    verbose: bool,
    workspace: str | None = None,
) -> None:
    """Shared orchestration for `bm cloud push` / `bm cloud pull`.

    Detects conflicts first, then aborts (the default) or applies the chosen
    resolution. Uses additive `rclone copy`, so it never deletes on the
    destination — safe for Team workspaces and therefore not gated.

    Routes through the resolved workspace's own tenant-scoped rclone remote, so a
    Team project reads/writes the right bucket (see #919).
    """
    config = ConfigManager().config
    _require_cloud_credentials(config)

    try:
        # --- Resolve the target workspace and its tenant-scoped remote ---
        # Tigris credentials are bucket/tenant-scoped, so each workspace has its
        # own rclone remote. Resolve which workspace this project belongs to
        # (config or --workspace override) before touching any bucket.
        try:
            target_workspace = run_with_cleanup(
                _get_workspace_for_project(name, config, workspace_override=workspace)
            )
        except Exception as exc:
            console.print(f"[red]Error resolving workspace for project '{name}': {exc}[/red]")
            raise typer.Exit(1)

        remote_name = remote_name_for_workspace(
            target_workspace.slug, is_default=target_workspace.is_default
        )

        # Trigger: the workspace's remote has not been configured yet.
        # Why: provisioning mints tenant-scoped credentials and must be explicit
        # (no surprise key generation); push/pull only transfer.
        # Outcome: stop with the exact setup command for this workspace.
        if not rclone_remote_exists(remote_name):
            setup_target = (
                "" if target_workspace.is_default else f" --workspace {target_workspace.slug}"
            )
            console.print(f"[red]Workspace '{target_workspace.slug}' is not set up for sync.[/red]")
            console.print(f"\nRun: bm cloud setup{setup_target}")
            raise typer.Exit(1)

        # Get tenant info for bucket name, scoped to the resolved workspace
        tenant_info = run_with_cleanup(get_mount_info(workspace_id=target_workspace.tenant_id))
        bucket_name = tenant_info.bucket_name

        # Get project info from the same workspace we resolved above, so the
        # project path and the bucket/remote all refer to one tenant.
        with force_routing(cloud=True):
            project_data = run_with_cleanup(
                _get_cloud_project(name, workspace_id=target_workspace.tenant_id)
            )
        if not project_data:
            console.print(f"[red]Error: Project '{name}' not found[/red]")
            raise typer.Exit(1)

        sync_project, _ = _get_sync_project(name, config, project_data, remote_name=remote_name)

        # --- Detect before transferring ---
        plan = project_diff(sync_project, bucket_name, direction)

        # Trigger: rclone could not read/hash some files.
        # Why: comparing is the whole basis for a safe transfer — never guess.
        # Outcome: abort before moving any bytes.
        if plan.errors:
            console.print(
                f"[red]{direction.capitalize()} aborted: rclone could not compare "
                f"{len(plan.errors)} file(s)[/red]"
            )
            for path in plan.errors:
                console.print(f"  [red]![/red] {path}")
            raise typer.Exit(1)

        # Trigger: files differ on both sides and the user chose no resolution.
        # Why: "no surprises" — never silently pick a winner.
        # Outcome: list the conflicts and exit, like git refusing to clobber.
        if plan.conflicts and on_conflict is ConflictStrategy.fail:
            _print_conflict_abort(name, direction, plan)
            raise typer.Exit(1)

        # --- Transfer ---
        arrow = "cloud -> local" if direction == "pull" else "local -> cloud"
        console.print(f"[blue]{direction.capitalize()} {name} ({arrow})...[/blue]")

        conflict_suffix = datetime.now().strftime("%Y%m%d-%H%M%S")
        success = project_transfer(
            sync_project,
            bucket_name,
            direction,
            plan,
            strategy=on_conflict.value,
            conflict_suffix=conflict_suffix,
            dry_run=dry_run,
            verbose=verbose,
        )

        if not success:
            console.print(f"[red]{name} {direction} failed[/red]")
            raise typer.Exit(1)

        console.print(f"[green]{name} {direction} completed successfully[/green]")

        # Without a sync baseline (see #862) we cannot tell an intentional delete
        # from a file the other side simply never had, so deletions never sync.
        if plan.dest_only:
            kept_on = "local" if direction == "pull" else "cloud"
            console.print(
                f"[dim]{len(plan.dest_only)} file(s) exist only on {kept_on} and were left "
                "untouched (deletions are not propagated).[/dim]"
            )

    except RcloneError as e:
        console.print(f"[red]{direction.capitalize()} error: {e}[/red]")
        raise typer.Exit(1)
    except typer.Exit:
        # Already-handled exits (not found, conflicts, errors) propagate cleanly.
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@cloud_app.command("pull")
def pull_project_command(
    name: str = typer.Option(..., "--name", "--project", help="Project name to pull"),
    on_conflict: ConflictStrategy = typer.Option(
        ConflictStrategy.fail,
        "--on-conflict",
        help="Resolve files that differ on both sides (default: fail and list them)",
    ),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        help="Workspace (slug, name, or tenant_id) when the project name is ambiguous",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without pulling"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
) -> None:
    """Fetch cloud changes into local (cloud -> local), git-pull style.

    Additive and Team-safe: downloads new/changed cloud files and never deletes
    local files. A file that differs on both sides is a conflict; by default
    pull aborts and lists them. Deletions are not propagated (see #862).

    Examples:
      bm cloud pull --name research
      bm cloud pull --name research --dry-run
      bm cloud pull --name research --on-conflict keep-cloud
      bm cloud pull --name research --workspace acme
    """
    _run_directional_transfer(
        name, "pull", on_conflict=on_conflict, dry_run=dry_run, verbose=verbose, workspace=workspace
    )


@cloud_app.command("push")
def push_project_command(
    name: str = typer.Option(..., "--name", "--project", help="Project name to push"),
    on_conflict: ConflictStrategy = typer.Option(
        ConflictStrategy.fail,
        "--on-conflict",
        help="Resolve files that differ on both sides (default: fail and list them)",
    ),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        help="Workspace (slug, name, or tenant_id) when the project name is ambiguous",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without pushing"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
) -> None:
    """Upload local changes to cloud (local -> cloud), additive and Team-safe.

    Uploads new/changed local files and never deletes cloud files. A file that
    differs on both sides is a conflict; by default push aborts and lists them
    (like git rejecting a push when the remote is ahead — pull first). Deletions
    are not propagated (see #862).

    Examples:
      bm cloud push --name research
      bm cloud push --name research --dry-run
      bm cloud push --name research --on-conflict keep-local
      bm cloud push --name research --workspace acme
    """
    _run_directional_transfer(
        name, "push", on_conflict=on_conflict, dry_run=dry_run, verbose=verbose, workspace=workspace
    )


@cloud_app.command("bisync")
def bisync_project_command(
    name: str = typer.Option(..., "--name", "--project", help="Project name to bisync"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without syncing"),
    resync: bool = typer.Option(False, "--resync", help="Force new baseline"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
) -> None:
    """Two-way mirror: local <-> cloud (bidirectional sync).

    Personal workspaces only. This mirror can delete and overwrite files on both
    sides, so on Team workspaces use `bm cloud pull` (fetch) / `bm cloud push`
    (additive upload) instead.

    Examples:
      bm cloud bisync --name research --resync  # First time
      bm cloud bisync --name research           # Subsequent syncs
      bm cloud bisync --name research --dry-run # Preview changes
    """
    config = ConfigManager().config
    _require_cloud_credentials(config)
    _require_personal_workspace(name, config)

    try:
        # Get tenant info for bucket name.
        # TODO(#919): scope to the project's workspace like push/pull. Safe for now
        # because these mirror commands are gated to the (default-tenant) Personal
        # workspace, so the default mount info is correct.
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
            if sync_entry is None:
                raise RuntimeError(
                    f"Sync entry for project '{name}' unexpectedly missing after validation"
                )
            sync_entry.last_sync = datetime.now()
            sync_entry.bisync_initialized = True
            ConfigManager().save_config(config)
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
    name: str = typer.Option(..., "--name", "--project", help="Project name to check"),
    one_way: bool = typer.Option(False, "--one-way", help="Check one direction only (faster)"),
) -> None:
    """Verify file integrity between local and cloud (no changes made).

    Personal workspaces only: check compares against the Personal workspace
    mirror remote. On Team workspaces use `bm cloud pull --dry-run` /
    `bm cloud push --dry-run` to preview differences instead.

    Example:
      bm cloud check --name research
    """
    config = ConfigManager().config
    _require_cloud_credentials(config)

    try:
        # Get tenant info for bucket name.
        # TODO(#919): scope to the project's workspace like push/pull. Safe for now
        # because these mirror commands are gated to the (default-tenant) Personal
        # workspace, so the default mount info is correct.
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

    Personal workspaces only (bisync is a Personal-workspace mirror; on Team
    workspaces use `bm cloud pull` / `bm cloud push` instead).

    This removes the bisync metadata files, forcing a fresh --resync on next bisync.
    Useful when bisync gets into an inconsistent state or when remote path changes.
    """
    import shutil

    config = ConfigManager().config
    if _has_cloud_credentials(config):
        _require_personal_workspace(name, config)

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
        async with get_client(project_name=name) as client:
            projects_list = await ProjectClient(client).list_projects()
            project_names = [p.name for p in projects_list.projects]
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

        # Update project entry with sync path — path is always the local directory
        entry = config.projects.get(name)
        if entry:
            entry.path = resolved_path.as_posix()
            entry.local_sync_path = resolved_path.as_posix()
            entry.bisync_initialized = False
            entry.last_sync = None
        else:
            config.projects[name] = ProjectEntry(
                path=resolved_path.as_posix(),
                local_sync_path=resolved_path.as_posix(),
            )
        config_manager.save_config(config)

        # Create the project in the local DB so the MCP server can immediately use it
        async def _create_local_project():
            async with get_client() as client:
                data = {"name": name, "path": resolved_path.as_posix(), "set_default": False}
                return await ProjectClient(client).create_project(data)

        with force_routing(local=True):
            try:
                run_with_cleanup(_create_local_project())
            except Exception:
                pass  # Project may already exist locally; reconcile on next startup

        console.print(f"[green]Sync configured for project '{name}'[/green]")
        console.print(f"\nLocal sync path: {resolved_path}")
        # Lead with the Team-safe additive commands (work on any workspace); the
        # `sync`/`bisync` mirrors are Personal-workspace-only.
        console.print("\nNext steps:")
        console.print(f"  1. Preview a pull: bm cloud pull --name {name} --dry-run")
        console.print(f"  2. Fetch from cloud: bm cloud pull --name {name}")
        console.print(f"  3. Upload local changes: bm cloud push --name {name}")
        console.print(
            f"  Personal workspaces can also mirror with: bm cloud bisync --name {name} --resync"
        )
    except Exception as e:
        console.print(f"[red]Error configuring sync: {str(e)}[/red]")
        raise typer.Exit(1)
