"""Directional transfers (`bm cloud push` / `bm cloud pull`) over WebDAV.

On Personal workspaces these transfers run through rclone against object
storage. That requires tenant-scoped storage credentials, which are scoped to a
whole bucket and therefore cannot express "this member may read project A but
not project B" — on a Team workspace they would grant more access than the
service itself does, and minting them is restricted to workspace owners anyway,
so members were simply stuck (#1262).

The WebDAV surface enforces access where it belongs: every request is authorized
against the caller's access to the specific project. This module is the same
transfer engine over that transport, and it keeps the same contract:

- additive — nothing is ever deleted on the destination
- files that differ on both sides are conflicts, resolved only by an explicit
  ``--on-conflict`` choice
- deletions are not propagated (see #862)

Comparison is by entity tag plus size, falling back to last-modified plus size
when the service reports no entity tag we can treat as a content hash. See
``_compare`` for why that fallback errs toward reporting a conflict.
"""

import hashlib
import os
import tempfile
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Literal

import httpx
from rich.console import Console

from basic_memory.cli.commands.cloud.transfer import (
    ConflictStrategy,
    TransferDirection,
    TransferPlan,
    conflict_copy_name,
    strategy_overwrites_dest,
)
from basic_memory.cli.commands.cloud.webdav import (
    RemoteFile,
    WebdavError,
    download_file,
    etag_content_hash,
    list_project_files,
    upload_file,
)
from basic_memory.ignore_utils import load_gitignore_patterns, should_ignore_path
from basic_memory.mcp.async_client import get_cloud_proxy_client

console = Console()

ClientFactory = Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]

# How far two timestamps may drift and still be considered the same instant.
# HTTP-date has one-second resolution, so a local mtime of 10.9s and a reported
# time of 10s describe the same write. Kept tight on purpose: widening this
# window trades a rare spurious conflict for the risk of calling two different
# files identical, and losing an edit is the worse outcome.
MODIFY_WINDOW_SECONDS = 1.0

_HASH_CHUNK_BYTES = 1024 * 1024

# Whether two copies of a path hold the same bytes. "unknown" means the question
# could not be answered at all — never a silent "same".
Comparison = Literal["same", "differ", "unknown"]


@dataclass(frozen=True)
class LocalFile:
    """One file on this machine, addressed the same way the cloud addresses it."""

    path: str  # project-relative POSIX path
    size: int
    mtime: float


# --- Entry points used by the CLI ---


async def webdav_project_diff(
    project: str,
    local_root: Path,
    direction: TransferDirection,
    *,
    workspace_id: str,
    client_cm_factory: ClientFactory | None = None,
) -> TransferPlan:
    """Classify how local and cloud differ, without transferring anything.

    Mirrors ``project_diff`` on the rclone path: the caller inspects the plan and
    decides whether to abort before any bytes move.

    Raises:
        WebdavError: If the project cannot be listed.
    """
    cm_factory = client_cm_factory or partial(get_cloud_proxy_client, workspace=workspace_id)
    async with cm_factory() as client:
        remote_files = await list_project_files(client, project)

    return build_transfer_plan(
        local_root=local_root,
        remote_files=remote_files,
        direction=direction,
    )


