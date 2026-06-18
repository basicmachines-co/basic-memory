"""Tests for portable project file change planning."""

from basic_memory.indexing.change_planning import (
    ChangeReport,
    FileMoveCandidate,
    plan_file_changes,
)


def test_plan_file_changes_detects_new_modified_unchanged_and_deleted_files() -> None:
    report = plan_file_changes(
        storage_checksum_by_path={
            "unchanged.md": "same-checksum",
            "modified.md": "new-checksum",
            "new.md": "new-file-checksum",
        },
        db_checksum_by_path={
            "unchanged.md": "same-checksum",
            "modified.md": "old-checksum",
        },
        all_db_paths=("unchanged.md", "modified.md", "deleted.md"),
        move_candidates=(),
    )

    assert report == ChangeReport(
        new_files=["new.md"],
        modified_files=["modified.md"],
        deleted_files=["deleted.md"],
        moved_files={},
        unchanged_files=["unchanged.md"],
    )


def test_plan_file_changes_removes_moved_files_from_new_and_deleted_sets() -> None:
    report = plan_file_changes(
        storage_checksum_by_path={"new/note.md": "move-checksum"},
        db_checksum_by_path={},
        all_db_paths=("old/note.md",),
        move_candidates=(FileMoveCandidate(path="old/note.md", checksum="move-checksum"),),
    )

    assert report.new_files == []
    assert report.deleted_files == []
    assert report.moved_files == {"old/note.md": "new/note.md"}
    assert report.total_changes == 1


def test_plan_file_changes_treats_copy_as_new_when_original_path_still_exists() -> None:
    report = plan_file_changes(
        storage_checksum_by_path={
            "original.md": "shared-checksum",
            "copy.md": "shared-checksum",
        },
        db_checksum_by_path={"original.md": "shared-checksum"},
        all_db_paths=("original.md",),
        move_candidates=(FileMoveCandidate(path="original.md", checksum="shared-checksum"),),
    )

    assert report.moved_files == {}
    assert report.new_files == ["copy.md"]
    assert report.unchanged_files == ["original.md"]


def test_change_report_helper_properties_count_real_changes() -> None:
    report = ChangeReport(
        new_files=["new.md"],
        modified_files=["modified.md"],
        deleted_files=["deleted.md"],
        moved_files={"old.md": "new-name.md"},
        unchanged_files=["same.md"],
    )

    assert report.has_changes is True
    assert report.total_changes == 4
    assert ChangeReport().has_changes is False
