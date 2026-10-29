"""Project-scoped rclone operations for Personal Basic Memory Cloud workspaces.

This module provides the rclone half of the Personal cloud commands:
- `push` / `pull` (additive `rclone check` + `rclone copy`; never delete)
- `prune` (confirmed deletion of cloud files matching .bmignore)
- `check` and `project ls`
- Routes through the project's tenant-scoped remote (SyncProject.remote_name);
  the default tenant keeps "basic-memory-cloud", others use their own (see #919)

The rclone mirror commands (`sync`, `bisync`) were removed in 1.0 (#1845).
"""

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

from rich.console import Console

from basic_memory.cli.commands.cloud.rclone_filters import (
    convert_bmignore_to_rclone_filters,
    convert_bmignore_to_rclone_prune_filters,
)
from basic_memory.cli.commands.cloud.rclone_installer import is_rclone_installed
from basic_memory.cli.commands.cloud.transfer import (
    ConflictStrategy,
    TransferDirection,
    TransferPlan,
    conflict_copy_name,
    strategy_overwrites_dest,
)
from basic_memory.utils import normalize_project_path

console = Console()

# Tigris edge caching returns stale data for users outside the origin region (iad).
# --header is rclone's global flag that applies to ALL HTTP transactions (list, download,
# upload). This is critical because `rclone check` and `rclone lsf` start with S3
# ListObjectsV2, which is neither a download nor upload — so
# --header-download/--header-upload would miss list requests.
# See: https://www.tigrisdata.com/docs/objects/consistency/
TIGRIS_CONSISTENCY_HEADERS = [
    "--header",
    "X-Tigris-Consistent: true",
]


class RunResult(Protocol):
    @property
    def returncode(self) -> int: ...

    @property
    def stdout(self) -> str: ...

    @property
    def stderr(self) -> str: ...


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

    Uses <config dir>/.bmignore converted to rclone format.
    File is automatically created with default patterns on first use.

    Returns:
        Path to rclone filter file
    """
    return convert_bmignore_to_rclone_filters()


def get_bmignore_prune_filter_path() -> Path:
    """Get path to the inverted rclone filter used by prune.

    Selects exactly the paths .bmignore ignores (#1032); regenerated from
    <config dir>/.bmignore on every call.

    Returns:
        Path to inverted rclone filter file
    """
    return convert_bmignore_to_rclone_prune_filters()


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
# These power the `bm cloud push` / `bm cloud pull` commands on Personal
# workspaces. Unlike the removed mirror commands (`sync`/`bisync`, #1845), they
# use `rclone copy` so they never delete on the destination, and conflicts are
# surfaced to the caller rather than silently resolved. See issue #858 for the
# full design rationale. Team workspaces run the same commands over the WebDAV
# transport instead (`webdav_transfer.py`, #1262); the plan vocabulary both
# transports share lives in `transfer.py`.


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
            dest_rel = conflict_copy_name(rel_path, conflict_suffix)
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

    overwrite = strategy_overwrites_dest(direction, strategy)
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


# --- Prune (issue #1032) ---
#
# The transfer filter hides .bmignore paths on both sides of a transfer, so
# a file uploaded before its pattern was added to .bmignore is stranded on the
# cloud tenant. Prune targets exactly that set: it uses the *inverted* filter
# (each ignore pattern becomes an include rule, everything else is excluded) so
# rclone lsf finds the ignored files. Deletion receives that exact preview list
# through stdin, so files uploaded or newly ignored during confirmation cannot
# be deleted without appearing in the user's approved preview.


def project_prune_preview(
    project: SyncProject,
    bucket_name: str,
    *,
    run: RunFunc = subprocess.run,
    is_installed: IsInstalledFunc = is_rclone_installed,
    filter_path: Path | None = None,
) -> list[str]:
    """List remote files that match the local .bmignore patterns.

    Uses ``rclone lsf`` with the inverted (include) filter so the preview and
    ``project_prune``'s deletion pass select exactly the same set. Purely
    remote: no local_sync_path required.

    Returns:
        Project-relative paths of remote files that .bmignore now ignores.

    Raises:
        RcloneError: If rclone is not installed or the listing fails.
    """
    check_rclone_installed(is_installed=is_installed)

    remote_path = get_project_remote(project, bucket_name)
    filter_path = filter_path or get_bmignore_prune_filter_path()

    cmd = [
        "rclone",
        "lsf",
        remote_path,
        *TIGRIS_CONSISTENCY_HEADERS,
        "--recursive",
        "--files-only",
        "--filter-from",
        str(filter_path),
    ]

    result = run(cmd, capture_output=True, text=True)

    # Unlike rclone check, lsf exits non-zero only on real failures (auth,
    # missing remote, network) — never because files matched. Fail fast so the
    # caller cannot mistake a broken listing for "nothing to prune".
    if result.returncode != 0:
        detail = result.stderr.strip() or f"rclone lsf exited with code {result.returncode}"
        raise RcloneError(f"Failed to list ignored cloud files for {project.name}: {detail}")

    return [line for line in result.stdout.splitlines() if line.strip()]


def project_prune(
    project: SyncProject,
    bucket_name: str,
    matches: Sequence[str],
    *,
    verbose: bool = False,
    run: RunFunc = subprocess.run,
    is_installed: IsInstalledFunc = is_rclone_installed,
) -> bool:
    """Delete exactly the remote files returned by the confirmed preview.

    Targeted cleanup for files uploaded before their pattern was added to
    .bmignore (#1032), without requiring a configured local_sync_path.
    Callers preview with ``project_prune_preview`` and pass the confirmed result
    here. ``--files-from-raw -`` keeps the delete set fixed even if the remote or
    .bmignore changes while the confirmation prompt is open.

    Returns:
        True if the deletion succeeded, False otherwise.

    Raises:
        RcloneError: If rclone is not installed.
    """
    check_rclone_installed(is_installed=is_installed)
    if not matches:
        return True

    remote_path = get_project_remote(project, bucket_name)

    cmd = [
        "rclone",
        "delete",
        remote_path,
        *TIGRIS_CONSISTENCY_HEADERS,
        "--files-from-raw",
        "-",
    ]

    if verbose:
        cmd.append("--verbose")

    result = run(cmd, input="".join(f"{path}\n" for path in matches), text=True)
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
