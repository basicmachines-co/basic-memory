"""Cloud bisync commands for Basic Memory CLI."""

import asyncio
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from basic_memory.cli.commands.cloud.api_client import CloudAPIError, make_api_request
from basic_memory.cli.commands.cloud.cloud_utils import (
    create_cloud_project,
    fetch_cloud_projects,
)
from basic_memory.cli.commands.cloud.rclone_config import (
    add_tenant_to_rclone_config,
)
from basic_memory.cli.commands.cloud.rclone_installer import RcloneInstallError, install_rclone
from basic_memory.config import ConfigManager
from basic_memory.ignore_utils import get_bmignore_path, create_default_bmignore
from basic_memory.schemas.cloud import (
    TenantMountInfo,
    MountCredentials,
)

console = Console()


class BisyncError(Exception):
    """Exception raised for bisync-related errors."""

    pass


class RcloneBisyncProfile:
    """Bisync profile with safety settings."""

    def __init__(
        self,
        name: str,
        conflict_resolve: str,
        max_delete: int,
        check_access: bool,
        description: str,
        extra_args: Optional[list[str]] = None,
    ):
        self.name = name
        self.conflict_resolve = conflict_resolve
        self.max_delete = max_delete
        self.check_access = check_access
        self.description = description
        self.extra_args = extra_args or []


# Bisync profiles based on SPEC-9 Phase 2.1
BISYNC_PROFILES = {
    "safe": RcloneBisyncProfile(
        name="safe",
        conflict_resolve="none",
        max_delete=10,
        check_access=False,
        description="Safe mode with conflict preservation (keeps both versions)",
    ),
    "balanced": RcloneBisyncProfile(
        name="balanced",
        conflict_resolve="newer",
        max_delete=25,
        check_access=False,
        description="Balanced mode - auto-resolve to newer file (recommended)",
    ),
    "fast": RcloneBisyncProfile(
        name="fast",
        conflict_resolve="newer",
        max_delete=50,
        check_access=False,
        description="Fast mode for rapid iteration (skip verification)",
    ),
}


async def get_mount_info() -> TenantMountInfo:
    """Get current tenant information from cloud API."""
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        response = await make_api_request(method="GET", url=f"{host_url}/tenant/mount/info")

        return TenantMountInfo.model_validate(response.json())
    except Exception as e:
        raise BisyncError(f"Failed to get tenant info: {e}") from e


async def generate_mount_credentials(tenant_id: str) -> MountCredentials:
    """Generate scoped credentials for syncing."""
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        response = await make_api_request(method="POST", url=f"{host_url}/tenant/mount/credentials")

        return MountCredentials.model_validate(response.json())
    except Exception as e:
        raise BisyncError(f"Failed to generate credentials: {e}") from e


def scan_local_directories(sync_dir: Path) -> list[str]:
    """Scan local sync directory for project folders.

    Args:
        sync_dir: Path to bisync directory

    Returns:
        List of directory names (project names)
    """
    if not sync_dir.exists():
        return []

    directories = []
    for item in sync_dir.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            directories.append(item.name)

    return directories


def get_bisync_state_path(tenant_id: str) -> Path:
    """Get path to bisync state directory."""
    return Path.home() / ".basic-memory" / "bisync-state" / tenant_id


def get_bisync_directory() -> Path:
    """Get bisync directory from config.

    Returns:
        Path to bisync directory (default: ~/basic-memory-cloud-sync)
    """
    config_manager = ConfigManager()
    config = config_manager.config

    sync_dir = config.bisync_config.get("sync_dir", str(Path.home() / "basic-memory-cloud-sync"))
    return Path(sync_dir).expanduser().resolve()


