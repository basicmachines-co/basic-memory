"""A private target must never be staged in a more public file (#1906)."""

import os
import stat
from pathlib import Path

import pytest

from basic_memory.file_utils import _create_atomic_temp_path


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o644])
def test_staging_permissions_precede_content(tmp_path: Path, mode: int):
    target = tmp_path / "note.md"
    target.write_text("old private content")
    target.chmod(mode)
    previous_umask = os.umask(0)
    try:
        staging, target_mode = _create_atomic_temp_path(target)
    finally:
        os.umask(previous_umask)
    try:
        assert target_mode == mode
        assert stat.S_IMODE(staging.stat().st_mode) & ~mode == 0
        assert staging.read_bytes() == b""
    finally:
        staging.unlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_content_inode_is_private_at_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "note.md"
    target.write_text("private")
    target.chmod(0o600)
    real_open = os.open
    created_modes: list[int] = []

    def observe_open(path: str | Path, flags: int, mode: int = 0o777) -> int:
        descriptor = real_open(path, flags, mode)
        # Observe the real inode immediately after creation, before chmod can
        # hide an exposure window. No filesystem behavior is replaced.
        if Path(path).name.startswith(".note.md."):
            created_modes.append(stat.S_IMODE(os.fstat(descriptor).st_mode))
        return descriptor

    monkeypatch.setattr(os, "open", observe_open)
    previous_umask = os.umask(0)
    try:
        staging, _ = _create_atomic_temp_path(target)
    finally:
        os.umask(previous_umask)
    staging.unlink()
    assert created_modes == [0o600]
