"""Local project scan parity with the legacy sync oracle."""

from pathlib import Path

from basic_memory.index import local_project_index_file_paths


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
