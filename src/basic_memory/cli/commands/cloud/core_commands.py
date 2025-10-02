"""Core cloud commands for Basic Memory CLI."""

import asyncio
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console

from basic_memory.cli.app import cloud_app
from basic_memory.cli.auth import CLIAuth
from basic_memory.config import ConfigManager
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    get_cloud_config,
    make_api_request,
    get_authenticated_headers,
)
from basic_memory.cli.commands.cloud.mount_commands import (
    mount_cloud_files,
    setup_cloud_mount,
    show_mount_status,
    unmount_cloud_files,
)
from basic_memory.cli.commands.cloud.bisync_commands import (
    run_bisync,
    run_bisync_watch,
    run_check,
    setup_cloud_bisync,
    show_bisync_status,
)
from basic_memory.cli.commands.cloud.rclone_config import MOUNT_PROFILES
from basic_memory.cli.commands.cloud.bisync_commands import BISYNC_PROFILES
from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path

console = Console()


@cloud_app.command()
def login():
    """Authenticate with WorkOS using OAuth Device Authorization flow and enable cloud mode."""

    async def _login():
        client_id, domain, host_url = get_cloud_config()
        auth = CLIAuth(client_id=client_id, authkit_domain=domain)

        success = await auth.login()
        if not success:
            console.print("[red]Login failed[/red]")
            raise typer.Exit(1)

        # Enable cloud mode after successful login
        config_manager = ConfigManager()
        config = config_manager.load_config()
        config.cloud_mode = True
        config_manager.save_config(config)

        console.print("[green]✓ Cloud mode enabled[/green]")
        console.print(f"[dim]All CLI commands now work against {host_url}[/dim]")

    asyncio.run(_login())


@cloud_app.command()
def logout():
    """Disable cloud mode and return to local mode."""

    # Disable cloud mode
    config_manager = ConfigManager()
    config = config_manager.load_config()
    config.cloud_mode = False
    config_manager.save_config(config)

    console.print("[green]✓ Cloud mode disabled[/green]")
    console.print("[dim]All CLI commands now work locally[/dim]")


