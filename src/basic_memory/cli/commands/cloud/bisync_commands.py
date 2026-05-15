"""Cloud bisync utility functions for Basic Memory CLI."""

from pathlib import Path

from basic_memory.cli.commands.cloud.api_client import make_api_request
from basic_memory.config import ConfigManager
from basic_memory.ignore_utils import create_default_bmignore, get_bmignore_path
from basic_memory.schemas.cloud import MountCredentials, TenantMountInfo


class BisyncError(Exception):
    """Exception raised for bisync-related errors."""

    pass


def _rclone_exclude_filters(pattern: str) -> list[str]:
    """Return rclone exclude filters for a gitignore-style pattern."""
    if pattern.endswith("/"):
        # Trigger: gitignore-style patterns ending in / are directory-only rules.
        # Why: stripping the slash would also exclude a same-named file.
        # Outcome: rclone keeps the directory rule and excludes recursive contents.
        return [f"- {pattern}", f"- {pattern}**"]

    path_pattern = pattern.removesuffix("/**")

    # Trigger: rclone treats a directory contents filter separately from the
    # directory/file path itself.
    # Why: files like config.json and directory markers like .obsidian must both
    # be excluded, along with anything below matching directories.
    # Outcome: every ignore pattern excludes the direct match and recursive children.
    return [f"- {path_pattern}", f"- {path_pattern}/**"]


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

                patterns.extend(_rclone_exclude_filters(line))

    except Exception:
        # If we can't read the file, create a minimal filter
        patterns = ["# Error reading .bmignore, using minimal filters", "- .git", "- .git/**"]

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
