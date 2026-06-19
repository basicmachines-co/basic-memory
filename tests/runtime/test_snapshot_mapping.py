from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from basic_memory.runtime import SnapshotObjectReference, SnapshotReference


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
