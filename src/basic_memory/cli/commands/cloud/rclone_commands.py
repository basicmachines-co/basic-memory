"""Project-scoped rclone sync commands for Basic Memory Cloud.

This module provides simplified, project-scoped rclone operations:
- Each project syncs independently
- Uses single "basic-memory-cloud" remote (not tenant-specific)
- Balanced defaults from SPEC-8 Phase 4 testing
- Per-project bisync state tracking

Replaces tenant-wide sync with project-scoped workflows.
"""

import configparser
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from rich.console import Console

from basic_memory.cli.commands.cloud.rclone_installer import is_rclone_installed
from basic_memory.utils import normalize_project_path

console = Console()


class RcloneError(Exception):
    """Exception raised for rclone command errors."""

    pass


def check_rclone_installed() -> None:
    """Check if rclone is installed and raise helpful error if not.

    Raises:
        RcloneError: If rclone is not installed with installation instructions
    """
    if not is_rclone_installed():
        raise RcloneError(
            "rclone is not installed.\n\n"
            "Install rclone by running: bm cloud setup\n"
            "Or install manually from: https://rclone.org/downloads/\n\n"
            "Windows users: Ensure you have a package manager installed (winget, chocolatey, or scoop)"
        )


@dataclass
class SyncProject:
    """Project configured for cloud sync.

    Attributes:
        name: Project name
        path: Cloud path (e.g., "app/data/research")
        local_sync_path: Local directory for syncing (optional)
    """

    name: str
    path: str
    local_sync_path: Optional[str] = None


def get_bmignore_filter_path() -> Path:
    """Get path to rclone filter file.

    Uses ~/.basic-memory/.bmignore converted to rclone format.
    File is automatically created with default patterns on first use.

    Returns:
        Path to rclone filter file
    """
    # Import here to avoid circular dependency
    from basic_memory.cli.commands.cloud.bisync_commands import (
        convert_bmignore_to_rclone_filters,
    )

    return convert_bmignore_to_rclone_filters()


def get_project_bisync_state(project_name: str) -> Path:
    """Get path to project's bisync state directory.

    Args:
        project_name: Name of the project

    Returns:
        Path to bisync state directory for this project
    """
    return Path.home() / ".basic-memory" / "bisync-state" / project_name


def bisync_initialized(project_name: str) -> bool:
    """Check if bisync has been initialized for this project.

    Args:
        project_name: Name of the project

    Returns:
        True if bisync state exists, False otherwise
    """
    state_path = get_project_bisync_state(project_name)
    return state_path.exists() and any(state_path.iterdir())


def get_rclone_config_path() -> Path:
    """Get path to rclone configuration file."""
    return Path.home() / ".config" / "rclone" / "rclone.conf"


def get_s3_credentials_from_rclone() -> tuple[str, str, str]:
    """Extract S3 credentials from rclone config.

    Returns:
        Tuple of (access_key, secret_key, endpoint)

    Raises:
        RcloneError: If rclone config is not found or missing credentials
    """
    config_path = get_rclone_config_path()
    if not config_path.exists():
        raise RcloneError(f"rclone config not found at {config_path}")

    config = configparser.ConfigParser()
    config.read(config_path)

    remote_name = "basic-memory-cloud"
    if not config.has_section(remote_name):
        raise RcloneError(f"rclone remote '{remote_name}' not found in config")

    try:
        access_key = config.get(remote_name, "access_key_id")
        secret_key = config.get(remote_name, "secret_access_key")
        endpoint = config.get(remote_name, "endpoint")
        return access_key, secret_key, endpoint
    except (configparser.NoOptionError, configparser.NoSectionError) as e:
        raise RcloneError(f"Missing S3 credentials in rclone config: {e}") from e


