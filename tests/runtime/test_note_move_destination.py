from __future__ import annotations

import pytest

from basic_memory.runtime import normalize_note_move_destination_path


def test_normalize_note_move_destination_path_trims_and_posix_normalizes() -> None:
    destination = normalize_note_move_destination_path("  archive/note.md  ")

    assert destination.file_path == "archive/note.md"


@pytest.mark.parametrize("destination_path", ["", "   ", "/archive/note.md"])
def test_normalize_note_move_destination_path_rejects_invalid_paths(
    destination_path: str,
) -> None:
    with pytest.raises(ValueError, match="Invalid destination path:"):
        normalize_note_move_destination_path(destination_path)
