"""Portable note move path contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from basic_memory.runtime.contracts import RuntimeFilePath


@dataclass(frozen=True, slots=True)
class RuntimeNoteMoveDestination:
    """Normalized relative destination path for one note move."""

    file_path: RuntimeFilePath


def normalize_note_move_destination_path(destination_path: str) -> RuntimeNoteMoveDestination:
    """Normalize the note move destination path shared by local and hosted runtimes."""
    accepted_path = destination_path.strip()
    if not accepted_path or accepted_path.startswith("/"):
        raise ValueError(f"Invalid destination path: {destination_path}")

    return RuntimeNoteMoveDestination(file_path=Path(accepted_path).as_posix())