def create_folder_markers(
    local_path: Path,
    bucket_name: str,
    cloud_path: str,
    verbose: bool = False,
) -> int:
    """Create S3 folder markers for all directories in local path.

    TigrisFS requires folder marker objects (empty S3 objects with keys ending in '/')
    to recognize directory structure. Rclone bisync does not create these automatically.

    Args:
        local_path: Local directory to scan
        bucket_name: S3 bucket name
        cloud_path: Cloud path prefix (e.g., "project-name")
        verbose: Show detailed output

    Returns:
        Number of folder markers created

    Raises:
        RcloneError: If unable to create folder markers
    """
    try:
        # Get S3 credentials from rclone config
        access_key, secret_key, endpoint = get_s3_credentials_from_rclone()

        # Create S3 client
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint,
        )

        # Collect all directories (relative to local_path)
        directories = set()
        for item in local_path.rglob("*"):
            if item.is_dir():
                # Get path relative to local_path
                rel_path = item.relative_to(local_path)
                # Add all parent directories too
                for parent in [rel_path] + list(rel_path.parents):
                    if parent != Path("."):
                        directories.add(parent)

        # Create folder markers in S3
        created_count = 0
        for directory in sorted(directories):
            # Convert to S3 key with cloud_path prefix
            s3_key = f"{cloud_path.rstrip('/')}/{directory.as_posix()}/"

            try:
                # Check if marker already exists
                s3_client.head_object(Bucket=bucket_name, Key=s3_key)
                if verbose:
                    console.print(f"[dim]Folder marker exists: {s3_key}[/dim]")
            except ClientError as e:
                if e.response["Error"]["Code"] == "404":
                    # Marker doesn't exist, create it
                    s3_client.put_object(Bucket=bucket_name, Key=s3_key, Body=b"")
                    created_count += 1
                    if verbose:
                        console.print(f"[green]Created folder marker: {s3_key}[/green]")
                else:
                    # Other error, re-raise
                    raise

        return created_count

    except ClientError as e:
        raise RcloneError(f"S3 error while creating folder markers: {e}") from e
    except Exception as e:
        raise RcloneError(f"Failed to create folder markers: {e}") from e


def get_project_remote(project: SyncProject, bucket_name: str) -> str:
    """Build rclone remote path for project.

    Args:
        project: Project with cloud path
        bucket_name: S3 bucket name

    Returns:
        Remote path like "basic-memory-cloud:bucket-name/basic-memory-llc"

    Note:
        The API returns paths like "/app/data/basic-memory-llc" because the S3 bucket
        is mounted at /app/data on the fly machine. We need to strip the /app/data/
        prefix to get the actual S3 path within the bucket.
    """
    # Normalize path to strip /app/data/ mount point prefix
    cloud_path = normalize_project_path(project.path).lstrip("/")
    return f"basic-memory-cloud:{bucket_name}/{cloud_path}"


def project_sync(
    project: SyncProject,
    bucket_name: str,
    dry_run: bool = False,
    verbose: bool = False,
) -> bool:
    """One-way sync: local → cloud.

    Makes cloud identical to local using rclone sync.

    Args:
        project: Project to sync
        bucket_name: S3 bucket name
        dry_run: Preview changes without applying
        verbose: Show detailed output

    Returns:
        True if sync succeeded, False otherwise

    Raises:
        RcloneError: If project has no local_sync_path configured or rclone not installed
    """
    check_rclone_installed()

    if not project.local_sync_path:
        raise RcloneError(f"Project {project.name} has no local_sync_path configured")

    local_path = Path(project.local_sync_path).expanduser()
    remote_path = get_project_remote(project, bucket_name)
    filter_path = get_bmignore_filter_path()

    cmd = [
        "rclone",
        "sync",
        str(local_path),
        remote_path,
        "--filter-from",
        str(filter_path),
    ]

    if verbose:
        cmd.append("--verbose")
    else:
        cmd.append("--progress")

    if dry_run:
        cmd.append("--dry-run")

    result = subprocess.run(cmd, text=True)
    return result.returncode == 0


