from dataclasses import dataclass
from datetime import UTC, datetime

from basic_memory.runtime import SnapshotObjectReference


@dataclass(frozen=True, slots=True)
class _SnapshotObjectSource:
    key: str
    size: int
    last_modified: datetime
    etag: str


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
