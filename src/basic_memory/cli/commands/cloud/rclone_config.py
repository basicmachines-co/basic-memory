"""rclone configuration management for Basic Memory Cloud."""

import configparser
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console

console = Console()


class RcloneConfigError(Exception):
    """Exception raised for rclone configuration errors."""

    pass


class RcloneMountProfile:
    """Mount profile with optimized settings."""

    def __init__(
        self,
        name: str,
        cache_time: str,
        poll_interval: str,
        attr_timeout: str,
        write_back: str,
        description: str,
        extra_args: Optional[List[str]] = None,
    ):
        self.name = name
        self.cache_time = cache_time
        self.poll_interval = poll_interval
        self.attr_timeout = attr_timeout
        self.write_back = write_back
        self.description = description
        self.extra_args = extra_args or []


# Mount profiles based on SPEC-7 Phase 4 testing
MOUNT_PROFILES = {
    "fast": RcloneMountProfile(
        name="fast",
        cache_time="5s",
        poll_interval="3s",
        attr_timeout="3s",
        write_back="1s",
        description="Ultra-fast development (5s sync, higher bandwidth)",
    ),
    "balanced": RcloneMountProfile(
        name="balanced",
        cache_time="10s",
        poll_interval="5s",
        attr_timeout="5s",
        write_back="2s",
        description="Fast development (10-15s sync, recommended)",
    ),
    "safe": RcloneMountProfile(
        name="safe",
        cache_time="15s",
        poll_interval="10s",
        attr_timeout="10s",
        write_back="5s",
        description="Conflict-aware mount with backup",
        extra_args=[
            "--conflict-suffix",
            ".conflict-{DateTimeExt}",
            "--backup-dir",
            "~/.basic-memory/conflicts",
            "--track-renames",
        ],
    ),
}


def get_rclone_config_path() -> Path:
    """Get the path to rclone configuration file."""
    config_dir = Path.home() / ".config" / "rclone"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "rclone.conf"


def backup_rclone_config() -> Optional[Path]:
    """Create a backup of existing rclone config."""
    config_path = get_rclone_config_path()
    if not config_path.exists():
        return None

    backup_path = config_path.with_suffix(f".conf.backup-{os.getpid()}")
    shutil.copy2(config_path, backup_path)
    console.print(f"[dim]Created backup: {backup_path}[/dim]")
    return backup_path


def load_rclone_config() -> configparser.ConfigParser:
    """Load existing rclone configuration."""
    config = configparser.ConfigParser()
    config_path = get_rclone_config_path()

    if config_path.exists():
        config.read(config_path)

    return config


def save_rclone_config(config: configparser.ConfigParser) -> None:
    """Save rclone configuration to file."""
    config_path = get_rclone_config_path()

    with open(config_path, "w") as f:
        config.write(f)

    console.print(f"[dim]Updated rclone config: {config_path}[/dim]")


def add_tenant_to_rclone_config(
    tenant_id: str,
    bucket_name: str,
    access_key: str,
    secret_key: str,
    endpoint: str = "https://fly.storage.tigris.dev",
    region: str = "auto",
) -> str:
    """Add tenant configuration to rclone config file."""

    # Backup existing config
    backup_rclone_config()

    # Load existing config
    config = load_rclone_config()

    # Create section name
    section_name = f"basic-memory-{tenant_id}"

    # Add/update the tenant section
    if not config.has_section(section_name):
        config.add_section(section_name)

    config.set(section_name, "type", "s3")
    config.set(section_name, "provider", "Other")
    config.set(section_name, "access_key_id", access_key)
    config.set(section_name, "secret_access_key", secret_key)
    config.set(section_name, "endpoint", endpoint)
    config.set(section_name, "region", region)

    # Save updated config
    save_rclone_config(config)

    console.print(f"[green]✓ Added tenant {tenant_id} to rclone config[/green]")
    return section_name


def remove_tenant_from_rclone_config(tenant_id: str) -> bool:
    """Remove tenant configuration from rclone config."""
    config = load_rclone_config()
    section_name = f"basic-memory-{tenant_id}"

    if config.has_section(section_name):
        backup_rclone_config()
        config.remove_section(section_name)
        save_rclone_config(config)
        console.print(f"[green]✓ Removed tenant {tenant_id} from rclone config[/green]")
        return True

    return False


def get_default_mount_path() -> Path:
    """Get default mount path (fixed location per SPEC-9).

    Returns:
        Fixed mount path: ~/basic-memory-cloud/
    """
    return Path.home() / "basic-memory-cloud"


def build_mount_command(
    tenant_id: str, bucket_name: str, mount_path: Path, profile: RcloneMountProfile
) -> List[str]:
    """Build rclone mount command with optimized settings."""

    rclone_remote = f"basic-memory-{tenant_id}:{bucket_name}"

    cmd = [
        "rclone",
        "nfsmount",
        rclone_remote,
        str(mount_path),
        "--vfs-cache-mode",
        "writes",
        "--dir-cache-time",
        profile.cache_time,
        "--vfs-cache-poll-interval",
        profile.poll_interval,
        "--attr-timeout",
        profile.attr_timeout,
        "--vfs-write-back",
        profile.write_back,
        "--daemon",
    ]

    # Add profile-specific extra arguments
    cmd.extend(profile.extra_args)

    return cmd


def is_path_mounted(mount_path: Path) -> bool:
    """Check if a path is currently mounted."""
    if not mount_path.exists():
        return False

    try:
        # Check if mount point is actually mounted by looking for mount table entry
        result = subprocess.run(["mount"], capture_output=True, text=True, check=False)

        if result.returncode == 0:
            # Look for our mount path in mount output
            mount_str = str(mount_path.resolve())
            return mount_str in result.stdout

        return False
    except Exception:
        return False


def get_rclone_processes() -> List[Dict[str, str]]:
    """Get list of running rclone processes."""
    try:
        # Use ps to find rclone processes
        result = subprocess.run(
            ["ps", "-eo", "pid,args"], capture_output=True, text=True, check=False
        )

        processes = []
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "rclone" in line and "basic-memory" in line:
                    parts = line.strip().split(None, 1)
                    if len(parts) >= 2:
                        processes.append({"pid": parts[0], "command": parts[1]})

        return processes
    except Exception:
        return []


def kill_rclone_process(pid: str) -> bool:
    """Kill a specific rclone process."""
    try:
        subprocess.run(["kill", pid], check=True)
        console.print(f"[green]✓ Killed rclone process {pid}[/green]")
        return True
    except subprocess.CalledProcessError:
        console.print(f"[red]✗ Failed to kill rclone process {pid}[/red]")
        return False


def unmount_path(mount_path: Path) -> bool:
    """Unmount a mounted path."""
    if not is_path_mounted(mount_path):
        return True

    try:
        subprocess.run(["umount", str(mount_path)], check=True)
        console.print(f"[green]✓ Unmounted {mount_path}[/green]")
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗ Failed to unmount {mount_path}: {e}[/red]")
        return False


def cleanup_orphaned_rclone_processes() -> int:
    """Clean up orphaned rclone processes for basic-memory."""
    processes = get_rclone_processes()
    killed_count = 0

    for proc in processes:
        console.print(
            f"[yellow]Found rclone process: {proc['pid']} - {proc['command'][:80]}...[/yellow]"
        )
        if kill_rclone_process(proc["pid"]):
            killed_count += 1

    return killed_count
