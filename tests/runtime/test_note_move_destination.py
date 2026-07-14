from __future__ import annotations

import pytest

from basic_memory.runtime.note_move import normalize_note_move_destination_path


def test_normalize_note_move_destination_path_trims_and_posix_normalizes() -> None:
    assert normalize_note_move_destination_path("  archive/note.md  ") == "archive/note.md"


def test_normalize_note_move_destination_path_rejects_whitespace_prefixed_absolute() -> None:
    # Regression: the old inline move_entity check tested startswith("/") on the
    # raw string, so "  /abs" slipped past validation.
    with pytest.raises(ValueError, match="Invalid destination path:"):
        normalize_note_move_destination_path("  /archive/note.md")


@pytest.mark.parametrize("destination_path", ["", "   ", "/archive/note.md"])
def test_normalize_note_move_destination_path_rejects_invalid_paths(
    destination_path: str,
) -> None:
    with pytest.raises(ValueError, match="Invalid destination path:"):
        normalize_note_move_destination_path(destination_path)


@pytest.mark.parametrize(
    "destination_path",
    [
        "../../evil.md",
        "folder/../../evil.md",
        "..\\..\\evil.md",
        "C:/evil.md",
        "C:\\evil.md",
        "\\\\host\\share\\evil.md",
        # Windows drive-relative: is_absolute() is False, but on a Windows host
        # joining it onto the project root discards the root entirely.
        "C:evil.md",
        "C:..\\evil.md",
        # Windows rooted (drive-less): is_absolute() is False, but joining
        # rewrites the base path's root on a Windows host.
        "\\evil.md",
        "\\archive\\evil.md",
    ],
)
def test_normalize_note_move_destination_path_rejects_escapes(destination_path: str) -> None:
    # These join onto the project root and escape it: ".." traversal (either
    # separator), Windows drive/UNC roots (absolute on Windows), drive-relative
    # destinations ("C:evil.md"), and rooted destinations ("\\evil.md").
    with pytest.raises(ValueError, match="Invalid destination path:"):
        normalize_note_move_destination_path(destination_path)


def test_normalize_note_move_destination_path_allows_nested_relative() -> None:
    assert normalize_note_move_destination_path("a/b/c/note.md") == "a/b/c/note.md"
