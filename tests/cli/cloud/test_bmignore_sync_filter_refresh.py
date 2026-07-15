"""Destructive one-way sync must never use a stale .bmignore filter."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from basic_memory.cli.commands.cloud import rclone_commands
from basic_memory.cli.commands.cloud.bisync_commands import (
    BisyncError,
    convert_bmignore_to_rclone_filters,
)
from basic_memory.cli.commands.cloud.rclone_commands import SyncProject, project_sync
from basic_memory.ignore_utils import get_bmignore_path


def test_force_refresh_bypasses_newer_filter_cache(config_home) -> None:
    """A destructive caller can rebuild even when cache mtimes are misleading."""
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text("old-pattern\n", encoding="utf-8")
    filter_path = convert_bmignore_to_rclone_filters()

    bmignore.write_text("new-pattern\n", encoding="utf-8")
    future = filter_path.stat().st_mtime + 60
    os.utime(filter_path, (future, future))

    assert "old-pattern" in convert_bmignore_to_rclone_filters().read_text()
    refreshed = convert_bmignore_to_rclone_filters(force=True)
    assert "new-pattern" in refreshed.read_text()
    assert "old-pattern" not in refreshed.read_text()


def test_project_sync_forces_current_bmignore_filter(tmp_path, monkeypatch) -> None:
    """The --delete-excluded mirror requests a freshly generated filter."""
    filter_path = tmp_path / ".bmignore.rclone"
    filter_path.write_text("- current-pattern\n", encoding="utf-8")
    filter_requests: list[tuple[bool, bool]] = []

    def fake_filter_path(*, force: bool = False, fail_on_read_error: bool = False):
        filter_requests.append((force, fail_on_read_error))
        return filter_path

    monkeypatch.setattr(rclone_commands, "get_bmignore_filter_path", fake_filter_path)
    project = SyncProject(name="research", path="/research", local_sync_path=str(tmp_path))

    result = project_sync(
        project,
        "bucket",
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
        is_installed=lambda: True,
    )

    assert result is True
    assert filter_requests == [(True, True)]


def test_destructive_filter_refresh_fails_when_bmignore_is_unreadable(
    config_home, monkeypatch
) -> None:
    """A destructive mirror must not fall back to guessed ignore rules."""
    bmignore = get_bmignore_path()
    bmignore.parent.mkdir(parents=True, exist_ok=True)
    bmignore.write_text("private/**\n", encoding="utf-8")
    original_open = Path.open

    def fail_bmignore_read(path: Path, *args, **kwargs):
        if path == bmignore:
            raise PermissionError("permission denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_bmignore_read)

    with pytest.raises(BisyncError, match="Failed to read .*permission denied"):
        convert_bmignore_to_rclone_filters(force=True, fail_on_read_error=True)
