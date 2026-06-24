"""Shared utility functions."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_info() -> tuple[str, str]:
    return platform.platform(), sys.version.split()[0]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_command(
    args: list[str],
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=check, env=env)


def git_sha(path: Path) -> str | None:
    try:
        result = run_command(["git", "-C", str(path), "rev-parse", "HEAD"])
    except subprocess.CalledProcessError:
        return None
    return result.stdout.strip() or None


def resolve_remote_main_sha(repo_url: str) -> str | None:
    try:
        result = run_command(["git", "ls-remote", repo_url, "refs/heads/main"])
    except subprocess.CalledProcessError:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    return raw.split()[0]


def safe_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default
