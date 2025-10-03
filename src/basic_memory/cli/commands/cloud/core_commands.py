"""Core cloud commands for Basic Memory CLI."""

import asyncio
from typing import Optional

import typer
from rich.console import Console

from basic_memory.cli.app import cloud_app
from basic_memory.cli.auth import CLIAuth
from basic_memory.config import ConfigManager
from basic_memory.cli.commands.cloud.api_client import (
    CloudAPIError,
    SubscriptionRequiredError,
    get_cloud_config,
    make_api_request,
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

console = Console()


@cloud_app.command()
def login():
    """Authenticate with WorkOS using OAuth Device Authorization flow and enable cloud mode."""

    async def _login():
        client_id, domain, host_url = get_cloud_config()
        auth = CLIAuth(client_id=client_id, authkit_domain=domain)

        try:
            success = await auth.login()
            if not success:
                console.print("[red]Login failed[/red]")
                raise typer.Exit(1)

            # Test subscription access by calling a protected endpoint
            console.print("[dim]Verifying subscription access...[/dim]")
            await make_api_request("GET", f"{host_url.rstrip('/')}/proxy/health")

            # Enable cloud mode after successful login and subscription validation
            config_manager = ConfigManager()
            config = config_manager.load_config()
            config.cloud_mode = True
            config_manager.save_config(config)

            console.print("[green]✓ Cloud mode enabled[/green]")
            console.print(f"[dim]All CLI commands now work against {host_url}[/dim]")

        except SubscriptionRequiredError as e:
            console.print("\n[red]✗ Subscription Required[/red]\n")
            console.print(f"[yellow]{e.args[0]}[/yellow]\n")
            console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
            console.print(
                "[dim]Once you have an active subscription, run [bold]bm cloud login[/bold] again.[/dim]"
            )
            raise typer.Exit(1)

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


@cloud_app.command("status")
def status(
    bisync: bool = typer.Option(
        True,
        "--bisync/--mount",
        help="Show bisync status (default) or mount status",
    ),
) -> None:
    """Check cloud mode status and cloud instance health.

    Shows cloud mode status, instance health, and sync/mount status.
    Use --bisync (default) to show bisync status or --mount for mount status.
    """
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

        # Show sync/mount status based on flag
        console.print()
        if bisync:
            show_bisync_status()
        else:
            show_mount_status()

    except CloudAPIError as e:
        console.print(f"[red]Error checking cloud health: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)


# Mount commands


@cloud_app.command("setup")
def setup(
    bisync: bool = typer.Option(
        True,
        "--bisync/--mount",
        help="Use bidirectional sync (recommended) or mount as network drive",
    ),
    sync_dir: Optional[str] = typer.Option(
        None,
        "--dir",
        help="Custom sync directory for bisync (default: ~/basic-memory-cloud-sync)",
    ),
) -> None:
    """Set up cloud file access with automatic rclone installation and configuration.

    Default: Sets up bidirectional sync (recommended).\n
    Use --mount: Sets up mount as network drive (alternative workflow).\n

    Examples:\n
      bm cloud setup              # Setup bisync (default)\n
      bm cloud setup --mount      # Setup mount instead\n
      bm cloud setup --dir ~/sync # Custom bisync directory\n
    """
    if bisync:
        setup_cloud_bisync(sync_dir=sync_dir)
    else:
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


# Bisync commands


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
    """Show current bisync status and configuration.

    DEPRECATED: Use 'bm cloud status' instead (bisync is now the default).
    """
    console.print(
        "[yellow]Note: 'bisync-status' is deprecated. Use 'bm cloud status' instead.[/yellow]"
    )
    console.print("[dim]Showing bisync status...[/dim]\n")
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
