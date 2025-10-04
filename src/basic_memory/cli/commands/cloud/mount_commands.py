"""Cloud mount commands for Basic Memory CLI."""

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from basic_memory.cli.commands.cloud.api_client import CloudAPIError, make_api_request
from basic_memory.cli.commands.cloud.rclone_config import (
    MOUNT_PROFILES,
    add_tenant_to_rclone_config,
    build_mount_command,
    cleanup_orphaned_rclone_processes,
    get_default_mount_path,
    get_rclone_processes,
    is_path_mounted,
    unmount_path,
)
from basic_memory.cli.commands.cloud.rclone_installer import RcloneInstallError, install_rclone
from basic_memory.config import ConfigManager

console = Console()


class MountError(Exception):
    """Exception raised for mount-related errors."""

    pass


async def get_tenant_info() -> dict:
    """Get current tenant information from cloud API."""
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        response = await make_api_request(method="GET", url=f"{host_url}/tenant/mount/info")

        return response.json()
    except Exception as e:
        raise MountError(f"Failed to get tenant info: {e}") from e


async def generate_mount_credentials(tenant_id: str) -> dict:
    """Generate scoped credentials for mounting."""
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        response = await make_api_request(method="POST", url=f"{host_url}/tenant/mount/credentials")

        return response.json()
    except Exception as e:
        raise MountError(f"Failed to generate mount credentials: {e}") from e


def setup_cloud_mount() -> None:
    """Set up cloud mount with rclone installation and configuration."""
    console.print("[bold blue]Basic Memory Cloud Setup[/bold blue]")
    console.print("Setting up local file access to your cloud tenant...\n")

    try:
        # Step 1: Install rclone
        console.print("[blue]Step 1: Installing rclone...[/blue]")
        install_rclone()

        # Step 2: Get tenant info
        console.print("\n[blue]Step 2: Getting tenant information...[/blue]")
        tenant_info = asyncio.run(get_tenant_info())

        tenant_id = tenant_info.get("tenant_id")
        bucket_name = tenant_info.get("bucket_name")

        if not tenant_id or not bucket_name:
            raise MountError("Invalid tenant information received from cloud API")

        console.print(f"[green]✓ Found tenant: {tenant_id}[/green]")
        console.print(f"[green]✓ Bucket: {bucket_name}[/green]")

        # Step 3: Generate mount credentials
        console.print("\n[blue]Step 3: Generating mount credentials...[/blue]")
        creds = asyncio.run(generate_mount_credentials(tenant_id))

        access_key = creds.get("access_key")
        secret_key = creds.get("secret_key")

        if not access_key or not secret_key:
            raise MountError("Failed to generate mount credentials")

        console.print("[green]✓ Generated secure credentials[/green]")

        # Step 4: Configure rclone
        console.print("\n[blue]Step 4: Configuring rclone...[/blue]")
        add_tenant_to_rclone_config(
            tenant_id=tenant_id,
            bucket_name=bucket_name,
            access_key=access_key,
            secret_key=secret_key,
        )

        # Step 5: Perform initial mount
        console.print("\n[blue]Step 5: Mounting cloud files...[/blue]")
        mount_path = get_default_mount_path()
        MOUNT_PROFILES["balanced"]

        mount_cloud_files(
            tenant_id=tenant_id,
            bucket_name=bucket_name,
            mount_path=mount_path,
            profile_name="balanced",
        )

        console.print("\n[bold green]✓ Cloud setup completed successfully![/bold green]")
        console.print("\nYour cloud files are now accessible at:")
        console.print(f"  {mount_path}")
        console.print("\nYou can now edit files locally and they will sync to the cloud!")
        console.print("\nUseful commands:")
        console.print("  basic-memory cloud mount-status    # Check mount status")
        console.print("  basic-memory cloud unmount         # Unmount files")
        console.print("  basic-memory cloud mount --profile fast  # Remount with faster sync")

    except (RcloneInstallError, MountError, CloudAPIError) as e:
        console.print(f"\n[red]Setup failed: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[red]Unexpected error during setup: {e}[/red]")
        raise typer.Exit(1)