async def webdav_project_transfer(
    project: str,
    local_root: Path,
    direction: TransferDirection,
    plan: TransferPlan,
    *,
    workspace_id: str,
    strategy: ConflictStrategy = "fail",
    conflict_suffix: str = "",
    dry_run: bool = False,
    verbose: bool = False,
    client_cm_factory: ClientFactory | None = None,
) -> None:
    """Execute a directional transfer for the chosen conflict strategy.

    Callers detect conflicts with ``webdav_project_diff`` first and abort when
    ``strategy == "fail"`` and conflicts exist; this function assumes that gate
    has already passed and applies the resolution.

    Raises:
        WebdavError: If any transfer fails, or if the cloud names a file that
            would be written outside the project directory.
    """
    # keep-both: preserve the destination's version and drop the incoming one
    # beside it as a conflict copy, then do an additive (new-only) pass.
    renames = (
        [(rel_path, conflict_copy_name(rel_path, conflict_suffix)) for rel_path in plan.conflicts]
        if strategy == "keep-both"
        else []
    )

    overwrite = strategy_overwrites_dest(direction, strategy)
    copies = [(rel_path, rel_path) for rel_path in plan.new]
    if overwrite:
        copies.extend((rel_path, rel_path) for rel_path in plan.conflicts)

    transfers = renames + copies
    if not transfers:
        console.print("[dim]Nothing to transfer.[/dim]")
        return

    if dry_run:
        console.print(f"[dim]Dry run: {len(transfers)} file(s) would be transferred.[/dim]")
        for source_rel, dest_rel in transfers:
            suffix = "" if source_rel == dest_rel else f" -> {dest_rel}"
            console.print(f"  [dim]{source_rel}{suffix}[/dim]")
        return

    cm_factory = client_cm_factory or partial(get_cloud_proxy_client, workspace=workspace_id)
    async with cm_factory() as client:
        for source_rel, dest_rel in transfers:
            if verbose:
                suffix = "" if source_rel == dest_rel else f" -> {dest_rel}"
                console.print(f"  {source_rel}{suffix}")
            if direction == "pull":
                await _pull_file(client, project, local_root, source_rel, dest_rel)
            else:
                await _push_file(client, project, local_root, source_rel, dest_rel)

    console.print(f"[dim]Transferred {len(transfers)} file(s).[/dim]")


# --- Planning ---


def build_transfer_plan(
    *,
    local_root: Path,
    remote_files: list[RemoteFile],
    direction: TransferDirection,
) -> TransferPlan:
    """Compare both sides and classify every path into the transfer plan.

    The ignore patterns are applied to the cloud listing as well as the local
    scan, so an ignored path is invisible on both sides — the same thing rclone's
    ``--filter-from`` does for the Personal path.
    """
    ignore_patterns = load_gitignore_patterns(local_root, use_gitignore=False)
    local_files = scan_local_files(local_root, ignore_patterns)

    remote_by_path: dict[str, RemoteFile] = {}
    for remote in remote_files:
        # Validate here rather than at write time: a listing that names a path
        # outside the project is a broken or hostile response, and the user
        # should see that before a plan is presented, not mid-transfer.
        local_equivalent = _safe_local_path(local_root, remote.path)
        if should_ignore_path(local_equivalent, local_root, ignore_patterns):
            continue
        remote_by_path[remote.path] = remote

    source_paths, dest_paths = (
        (set(remote_by_path), set(local_files))
        if direction == "pull"
        else (set(local_files), set(remote_by_path))
    )

    plan = TransferPlan(
        new=sorted(source_paths - dest_paths),
        dest_only=sorted(dest_paths - source_paths),
    )

    for path in sorted(source_paths & dest_paths):
        comparison = _compare(local_files[path], remote_by_path[path], local_root)
        if comparison == "differ":
            plan.conflicts.append(path)
        elif comparison == "unknown":
            plan.errors.append(path)

    return plan


def scan_local_files(local_root: Path, ignore_patterns: set[str]) -> dict[str, LocalFile]:
    """Walk the project directory, skipping anything the ignore patterns match.

    Only ``.bmignore`` patterns apply, matching the filter the rclone path builds
    for push/pull. A project's ``.gitignore`` deliberately does not participate:
    it is scoped to `bm cloud upload`, and honoring it here would make a transfer
    depend on which machine ran it.
    """
    files: dict[str, LocalFile] = {}

    for root, dirs, filenames in os.walk(local_root):
        root_path = Path(root)
        dirs[:] = [
            name
            for name in dirs
            if not should_ignore_path(root_path / name, local_root, ignore_patterns)
        ]

        for filename in filenames:
            file_path = root_path / filename
            if should_ignore_path(file_path, local_root, ignore_patterns):
                continue
            stat = file_path.stat()
            rel_path = file_path.relative_to(local_root).as_posix()
            files[rel_path] = LocalFile(path=rel_path, size=stat.st_size, mtime=stat.st_mtime)

    return files


