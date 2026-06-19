"""Project ZIP import planning.

The hosted import worker receives a single archive from shared storage, but the
write phase wants a deterministic list of project-relative files. This module
keeps archive validation, root stripping, and ignore handling separate from the
runtime adapter that eventually uploads or writes the selected files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, ZipInfo

from basic_memory.ignore_utils import DEFAULT_IGNORE_PATTERNS, should_ignore_path

ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tgz", ".tbz2"}
MAX_PROJECT_ZIP_EXPANDED_BYTES = 100 * 1024 * 1024
_ARCHIVE_BASE_PATH = Path("/archive")
_DRIVE_LETTER_PATH = re.compile(r"^[A-Za-z]:")


class ProjectZipImportError(ValueError):
    """Raised when a project ZIP cannot be imported safely."""


@dataclass(frozen=True, slots=True)
class ProjectZipEntry:
    """One file selected for upload from a project ZIP archive."""

    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ProjectZipImportPlan:
    """Validated project ZIP contents ready for upload."""

    entries: tuple[ProjectZipEntry, ...]
    ignored_files: int
    skipped_archives: int

    @property
    def uploaded_files(self) -> int:
        """Return the number of files selected for upload."""
        return len(self.entries)


def build_project_zip_import_plan(
    archive_bytes: bytes,
    *,
    destination_folder: str = "",
) -> ProjectZipImportPlan:
    """Validate and flatten a project ZIP into upload-ready entries."""
    try:
        with ZipFile(BytesIO(archive_bytes)) as archive:
            file_infos = [info for info in archive.infolist() if not info.is_dir()]
            ensure_expanded_size_within_limit(file_infos)
            stripped_paths = strip_common_root(
                [validated_archive_path(info.filename) for info in file_infos]
            )
            destination = normalize_destination_folder(destination_folder)
            archive_entries = list(zip(file_infos, stripped_paths, strict=True))
            ignore_patterns = load_archive_root_ignore_patterns(archive, archive_entries)

            entries: list[ProjectZipEntry] = []
            ignored_files = 0
            skipped_archives = 0

            for info, relative_path in archive_entries:
                if should_ignore_path(
                    _ARCHIVE_BASE_PATH / relative_path,
                    _ARCHIVE_BASE_PATH,
                    ignore_patterns,
                ):
                    ignored_files += 1
                    continue

                if PurePosixPath(relative_path).suffix.lower() in ARCHIVE_EXTENSIONS:
                    skipped_archives += 1
                    continue

                target_path = join_destination(destination, relative_path)
                entries.append(
                    ProjectZipEntry(
                        path=target_path,
                        content=archive.read(info),
                    )
                )

            entries.sort(key=lambda entry: entry.path)
            return ProjectZipImportPlan(
                entries=tuple(entries),
                ignored_files=ignored_files,
                skipped_archives=skipped_archives,
            )
    except BadZipFile as exc:
        raise ProjectZipImportError("Import file must be a valid ZIP archive") from exc


def ensure_expanded_size_within_limit(file_infos: list[ZipInfo]) -> None:
    """Fail before reading content when a ZIP expands beyond the worker limit."""
    expanded_bytes = 0
    for info in file_infos:
        expanded_bytes += info.file_size
        if expanded_bytes > MAX_PROJECT_ZIP_EXPANDED_BYTES:
            max_mb = MAX_PROJECT_ZIP_EXPANDED_BYTES // (1024 * 1024)
            raise ProjectZipImportError(f"ZIP archive expands beyond the {max_mb}MB import limit")


def validated_archive_path(raw_path: str) -> str:
    """Return a normalized archive path or fail on unsafe input."""
    if "\\" in raw_path:
        raise ProjectZipImportError(f"ZIP archive contains unsafe path: {raw_path}")
    if raw_path.startswith("/") or _DRIVE_LETTER_PATH.match(raw_path):
        raise ProjectZipImportError(f"ZIP archive contains unsafe path: {raw_path}")

    path = PurePosixPath(raw_path)
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ProjectZipImportError(f"ZIP archive contains unsafe path: {raw_path}")

    return path.as_posix()


def strip_common_root(paths: list[str]) -> list[str]:
    """Strip one shared top-level folder from archive file paths."""
    if not paths:
        return []

    parts_by_path = [PurePosixPath(path).parts for path in paths]
    common_root = parts_by_path[0][0]
    if all(len(parts) > 1 and parts[0] == common_root for parts in parts_by_path):
        return [PurePosixPath(*parts[1:]).as_posix() for parts in parts_by_path]

    return paths


def normalize_destination_folder(destination_folder: str) -> str:
    """Normalize an optional import destination folder."""
    destination = destination_folder.strip().strip("/")
    if not destination:
        return ""
    return validated_archive_path(destination)


def join_destination(destination: str, relative_path: str) -> str:
    """Join a normalized destination and archive-relative path."""
    path = f"{destination}/{relative_path}" if destination else relative_path
    return validated_archive_path(path)


def load_archive_root_ignore_patterns(
    archive: ZipFile,
    archive_entries: list[tuple[ZipInfo, str]],
) -> set[str]:
    """Load Basic Memory defaults plus archive-root .bmignore and .gitignore."""
    patterns = set(DEFAULT_IGNORE_PATTERNS)
    by_relative_path = {
        relative_path: info
        for info, relative_path in archive_entries
        if relative_path in {".bmignore", ".gitignore"}
    }

    for ignore_filename in (".bmignore", ".gitignore"):
        info = by_relative_path.get(ignore_filename)
        if info is None:
            continue

        try:
            content = archive.read(info).decode("utf-8")
        except UnicodeDecodeError:
            continue

        for line in content.splitlines():
            pattern = line.strip()
            if pattern and not pattern.startswith("#"):
                patterns.add(pattern)

    return patterns