def validate_bisync_directory(bisync_dir: Path) -> None:
    """Validate bisync directory doesn't conflict with mount.

    Raises:
        BisyncError: If bisync directory conflicts with mount directory
    """
    # Get fixed mount directory
    mount_dir = (Path.home() / "basic-memory-cloud").resolve()

    # Check if bisync dir is the same as mount dir
    if bisync_dir == mount_dir:
        raise BisyncError(
            f"Cannot use {bisync_dir} for bisync - it's the mount directory!\n"
            f"Mount and bisync must use different directories.\n\n"
            f"Options:\n"
            f"  1. Use default: ~/basic-memory-cloud-sync/\n"
            f"  2. Specify different directory: --dir ~/my-sync-folder"
        )

    # Check if mount is active at this location
    result = subprocess.run(["mount"], capture_output=True, text=True)
    if str(bisync_dir) in result.stdout and "rclone" in result.stdout:
        raise BisyncError(
            f"{bisync_dir} is currently mounted via 'bm cloud mount'\n"
            f"Cannot use mounted directory for bisync.\n\n"
            f"Either:\n"
            f"  1. Unmount first: bm cloud unmount\n"
            f"  2. Use different directory for bisync"
        )


def convert_bmignore_to_rclone_filters() -> Path:
    """Convert .bmignore patterns to rclone filter format.

    Reads ~/.basic-memory/.bmignore (gitignore-style) and converts to
    ~/.basic-memory/.bmignore.rclone (rclone filter format).

    Only regenerates if .bmignore has been modified since last conversion.

    Returns:
        Path to converted rclone filter file
    """
    # Ensure .bmignore exists
    create_default_bmignore()

    bmignore_path = get_bmignore_path()
    # Create rclone filter path: ~/.basic-memory/.bmignore -> ~/.basic-memory/.bmignore.rclone
    rclone_filter_path = bmignore_path.parent / f"{bmignore_path.name}.rclone"

    # Skip regeneration if rclone file is newer than bmignore
    if rclone_filter_path.exists():
        bmignore_mtime = bmignore_path.stat().st_mtime
        rclone_mtime = rclone_filter_path.stat().st_mtime
        if rclone_mtime >= bmignore_mtime:
            return rclone_filter_path

    # Read .bmignore patterns
    patterns = []
    try:
        with bmignore_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Keep comments and empty lines
                if not line or line.startswith("#"):
                    patterns.append(line)
                    continue

                # Convert gitignore pattern to rclone filter syntax
                # gitignore: node_modules  → rclone: - node_modules/**
                # gitignore: *.pyc        → rclone: - *.pyc
                if "*" in line:
                    # Pattern already has wildcard, just add exclude prefix
                    patterns.append(f"- {line}")
                else:
                    # Directory pattern - add /** for recursive exclude
                    patterns.append(f"- {line}/**")

    except Exception:
        # If we can't read the file, create a minimal filter
        patterns = ["# Error reading .bmignore, using minimal filters", "- .git/**"]

    # Write rclone filter file
    rclone_filter_path.write_text("\n".join(patterns) + "\n")

    return rclone_filter_path


def get_bisync_filter_path() -> Path:
    """Get path to bisync filter file.

    Uses ~/.basic-memory/.bmignore (converted to rclone format).
    The file is automatically created with default patterns on first use.

    Returns:
        Path to rclone filter file
    """
    return convert_bmignore_to_rclone_filters()


def bisync_state_exists(tenant_id: str) -> bool:
    """Check if bisync state exists (has been initialized)."""
    state_path = get_bisync_state_path(tenant_id)
    return state_path.exists() and any(state_path.iterdir())