def project_bisync(
    project: SyncProject,
    bucket_name: str,
    dry_run: bool = False,
    resync: bool = False,
    verbose: bool = False,
) -> bool:
    """Two-way sync: local ↔ cloud.

    Uses rclone bisync with balanced defaults:
    - conflict_resolve: newer (auto-resolve to most recent)
    - max_delete: 25 (safety limit)
    - compare: modtime (ignore size differences from line ending conversions)
    - check_access: false (skip for performance)

    Args:
        project: Project to sync
        bucket_name: S3 bucket name
        dry_run: Preview changes without applying
        resync: Force resync to establish new baseline
        verbose: Show detailed output

    Returns:
        True if bisync succeeded, False otherwise

    Raises:
        RcloneError: If project has no local_sync_path, needs --resync, or rclone not installed
    """
    check_rclone_installed()

    if not project.local_sync_path:
        raise RcloneError(f"Project {project.name} has no local_sync_path configured")

    local_path = Path(project.local_sync_path).expanduser()
    remote_path = get_project_remote(project, bucket_name)
    filter_path = get_bmignore_filter_path()
    state_path = get_project_bisync_state(project.name)

    # Ensure state directory exists
    state_path.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rclone",
        "bisync",
        str(local_path),
        remote_path,
        "--create-empty-src-dirs",
        "--resilient",
        "--conflict-resolve=newer",
        "--max-delete=25",
        "--compare=modtime",  # Ignore size differences from line ending conversions
        "--filter-from",
        str(filter_path),
        "--workdir",
        str(state_path),
    ]

    if verbose:
        cmd.append("--verbose")
    else:
        cmd.append("--progress")

    if dry_run:
        cmd.append("--dry-run")

    if resync:
        cmd.append("--resync")

    # Check if first run requires resync
    if not resync and not bisync_initialized(project.name) and not dry_run:
        raise RcloneError(
            f"First bisync for {project.name} requires --resync to establish baseline.\n"
            f"Run: bm project bisync --name {project.name} --resync"
        )

    result = subprocess.run(cmd, text=True)
    success = result.returncode == 0

    # Create folder markers after successful bisync (not for dry runs)
    if success and not dry_run:
        try:
            # Normalize path to strip /app/data/ mount point prefix
            cloud_path = normalize_project_path(project.path).lstrip("/")

            console.print("[blue]Creating S3 folder markers for TigrisFS...[/blue]")
            created_count = create_folder_markers(
                local_path=local_path,
                bucket_name=bucket_name,
                cloud_path=cloud_path,
                verbose=verbose,
            )

            if created_count > 0:
                console.print(f"[green]Created {created_count} folder markers[/green]")
            else:
                console.print("[dim]All folder markers already exist[/dim]")

        except RcloneError as e:
            # Log warning but don't fail the bisync operation
            console.print(f"[yellow]Warning: Could not create folder markers: {e}[/yellow]")
            console.print(
                "[yellow]Files may not be visible in TigrisFS mount until markers are created[/yellow]"
            )

    return success


def project_check(
    project: SyncProject,
    bucket_name: str,
    one_way: bool = False,
) -> bool:
    """Check integrity between local and cloud.

    Verifies files match without transferring data.

    Args:
        project: Project to check
        bucket_name: S3 bucket name
        one_way: Only check for missing files on destination (faster)

    Returns:
        True if files match, False if differences found

    Raises:
        RcloneError: If project has no local_sync_path configured or rclone not installed
    """
    check_rclone_installed()

    if not project.local_sync_path:
        raise RcloneError(f"Project {project.name} has no local_sync_path configured")

    local_path = Path(project.local_sync_path).expanduser()
    remote_path = get_project_remote(project, bucket_name)
    filter_path = get_bmignore_filter_path()

    cmd = [
        "rclone",
        "check",
        str(local_path),
        remote_path,
        "--filter-from",
        str(filter_path),
    ]

    if one_way:
        cmd.append("--one-way")

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def project_ls(
    project: SyncProject,
    bucket_name: str,
    path: Optional[str] = None,
) -> list[str]:
    """List files in remote project.

    Args:
        project: Project to list files from
        bucket_name: S3 bucket name
        path: Optional subdirectory within project

    Returns:
        List of file paths

    Raises:
        subprocess.CalledProcessError: If rclone command fails
        RcloneError: If rclone is not installed
    """
    check_rclone_installed()

    remote_path = get_project_remote(project, bucket_name)
    if path:
        remote_path = f"{remote_path}/{path}"

    cmd = ["rclone", "ls", remote_path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.splitlines()