def _compare(local: LocalFile, remote: RemoteFile, local_root: Path) -> Comparison:
    """Decide whether two copies of a path hold the same bytes.

    Size settles it whenever it differs, and is checked first so a large file is
    never read just to learn what its length already proved.

    When the entity tag is a content hash, the comparison is exact. When it is
    not — absent, opaque, or the multipart ``-N`` shape — the fallback is
    last-modified plus size, and matching timestamps are required for a "same"
    verdict. That errs toward reporting a conflict, which the user can resolve
    with an explicit ``--on-conflict`` choice; the opposite error would silently
    skip a file that really did change and lose an edit.

    The fallback is deliberately the weaker path. A pull carries the cloud's
    timestamp onto the local copy, so the two line up afterwards; a push cannot,
    because the stored timestamp is when the write landed rather than when the
    file was edited. A file that lacks a usable entity tag and was last pushed
    from here will therefore keep reporting as a conflict until the tag is
    comparable again — visible and recoverable, unlike a lost edit.
    """
    if local.size != remote.size:
        return "differ"

    content_hash = etag_content_hash(remote.etag)
    if content_hash is not None:
        return "same" if _file_content_hash(local_root / local.path) == content_hash else "differ"

    if remote.modified is None:
        return "unknown"

    drift = abs(remote.modified.timestamp() - local.mtime)
    return "same" if drift <= MODIFY_WINDOW_SECONDS else "differ"


def _file_content_hash(path: Path) -> str:
    """Hash a local file for comparison against the store's entity tag.

    MD5 is not a choice here — it is the digest the object store reports for a
    single-part object. This is a content fingerprint, never a security control.
    """
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- Single-file transfers ---


async def _pull_file(
    client: httpx.AsyncClient,
    project: str,
    local_root: Path,
    source_rel: str,
    dest_rel: str,
) -> None:
    """Download one cloud file and land it under the destination path."""
    target = _safe_local_path(local_root, dest_rel)
    downloaded = await download_file(client, project, source_rel)

    target.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling temp file and rename into place: an interrupted pull
    # must never leave a half-written note in the user's knowledge base.
    handle, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".part"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(downloaded.content)
        # Carry the cloud's timestamp onto the local copy, the way rclone does.
        # Without it every pulled file would look freshly modified, and the
        # last-modified fallback in _compare could never report a match.
        if downloaded.modified is not None:
            stamp = downloaded.modified.timestamp()
            os.utime(temp_path, (stamp, stamp))
        os.replace(temp_path, target)
    finally:
        # A no-op once the rename succeeded; the cleanup path when it did not.
        temp_path.unlink(missing_ok=True)


async def _push_file(
    client: httpx.AsyncClient,
    project: str,
    local_root: Path,
    source_rel: str,
    dest_rel: str,
) -> None:
    """Upload one local file to the destination path in the cloud project."""
    source = _safe_local_path(local_root, source_rel)
    stat = source.stat()
    await upload_file(
        client,
        project,
        dest_rel,
        content=source.read_bytes(),
        mtime=int(stat.st_mtime),
    )


def _safe_local_path(local_root: Path, rel_path: str) -> Path:
    """Resolve a project-relative path, refusing anything that escapes the project.

    On pull these paths come from the service's listing, so remote input decides
    where this machine writes. An absolute path, a ``..`` segment, or a Windows
    separator must never be honored.

    Raises:
        WebdavError: If the path would resolve outside the project directory.
    """
    candidate = PurePosixPath(rel_path)
    if "\\" in rel_path or candidate.is_absolute() or ".." in candidate.parts:
        raise WebdavError(f"Refusing to transfer a path outside the project: {rel_path!r}")
    return local_root.joinpath(*candidate.parts)