def build_bisync_command(
    tenant_id: str,
    bucket_name: str,
    local_path: Path,
    profile: RcloneBisyncProfile,
    dry_run: bool = False,
    resync: bool = False,
    verbose: bool = False,
) -> list[str]:
    """Build rclone bisync command with profile settings."""

    # Sync with the entire bucket root (all projects)
    rclone_remote = f"basic-memory-{tenant_id}:{bucket_name}"
    filter_path = get_bisync_filter_path()
    state_path = get_bisync_state_path(tenant_id)

    # Ensure state directory exists
    state_path.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rclone",
        "bisync",
        str(local_path),
        rclone_remote,
        "--create-empty-src-dirs",
        "--resilient",
        f"--conflict-resolve={profile.conflict_resolve}",
        f"--max-delete={profile.max_delete}",
        "--filters-file",
        str(filter_path),
        "--workdir",
        str(state_path),
    ]

    # Add verbosity flags
    if verbose:
        cmd.append("--verbose")  # Full details with file-by-file output
    else:
        # Show progress bar during transfers
        cmd.append("--progress")

    if profile.check_access:
        cmd.append("--check-access")

    if dry_run:
        cmd.append("--dry-run")

    if resync:
        cmd.append("--resync")

    cmd.extend(profile.extra_args)

    return cmd


def setup_cloud_bisync(sync_dir: Optional[str] = None) -> None:
    """Set up cloud bisync with rclone installation and configuration.

    Args:
        sync_dir: Optional custom sync directory path. If not provided, uses config default.
    """
    console.print("[bold blue]Basic Memory Cloud Bisync Setup[/bold blue]")
    console.print("Setting up bidirectional sync to your cloud tenant...\n")

    try:
        # Step 1: Install rclone
        console.print("[blue]Step 1: Installing rclone...[/blue]")
        install_rclone()

        # Step 2: Get mount info (for tenant_id, bucket)
        console.print("\n[blue]Step 2: Getting tenant information...[/blue]")
        tenant_info = asyncio.run(get_mount_info())

        tenant_id = tenant_info.tenant_id
        bucket_name = tenant_info.bucket_name

        console.print(f"[green]✓ Found tenant: {tenant_id}[/green]")
        console.print(f"[green]✓ Bucket: {bucket_name}[/green]")

        # Step 3: Generate credentials
        console.print("\n[blue]Step 3: Generating sync credentials...[/blue]")
        creds = asyncio.run(generate_mount_credentials(tenant_id))

        access_key = creds.access_key
        secret_key = creds.secret_key

        console.print("[green]✓ Generated secure credentials[/green]")

        # Step 4: Configure rclone
        console.print("\n[blue]Step 4: Configuring rclone...[/blue]")
        add_tenant_to_rclone_config(
            tenant_id=tenant_id,
            bucket_name=bucket_name,
            access_key=access_key,
            secret_key=secret_key,
        )

        # Step 5: Configure and create local directory
        console.print("\n[blue]Step 5: Configuring sync directory...[/blue]")

        # If custom sync_dir provided, save to config
        if sync_dir:
            config_manager = ConfigManager()
            config = config_manager.load_config()
            config.bisync_config["sync_dir"] = sync_dir
            config_manager.save_config(config)
            console.print("[green]✓ Saved custom sync directory to config[/green]")

        # Get bisync directory (from config or default)
        local_path = get_bisync_directory()

        # Validate bisync directory
        validate_bisync_directory(local_path)

        # Create directory
        local_path.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓ Created sync directory: {local_path}[/green]")

        # Step 6: Perform initial resync
        console.print("\n[blue]Step 6: Performing initial sync...[/blue]")
        console.print("[yellow]This will establish the baseline for bidirectional sync.[/yellow]")

        run_bisync(
            tenant_id=tenant_id,
            bucket_name=bucket_name,
            local_path=local_path,
            profile_name="balanced",
            resync=True,
        )

        console.print("\n[bold green]✓ Bisync setup completed successfully![/bold green]")
        console.print("\nYour local files will now sync bidirectionally with the cloud!")
        console.print(f"\nLocal directory: {local_path}")
        console.print("\nUseful commands:")
        console.print("  bm sync                      # Run sync (recommended)")
        console.print("  bm sync --watch              # Start watch mode")
        console.print("  bm cloud status              # Check sync status")
        console.print("  bm cloud check               # Verify file integrity")
        console.print("  bm cloud bisync --dry-run    # Preview changes (advanced)")

    except (RcloneInstallError, BisyncError, CloudAPIError) as e:
        console.print(f"\n[red]Setup failed: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[red]Unexpected error during setup: {e}[/red]")
        raise typer.Exit(1)


