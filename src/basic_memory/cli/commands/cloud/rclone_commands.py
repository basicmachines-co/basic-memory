"""Project-scoped rclone sync commands for Basic Memory Cloud.

This module provides simplified, project-scoped rclone operations:
- Each project syncs independently
- Routes through the project's tenant-scoped remote (SyncProject.remote_name);
  the default tenant keeps "basic-memory-cloud", others use their own (see #919)
- Balanced defaults from SPEC-8 Phase 4 testing
- Per-project bisync state tracking

Replaces tenant-wide sync with project-scoped workflows.
"""

import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Callable, Literal, Optional, Protocol

from loguru import logger
from rich.console import Console

from basic_memory.cli.commands.cloud.rclone_installer import is_rclone_installed
from basic_memory.config import resolve_data_dir
from basic_memory.utils import normalize_project_path

console = Console()

# Minimum rclone version for --create-empty-src-dirs support
MIN_RCLONE_VERSION_EMPTY_DIRS = (1, 64, 0)

# Tigris edge caching returns stale data for users outside the origin region (iad).
# --header is rclone's global flag that applies to ALL HTTP transactions (list, download,
# upload). This is critical because bisync starts with S3 ListObjectsV2, which is neither
# a download nor upload — so --header-download/--header-upload would miss list requests.
# See: https://www.tigrisdata.com/docs/objects/consistency/
TIGRIS_CONSISTENCY_HEADERS = [
    "--header",
    "X-Tigris-Consistent: true",
]


class RunResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


RunFunc = Callable[..., RunResult]
IsInstalledFunc = Callable[[], bool]


class RcloneError(Exception):
    """Exception raised for rclone command errors."""

    pass


def check_rclone_installed(is_installed: IsInstalledFunc = is_rclone_installed) -> None:
    """Check if rclone is installed and raise helpful error if not.

    Raises:
        RcloneError: If rclone is not installed with installation instructions
    """
    if not is_installed():
        raise RcloneError(
            "rclone is not installed.\n\n"
            "Install rclone by running: bm cloud setup\n"
            "Or install manually from: https://rclone.org/downloads/\n\n"
            "Windows users: Ensure you have a package manager installed (winget, chocolatey, or scoop)"
        )


@lru_cache(maxsize=1)
def get_rclone_version(run: RunFunc = subprocess.run) -> tuple[int, int, int] | None:
    """Get rclone version as (major, minor, patch) tuple.

    Returns:
        Version tuple like (1, 64, 2), or None if version cannot be determined.

    Note:
        Result is cached since rclone version won't change during runtime.
    """
    try:
        result = run(["rclone", "version"], capture_output=True, text=True, timeout=10)
        # Parse "rclone v1.64.2" or "rclone v1.60.1-DEV"
        match = re.search(r"v(\d+)\.(\d+)\.(\d+)", result.stdout)
        if match:
            version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            logger.debug(f"Detected rclone version: {version}")
            return version
    except Exception as e:
        logger.warning(f"Could not determine rclone version: {e}")
    return None


def supports_create_empty_src_dirs(version: tuple[int, int, int] | None) -> bool:
    """Check if installed rclone supports --create-empty-src-dirs flag.

    Returns:
        True if rclone version >= 1.64.0, False otherwise.
    """
    if version is None:
        # If we can't determine version, assume older and skip the flag
        return False
    return version >= MIN_RCLONE_VERSION_EMPTY_DIRS


@dataclass
class SyncProject:
    """Project configured for cloud sync.

    Attributes:
        name: Project name
        path: Cloud path (e.g., "app/data/research")
        local_sync_path: Local directory for syncing (optional)
        remote_name: rclone remote serving this project's tenant bucket. Defaults
            to the legacy single remote; team/non-default workspaces use their own
            (see remote_name_for_workspace).
    """

    name: str
    path: str
    local_sync_path: Optional[str] = None
    remote_name: str = "basic-memory-cloud"


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

    Honors ``BASIC_MEMORY_CONFIG_DIR`` so isolated instances each keep their
    own bisync state alongside their config.

    Args:
        project_name: Name of the project

    Returns:
        Path to bisync state directory for this project
    """
    return resolve_data_dir() / "bisync-state" / project_name


def bisync_initialized(project_name: str) -> bool:
    """Check if bisync has been initialized for this project.

    Args:
        project_name: Name of the project

    Returns:
        True if bisync state exists, False otherwise
    """
    state_path = get_project_bisync_state(project_name)
    return state_path.exists() and any(state_path.iterdir())


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

        The remote name comes from the project so non-default/team workspaces route
        through their own tenant-scoped remote (see #919).
    """
    # Normalize path to strip /app/data/ mount point prefix
    cloud_path = normalize_project_path(project.path).lstrip("/")
    return f"{project.remote_name}:{bucket_name}/{cloud_path}"


