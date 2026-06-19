"""Tests for project ZIP import planning."""

from io import BytesIO
from zipfile import ZipFile

import pytest

from basic_memory.importers import (
    ProjectZipImportError,
    ProjectZipImportPlan,
    ProjectZipEntry,
    build_project_zip_import_plan,
)
from basic_memory.importers import project_zip_import


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def test_project_zip_import_plan_strips_common_root_and_skips_ignored_files() -> None:
    plan = build_project_zip_import_plan(
        zip_bytes(
            {
                "project/.gitignore": b"ignored.txt\n",
                "project/notes/a.md": b"# A",
                "project/ignored.txt": b"ignored",
                "project/archive.zip": b"nested",
            }
        ),
        destination_folder="/imports/team/",
    )

    assert plan == ProjectZipImportPlan(
        entries=(ProjectZipEntry(path="imports/team/notes/a.md", content=b"# A"),),
        ignored_files=2,
        skipped_archives=1,
    )
    assert plan.uploaded_files == 1


def test_project_zip_import_plan_preserves_multiple_roots_and_sorts_entries() -> None:
    plan = build_project_zip_import_plan(
        zip_bytes(
            {
                "zeta.md": b"Z",
                "alpha/a.md": b"A",
            }
        )
    )

    assert plan.entries == (
        ProjectZipEntry(path="alpha/a.md", content=b"A"),
        ProjectZipEntry(path="zeta.md", content=b"Z"),
    )


def test_project_zip_import_plan_ignores_binary_root_ignore_file() -> None:
    plan = build_project_zip_import_plan(
        zip_bytes(
            {
                ".bmignore": b"\xff",
                "notes/a.md": b"# A",
            }
        )
    )

    assert plan.entries == (ProjectZipEntry(path="notes/a.md", content=b"# A"),)
    assert plan.ignored_files == 1


def test_project_zip_import_plan_handles_empty_archives() -> None:
    assert build_project_zip_import_plan(zip_bytes({})) == ProjectZipImportPlan(
        entries=(),
        ignored_files=0,
        skipped_archives=0,
    )


@pytest.mark.parametrize(
    "path",
    [
        r"folder\bad.md",
        "/absolute.md",
        "C:/drive.md",
        "../escape.md",
    ],
)
def test_project_zip_import_plan_rejects_unsafe_archive_paths(path: str) -> None:
    with pytest.raises(ProjectZipImportError, match="unsafe path"):
        build_project_zip_import_plan(zip_bytes({path: b"bad"}))


def test_project_zip_import_plan_rejects_unsafe_destination_folder() -> None:
    with pytest.raises(ProjectZipImportError, match="unsafe path"):
        build_project_zip_import_plan(
            zip_bytes({"notes/a.md": b"# A"}),
            destination_folder="../imports",
        )


def test_project_zip_import_plan_rejects_corrupt_zip() -> None:
    with pytest.raises(ProjectZipImportError, match="valid ZIP archive"):
        build_project_zip_import_plan(b"not a zip")


def test_project_zip_import_plan_enforces_expanded_size_limit(monkeypatch) -> None:
    monkeypatch.setattr(project_zip_import, "MAX_PROJECT_ZIP_EXPANDED_BYTES", 2)

    with pytest.raises(ProjectZipImportError, match="import limit"):
        build_project_zip_import_plan(zip_bytes({"notes/a.md": b"abc"}))