def mount_cloud_files(
    tenant_id: Optional[str] = None,
    bucket_name: Optional[str] = None,
    mount_path: Optional[Path] = None,
    profile_name: str = "balanced",
) -> None:
    """Mount cloud files with specified profile."""

    try:
        # Get tenant info if not provided
        if not tenant_id or not bucket_name:
            tenant_info = asyncio.run(get_tenant_info())
            tenant_id = tenant_info.get("tenant_id")
            bucket_name = tenant_info.get("bucket_name")

            if not tenant_id or not bucket_name:
                raise MountError("Could not determine tenant information")

        # Set default mount path if not provided
        if not mount_path:
            mount_path = get_default_mount_path()

        # Get mount profile
        if profile_name not in MOUNT_PROFILES:
            raise MountError(
                f"Unknown profile: {profile_name}. Available: {list(MOUNT_PROFILES.keys())}"
            )

        profile = MOUNT_PROFILES[profile_name]

        # Check if already mounted
        if is_path_mounted(mount_path):
            console.print(f"[yellow]Path {mount_path} is already mounted[/yellow]")
            console.print("Use 'basic-memory cloud unmount' first, or mount to a different path")
            return

        # Create mount directory
        mount_path.mkdir(parents=True, exist_ok=True)

        # Build and execute mount command
        mount_cmd = build_mount_command(tenant_id, bucket_name, mount_path, profile)

        console.print(
            f"[blue]Mounting with profile '{profile_name}' ({profile.description})...[/blue]"
        )
        console.print(f"[dim]Command: {' '.join(mount_cmd)}[/dim]")

        result = subprocess.run(mount_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            error_msg = result.stderr or "Unknown error"
            raise MountError(f"Mount command failed: {error_msg}")

        # Wait a moment for mount to establish
        time.sleep(2)

        # Verify mount
        if is_path_mounted(mount_path):
            console.print(f"[green]✓ Successfully mounted to {mount_path}[/green]")
            console.print(f"[green]✓ Sync profile: {profile.description}[/green]")
        else:
            raise MountError("Mount command succeeded but path is not mounted")

    except MountError:
        raise
    except Exception as e:
        raise MountError(f"Unexpected error during mount: {e}") from e


def unmount_cloud_files(tenant_id: Optional[str] = None) -> None:
    """Unmount cloud files."""

    try:
        # Get tenant info if not provided
        if not tenant_id:
            tenant_info = asyncio.run(get_tenant_info())
            tenant_id = tenant_info.get("tenant_id")

            if not tenant_id:
                raise MountError("Could not determine tenant ID")

        mount_path = get_default_mount_path()

        if not is_path_mounted(mount_path):
            console.print(f"[yellow]Path {mount_path} is not mounted[/yellow]")
            return

        console.print(f"[blue]Unmounting {mount_path}...[/blue]")

        # Unmount the path
        if unmount_path(mount_path):
            console.print(f"[green]✓ Successfully unmounted {mount_path}[/green]")

            # Clean up any orphaned rclone processes
            killed_count = cleanup_orphaned_rclone_processes()
            if killed_count > 0:
                console.print(
                    f"[green]✓ Cleaned up {killed_count} orphaned rclone process(es)[/green]"
                )
        else:
            console.print(f"[red]✗ Failed to unmount {mount_path}[/red]")
            console.print("You may need to manually unmount or restart your system")

    except MountError:
        raise
    except Exception as e:
        raise MountError(f"Unexpected error during unmount: {e}") from e


def show_mount_status() -> None:
    """Show current mount status and running processes."""

    try:
        # Get tenant info
        tenant_info = asyncio.run(get_tenant_info())
        tenant_id = tenant_info.get("tenant_id")

        if not tenant_id:
            console.print("[red]Could not determine tenant ID[/red]")
            return

        mount_path = get_default_mount_path()

        # Create status table
        table = Table(title="Cloud Mount Status", show_header=True, header_style="bold blue")
        table.add_column("Property", style="green", min_width=15)
        table.add_column("Value", style="dim", min_width=30)

        # Check mount status
        is_mounted = is_path_mounted(mount_path)
        mount_status = "[green]✓ Mounted[/green]" if is_mounted else "[red]✗ Not mounted[/red]"

        table.add_row("Tenant ID", tenant_id)
        table.add_row("Mount Path", str(mount_path))
        table.add_row("Status", mount_status)

        # Get rclone processes
        processes = get_rclone_processes()
        if processes:
            table.add_row("rclone Processes", f"{len(processes)} running")
        else:
            table.add_row("rclone Processes", "None")

        console.print(table)

        # Show running processes details
        if processes:
            console.print("\n[bold]Running rclone processes:[/bold]")
            for proc in processes:
                console.print(f"  PID {proc['pid']}: {proc['command'][:80]}...")

        # Show mount profiles
        console.print("\n[bold]Available mount profiles:[/bold]")
        for name, profile in MOUNT_PROFILES.items():
            console.print(f"  {name}: {profile.description}")

    except Exception as e:
        console.print(f"[red]Error getting mount status: {e}[/red]")
        raise typer.Exit(1)
