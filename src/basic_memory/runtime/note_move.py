"""Portable note move path contracts."""

from __future__ import annotations

from pathlib import PurePosixPath

from basic_memory.runtime.storage import RuntimeFilePath
from basic_memory.utils import nfc_path, valid_project_path_value


def normalize_note_move_destination_path(destination_path: str) -> RuntimeFilePath:
    """Normalize the note move destination path shared by local and hosted runtimes.

    This is the only containment check before the destination is joined onto the
    project root, so it must reject anything that escapes it. A bare leading "/"
    check is not enough: a Windows drive/UNC root ("C:/x", "\\\\host\\share") is
    absolute on Windows (``base_path / "C:/x"`` discards base_path), and ".."
    segments (in either path convention) walk out of the project.
    """
    accepted_path = destination_path.strip()
    if not accepted_path or not valid_project_path_value(accepted_path):
        raise ValueError(f"Invalid destination path: {destination_path}")

    return nfc_path(PurePosixPath(accepted_path).as_posix())
