"""Tests for portable project file change planning."""

from collections.abc import Mapping

import pytest

from basic_memory.indexing.change_planning import (
    ChangeDetectionSnapshot,
    ChangeReport,
    FileMoveCandidate,
    plan_change_detection_snapshot,
    plan_file_changes,
    storage_checksums_from_sources,
)
from basic_memory.indexing.change_detector import detect_project_file_changes


class StorageObject:
    def __init__(self, checksum: str) -> None:
        self.checksum = checksum


def test_plan_change_detection_snapshot_maps_typed_runtime_state() -> None:
    storage_checksum_by_path = storage_checksums_from_sources(
        {
            "unchanged.md": StorageObject("same-checksum"),
            "modified.md": StorageObject("new-checksum"),
            "new/moved.md": StorageObject("moved-checksum"),
            "new.md": StorageObject("new-file-checksum"),
        }
    )
    snapshot = ChangeDetectionSnapshot(
        storage_checksum_by_path=storage_checksum_by_path,
        db_checksum_by_path={
            "unchanged.md": "same-checksum",
            "modified.md": "old-checksum",
        },
        all_db_paths=("unchanged.md", "modified.md", "old/moved.md", "deleted.md"),
        move_candidates=(FileMoveCandidate(path="old/moved.md", checksum="moved-checksum"),),
    )

    assert snapshot.new_file_checksum_by_path == {
        "new/moved.md": "moved-checksum",
        "new.md": "new-file-checksum",
    }
    assert plan_change_detection_snapshot(snapshot) == ChangeReport(
        new_files=["new.md"],
        modified_files=["modified.md"],
        deleted_files=["deleted.md"],
        moved_files={"old/moved.md": "new/moved.md"},
        unchanged_files=["unchanged.md"],
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


class FakeChangeDetectionStore:
    def __init__(self) -> None:
        self.loaded_checksum_paths: tuple[str, ...] | None = None
        self.loaded_move_checksums: dict[str, str] | None = None

    async def load_indexed_file_checksums(self, paths: tuple[str, ...]) -> dict[str, str]:
        self.loaded_checksum_paths = paths
        return {
            "unchanged.md": "same-checksum",
            "modified.md": "old-checksum",
        }

    async def load_all_indexed_paths(self) -> tuple[str, ...]:
        return ("unchanged.md", "modified.md", "old/moved.md", "deleted.md")

    async def load_move_candidates(
        self,
        new_file_checksums: Mapping[str, str],
    ) -> tuple[FileMoveCandidate, ...]:
        self.loaded_move_checksums = dict(new_file_checksums)
        return (FileMoveCandidate(path="old/moved.md", checksum="moved-checksum"),)


@pytest.mark.asyncio
async def test_detect_project_file_changes_loads_store_state_and_plans_moves() -> None:
    store = FakeChangeDetectionStore()

    report = await detect_project_file_changes(
        {
            "unchanged.md": StorageObject("same-checksum"),
            "modified.md": StorageObject("new-checksum"),
            "new/moved.md": StorageObject("moved-checksum"),
            "new.md": StorageObject("new-file-checksum"),
        },
        store=store,
    )

    assert store.loaded_checksum_paths == (
        "unchanged.md",
        "modified.md",
        "new/moved.md",
        "new.md",
    )
    assert store.loaded_move_checksums == {
        "new/moved.md": "moved-checksum",
        "new.md": "new-file-checksum",
    }
    assert report == ChangeReport(
        new_files=["new.md"],
        modified_files=["modified.md"],
        deleted_files=["deleted.md"],
        moved_files={"old/moved.md": "new/moved.md"},
        unchanged_files=["unchanged.md"],
    )
