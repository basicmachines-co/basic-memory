"""Local project scan parity with the legacy sync oracle."""

from pathlib import Path

import pytest

import basic_memory.index.local_project as local_project
from basic_memory.index.local_project import (
    LocalProjectIndexObservedFileSource,
    local_project_index_file_paths,
)
from basic_memory.services import FileService


def test_local_project_index_file_paths_skips_unreadable_entries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Unreadable paths should not stop the project-wide index scan."""
    accessible_path = tmp_path / "accessible.md"
    unreadable_path = tmp_path / "restricted.md"
    accessible_path.write_text("# Accessible\n", encoding="utf-8")
    unreadable_path.write_text("# Restricted\n", encoding="utf-8")

    original_is_file = Path.is_file

    def is_file_or_permission_error(path: Path) -> bool:
        if path == unreadable_path:
            raise PermissionError("permission denied")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", is_file_or_permission_error)

    assert local_project_index_file_paths(tmp_path, ignore_patterns=set()) == ("accessible.md",)


def test_local_project_index_file_paths_keeps_discovered_files_when_walk_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A traversal error should not discard files discovered before the error."""
    project_root = tmp_path.resolve()
    accessible_path = project_root / "accessible.md"
    accessible_path.write_text("# Accessible\n", encoding="utf-8")

    def walk_then_permission_error(top, *args, **kwargs):
        yield (str(project_root), [], ["accessible.md"])
        raise PermissionError("permission denied")

    monkeypatch.setattr(local_project.os, "walk", walk_then_permission_error)

    assert local_project_index_file_paths(project_root, ignore_patterns=set()) == ("accessible.md",)


def test_local_project_index_file_paths_prunes_ignored_directories(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Ignored/hidden directories are pruned before descent, not just filtered."""
    project_root = tmp_path.resolve()
    (project_root / "keep.md").write_text("# keep\n", encoding="utf-8")
    node_modules = project_root / "node_modules"
    node_modules.mkdir()
    (node_modules / "huge.md").write_text("# huge\n", encoding="utf-8")
    hidden = project_root / ".hidden"
    hidden.mkdir()
    (hidden / "secret.md").write_text("# secret\n", encoding="utf-8")

    visited: list[str] = []
    original_is_file = Path.is_file

    def recording_is_file(path: Path) -> bool:
        visited.append(str(path))
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", recording_is_file)

    result = local_project_index_file_paths(project_root, ignore_patterns={"node_modules"})

    assert result == ("keep.md",)
    # Pruning means the walker never descended into (or stat'd) the ignored dirs.
    assert not any("node_modules" in path for path in visited)
    assert not any(".hidden" in path for path in visited)


def test_local_project_index_file_paths_aborts_when_root_unreadable(tmp_path: Path) -> None:
    """A missing/unmounted project root must raise, not return an empty snapshot —
    the coordinator would otherwise classify every indexed entity as deleted."""
    missing_root = tmp_path / "missing-root"

    with pytest.raises(OSError):
        local_project_index_file_paths(missing_root, ignore_patterns=set())


def test_local_project_index_file_paths_skips_symlinked_files(tmp_path: Path) -> None:
    """Symlinked files must not be indexed (their target may be outside the project)."""
    project_root = (tmp_path / "project").resolve()
    project_root.mkdir()
    (project_root / "keep.md").write_text("# keep\n", encoding="utf-8")
    target = tmp_path / "outside.md"
    target.write_text("# secret\n", encoding="utf-8")
    try:
        (project_root / "linked.md").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    assert local_project_index_file_paths(project_root, ignore_patterns=set()) == ("keep.md",)


@pytest.mark.asyncio
async def test_local_project_index_observed_file_source_skips_files_missing_after_scan(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Files that disappear after discovery should not fail project observation."""
    keep_path = tmp_path / "keep.md"
    keep_path.write_text("# Keep\n", encoding="utf-8")
    warnings: list[tuple[str, dict[str, object]]] = []

    def discovered_paths(*args, **kwargs) -> tuple[str, ...]:
        return ("missing.md", "keep.md")

    def record_warning(message: str, **kwargs: object) -> None:
        warnings.append((message, kwargs))

    monkeypatch.setattr(
        "basic_memory.index.local_project.local_project_index_file_paths",
        discovered_paths,
    )
    monkeypatch.setattr(local_project.logger, "warning", record_warning)

    observed = await LocalProjectIndexObservedFileSource(
        FileService(tmp_path),
    ).list_observed_index_files()

    assert tuple(file.path for file in observed) == ("keep.md",)
    assert warnings
    assert warnings[0][0] == "Skipping local index file that could not be observed"
    assert warnings[0][1]["path"] == "missing.md"