# --- Directional transfer primitives (push / pull) ---
#
# These power the Team-safe `bm cloud push` / `bm cloud pull` commands. Unlike
# the mirror operations (`sync`/`bisync`), they use `rclone copy` so they never
# delete on the destination, and conflicts are surfaced to the caller rather
# than silently resolved. See issue #858 for the full design rationale.

# push = local -> cloud, pull = cloud -> local.
TransferDirection = Literal["push", "pull"]

# How a directional transfer treats files that differ on both sides. "fail" is
# the safe default: the caller is expected to abort before any transfer runs.
ConflictStrategy = Literal["fail", "keep-local", "keep-cloud", "keep-both"]


@dataclass
class TransferPlan:
    """Classification of how local and cloud differ for a directional transfer.

    Built from ``rclone check --combined``. Paths are relative to the project
    root. ``conflicts`` are files present on both sides with differing content —
    without a sync baseline (see #862) every divergence is a conflict, because
    we cannot tell a teammate's edit from a stale local copy.
    """

    new: list[str] = field(default_factory=list)  # only on source → safe to bring over
    conflicts: list[str] = field(default_factory=list)  # differ on both sides
    dest_only: list[str] = field(default_factory=list)  # only on destination → left untouched
    errors: list[str] = field(default_factory=list)  # rclone could not read/hash


def _transfer_endpoints(project: SyncProject, bucket_name: str) -> tuple[str, str]:
    """Return (local_path, remote_path) strings for a project's transfer.

    Raises:
        RcloneError: If the project has no local_sync_path configured.
    """
    if not project.local_sync_path:
        raise RcloneError(f"Project {project.name} has no local_sync_path configured")
    local_path = str(Path(project.local_sync_path).expanduser())
    remote_path = get_project_remote(project, bucket_name)
    return local_path, remote_path


def _build_transfer_cmd(
    operation: str,
    source: str,
    dest: str,
    *,
    filter_path: Path,
    dry_run: bool,
    verbose: bool,
    extra_flags: tuple[str, ...] = (),
) -> list[str]:
    """Build an rclone sync/copy command with the shared Basic Memory flags.

    All directional transfers share the same tail: Tigris consistency headers,
    the .bmignore filter, and --local-no-preallocate (a no-op when local is the
    source, required when local is the destination on pull — see rclone#6801).
    """
    cmd = [
        "rclone",
        operation,
        source,
        dest,
        *TIGRIS_CONSISTENCY_HEADERS,
        "--filter-from",
        str(filter_path),
        # Prevent NUL byte padding on virtual filesystems (e.g. Google Drive File Stream)
        # See: rclone/rclone#6801
        "--local-no-preallocate",
        *extra_flags,
    ]

    if verbose:
        cmd.append("--verbose")
    else:
        cmd.append("--progress")

    if dry_run:
        cmd.append("--dry-run")

    return cmd


