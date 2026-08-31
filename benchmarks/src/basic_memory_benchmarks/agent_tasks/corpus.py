"""Seed-corpus handling: checksum, per-task copies, timestamps, baseline.

The corpus is a hand-written fixture checked into
``benchmarks/datasets/agent-tasks/corpus``. Every task gets a fresh copy of
the full corpus (one snapshot — the fairness contract), with file mtimes aged
deterministically so recency-window tasks have a stable gold set.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path
from types import MappingProxyType

DEFAULT_AGE_DAYS = 30
RECENT_AGE_DAYS = 1
SECONDS_PER_DAY = 86_400

# File age in days, applied via os.utime after each per-task copy and before
# `bm project add`, so BM's sync derives updated_at from a controlled mtime.
# Exactly these three files sit inside the recency window that
# `continue-recent-window` grades on; everything else is DEFAULT_AGE_DAYS old.
TIMESTAMPS: MappingProxyType[str, int] = MappingProxyType(
    {
        "worklog/2026-08-27-spec-9-session-2.md": RECENT_AGE_DAYS,
        "worklog/2026-08-27-infra-notes.md": RECENT_AGE_DAYS,
        "tasks/migrate-ci-to-uv.md": RECENT_AGE_DAYS,
    }
)


def corpus_files(corpus_dir: Path) -> list[str]:
    """Sorted relpaths of every file in the corpus.

    Every extension, not just ``.md``: dataset corpora (xAFS) carry
    non-markdown resources (``.eml`` mails) that the checksum, copy, and
    timestamp passes must all see. The shipped agent-tasks corpus is md-only,
    so its behavior is unchanged.
    """
    return sorted(
        str(path.relative_to(corpus_dir)) for path in corpus_dir.rglob("*") if path.is_file()
    )


def corpus_checksum(corpus_dir: Path) -> tuple[str, int]:
    """Content fingerprint over (relpath, bytes) pairs; pins the corpus snapshot."""
    digest = hashlib.sha256()
    relpaths = corpus_files(corpus_dir)
    for relpath in relpaths:
        digest.update(relpath.encode("utf-8"))
        digest.update(b"\x00")
        digest.update((corpus_dir / relpath).read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest(), len(relpaths)


def copy_corpus(corpus_dir: Path, project_dir: Path, *, now: float | None = None) -> None:
    """Copy the corpus into a fresh project dir and apply the aged mtimes."""
    if project_dir.exists():
        raise RuntimeError(f"Project dir already exists: {project_dir}")
    shutil.copytree(corpus_dir, project_dir)
    apply_timestamps(project_dir, now=now)


def apply_timestamps(project_dir: Path, *, now: float | None = None) -> None:
    resolved_now = time.time() if now is None else now
    for relpath in corpus_files(project_dir):
        age_days = TIMESTAMPS.get(relpath, DEFAULT_AGE_DAYS)
        stamp = resolved_now - age_days * SECONDS_PER_DAY
        os.utime(project_dir / relpath, (stamp, stamp))


def snapshot_baseline(project_dir: Path) -> dict[str, str]:
    """relpath -> file text for the markdown notes only (taken after seed settle).

    Deliberately narrower than ``corpus_files``: state-tracking graders reason
    about markdown notes only (``grading._markdown_relpaths``), and a corpus
    with a binary resource would crash ``read_text`` here.
    """
    return {
        relpath: (project_dir / relpath).read_text(encoding="utf-8")
        for relpath in corpus_files(project_dir)
        if relpath.endswith(".md")
    }
