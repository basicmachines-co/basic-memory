"""Cloud transfer commands for Basic Memory projects.

`bm cloud pull` / `bm cloud push` are the way to move files between a local
project and Basic Memory Cloud, on Personal and Team workspaces alike. The
rclone mirror commands (`sync`, `bisync`, `bisync-reset`) were removed in 1.0
(#1845); `removed_mirror_commands` keeps their names as migration stubs.
"""

import os
import shlex
from datetime import datetime
from enum import Enum
from pathlib import Path

import typer
from rich.console import Console

from basic_memory.cli.app import cloud_app
from basic_memory.cli.commands.cloud.mount_api import get_mount_info
from basic_memory.cli.commands.cloud.rclone_commands import (
    RcloneError,
    SyncProject,
    get_bmignore_prune_filter_path,
    project_check,
    project_diff,
    project_prune,
    project_prune_preview,
    project_transfer,
)
from basic_memory.ignore_utils import get_bmignore_path
from basic_memory.utils import shell_command
from basic_memory.cli.commands.cloud.rclone_config import (
    DEFAULT_RCLONE_REMOTE,
    rclone_remote_exists,
    remote_name_for_workspace,
)
from basic_memory.cli.commands.cloud.transfer import TransferDirection, TransferPlan
from basic_memory.cli.commands.cloud.webdav import WebdavError
from basic_memory.cli.commands.cloud.webdav_transfer import (
    webdav_project_diff,
    webdav_project_transfer,
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
from basic_memory.cli.markup import literal

console = Console()

TEAM_WORKSPACE_PRUNE_UNSUPPORTED = (
    "The prune operation deletes files from the shared bucket based on your "
    "local .bmignore, which could remove a teammate's files, so it is only "
    "supported on Personal workspaces.\n"
    "Team workspaces use the additive `bm cloud push --name {name}` / "
    "`bm cloud pull --name {name}` transfers, which never delete."
)


class ConflictStrategy(str, Enum):
    """How push/pull resolves files that differ on both sides.

    Default is ``fail``: surface the conflicts and abort before transferring,
    leaving the user to re-run with an explicit resolution — like git refusing
    to clobber local changes.

    This is the Typer-facing enum; both transfer engines accept the same values
    as the ``ConflictStrategy`` Literal in ``transfer``. The orchestration
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


def _require_personal_workspace(name: str, config: BasicMemoryConfig) -> WorkspaceInfo:
    """Exit before prune work when the target workspace is not personal.

    Prune deletes from the bucket based on this machine's .bmignore, so it is
    gated to Personal workspaces; Team users are pointed at push/pull.
    """
    try:
        workspace = run_with_cleanup(_get_workspace_for_project(name, config))
    except Exception as exc:
        console.print(
            f"[red]Error resolving workspace for project '{literal(name)}': {literal(exc)}[/red]"
        )
        raise typer.Exit(1)

    if workspace.workspace_type != "personal":
        # The template embeds `--name {name}`; quote it before rendering so a
        # name with a space stays one argument in the command it prints.
        console.print(
            TEAM_WORKSPACE_PRUNE_UNSUPPORTED.format(name=shlex.quote(name)),
            style="red",
            markup=False,
        )
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


def _require_local_sync_path(name: str, config: BasicMemoryConfig) -> str:
    """Resolve the local directory a project syncs against, or exit with guidance.

    Both transports need this: the rclone path passes it to rclone, the WebDAV
    path walks it directly.
    """
    sync_entry = config.projects.get(name)
    # Support both new (path) and legacy (local_sync_path) configs
    local_sync_path = (sync_entry.local_sync_path or sync_entry.path) if sync_entry else None

    if not local_sync_path or not os.path.isabs(local_sync_path):
        console.print(
            f"[red]Error: Project '{literal(name)}' has no local sync path configured[/red]"
        )
        console.print(
            f"\nConfigure sync with: "
            f"{literal(shell_command('bm', 'cloud', 'sync-setup', name, '~/path/to/local'))}"
        )
        raise typer.Exit(1)

    return local_sync_path


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
    local_sync_path = _require_local_sync_path(name, config)

    sync_project = SyncProject(
        name=project_data.name,
        path=normalize_project_path(project_data.path),
        local_sync_path=local_sync_path,
        remote_name=remote_name,
    )
    return sync_project, local_sync_path


# --- Commands ---


@cloud_app.command("prune")
def prune_project_command(
    name: str = typer.Option(..., "--name", "--project", help="Project name to prune"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview matching files without deleting"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Delete without confirmation prompt"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output"),
) -> None:
    """Delete cloud files that match your local .bmignore patterns.

    Targeted cleanup for files that synced before their pattern was added to
    .bmignore in the Basic Memory config directory ($BASIC_MEMORY_CONFIG_DIR,
    default ~/.basic-memory). Push and pull skip ignored paths and never
    delete, so those uploads stay on the cloud (#1032). Prune lists the
    matching remote files and deletes them only after confirmation.

    Personal workspaces only: prune deletes from the bucket based on this
    machine's ignore file, so on Team workspaces only the additive
    `bm cloud push` / `bm cloud pull` transfers are available.

    Examples:
      bm cloud prune --name research --dry-run
      bm cloud prune --name research
      bm cloud prune --name research --yes
    """
    config = ConfigManager().config
    _require_cloud_credentials(config)
    target_workspace = _require_personal_workspace(name, config)

    try:
        remote_name = remote_name_for_workspace(
            target_workspace.slug,
            is_default=target_workspace.is_default,
        )
        tenant_info = run_with_cleanup(get_mount_info(workspace_id=target_workspace.tenant_id))
        bucket_name = tenant_info.bucket_name

        # Constraint: project names are not globally unique across workspaces.
        # Keep lookup, credentials, remote, and deletion on the workspace that
        # the Personal-workspace guard resolved.
        with force_routing(cloud=True):
            project_data = run_with_cleanup(
                _get_cloud_project(name, workspace_id=target_workspace.tenant_id)
            )
        if not project_data:
            console.print(f"[red]Error: Project '{literal(name)}' not found[/red]")
            raise typer.Exit(1)

        # Prune inspects and deletes only remote files, so unlike push/pull it
        # needs no local_sync_path — cleanup works from any machine that has the
        # .bmignore patterns.
        sync_project = SyncProject(
            name=project_data.name,
            path=normalize_project_path(project_data.path),
            remote_name=remote_name,
        )

        console.print(
            f"[blue]Scanning {literal(name)} for cloud files matching {literal(get_bmignore_path())}...[/blue]"
        )
        # Trigger: preview and deletion are separated by an interactive prompt.
        # Why: the remote and .bmignore can both change while the prompt is open.
        # Outcome: pass the exact previewed paths to deletion so nothing outside
        # the user's approved list can be removed.
        prune_filter_path = get_bmignore_prune_filter_path()
        matches = project_prune_preview(
            sync_project,
            bucket_name,
            filter_path=prune_filter_path,
        )

        if not matches:
            console.print(f"[green]No cloud files in {literal(name)} match .bmignore[/green]")
            return

        console.print(f"[yellow]{len(matches)} cloud file(s) match .bmignore:[/yellow]")
        for path in matches:
            console.print(f"  [yellow]-[/yellow] {literal(path)}")

        if dry_run:
            console.print(
                "\n[dim]Dry run: nothing deleted. Re-run without --dry-run to delete.[/dim]"
            )
            return

        # Trigger: no --yes flag.
        # Why: prune permanently deletes remote files; require an explicit
        # go-ahead on the exact list shown above.
        # Outcome: abort cleanly unless the user confirms.
        if not yes and not typer.confirm(f"\nDelete these {len(matches)} file(s) from cloud?"):
            console.print("[yellow]Prune cancelled - nothing deleted[/yellow]")
            raise typer.Exit(0)

        success = project_prune(
            sync_project,
            bucket_name,
            matches,
            verbose=verbose,
        )

        if success:
            console.print(f"[green]Pruned ignored files from {literal(name)}[/green]")
        else:
            console.print(f"[red]{literal(name)} prune failed[/red]")
            raise typer.Exit(1)

    except RcloneError as e:
        console.print(f"[red]Prune error: {literal(e)}[/red]")
        raise typer.Exit(1)
    except typer.Exit:
        # Already-handled exits (not found, cancelled, failed) propagate cleanly.
        raise
    except Exception as e:
        console.print(f"[red]Error: {literal(e)}[/red]")
        raise typer.Exit(1)


def _print_conflict_abort(name: str, direction: TransferDirection, plan: TransferPlan) -> None:
    """Explain a conflict abort and how to resolve it (git-pull style)."""
    console.print(
        f"[red]{direction.capitalize()} aborted: {len(plan.conflicts)} file(s) differ between "
        f"local and cloud.[/red]"
    )
    for path in plan.conflicts:
        console.print(f"  [yellow]*[/yellow] {literal(path)}")
    console.print("\nRe-run with one of:")
    console.print("  [dim]--on-conflict keep-cloud[/dim]  take the cloud version")
    console.print("  [dim]--on-conflict keep-local[/dim]  keep your local version")
    console.print(
        "  [dim]--on-conflict keep-both[/dim]   keep both (writes <name>.conflict-<date>)"
    )


# Why the two transports word this differently: rclone reports files it could not
# read or hash, while the WebDAV transport reports files the service described
# without any validator it could compare. Same abort, different cause.
RCLONE_COMPARE_FAILURE = "rclone could not compare {count} file(s)"
WEBDAV_COMPARE_FAILURE = (
    "the cloud reported no comparable checksum or timestamp for {count} file(s)"
)


def _check_plan(
    name: str,
    direction: TransferDirection,
    plan: TransferPlan,
    on_conflict: ConflictStrategy,
    *,
    compare_failure: str,
) -> None:
    """Apply the pre-transfer gates shared by both transports.

    Trigger: files that could not be compared, or conflicts with no chosen
    resolution.
    Why: comparing is the whole basis for a safe transfer — never guess, and
    never silently pick a winner.
    Outcome: abort before moving any bytes, listing what needs attention.
    """
    if plan.errors:
        console.print(
            f"[red]{direction.capitalize()} aborted: "
            f"{compare_failure.format(count=len(plan.errors))}[/red]"
        )
        for path in plan.errors:
            console.print(f"  [red]![/red] {literal(path)}")
        raise typer.Exit(1)

    if plan.conflicts and on_conflict is ConflictStrategy.fail:
        _print_conflict_abort(name, direction, plan)
        raise typer.Exit(1)


def _report_transfer_complete(name: str, direction: TransferDirection, plan: TransferPlan) -> None:
    """Announce success and account for what was deliberately left alone."""
    console.print(f"[green]{literal(name)} {direction} completed successfully[/green]")

    # Without a sync baseline (see #862) we cannot tell an intentional delete
    # from a file the other side simply never had, so deletions never sync.
    if plan.dest_only:
        kept_on = "local" if direction == "pull" else "cloud"
        console.print(
            f"[dim]{len(plan.dest_only)} file(s) exist only on {kept_on} and were left "
            "untouched (deletions are not propagated).[/dim]"
        )


def _run_webdav_directional_transfer(
    name: str,
    direction: TransferDirection,
    *,
    config: BasicMemoryConfig,
    workspace: WorkspaceInfo,
    on_conflict: ConflictStrategy,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Run a Team-workspace push/pull over the cloud WebDAV surface.

    No `bm cloud setup`, no rclone remote, no storage credentials: the service
    authorizes every request against the caller's access to this project, which
    is the model a shared workspace needs (#1262). Conflict handling, additive
    semantics, and the CLI's output are the same as the Personal path.
    """
    with force_routing(cloud=True):
        project_data = run_with_cleanup(_get_cloud_project(name, workspace_id=workspace.tenant_id))
    if not project_data:
        console.print(f"[red]Error: Project '{literal(name)}' not found[/red]")
        raise typer.Exit(1)

    local_root = Path(_require_local_sync_path(name, config))

    # --- Detect before transferring ---
    plan = run_with_cleanup(
        webdav_project_diff(
            project_data.name,
            local_root,
            direction,
            workspace_id=workspace.tenant_id,
        )
    )
    _check_plan(name, direction, plan, on_conflict, compare_failure=WEBDAV_COMPARE_FAILURE)

    # --- Transfer ---
    arrow = "cloud -> local" if direction == "pull" else "local -> cloud"
    console.print(f"[blue]{direction.capitalize()} {literal(name)} ({arrow})...[/blue]")

    run_with_cleanup(
        webdav_project_transfer(
            project_data.name,
            local_root,
            direction,
            plan,
            workspace_id=workspace.tenant_id,
            strategy=on_conflict.value,
            conflict_suffix=datetime.now().strftime("%Y%m%d-%H%M%S"),
            dry_run=dry_run,
            verbose=verbose,
        )
    )

    _report_transfer_complete(name, direction, plan)


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
    resolution. Both transports are additive — neither ever deletes on the
    destination — which is what makes these commands safe on Team workspaces.

    The transport depends on the workspace. Personal workspaces run `rclone copy`
    against their own tenant-scoped remote, so the project reads/writes the right
    bucket (see #919). Team workspaces go over WebDAV instead: storage
    credentials are scoped to a whole tenant bucket and so cannot honor
    per-project access, and minting them is owner-only, which left members unable
    to run these commands at all (#1262).
    """
    config = ConfigManager().config
    _require_cloud_credentials(config)

    try:
        # --- Resolve the target workspace and its transport ---
        # The workspace decides both the transport and, on the Personal path,
        # which tenant-scoped rclone remote to use. Resolve which workspace this
        # project belongs to (config or --workspace override) before going near
        # a bucket or the cloud's file surface.
        try:
            target_workspace = run_with_cleanup(
                _get_workspace_for_project(name, config, workspace_override=workspace)
            )
        except Exception as exc:
            console.print(
                f"[red]Error resolving workspace for project '{literal(name)}': {literal(exc)}[/red]"
            )
            raise typer.Exit(1)

        # Trigger: the resolved workspace is shared (an organization workspace).
        # Why: the rclone path below needs tenant-wide storage credentials, which
        # bypass the service's per-project access control and can only be minted
        # by a workspace owner — every other member got a 403 pointing at a
        # command that could never succeed (#1262).
        # Outcome: Team transfers run over WebDAV, where the service applies
        # per-project access to each request. Personal is untouched.
        if target_workspace.workspace_type != "personal":
            _run_webdav_directional_transfer(
                name,
                direction,
                config=config,
                workspace=target_workspace,
                on_conflict=on_conflict,
                dry_run=dry_run,
                verbose=verbose,
            )
            return

        remote_name = remote_name_for_workspace(
            target_workspace.slug, is_default=target_workspace.is_default
        )

        # Trigger: the workspace's remote has not been configured yet.
        # Why: provisioning mints tenant-scoped credentials and must be explicit
        # (no surprise key generation); push/pull only transfer.
        # Outcome: stop with the exact setup command for this workspace.
        if not rclone_remote_exists(remote_name):
            setup_parts = ["bm", "cloud", "setup"]
            if not target_workspace.is_default:
                setup_parts += ["--workspace", target_workspace.slug]
            console.print(
                f"[red]Workspace '{literal(target_workspace.slug)}' is not set up for sync.[/red]"
            )
            console.print(f"\nRun: {literal(shell_command(*setup_parts))}")
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
            console.print(f"[red]Error: Project '{literal(name)}' not found[/red]")
            raise typer.Exit(1)

        sync_project, _ = _get_sync_project(name, config, project_data, remote_name=remote_name)

        # --- Detect before transferring ---
        plan = project_diff(sync_project, bucket_name, direction)
        _check_plan(name, direction, plan, on_conflict, compare_failure=RCLONE_COMPARE_FAILURE)

        # --- Transfer ---
        arrow = "cloud -> local" if direction == "pull" else "local -> cloud"
        console.print(f"[blue]{direction.capitalize()} {literal(name)} ({arrow})...[/blue]")

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
            console.print(f"[red]{literal(name)} {direction} failed[/red]")
            raise typer.Exit(1)

        _report_transfer_complete(name, direction, plan)

    except (RcloneError, WebdavError) as e:
        console.print(f"[red]{direction.capitalize()} error: {literal(e)}[/red]")
        raise typer.Exit(1)
    except typer.Exit:
        # Already-handled exits (not found, conflicts, errors) propagate cleanly.
        raise
    except Exception as e:
        console.print(f"[red]Error: {literal(e)}[/red]")
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


@cloud_app.command("check")
def check_project_command(
    name: str = typer.Option(..., "--name", "--project", help="Project name to check"),
    one_way: bool = typer.Option(False, "--one-way", help="Check one direction only (faster)"),
) -> None:
    """Verify file integrity between local and cloud (no changes made).

    Legacy, Personal workspaces only: check compares against the default
    Personal workspace remote. Use `bm cloud pull --dry-run` /
    `bm cloud push --dry-run` to preview differences instead.

    Example:
      bm cloud check --name research
    """
    config = ConfigManager().config
    _require_cloud_credentials(config)

    try:
        # Get tenant info for bucket name.
        # TODO(#919): scope to the project's workspace like push/pull. Check
        # reads the default tenant's mount info and remote.
        tenant_info = run_with_cleanup(get_mount_info())
        bucket_name = tenant_info.bucket_name

        # Get project info
        with force_routing(cloud=True):
            project_data = run_with_cleanup(_get_cloud_project(name))
        if not project_data:
            console.print(f"[red]Error: Project '{literal(name)}' not found[/red]")
            raise typer.Exit(1)

        sync_project, local_sync_path = _get_sync_project(name, config, project_data)

        # Run check
        console.print(f"[blue]Checking {literal(name)} integrity...[/blue]")
        match = project_check(sync_project, bucket_name, one_way=one_way)

        if match:
            console.print(f"[green]{literal(name)} files match[/green]")
        else:
            console.print(f"[yellow]!{literal(name)} has differences[/yellow]")

    except RcloneError as e:
        console.print(f"[red]Check error: {literal(e)}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error: {literal(e)}[/red]")
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

        console.print(f"[green]Sync configured for project '{literal(name)}'[/green]")
        console.print(f"\nLocal sync path: {literal(resolved_path)}")
        # Push/pull is the transfer workflow on every workspace.
        console.print("\nNext steps:")
        console.print(
            f"  1. Preview a pull: {literal(shell_command('bm', 'cloud', 'pull', '--name', name, '--dry-run'))}"
        )
        console.print(
            f"  2. Fetch from cloud: {literal(shell_command('bm', 'cloud', 'pull', '--name', name))}"
        )
        console.print(
            f"  3. Upload local changes: {literal(shell_command('bm', 'cloud', 'push', '--name', name))}"
        )
    except Exception as e:
        console.print(f"[red]Error configuring sync: {literal(str(e))}[/red]")
        raise typer.Exit(1)
