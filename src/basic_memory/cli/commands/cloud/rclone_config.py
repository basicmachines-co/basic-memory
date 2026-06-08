"""rclone configuration management for Basic Memory Cloud.

This module provides simplified rclone configuration for SPEC-20.
Uses a single "basic-memory-cloud" remote for all operations.
"""

import configparser
import os
import re
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


# The default remote serves the account's default tenant (back-compat with SPEC-20,
# which used a single "basic-memory-cloud" remote). Non-default workspaces each get
# their own remote, since Tigris credentials are bucket/tenant-scoped (see #919).
DEFAULT_RCLONE_REMOTE = "basic-memory-cloud"


# rclone remote section names allow letters, digits, hyphens, and underscores.
# The slug comes from the cloud API (a trust boundary), so validate it before
# splicing it into a remote name to avoid a broken/unusable rclone.conf section.
_SAFE_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")


def remote_name_for_workspace(slug: str | None, *, is_default: bool) -> str:
    """Return the rclone remote name for a workspace.

    The default workspace keeps the legacy ``basic-memory-cloud`` remote so
    existing setups keep working; other workspaces get ``basic-memory-cloud-<slug>``.

    Raises:
        RcloneConfigError: If a non-default workspace slug contains characters
            that are not valid in an rclone remote name.
    """
    if is_default or not slug:
        return DEFAULT_RCLONE_REMOTE
    if not _SAFE_SLUG.match(slug):
        raise RcloneConfigError(
            f"Workspace slug '{slug}' cannot be used as an rclone remote name "
            "(allowed: letters, digits, hyphens, underscores)."
        )
    return f"{DEFAULT_RCLONE_REMOTE}-{slug}"


def rclone_remote_exists(remote_name: str) -> bool:
    """Return whether an rclone remote section is already configured."""
    return load_rclone_config().has_section(remote_name)


def configure_rclone_remote(
    access_key: str,
    secret_key: str,
    endpoint: str = "https://fly.storage.tigris.dev",
    region: str = "auto",
    remote_name: str = DEFAULT_RCLONE_REMOTE,
) -> str:
    """Configure an rclone remote for one tenant's bucket.

    Each tenant (personal or team) has its own bucket-scoped credentials, so a
    remote maps 1:1 to a tenant. The default tenant keeps ``basic-memory-cloud``;
    other workspaces pass ``remote_name`` from :func:`remote_name_for_workspace`.

    Args:
        access_key: S3 access key ID
        secret_key: S3 secret access key
        endpoint: S3-compatible endpoint URL
        region: S3 region (default: auto)
        remote_name: rclone remote section name to write

    Returns:
        The remote name that was configured
    """
    # Backup existing config
    backup_rclone_config()

    # Load existing config
    config = load_rclone_config()

    # Add/update the remote section
    if not config.has_section(remote_name):
        config.add_section(remote_name)

    config.set(remote_name, "type", "s3")
    config.set(remote_name, "provider", "Other")
    config.set(remote_name, "access_key_id", access_key)
    config.set(remote_name, "secret_access_key", secret_key)
    config.set(remote_name, "endpoint", endpoint)
    config.set(remote_name, "region", region)
    # Prevent unnecessary encoding of filenames (only encode slashes and invalid UTF-8)
    # This prevents files with spaces like "Hello World.md" from being quoted
    config.set(remote_name, "encoding", "Slash,InvalidUtf8")
    # Save updated config
    save_rclone_config(config)

    console.print(f"[green]Configured rclone remote: {remote_name}[/green]")
    return remote_name
