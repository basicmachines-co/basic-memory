"""Local project scan parity with the legacy sync oracle."""

from pathlib import Path

import pytest

import basic_memory.index.local_project as local_project
from basic_memory.index import (
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

    def rglob_then_permission_error(path: Path, *args, **kwargs):
        assert path == project_root
        yield accessible_path
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "rglob", rglob_then_permission_error)

    assert local_project_index_file_paths(project_root, ignore_patterns=set()) == ("accessible.md",)


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
