"""Portable note move path contracts."""

from __future__ import annotations

from pathlib import Path

from basic_memory.runtime.contracts import RuntimeFilePath


def normalize_note_move_destination_path(destination_path: str) -> RuntimeFilePath:
    """Normalize the note move destination path shared by local and hosted runtimes."""
    accepted_path = destination_path.strip()
    if not accepted_path or accepted_path.startswith("/"):
        raise ValueError(f"Invalid destination path: {destination_path}")

    return Path(accepted_path).as_posix()