def project_sync(
    project: SyncProject,
    bucket_name: str,
    dry_run: bool = False,
    verbose: bool = False,
    *,
    run: RunFunc = subprocess.run,
    is_installed: IsInstalledFunc = is_rclone_installed,
    filter_path: Path | None = None,
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
    check_rclone_installed(is_installed=is_installed)

    if not project.local_sync_path:
        raise RcloneError(f"Project {project.name} has no local_sync_path configured")

    local_path = Path(project.local_sync_path).expanduser()
    remote_path = get_project_remote(project, bucket_name)
    filter_path = filter_path or get_bmignore_filter_path()

    cmd = _build_transfer_cmd(
        "sync",
        str(local_path),
        remote_path,
        filter_path=filter_path,
        dry_run=dry_run,
        verbose=verbose,
    )

    result = run(cmd, text=True)
    return result.returncode == 0


def _parse_check_combined(output: str) -> TransferPlan:
    """Parse ``rclone check --combined`` output into a TransferPlan.

    rclone emits one prefixed line per path (src is the transfer source):
      ``=`` identical, ``+`` only on src, ``-`` only on dst, ``*`` differ,
      ``!`` error reading/hashing. We ignore identical files.
    """
    plan = TransferPlan()
    for line in output.splitlines():
        symbol, _, path = line.partition(" ")
        path = path.strip()
        if not path:
            continue
        if symbol == "+":
            plan.new.append(path)
        elif symbol == "*":
            plan.conflicts.append(path)
        elif symbol == "-":
            plan.dest_only.append(path)
        elif symbol == "!":
            plan.errors.append(path)
        # "=" (identical) is intentionally dropped.
    return plan


def project_diff(
    project: SyncProject,
    bucket_name: str,
    direction: TransferDirection,
    *,
    run: RunFunc = subprocess.run,
    is_installed: IsInstalledFunc = is_rclone_installed,
    filter_path: Path | None = None,
) -> TransferPlan:
    """Classify how local and cloud differ for a push/pull, without transferring.

    Uses ``rclone check`` (content comparison) so the caller can surface
    conflicts before any data moves. The source side depends on direction:
    pull compares cloud→local, push compares local→cloud.

    Raises:
        RcloneError: If project has no local_sync_path configured or rclone not installed
    """
    check_rclone_installed(is_installed=is_installed)

    local_path, remote_path = _transfer_endpoints(project, bucket_name)
    filter_path = filter_path or get_bmignore_filter_path()

    # Source/dest order matters: rclone check reports "+" for files only on the
    # source, which is what we want to bring over.
    source, dest = (remote_path, local_path) if direction == "pull" else (local_path, remote_path)

    cmd = [
        "rclone",
        "check",
        source,
        dest,
        *TIGRIS_CONSISTENCY_HEADERS,
        "--filter-from",
        str(filter_path),
        "--combined",
        "-",
    ]

    # rclone check exits non-zero when files differ — that's expected here, so we
    # parse the combined listing rather than trusting the return code.
    result = run(cmd, capture_output=True, text=True)
    plan = _parse_check_combined(result.stdout)

    # Trigger: non-zero exit AND the combined listing produced no entries at all.
    # Why: a difference always yields +/-/*/! lines, so an empty listing on a
    # non-zero exit means the check itself failed (auth, missing remote, network,
    # bad filter) rather than finding zero differences. Without this guard the
    # caller would see an empty plan, transfer nothing, and report success.
    # Outcome: fail fast with rclone's stderr instead of a silent no-op.
    if result.returncode != 0 and not (plan.new or plan.conflicts or plan.dest_only or plan.errors):
        detail = result.stderr.strip() or f"rclone check exited with code {result.returncode}"
        raise RcloneError(f"Failed to compare {project.name} with cloud: {detail}")

    return plan


def project_copy(
    project: SyncProject,
    bucket_name: str,
    direction: TransferDirection,
    *,
    overwrite: bool,
    dry_run: bool = False,
    verbose: bool = False,
    run: RunFunc = subprocess.run,
    is_installed: IsInstalledFunc = is_rclone_installed,
    filter_path: Path | None = None,
) -> bool:
    """Additive transfer via ``rclone copy`` — never deletes on the destination.

    Trigger: ``overwrite=False`` adds ``--ignore-existing`` so files already on
    the destination are left as-is (used when the destination side wins a
    conflict, and for the no-conflict fast path).
    Why: keeps the loser's bytes intact unless the caller explicitly chose to
    overwrite, matching the "no surprises" contract.

    Raises:
        RcloneError: If project has no local_sync_path configured or rclone not installed
    """
    check_rclone_installed(is_installed=is_installed)

    local_path, remote_path = _transfer_endpoints(project, bucket_name)
    filter_path = filter_path or get_bmignore_filter_path()

    source, dest = (remote_path, local_path) if direction == "pull" else (local_path, remote_path)
    # Overwrite mode compares by checksum so the transfer decision matches
    # project_diff's content-based conflict detection (rclone check). Without
    # --checksum, copy's default size+modtime comparison could skip a file the
    # diff flagged as a conflict (same size, destination not older) — silently
    # ignoring the user's explicit keep-cloud/keep-local choice. New-only mode
    # uses --ignore-existing, which skips by existence so the comparison basis
    # does not matter.
    extra_flags = ("--checksum",) if overwrite else ("--ignore-existing",)

    cmd = _build_transfer_cmd(
        "copy",
        source,
        dest,
        filter_path=filter_path,
        dry_run=dry_run,
        verbose=verbose,
        extra_flags=extra_flags,
    )

    result = run(cmd, text=True)
    return result.returncode == 0


def _conflict_copy_name(rel_path: str, suffix: str) -> str:
    """Insert a ``.conflict-<suffix>`` marker before the extension of a rel path."""
    p = PurePosixPath(rel_path)
    return str(p.with_name(f"{p.stem}.conflict-{suffix}{p.suffix}"))


def project_copy_file(
    project: SyncProject,
    bucket_name: str,
    direction: TransferDirection,
    source_rel_path: str,
    dest_rel_path: str,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    run: RunFunc = subprocess.run,
    is_installed: IsInstalledFunc = is_rclone_installed,
) -> bool:
    """Copy a single file from source to destination under a (possibly renamed) path.

    Used for the ``keep-both`` strategy: the incoming version is written beside
    the destination's own copy as ``name.conflict-<date>`` so nothing is lost.

    Raises:
        RcloneError: If project has no local_sync_path configured or rclone not installed
    """
    check_rclone_installed(is_installed=is_installed)

    local_path, remote_path = _transfer_endpoints(project, bucket_name)
    source_root, dest_root = (
        (remote_path, local_path) if direction == "pull" else (local_path, remote_path)
    )

    cmd = [
        "rclone",
        "copyto",
        f"{source_root}/{source_rel_path}",
        f"{dest_root}/{dest_rel_path}",
        *TIGRIS_CONSISTENCY_HEADERS,
        # Matches _build_transfer_cmd: on pull this writes the conflict copy to
        # the local filesystem, where this prevents NUL byte padding on virtual
        # filesystems (e.g. Google Drive File Stream). See rclone/rclone#6801.
        "--local-no-preallocate",
    ]
    if verbose:
        cmd.append("--verbose")
    if dry_run:
        cmd.append("--dry-run")

    result = run(cmd, text=True)
    return result.returncode == 0


def _strategy_overwrites_dest(direction: TransferDirection, strategy: ConflictStrategy) -> bool:
    """True when the strategy lets the source side overwrite the destination.

    The source side is cloud on pull, local on push. "keep-cloud" wins on pull,
    "keep-local" wins on push; otherwise the destination is preserved.
    """
    if strategy == "keep-cloud":
        return direction == "pull"
    if strategy == "keep-local":
        return direction == "push"
    return False  # "fail" (no conflicts) and "keep-both" never overwrite existing dest files


def project_transfer(
    project: SyncProject,
    bucket_name: str,
    direction: TransferDirection,
    plan: TransferPlan,
    *,
    strategy: ConflictStrategy = "fail",
    conflict_suffix: str = "",
    dry_run: bool = False,
    verbose: bool = False,
    run: RunFunc = subprocess.run,
    is_installed: IsInstalledFunc = is_rclone_installed,
    filter_path: Path | None = None,
) -> bool:
    """Execute a directional transfer for the chosen conflict strategy.

    Callers detect conflicts with ``project_diff`` first and abort when
    ``strategy == "fail"`` and conflicts exist; this function assumes that gate
    has already passed and applies the resolution.
    """
    # keep-both: preserve the destination's version and drop the incoming one
    # beside it as a conflict copy, then do an additive (new-only) pass.
    if strategy == "keep-both":
        for rel_path in plan.conflicts:
            dest_rel = _conflict_copy_name(rel_path, conflict_suffix)
            copied = project_copy_file(
                project,
                bucket_name,
                direction,
                rel_path,
                dest_rel,
                dry_run=dry_run,
                verbose=verbose,
                run=run,
                is_installed=is_installed,
            )
            if not copied:
                return False

    overwrite = _strategy_overwrites_dest(direction, strategy)
    return project_copy(
        project,
        bucket_name,
        direction,
        overwrite=overwrite,
        dry_run=dry_run,
        verbose=verbose,
        run=run,
        is_installed=is_installed,
        filter_path=filter_path,
    )


def project_bisync(
    project: SyncProject,
    bucket_name: str,
    dry_run: bool = False,
    resync: bool = False,
    verbose: bool = False,
    *,
    run: RunFunc = subprocess.run,
    is_installed: IsInstalledFunc = is_rclone_installed,
    version: tuple[int, int, int] | None = None,
    filter_path: Path | None = None,
    state_path: Path | None = None,
    is_initialized: Callable[[str], bool] = bisync_initialized,
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
    check_rclone_installed(is_installed=is_installed)

    if not project.local_sync_path:
        raise RcloneError(f"Project {project.name} has no local_sync_path configured")

    local_path = Path(project.local_sync_path).expanduser()
    remote_path = get_project_remote(project, bucket_name)
    filter_path = filter_path or get_bmignore_filter_path()
    state_path = state_path or get_project_bisync_state(project.name)

    # Ensure state directory exists
    state_path.mkdir(parents=True, exist_ok=True)

    cmd = [
        "rclone",
        "bisync",
        str(local_path),
        remote_path,
        *TIGRIS_CONSISTENCY_HEADERS,
        "--resilient",
        "--conflict-resolve=newer",
        "--max-delete=25",
        "--compare=modtime",  # Ignore size differences from line ending conversions
        "--filter-from",
        str(filter_path),
        "--workdir",
        str(state_path),
        # Prevent NUL byte padding on virtual filesystems (e.g. Google Drive File Stream)
        # See: rclone/rclone#6801
        "--local-no-preallocate",
    ]

    # Add --create-empty-src-dirs if rclone version supports it (v1.64+)
    version = version if version is not None else get_rclone_version(run=run)
    if supports_create_empty_src_dirs(version):
        cmd.append("--create-empty-src-dirs")

    if verbose:
        cmd.append("--verbose")
    else:
        cmd.append("--progress")

    if dry_run:
        cmd.append("--dry-run")

    if resync:
        cmd.append("--resync")

    # Check if first run requires resync
    if not resync and not is_initialized(project.name) and not dry_run:
        raise RcloneError(
            f"First bisync for {project.name} requires --resync to establish baseline.\n"
            f"Run: bm project bisync --name {project.name} --resync"
        )

    result = run(cmd, text=True)
    return result.returncode == 0


def project_check(
    project: SyncProject,
    bucket_name: str,
    one_way: bool = False,
    *,
    run: RunFunc = subprocess.run,
    is_installed: IsInstalledFunc = is_rclone_installed,
    filter_path: Path | None = None,
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
    check_rclone_installed(is_installed=is_installed)

    if not project.local_sync_path:
        raise RcloneError(f"Project {project.name} has no local_sync_path configured")

    local_path = Path(project.local_sync_path).expanduser()
    remote_path = get_project_remote(project, bucket_name)
    filter_path = filter_path or get_bmignore_filter_path()

    cmd = [
        "rclone",
        "check",
        str(local_path),
        remote_path,
        *TIGRIS_CONSISTENCY_HEADERS,
        "--filter-from",
        str(filter_path),
    ]

    if one_way:
        cmd.append("--one-way")

    result = run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def project_ls(
    project: SyncProject,
    bucket_name: str,
    path: Optional[str] = None,
    *,
    run: RunFunc = subprocess.run,
    is_installed: IsInstalledFunc = is_rclone_installed,
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
    check_rclone_installed(is_installed=is_installed)

    remote_path = get_project_remote(project, bucket_name)
    if path:
        remote_path = f"{remote_path}/{path}"

    cmd = ["rclone", "ls", *TIGRIS_CONSISTENCY_HEADERS, remote_path]
    result = run(cmd, capture_output=True, text=True, check=True)
    return result.stdout.splitlines()
