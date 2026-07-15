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


def _rclone_include_filters(pattern: str) -> list[str]:
    """Return rclone include filters selecting exactly what a pattern ignores.

    Inverse of _rclone_exclude_filters, used by `bm cloud prune` to target
    already-uploaded ignored files for deletion (#1032). Directory-only
    patterns (trailing /) need just the contents rule: prune deletes files,
    and a bare `+ cache` would also select a same-named *file* that the
    directory-only exclude never hid.
    """
    if pattern.endswith("/"):
        return [f"+ {pattern}**"]

    path_pattern = pattern.removesuffix("/**")
    return [f"+ {path_pattern}", f"+ {path_pattern}/**"]


def _workspace_id_header(workspace_id: str | None) -> dict[str, str]:
    """Header that routes a /tenant/mount/* request to a specific tenant.

    The mount endpoints resolve the workspace from X-Workspace-ID (validating
    membership + subscription) and fall back to the user's default tenant when
    it is absent — so omitting it preserves the original default-tenant behavior.
    """
    return {"X-Workspace-ID": workspace_id} if workspace_id else {}


async def get_mount_info(*, workspace_id: str | None = None) -> TenantMountInfo:
    """Get tenant mount info (bucket name + tenant id) from the cloud API.

    Args:
        workspace_id: Tenant id of the target workspace. When omitted, the API
            uses the authenticated user's default tenant.
    """
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        response = await make_api_request(
            method="GET",
            url=f"{host_url}/tenant/mount/info",
            headers=_workspace_id_header(workspace_id),
        )

        return TenantMountInfo.model_validate(response.json())
    except Exception as e:
        raise BisyncError(f"Failed to get tenant info: {e}") from e


async def generate_mount_credentials(tenant_id: str) -> MountCredentials:
    """Generate scoped S3 credentials for syncing a specific tenant's bucket.

    Args:
        tenant_id: Tenant id whose bucket-scoped credentials to mint. Routed via
            X-Workspace-ID so team workspaces get their own bucket's credentials.
    """
    try:
        config_manager = ConfigManager()
        config = config_manager.config
        host_url = config.cloud_host.rstrip("/")

        # The mount endpoints resolve X-Workspace-ID by matching the workspace's
        # tenant_id, so passing a tenant_id here is the correct routing key.
        response = await make_api_request(
            method="POST",
            url=f"{host_url}/tenant/mount/credentials",
            headers=_workspace_id_header(tenant_id),
        )

        return MountCredentials.model_validate(response.json())
    except Exception as e:
        raise BisyncError(f"Failed to generate credentials: {e}") from e


def convert_bmignore_to_rclone_filters(*, force: bool = False) -> Path:
    """Convert .bmignore patterns to rclone filter format.

    Reads ~/.basic-memory/.bmignore (gitignore-style) and converts to
    ~/.basic-memory/.bmignore.rclone (rclone filter format).

    Only regenerates if .bmignore has been modified since last conversion,
    unless force=True is used for a destructive filter consumer.

    Returns:
        Path to converted rclone filter file
    """
    # Ensure .bmignore exists
    create_default_bmignore()

    bmignore_path = get_bmignore_path()
    # Create rclone filter path: ~/.basic-memory/.bmignore -> ~/.basic-memory/.bmignore.rclone
    rclone_filter_path = bmignore_path.parent / f"{bmignore_path.name}.rclone"

    # Skip regeneration if rclone file is newer than bmignore
    if rclone_filter_path.exists() and not force:
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


def convert_bmignore_to_rclone_prune_filters() -> Path:
    """Convert .bmignore patterns into an *inverted* rclone filter for prune.

    Where the sync filter excludes ignored paths from a transfer, the prune
    filter includes exactly those paths and excludes everything else, so
    `rclone lsf` / `rclone delete` operate on the ignored set alone (#1032).

    Always regenerated (no mtime caching like the exclude converter), and any
    read error propagates instead of falling back to minimal filters: this
    filter sits in front of `rclone delete`, where a stale or guessed pattern
    set is a data loss hazard, and prune runs rarely enough that caching buys
    nothing.

    Returns:
        Path to the generated rclone filter file
    """
    # Ensure .bmignore exists
    create_default_bmignore()

    bmignore_path = get_bmignore_path()
    prune_filter_path = bmignore_path.parent / f"{bmignore_path.name}.rclone-prune"

    patterns = []
    with bmignore_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # Keep comments and empty lines
            if not line or line.startswith("#"):
                patterns.append(line)
                continue

            patterns.extend(_rclone_include_filters(line))

    # The terminating exclude-all makes the include rules exhaustive: anything
    # not matched above is protected from deletion. `*` is unanchored in rclone
    # globs, so it matches the final path element of every file at any depth.
    patterns.append("- *")

    prune_filter_path.write_text("\n".join(patterns) + "\n")

    return prune_filter_path


def get_bisync_filter_path() -> Path:
    """Get path to bisync filter file.

    Uses ~/.basic-memory/.bmignore (converted to rclone format).
    The file is automatically created with default patterns on first use.

    Returns:
        Path to rclone filter file
    """
    return convert_bmignore_to_rclone_filters()
