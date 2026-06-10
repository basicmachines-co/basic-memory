#!/usr/bin/env python3
"""Seed and refresh shared pytest-testmon data for Git worktrees."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

TESTMON_FILENAMES = (".testmondata", ".testmondata-shm", ".testmondata-wal")
TESTMON_CACHE_ENV = "BM_TESTMON_CACHE_DIR"


class TestmonCacheResult(NamedTuple):
    status: str
    source_dir: Path
    destination_dir: Path
    copied: tuple[Path, ...]


def _run_git(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def resolve_repo_root(repo_root: Path | None = None) -> Path:
    if repo_root is not None:
        return repo_root.expanduser().resolve()

    return Path(_run_git(["rev-parse", "--show-toplevel"], Path.cwd())).resolve()


def resolve_cache_dir(repo_root: Path, cache_dir: Path | None = None) -> Path:
    if cache_dir is not None:
        return cache_dir.expanduser().resolve()

    if env_cache_dir := os.environ.get(TESTMON_CACHE_ENV):
        return Path(env_cache_dir).expanduser().resolve()

    git_common_dir = Path(_run_git(["rev-parse", "--git-common-dir"], repo_root))
    if not git_common_dir.is_absolute():
        git_common_dir = repo_root / git_common_dir

    return git_common_dir.resolve() / "testmon-cache" / "main"


def _testmon_datafile(directory: Path) -> Path:
    return directory / ".testmondata"


def _testmon_files(directory: Path) -> list[Path]:
    return [
        directory / filename for filename in TESTMON_FILENAMES if (directory / filename).is_file()
    ]


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _copy_testmon_files(source_dir: Path, destination_dir: Path) -> tuple[Path, ...]:
    destination_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for source in _testmon_files(source_dir):
        destination = destination_dir / source.name
        shutil.copy2(source, destination)
        copied.append(destination)

    return tuple(copied)


def seed_testmon_data(repo_root: Path, cache_dir: Path) -> TestmonCacheResult:
    local_datafile = _testmon_datafile(repo_root)
    shared_datafile = _testmon_datafile(cache_dir)

    if local_datafile.exists():
        return TestmonCacheResult(
            status="exists",
            source_dir=cache_dir,
            destination_dir=repo_root,
            copied=(),
        )

    if not shared_datafile.exists():
        return TestmonCacheResult(
            status="missing",
            source_dir=cache_dir,
            destination_dir=repo_root,
            copied=(),
        )

    # A worktree with sidecars but no main database is stale; replace the set
    # together so SQLite never sees a mixed local/cache snapshot.
    for filename in TESTMON_FILENAMES:
        _remove_path(repo_root / filename)

    copied = _copy_testmon_files(cache_dir, repo_root)
    return TestmonCacheResult(
        status="seeded",
        source_dir=cache_dir,
        destination_dir=repo_root,
        copied=copied,
    )


def refresh_testmon_data(repo_root: Path, cache_dir: Path) -> TestmonCacheResult:
    local_datafile = _testmon_datafile(repo_root)

    if not local_datafile.exists():
        raise FileNotFoundError(
            f"No local pytest-testmon data at {local_datafile}; run tests first."
        )

    cache_parent = cache_dir.parent
    cache_parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{cache_dir.name}.", dir=cache_parent))
    backup_dir = cache_parent / f".{cache_dir.name}.previous-{os.getpid()}"
    copied: tuple[Path, ...] = ()

    try:
        copied = _copy_testmon_files(repo_root, temp_dir)

        _remove_path(backup_dir)
        if cache_dir.exists():
            cache_dir.rename(backup_dir)

        try:
            temp_dir.rename(cache_dir)
        except Exception:
            if backup_dir.exists() and not cache_dir.exists():
                backup_dir.rename(cache_dir)
            raise
    finally:
        _remove_path(temp_dir)
        _remove_path(backup_dir)

    return TestmonCacheResult(
        status="refreshed",
        source_dir=repo_root,
        destination_dir=cache_dir,
        copied=tuple(cache_dir / path.name for path in copied),
    )


def _print_seed_result(result: TestmonCacheResult) -> None:
    if result.status == "seeded":
        print(f"Seeded pytest-testmon data from {result.source_dir} into {result.destination_dir}")
    elif result.status == "exists":
        print(f"Local pytest-testmon data already exists at {result.destination_dir}")
    elif result.status == "missing":
        print(
            f"No shared pytest-testmon baseline at {result.source_dir}; "
            "run `just testmon-refresh` after a full backend test run to create one."
        )
    else:
        raise ValueError(f"Unexpected seed result: {result.status}")


def _print_refresh_result(result: TestmonCacheResult) -> None:
    print(f"Published pytest-testmon data from {result.source_dir} to {result.destination_dir}")


def _print_status(repo_root: Path, cache_dir: Path) -> None:
    print(f"Repo root:      {repo_root}")
    print(f"Worktree data:  {_testmon_datafile(repo_root)}")
    print(f"Shared cache:   {_testmon_datafile(cache_dir)}")
    print(f"Worktree ready: {_testmon_datafile(repo_root).exists()}")
    print(f"Cache ready:    {_testmon_datafile(cache_dir).exists()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="Repository root to operate on (default: git rev-parse --show-toplevel)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help=(
            "Shared testmon cache directory "
            f"(default: ${TESTMON_CACHE_ENV} or <git-common-dir>/testmon-cache/main)"
        ),
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed", help="Copy shared testmon data into this worktree if missing")
    subparsers.add_parser("refresh", help="Publish this worktree's testmon data to the cache")
    subparsers.add_parser("status", help="Show local and shared testmon data paths")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = resolve_repo_root(args.repo_root)
    cache_dir = resolve_cache_dir(repo_root, args.cache_dir)

    if args.command == "seed":
        _print_seed_result(seed_testmon_data(repo_root=repo_root, cache_dir=cache_dir))
        return 0

    if args.command == "refresh":
        _print_refresh_result(refresh_testmon_data(repo_root=repo_root, cache_dir=cache_dir))
        return 0

    if args.command == "status":
        _print_status(repo_root=repo_root, cache_dir=cache_dir)
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
