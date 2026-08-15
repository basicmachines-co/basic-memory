"""Transport-agnostic vocabulary for directional transfers (push / pull).

`bm cloud push` / `bm cloud pull` present one contract to the user — additive
transfers that never delete on the destination, with files that differ on both
sides surfaced rather than silently resolved — but they reach the cloud over two
different transports: rclone against object storage on Personal workspaces, and
the service's WebDAV surface on Team workspaces (see #1262).

These types are that shared contract. They live here so neither transport has to
import the other: the WebDAV engine needs the plan vocabulary without dragging in
rclone's subprocess machinery, and vice versa.
"""

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

# push = local -> cloud, pull = cloud -> local.
TransferDirection = Literal["push", "pull"]

# How a directional transfer treats files that differ on both sides. "fail" is
# the safe default: the caller is expected to abort before any transfer runs.
ConflictStrategy = Literal["fail", "keep-local", "keep-cloud", "keep-both"]


@dataclass
class TransferPlan:
    """Classification of how local and cloud differ for a directional transfer.

    Paths are relative to the project root. ``conflicts`` are files present on
    both sides with differing content — without a sync baseline (see #862) every
    divergence is a conflict, because we cannot tell a teammate's edit from a
    stale local copy.
    """

    new: list[str] = field(default_factory=list)  # only on source → safe to bring over
    conflicts: list[str] = field(default_factory=list)  # differ on both sides
    dest_only: list[str] = field(default_factory=list)  # only on destination → left untouched
    errors: list[str] = field(default_factory=list)  # could not be compared at all


def conflict_copy_name(rel_path: str, suffix: str) -> str:
    """Insert a ``.conflict-<suffix>`` marker before the extension of a rel path."""
    p = PurePosixPath(rel_path)
    return str(p.with_name(f"{p.stem}.conflict-{suffix}{p.suffix}"))


def strategy_overwrites_dest(direction: TransferDirection, strategy: ConflictStrategy) -> bool:
    """True when the strategy lets the source side overwrite the destination.

    The source side is cloud on pull, local on push. "keep-cloud" wins on pull,
    "keep-local" wins on push; otherwise the destination is preserved.
    """
    if strategy == "keep-cloud":
        return direction == "pull"
    if strategy == "keep-local":
        return direction == "push"
    return False  # "fail" (no conflicts) and "keep-both" never overwrite existing dest files
