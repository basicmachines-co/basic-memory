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
- a path the plan cleared as new is only ever created, never used to replace
  something that arrived in the meantime — enforced at the write itself, by an
  exclusive create on pull and a conditional create on push
- deletions are not propagated (see #862)

Comparison is by entity tag plus size, falling back to last-modified plus size
when the service reports no entity tag we can treat as a content hash. See
``_compare`` for why that fallback errs toward reporting a conflict, and
``_drop_appeared_on_cloud`` for how a stale plan is kept from overwriting a note
nobody compared.
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
    DownloadedFile,
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


@dataclass(frozen=True)
class _Transfer:
    """One file to move, and whether it may replace something already there.

    ``create_only`` carries the plan's classification forward: a path the diff
    called `new` was cleared to be created, never to overwrite. That distinction
    is what makes the destination re-check below safe to act on.
    """

    source_rel: str
    dest_rel: str
    create_only: bool

    def describe(self) -> str:
        if self.source_rel == self.dest_rel:
            return self.source_rel
        return f"{self.source_rel} -> {self.dest_rel}"


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
        [
            _Transfer(rel_path, conflict_copy_name(rel_path, conflict_suffix), create_only=True)
            for rel_path in plan.conflicts
        ]
        if strategy == "keep-both"
        else []
    )

    # A path the plan called `new` must only ever be created. A path the user
    # resolved with keep-local/keep-cloud is an instruction to overwrite.
    overwrite = strategy_overwrites_dest(direction, strategy)
    copies = [_Transfer(rel_path, rel_path, create_only=True) for rel_path in plan.new]
    if overwrite:
        copies.extend(
            _Transfer(rel_path, rel_path, create_only=False) for rel_path in plan.conflicts
        )

    transfers = renames + copies
    if not transfers:
        console.print("[dim]Nothing to transfer.[/dim]")
        return

    if dry_run:
        console.print(f"[dim]Dry run: {len(transfers)} file(s) would be transferred.[/dim]")
        for transfer in transfers:
            console.print(f"  [dim]{transfer.describe()}[/dim]")
        return

    cm_factory = client_cm_factory or partial(get_cloud_proxy_client, workspace=workspace_id)
    async with cm_factory() as client:
        if direction == "push":
            transfers, appeared = await _drop_appeared_on_cloud(client, project, transfers)
        else:
            appeared = []

        transferred = 0
        for transfer in transfers:
            if verbose:
                console.print(f"  {transfer.describe()}")
            if direction == "pull":
                written = await _pull_file(client, project, local_root, transfer)
            else:
                written = await _push_file(client, project, local_root, transfer)
            if not written:
                appeared.append(transfer.dest_rel)
                continue
            transferred += 1

    console.print(f"[dim]Transferred {transferred} file(s).[/dim]")
    _report_appeared(appeared)


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

    Links are not followed and symlinked files are skipped, so push can never
    read bytes from outside the project boundary — the same rule the local
    project scanner applies for the same reason.
    """
    files: dict[str, LocalFile] = {}

    for root, dirs, filenames in os.walk(local_root, followlinks=False):
        root_path = Path(root)
        dirs[:] = [
            name
            for name in dirs
            if not (root_path / name).is_symlink()
            and not should_ignore_path(root_path / name, local_root, ignore_patterns)
        ]

        for filename in filenames:
            file_path = root_path / filename
            if file_path.is_symlink():
                continue
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


# --- Guarding against a destination that moved under the plan ---
#
# The plan is a snapshot. Between classifying a path as `new` and writing it, the
# destination can gain that path — a teammate's push, another machine's pull, the
# user's own editor. Acting on the stale classification would destroy a note that
# nobody asked to replace, even under the default `--on-conflict fail`.
#
# The Personal path does not have this problem, and not because of its plan:
# `project_copy` passes `--ignore-existing`, and rclone evaluates that against
# the destination listing it makes at copy time, not against the earlier
# `rclone check`. So a file that appeared in between is skipped there.
#
# What actually holds the line here is a precondition evaluated at the write
# itself, per direction: an exclusive create on pull, and `If-None-Match: *` on
# push. Both are atomic with the write, so no listing this client made — however
# recent — is standing between a teammate and their note. The re-list below is
# an optimization and a reporting aid, not the correctness boundary: it spares
# pointless round trips and names what was skipped, and it is allowed to be
# stale, because the write refuses on its own.


async def _drop_appeared_on_cloud(
    client: httpx.AsyncClient, project: str, transfers: list[_Transfer]
) -> tuple[list[_Transfer], list[str]]:
    """Re-read the cloud and drop create-only pushes whose path now exists.

    The analogue of rclone re-listing the destination at copy time: it avoids
    uploading bytes that are certain to be refused, and it names the collisions
    up front instead of one at a time. It is deliberately not the safety
    mechanism — the window between this listing and the Nth PUT grows with every
    file ahead of it in the queue. ``If-None-Match: *`` on each create-only
    upload is what closes that window.
    """
    create_only = {transfer.dest_rel for transfer in transfers if transfer.create_only}
    if not create_only:
        return transfers, []

    existing = {remote.path for remote in await list_project_files(client, project)}
    appeared = sorted(create_only & existing)
    if not appeared:
        return transfers, []

    kept = [
        transfer
        for transfer in transfers
        if not (transfer.create_only and transfer.dest_rel in existing)
    ]
    return kept, appeared


def _report_appeared(appeared: list[str]) -> None:
    """Name the files that were left alone because the destination gained them."""
    if not appeared:
        return

    console.print(
        f"[yellow]{len(appeared)} file(s) appeared on the destination after this transfer "
        "was planned and were left untouched:[/yellow]"
    )
    for path in appeared:
        console.print(f"  [yellow]*[/yellow] {path}")
    console.print("[dim]Re-run to compare them and resolve with --on-conflict.[/dim]")


# --- Single-file transfers ---


async def _pull_file(
    client: httpx.AsyncClient,
    project: str,
    local_root: Path,
    transfer: _Transfer,
) -> bool:
    """Download one cloud file and land it under the destination path.

    Nothing is created at the destination until the bytes exist: the download
    lands in a sibling temp file, and only then is the name claimed. Returns
    False when a create-only transfer found the name already taken, so the
    caller can report it instead of replacing a note it never compared.
    """
    target = _safe_local_path(local_root, transfer.dest_rel)
    if not transfer.create_only:
        _refuse_symlink(target, transfer.dest_rel)

    downloaded = await download_file(client, project, transfer.source_rel)

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _write_temp_file(target, downloaded)
    try:
        if transfer.create_only:
            return _publish_new(temp_path, target, downloaded)
        # An explicit keep-cloud is an instruction to replace what is there.
        os.replace(temp_path, target)
        return True
    finally:
        # After a rename the temp name is already gone; after a link or a
        # direct write it is the copy to drop.
        temp_path.unlink(missing_ok=True)


def _write_temp_file(target: Path, downloaded: DownloadedFile) -> Path:
    """Stage the downloaded bytes beside the destination, fully written."""
    handle, temp_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".part"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(downloaded.content)
        # The timestamp is set here, before publication, because it lives on the
        # inode — a hardlinked publish shares it, and there is no window in which
        # the published note carries the wrong mtime.
        _apply_modified(temp_path, downloaded)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    return temp_path


def _publish_new(temp_path: Path, target: Path, downloaded: DownloadedFile) -> bool:
    """Claim a name that nothing else holds, atomically.

    ``os.link`` is the atomic no-replace publish: it fails outright when the
    name is taken, so a note that appeared during the download is never
    destroyed, and no reader ever observes a half-written file.
    """
    try:
        os.link(temp_path, target)
        return True
    except FileExistsError:
        return False
    except OSError:
        # Trigger: the filesystem cannot hardlink at all — exFAT, and the
        # virtual/network mounts this project already accommodates elsewhere.
        # Why: refusing outright would break pull for those users, and the
        # property that has to hold — never destroying a note that appeared —
        # does not actually need links. An exclusive create claims the name just
        # as atomically.
        # Outcome: the same no-clobber guarantee, weaker only in that a reader
        # can catch the new file mid-write. No unlinked filesystem can do
        # better, and a real failure (no space, no permission) still surfaces
        # from the create below rather than being swallowed here.
        return _publish_new_without_link(target, downloaded)


def _publish_new_without_link(target: Path, downloaded: DownloadedFile) -> bool:
    """Claim the name with an exclusive create, then write through it."""
    try:
        handle = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False

    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(downloaded.content)
    except BaseException:
        # Never leave a note behind that holds none of the content.
        target.unlink(missing_ok=True)
        raise

    _apply_modified(target, downloaded)
    return True


def _apply_modified(path: Path, downloaded: DownloadedFile) -> None:
    """Carry the cloud's timestamp onto the local copy, the way rclone does.

    Without it every pulled file would look freshly modified, and the
    last-modified fallback in ``_compare`` could never report a match.
    """
    if downloaded.modified is None:
        return
    stamp = downloaded.modified.timestamp()
    os.utime(path, (stamp, stamp))


async def _push_file(
    client: httpx.AsyncClient,
    project: str,
    local_root: Path,
    transfer: _Transfer,
) -> bool:
    """Upload one local file to the destination path in the cloud project.

    Returns False when a create-only upload was refused because the path now
    exists in the cloud, mirroring the pull side.
    """
    source = _safe_local_path(local_root, transfer.source_rel)
    _refuse_symlink(source, transfer.source_rel)
    stat = source.stat()
    return await upload_file(
        client,
        project,
        transfer.dest_rel,
        content=source.read_bytes(),
        mtime=int(stat.st_mtime),
        create_only=transfer.create_only,
    )


def _safe_local_path(local_root: Path, rel_path: str) -> Path:
    """Resolve a project-relative path, refusing anything that escapes the project.

    On pull these paths come from the service's listing, so remote input decides
    where this machine writes. An absolute path, a ``..`` segment, or a Windows
    separator must never be honored.

    Those are lexical checks, and a lexical check cannot see a link. The parent
    chain — the part that actually decides which directory is read from or
    written into — is resolved and required to stay inside the project.
    Otherwise a symlinked directory would let push read bytes from outside the
    project into a shared workspace, and let pull write through to somewhere the
    user never pointed at.

    The final component is left to the caller: whether a link there should be
    refused or simply treated as "already taken" depends on what the transfer is
    about to do to it.

    Raises:
        WebdavError: If the path would resolve outside the project directory.
    """
    candidate = PurePosixPath(rel_path)
    if "\\" in rel_path or candidate.is_absolute() or ".." in candidate.parts:
        raise WebdavError(f"Refusing to transfer a path outside the project: {rel_path!r}")

    target = local_root.joinpath(*candidate.parts)
    root = Path(os.path.realpath(local_root))
    parent = Path(os.path.realpath(target.parent))
    if parent != root and root not in parent.parents:
        raise WebdavError(f"Refusing to transfer through a link out of the project: {rel_path!r}")

    return target


def _refuse_symlink(path: Path, rel_path: str) -> None:
    """Refuse to read from, or write over, a path that is itself a link.

    Used where the transfer would otherwise follow it: reading a push source, or
    replacing a file the user resolved with keep-cloud. Create-only pulls need no
    such check — their exclusive create already refuses a link outright.
    """
    if path.is_symlink():
        raise WebdavError(f"Refusing to transfer a symlinked path: {rel_path!r}")
