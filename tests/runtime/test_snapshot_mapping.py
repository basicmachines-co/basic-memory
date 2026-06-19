from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from basic_memory.runtime import (
    SnapshotBrowseFile,
    SnapshotObjectReference,
    SnapshotReference,
    plan_snapshot_name,
    should_include_runtime_archive_path,
    should_include_snapshot_archive_path,
    snapshot_browse_project_names,
    snapshot_key_project_name,
    snapshot_key_project_names,
    snapshot_restore_folder_prefix,
)


@dataclass(frozen=True, slots=True)
class _SnapshotObjectSource:
    key: str
    size: int
    last_modified: datetime
    etag: str


@dataclass(frozen=True, slots=True)
class _SnapshotReferenceSource:
    tenant_id: UUID
    bucket_name: str
    name: str
    snapshot_version: str


def test_snapshot_reference_maps_from_record_source() -> None:
    tenant_id = uuid4()

    reference = SnapshotReference.from_source(
        _SnapshotReferenceSource(
            tenant_id=tenant_id,
            bucket_name="tenant-bucket",
            name="daily",
            snapshot_version="snapshot-42",
        )
    )

    assert reference == SnapshotReference(
        tenant_id=tenant_id,
        bucket_name="tenant-bucket",
        snapshot_name="daily",
        snapshot_version="snapshot-42",
    )


def test_snapshot_object_reference_maps_from_storage_source() -> None:
    modified_at = datetime(2026, 6, 19, 14, 0, tzinfo=UTC)

    reference = SnapshotObjectReference.from_source(
        _SnapshotObjectSource(
            key="project/note.md",
            size=42,
            last_modified=modified_at,
            etag="abc123",
        )
    )

    assert reference == SnapshotObjectReference(
        key="project/note.md",
        size=42,
        last_modified=modified_at,
        etag="abc123",
    )


def test_snapshot_browse_file_maps_from_storage_source() -> None:
    modified_at = datetime(2026, 6, 19, 14, 0, tzinfo=UTC)

    file = SnapshotBrowseFile.from_source(
        _SnapshotObjectSource(
            key="project/note.md",
            size=42,
            last_modified=modified_at,
            etag="abc123",
        )
    )

    assert file == SnapshotBrowseFile(
        key="project/note.md",
        size=42,
        last_modified=modified_at,
        etag="abc123",
    )


def test_snapshot_browse_project_names_returns_sorted_unique_top_level_folders() -> None:
    modified_at = datetime(2026, 6, 19, 14, 0, tzinfo=UTC)
    files = (
        SnapshotBrowseFile(
            key="z-project/notes/a.md",
            size=1,
            last_modified=modified_at,
            etag=None,
        ),
        SnapshotBrowseFile(
            key="a-project/notes/b.md",
            size=2,
            last_modified=modified_at,
            etag='"b"',
        ),
        SnapshotBrowseFile(
            key="a-project/notes/c.md",
            size=3,
            last_modified=modified_at,
            etag='"c"',
        ),
        SnapshotBrowseFile(
            key="root.md",
            size=4,
            last_modified=modified_at,
            etag='"root"',
        ),
    )

    assert snapshot_browse_project_names(files) == ("a-project", "z-project")


def test_snapshot_key_project_name_returns_top_level_folder() -> None:
    assert snapshot_key_project_name("project/notes/a.md") == "project"
    assert snapshot_key_project_name("root.md") is None


def test_snapshot_key_project_names_returns_sorted_unique_project_folders() -> None:
    assert snapshot_key_project_names(
        (
            "z-project/notes/a.md",
            "a-project/notes/b.md",
            "a-project/notes/c.md",
            "root.md",
        )
    ) == ("a-project", "z-project")


def test_snapshot_restore_folder_prefix_normalizes_folder_prefix() -> None:
    assert snapshot_restore_folder_prefix("project/notes") == "project/notes/"
    assert snapshot_restore_folder_prefix("project/notes/") == "project/notes/"


def test_should_include_runtime_archive_path_filters_internal_paths() -> None:
    assert should_include_runtime_archive_path("project/notes/visible.md") is True
    assert should_include_runtime_archive_path("project/.hidden/secret.md") is False
    assert should_include_runtime_archive_path("project/__pycache__/module.pyc") is False


def test_should_include_snapshot_archive_path_uses_runtime_archive_filter() -> None:
    assert should_include_snapshot_archive_path("project/notes/visible.md") is True
    assert should_include_snapshot_archive_path("project/.hidden/secret.md") is False


def test_plan_snapshot_name_formats_manual_snapshot_name() -> None:
    created_at = datetime(2026, 6, 19, 15, 4, 5, tzinfo=UTC)

    assert (
        plan_snapshot_name(
            description="My Backup",
            created_at=created_at,
            auto=False,
        )
        == "manual-my-backup-20260619-150405"
    )


def test_plan_snapshot_name_formats_auto_snapshot_name() -> None:
    created_at = datetime(2026, 6, 19, 15, 4, 5, tzinfo=UTC)

    assert (
        plan_snapshot_name(
            description="Daily Backup",
            created_at=created_at,
            auto=True,
        )
        == "auto-daily-backup-20260619-150405"
    )


def test_plan_snapshot_name_truncates_description_slug() -> None:
    created_at = datetime(2026, 6, 19, 15, 4, 5, tzinfo=UTC)

    assert (
        plan_snapshot_name(
            description="A" * 60,
            created_at=created_at,
            auto=False,
        )
        == f"manual-{'a' * 50}-20260619-150405"
    )