def run_bisync(
    tenant_id: Optional[str] = None,
    bucket_name: Optional[str] = None,
    local_path: Optional[Path] = None,
    profile_name: str = "balanced",
    dry_run: bool = False,
    resync: bool = False,
    verbose: bool = False,
) -> bool:
    """Run rclone bisync with specified profile."""

    try:
        # Get tenant info if not provided
        if not tenant_id or not bucket_name:
            tenant_info = asyncio.run(get_mount_info())
            tenant_id = tenant_info.tenant_id
            bucket_name = tenant_info.bucket_name

        # Set default local path if not provided
        if not local_path:
            local_path = get_bisync_directory()

        # Validate bisync directory
        validate_bisync_directory(local_path)

        # Check if local path exists
        if not local_path.exists():
            raise BisyncError(
                f"Local directory {local_path} does not exist. Run 'basic-memory cloud bisync-setup' first."
            )

        # Get bisync profile
        if profile_name not in BISYNC_PROFILES:
            raise BisyncError(
                f"Unknown profile: {profile_name}. Available: {list(BISYNC_PROFILES.keys())}"
            )

        profile = BISYNC_PROFILES[profile_name]

        # Auto-register projects before sync (unless dry-run or resync)
        if not dry_run and not resync:
            try:
                console.print("[dim]Checking for new projects...[/dim]")

                # Fetch cloud projects and extract directory names from paths
                cloud_data = asyncio.run(fetch_cloud_projects())
                cloud_projects = cloud_data.projects

                # Extract directory names from cloud project paths
                # Compare directory names, not project names
                # Cloud path /app/data/basic-memory -> directory name "basic-memory"
                cloud_dir_names = set()
                for p in cloud_projects:
                    path = p.path
                    # Strip /app/data/ prefix if present (cloud mode)
                    if path.startswith("/app/data/"):
                        path = path[len("/app/data/") :]
                    # Get the last segment (directory name)
                    dir_name = Path(path).name
                    cloud_dir_names.add(dir_name)

                # Scan local directories
                local_dirs = scan_local_directories(local_path)

                # Create missing cloud projects
                new_projects = []
                for dir_name in local_dirs:
                    if dir_name not in cloud_dir_names:
                        new_projects.append(dir_name)

                if new_projects:
                    console.print(
                        f"[blue]Found {len(new_projects)} new local project(s), creating on cloud...[/blue]"
                    )
                    for project_name in new_projects:
                        try:
                            asyncio.run(create_cloud_project(project_name))
                            console.print(f"[green]  ✓ Created project: {project_name}[/green]")
                        except BisyncError as e:
                            console.print(
                                f"[yellow]  ⚠ Could not create {project_name}: {e}[/yellow]"
                            )
                else:
                    console.print("[dim]All local projects already registered on cloud[/dim]")

            except Exception as e:
                console.print(f"[yellow]Warning: Project auto-registration failed: {e}[/yellow]")
                console.print("[yellow]Continuing with sync anyway...[/yellow]")

        # Check if first run and require resync
        if not resync and not bisync_state_exists(tenant_id) and not dry_run:
            raise BisyncError(
                "First bisync requires --resync to establish baseline. "
                "Run: basic-memory cloud bisync --resync"
            )

        # Build and execute bisync command
        bisync_cmd = build_bisync_command(
            tenant_id,
            bucket_name,
            local_path,
            profile,
            dry_run=dry_run,
            resync=resync,
            verbose=verbose,
        )

        if dry_run:
            console.print("[yellow]DRY RUN MODE - No changes will be made[/yellow]")

        console.print(
            f"[blue]Running bisync with profile '{profile_name}' ({profile.description})...[/blue]"
        )
        console.print(f"[dim]Command: {' '.join(bisync_cmd)}[/dim]")
        console.print()  # Blank line before output

        # Stream output in real-time so user sees progress
        result = subprocess.run(bisync_cmd, text=True)

        if result.returncode != 0:
            raise BisyncError(f"Bisync command failed with code {result.returncode}")

        console.print()  # Blank line after output

        if dry_run:
            console.print("[green]✓ Dry run completed successfully[/green]")
        elif resync:
            console.print("[green]✓ Initial sync baseline established[/green]")
        else:
            console.print("[green]✓ Sync completed successfully[/green]")

        # Notify container to refresh cache (if not dry run)
        if not dry_run:
            try:
                asyncio.run(notify_container_sync(tenant_id))
            except Exception as e:
                console.print(f"[yellow]Warning: Could not notify container: {e}[/yellow]")

        return True

    except BisyncError:
        raise
    except Exception as e:
        raise BisyncError(f"Unexpected error during bisync: {e}") from e


