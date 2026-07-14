"""Portable note move path contracts."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from basic_memory.runtime.storage import RuntimeFilePath


def normalize_note_move_destination_path(destination_path: str) -> RuntimeFilePath:
    """Normalize the note move destination path shared by local and hosted runtimes.

    This is the only containment check before the destination is joined onto the
    project root, so it must reject anything that escapes it. A bare leading "/"
    check is not enough: a Windows drive/UNC root ("C:/x", "\\\\host\\share") is
    absolute on Windows (``base_path / "C:/x"`` discards base_path), and ".."
    segments (in either path convention) walk out of the project.
    """
    accepted_path = destination_path.strip()
    if not accepted_path:
        raise ValueError(f"Invalid destination path: {destination_path}")

    # Reject absolute destinations under either OS convention, not just the host's.
    # is_absolute() alone is not enough on Windows: a drive-relative path ("C:evil.md")
    # and a rooted path ("\\evil.md") both report is_absolute() == False, yet joining
    # either onto a base path on a Windows host discards or rewrites the base.
    windows_path = PureWindowsPath(accepted_path)
    if (
        accepted_path.startswith(("/", "\\"))
        or windows_path.drive
        or windows_path.is_absolute()
    ):
        raise ValueError(f"Invalid destination path: {destination_path}")

    # Reject traversal under either separator convention: PurePosixPath splits on
    # "/", PureWindowsPath also on "\\", so together they catch "a/../b" and "a\\..\\b".
    if ".." in PurePosixPath(accepted_path).parts or ".." in PureWindowsPath(accepted_path).parts:
        raise ValueError(f"Invalid destination path: {destination_path}")

    return PurePosixPath(accepted_path).as_posix()
