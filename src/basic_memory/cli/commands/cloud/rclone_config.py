"""rclone configuration management for Basic Memory Cloud.

This module provides simplified rclone configuration for SPEC-20.
Uses a single "basic-memory-cloud" remote for all operations.
"""

import configparser
import os
import shutil
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()


class RcloneConfigError(Exception):
    """Exception raised for rclone configuration errors."""

    pass


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


def configure_rclone_remote(
    access_key: str,
    secret_key: str,
    endpoint: str = "https://fly.storage.tigris.dev",
    region: str = "auto",
) -> str:
    """Configure single rclone remote named 'basic-memory-cloud'.

    This is the simplified approach from SPEC-20 that uses one remote
    for all Basic Memory cloud operations (not tenant-specific).

    Args:
        access_key: S3 access key ID
        secret_key: S3 secret access key
        endpoint: S3-compatible endpoint URL
        region: S3 region (default: auto)

    Returns:
        The remote name: "basic-memory-cloud"
    """
    # Backup existing config
    backup_rclone_config()

    # Load existing config
    config = load_rclone_config()

    # Single remote name (not tenant-specific)
    REMOTE_NAME = "basic-memory-cloud"

    # Add/update the remote section
    if not config.has_section(REMOTE_NAME):
        config.add_section(REMOTE_NAME)

    config.set(REMOTE_NAME, "type", "s3")
    config.set(REMOTE_NAME, "provider", "Other")
    config.set(REMOTE_NAME, "access_key_id", access_key)
    config.set(REMOTE_NAME, "secret_access_key", secret_key)
    config.set(REMOTE_NAME, "endpoint", endpoint)
    config.set(REMOTE_NAME, "region", region)
    # Prevent unnecessary encoding of filenames (only encode slashes and invalid UTF-8)
    # This prevents files with spaces like "Hello World.md" from being quoted
    config.set(REMOTE_NAME, "encoding", "Slash,InvalidUtf8")
    # Save updated config
    save_rclone_config(config)

    console.print(f"[green]Configured rclone remote: {REMOTE_NAME}[/green]")
    return REMOTE_NAME