async def notify_container_sync(tenant_id: str) -> None:
    """Sync all projects after bisync completes."""
    try:
        from basic_memory.cli.commands.command_utils import run_sync

        # Fetch all projects and sync each one
        cloud_data = await fetch_cloud_projects()
        projects = cloud_data.projects

        if not projects:
            console.print("[dim]No projects to sync[/dim]")
            return

        console.print(f"[blue]Notifying cloud to index {len(projects)} project(s)...[/blue]")

        for project in projects:
            project_name = project.name
            if project_name:
                try:
                    await run_sync(project=project_name)
                except Exception as e:
                    # Non-critical, log and continue
                    console.print(f"[yellow]  ⚠ Sync failed for {project_name}: {e}[/yellow]")

        console.print("[dim]Note: Cloud indexing has started and may take a few moments[/dim]")

    except Exception as e:
        # Non-critical, don't fail the bisync
        console.print(f"[yellow]Warning: Post-sync failed: {e}[/yellow]")


def run_bisync_watch(
    tenant_id: Optional[str] = None,
    bucket_name: Optional[str] = None,
    local_path: Optional[Path] = None,
    profile_name: str = "balanced",
    interval_seconds: int = 60,
) -> None:
    """Run bisync in watch mode with periodic syncs."""

    console.print("[bold blue]Starting bisync watch mode[/bold blue]")
    console.print(f"Sync interval: {interval_seconds} seconds")
    console.print("Press Ctrl+C to stop\n")

    try:
        while True:
            try:
                start_time = time.time()

                run_bisync(
                    tenant_id=tenant_id,
                    bucket_name=bucket_name,
                    local_path=local_path,
                    profile_name=profile_name,
                )

                elapsed = time.time() - start_time
                console.print(f"[dim]Sync completed in {elapsed:.1f}s[/dim]")

                # Wait for next interval
                time.sleep(interval_seconds)

            except BisyncError as e:
                console.print(f"[red]Sync error: {e}[/red]")
                console.print(f"[yellow]Retrying in {interval_seconds} seconds...[/yellow]")
                time.sleep(interval_seconds)

    except KeyboardInterrupt:
        console.print("\n[yellow]Watch mode stopped[/yellow]")