@cloud_app.command("upload")
def upload_files(
    project: str = typer.Argument(..., help="Project name to upload to"),
    path_to_files: str = typer.Argument(..., help="Local path to files or directory to upload"),
    preserve_timestamps: bool = typer.Option(
        True,
        "--preserve-timestamps/--no-preserve-timestamps",
        help="Preserve file modification times",
    ),
    respect_gitignore: bool = typer.Option(
        True,
        "--respect-gitignore/--no-gitignore",
        help="Respect .gitignore patterns and skip common development artifacts",
    ),
) -> None:
    """Upload files to a cloud project using WebDAV."""

    # Get cloud configuration
    _, _, host_url = get_cloud_config()
    host_url = host_url.rstrip("/")

    # Validate local path
    local_path = Path(path_to_files).expanduser().resolve()
    if not local_path.exists():
        console.print(f"[red]Error: Path '{path_to_files}' does not exist[/red]")
        raise typer.Exit(1)

    # Prepare headers
    headers = {}

    try:
        # Load gitignore patterns (only if enabled)
        ignore_patterns = load_gitignore_patterns(local_path) if respect_gitignore else set()

        # Collect files to upload
        files_to_upload = []
        ignored_count = 0

        if local_path.is_file():
            # Single file upload - check if it should be ignored
            if not respect_gitignore or not should_ignore_path(
                local_path, local_path.parent, ignore_patterns
            ):
                files_to_upload.append(local_path)
            else:
                ignored_count += 1
        else:
            # Recursively collect all files
            for file_path in local_path.rglob("*"):
                if file_path.is_file():
                    if not respect_gitignore or not should_ignore_path(
                        file_path, local_path, ignore_patterns
                    ):
                        files_to_upload.append(file_path)
                    else:
                        ignored_count += 1

        # Show summary
        if ignored_count > 0 and respect_gitignore:
            console.print(
                f"[dim]Ignored {ignored_count} file(s) based on .gitignore and default patterns[/dim]"
            )

        if not files_to_upload:
            console.print("[yellow]No files found to upload[/yellow]")
            return

        console.print(
            f"[blue]Uploading {len(files_to_upload)} file(s) to project '{project}' on {host_url}...[/blue]"
        )

        # Upload files using WebDAV
        asyncio.run(
            _upload_files_webdav(
                files_to_upload=files_to_upload,
                local_base_path=local_path,
                project=project,
                host_url=host_url,
                headers=headers,
                preserve_timestamps=preserve_timestamps,
            )
        )

        console.print(f"[green]Successfully uploaded {len(files_to_upload)} file(s)![/green]")

    except CloudAPIError as e:
        console.print(f"[red]Error uploading files: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)


async def _upload_files_webdav(
    files_to_upload: list[Path],
    local_base_path: Path,
    project: str,
    host_url: str,
    headers: dict,
    preserve_timestamps: bool,
) -> None:
    """Upload files using WebDAV protocol."""

    # Get authentication headers for WebDAV uploads
    auth_headers = await get_authenticated_headers()

    async with httpx.AsyncClient(timeout=300.0) as client:
        for file_path in files_to_upload:
            # Calculate relative path for WebDAV outside try block
            if local_base_path.is_file():
                # Single file upload - use just the filename
                relative_path = file_path.name
            else:
                # Directory upload - preserve structure
                relative_path = file_path.relative_to(local_base_path)

            try:
                # WebDAV URL
                webdav_url = f"{host_url}/proxy/{project}/webdav/{relative_path}"

                # Prepare upload headers
                upload_headers = dict(headers)
                upload_headers.update(auth_headers)

                # Add timestamp preservation header if requested
                if preserve_timestamps:
                    mtime = file_path.stat().st_mtime
                    upload_headers["X-OC-Mtime"] = str(mtime)

                # Disable compression for WebDAV as well
                upload_headers.setdefault("Accept-Encoding", "identity")

                # Read file content
                file_content = file_path.read_bytes()

                # console.print(f"[dim]Uploading {relative_path} to {webdav_url}[/dim]")

                # Upload file
                response = await client.put(
                    webdav_url, content=file_content, headers=upload_headers
                )

                # console.print(f"[dim]WebDAV response status: {response.status_code}[/dim]")
                response.raise_for_status()

                # Show file upload progress
                console.print(f"  ✓ {relative_path}")

            except httpx.HTTPError as e:
                console.print(f"  ✗ {relative_path} - {e}")
                if hasattr(e, "response") and e.response is not None:  # pyright: ignore [reportAttributeAccessIssue]
                    response = e.response  # type: ignore
                    console.print(f"[red]WebDAV Response status: {response.status_code}[/red]")
                    console.print(f"[red]WebDAV Response headers: {dict(response.headers)}[/red]")
                raise CloudAPIError(f"Failed to upload {file_path.name}: {e}") from e


@cloud_app.command("status")
def status() -> None:
    """Check cloud mode status and cloud instance health."""

    # Check cloud mode
    config_manager = ConfigManager()
    config = config_manager.load_config()

    console.print("[bold blue]Cloud Mode Status[/bold blue]")
    if config.cloud_mode:
        console.print("  Mode: [green]Cloud (enabled)[/green]")
        console.print(f"  Host: {config.cloud_host}")
        console.print("  [dim]All CLI commands work against cloud[/dim]")
    else:
        console.print("  Mode: [yellow]Local (disabled)[/yellow]")
        console.print("  [dim]All CLI commands work locally[/dim]")
        console.print("\n[dim]To enable cloud mode, run: bm cloud login[/dim]")
        return

    # Get cloud configuration
    _, _, host_url = get_cloud_config()
    host_url = host_url.rstrip("/")

    # Prepare headers
    headers = {}

    try:
        console.print("\n[blue]Checking cloud instance health...[/blue]")

        # Make API request to check health
        response = asyncio.run(
            make_api_request(method="GET", url=f"{host_url}/proxy/health", headers=headers)
        )

        health_data = response.json()

        console.print("[green]Cloud instance is healthy[/green]")

        # Display status details
        if "status" in health_data:
            console.print(f"  Status: {health_data['status']}")
        if "version" in health_data:
            console.print(f"  Version: {health_data['version']}")
        if "timestamp" in health_data:
            console.print(f"  Timestamp: {health_data['timestamp']}")

    except CloudAPIError as e:
        console.print(f"[red]Error checking cloud health: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)


# Mount commands


@cloud_app.command("setup")
def setup() -> None:
    """Set up local file access with automatic rclone installation and configuration."""
    setup_cloud_mount()


@cloud_app.command("mount")
def mount(
    profile: str = typer.Option(
        "balanced", help=f"Mount profile: {', '.join(MOUNT_PROFILES.keys())}"
    ),
    path: Optional[str] = typer.Option(
        None, help="Custom mount path (default: ~/basic-memory-{tenant-id})"
    ),
) -> None:
    """Mount cloud files locally for editing."""
    try:
        mount_cloud_files(profile_name=profile)
    except Exception as e:
        console.print(f"[red]Mount failed: {e}[/red]")
        raise typer.Exit(1)


@cloud_app.command("unmount")
def unmount() -> None:
    """Unmount cloud files."""
    try:
        unmount_cloud_files()
    except Exception as e:
        console.print(f"[red]Unmount failed: {e}[/red]")
        raise typer.Exit(1)


@cloud_app.command("mount-status")
def mount_status() -> None:
    """Show current mount status."""
    show_mount_status()


# Bisync commands


@cloud_app.command("bisync-setup")
def bisync_setup(
    dir: Optional[str] = typer.Option(
        None,
        "--dir",
        help="Custom sync directory (default: ~/basic-memory-cloud-sync)",
    ),
) -> None:
    """Set up bidirectional sync with automatic rclone installation and configuration."""
    setup_cloud_bisync(sync_dir=dir)


@cloud_app.command("bisync")
def bisync(
    profile: str = typer.Option(
        "balanced", help=f"Bisync profile: {', '.join(BISYNC_PROFILES.keys())}"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without syncing"),
    resync: bool = typer.Option(False, "--resync", help="Force resync to establish new baseline"),
    watch: bool = typer.Option(False, "--watch", help="Run continuous sync in watch mode"),
    interval: int = typer.Option(60, "--interval", help="Sync interval in seconds for watch mode"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed sync output"),
) -> None:
    """Run bidirectional sync between local files and cloud storage.

    Examples:
      basic-memory cloud bisync                    # Manual sync with balanced profile
      basic-memory cloud bisync --dry-run          # Preview what would be synced
      basic-memory cloud bisync --resync           # Establish new baseline
      basic-memory cloud bisync --watch            # Continuous sync every 60s
      basic-memory cloud bisync --watch --interval 30  # Continuous sync every 30s
      basic-memory cloud bisync --profile safe     # Use safe profile (keep conflicts)
      basic-memory cloud bisync --verbose          # Show detailed file sync output
    """
    try:
        if watch:
            run_bisync_watch(profile_name=profile, interval_seconds=interval)
        else:
            run_bisync(profile_name=profile, dry_run=dry_run, resync=resync, verbose=verbose)
    except Exception as e:
        console.print(f"[red]Bisync failed: {e}[/red]")
        raise typer.Exit(1)


@cloud_app.command("bisync-status")
def bisync_status() -> None:
    """Show current bisync status and configuration."""
    show_bisync_status()


@cloud_app.command("check")
def check(
    one_way: bool = typer.Option(
        False,
        "--one-way",
        help="Only check for missing files on destination (faster)",
    ),
) -> None:
    """Check file integrity between local and cloud storage using rclone check.

    Verifies that files match between your local bisync directory and cloud storage
    without transferring any data. This is useful for validating sync integrity.

    Examples:
      bm cloud check              # Full integrity check
      bm cloud check --one-way    # Faster check (missing files only)
    """
    try:
        run_check(one_way=one_way)
    except Exception as e:
        console.print(f"[red]Check failed: {e}[/red]")
        raise typer.Exit(1)