def show_bisync_status() -> None:
    """Show current bisync status and configuration."""

    try:
        # Get tenant info
        tenant_info = asyncio.run(get_mount_info())
        tenant_id = tenant_info.tenant_id

        local_path = get_bisync_directory()
        state_path = get_bisync_state_path(tenant_id)

        # Create status table
        table = Table(title="Cloud Bisync Status", show_header=True, header_style="bold blue")
        table.add_column("Property", style="green", min_width=20)
        table.add_column("Value", style="dim", min_width=30)

        # Check initialization status
        is_initialized = bisync_state_exists(tenant_id)
        init_status = (
            "[green]✓ Initialized[/green]" if is_initialized else "[red]✗ Not initialized[/red]"
        )

        table.add_row("Tenant ID", tenant_id)
        table.add_row("Local Directory", str(local_path))
        table.add_row("Status", init_status)
        table.add_row("State Directory", str(state_path))

        # Check for last sync info
        if is_initialized:
            # Look for most recent state file
            state_files = list(state_path.glob("*.lst"))
            if state_files:
                latest = max(state_files, key=lambda p: p.stat().st_mtime)
                last_sync = datetime.fromtimestamp(latest.stat().st_mtime)
                table.add_row("Last Sync", last_sync.strftime("%Y-%m-%d %H:%M:%S"))

        console.print(table)

        # Show bisync profiles
        console.print("\n[bold]Available bisync profiles:[/bold]")
        for name, profile in BISYNC_PROFILES.items():
            console.print(f"  {name}: {profile.description}")
            console.print(f"    - Conflict resolution: {profile.conflict_resolve}")
            console.print(f"    - Max delete: {profile.max_delete} files")

        console.print("\n[dim]To use a profile: bm cloud bisync --profile <name>[/dim]")

        # Show setup instructions if not initialized
        if not is_initialized:
            console.print("\n[yellow]To initialize bisync, run:[/yellow]")
            console.print("  bm cloud setup")
            console.print("  or")
            console.print("  bm cloud bisync --resync")

    except Exception as e:
        console.print(f"[red]Error getting bisync status: {e}[/red]")
        raise typer.Exit(1)


def run_check(
    tenant_id: Optional[str] = None,
    bucket_name: Optional[str] = None,
    local_path: Optional[Path] = None,
    one_way: bool = False,
) -> bool:
    """Check file integrity between local and cloud using rclone check.

    Args:
        tenant_id: Cloud tenant ID (auto-detected if not provided)
        bucket_name: S3 bucket name (auto-detected if not provided)
        local_path: Local bisync directory (uses config default if not provided)
        one_way: If True, only check for missing files on destination (faster)

    Returns:
        True if check passed (files match), False if differences found
    """
    try:
        # Check if rclone is installed
        from basic_memory.cli.commands.cloud.rclone_installer import is_rclone_installed

        if not is_rclone_installed():
            raise BisyncError(
                "rclone is not installed. Run 'bm cloud bisync-setup' first to set up cloud sync."
            )

        # Get tenant info if not provided
        if not tenant_id or not bucket_name:
            tenant_info = asyncio.run(get_mount_info())
            tenant_id = tenant_id or tenant_info.tenant_id
            bucket_name = bucket_name or tenant_info.bucket_name

        # Get local path from config
        if not local_path:
            local_path = get_bisync_directory()

        # Check if bisync is initialized
        if not bisync_state_exists(tenant_id):
            raise BisyncError(
                "Bisync not initialized. Run 'bm cloud bisync --resync' to establish baseline."
            )

        # Build rclone check command
        rclone_remote = f"basic-memory-{tenant_id}:{bucket_name}"
        filter_path = get_bisync_filter_path()

        cmd = [
            "rclone",
            "check",
            str(local_path),
            rclone_remote,
            "--filter-from",
            str(filter_path),
        ]

        if one_way:
            cmd.append("--one-way")

        console.print("[bold blue]Checking file integrity between local and cloud[/bold blue]")
        console.print(f"[dim]Local:  {local_path}[/dim]")
        console.print(f"[dim]Remote: {rclone_remote}[/dim]")
        console.print(f"[dim]Command: {' '.join(cmd)}[/dim]")
        console.print()

        # Run check command
        result = subprocess.run(cmd, capture_output=True, text=True)

        # rclone check returns:
        # 0 = success (all files match)
        # non-zero = differences found or error
        if result.returncode == 0:
            console.print("[green]✓ All files match between local and cloud[/green]")
            return True
        else:
            console.print("[yellow]⚠ Differences found:[/yellow]")
            if result.stderr:
                console.print(result.stderr)
            if result.stdout:
                console.print(result.stdout)
            console.print("\n[dim]To sync differences, run: bm sync[/dim]")
            return False

    except BisyncError:
        raise
    except Exception as e:
        raise BisyncError(f"Check failed: {e}") from e
