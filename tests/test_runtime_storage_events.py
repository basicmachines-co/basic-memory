"""Tests for portable storage-event helpers."""

from basic_memory.runtime import (
    StorageEventInput,
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
    group_storage_events_by_bucket,
    storage_event_payload_from_input,
)


def storage_event(
    *,
    bucket_name: str,
    key: str,
    event_name: str = "OBJECT_CREATED_PUT",
    etag: str = "etag",
) -> StorageEventPayload:
    return StorageEventPayload(
        event_name=event_name,
        event_time="2026-06-19T10:15:00Z",
        object_version=StorageObjectVersion(
            identity=StorageObjectIdentity(bucket_name=bucket_name, key=key),
            etag=etag,
            size=10,
        ),
    )


def test_group_storage_events_by_bucket_preserves_bucket_and_arrival_order() -> None:
    first = storage_event(bucket_name="alpha", key="main/a.md", etag="a")
    second = storage_event(bucket_name="beta", key="main/b.md", etag="b")
    third = storage_event(bucket_name="alpha", key="main/c.md", etag="c")

    grouped = group_storage_events_by_bucket((first, second, third))

    assert grouped == {
        "alpha": (first, third),
        "beta": (second,),
    }


def test_storage_event_payload_from_input_builds_runtime_payload() -> None:
    payload = storage_event_payload_from_input(
        StorageEventInput(
            event_name="OBJECT_CREATED_POST",
            event_time="2026-06-19T10:15:00Z",
            bucket_name="tenant-bucket",
            object_key="main/notes/a.md",
            etag='"etag-a"',
            size=42,
        )
    )

    assert payload.event_name == "OBJECT_CREATED_POST"
    assert payload.event_time == "2026-06-19T10:15:00Z"
    assert payload.bucket_name == "tenant-bucket"
    assert payload.object_key == "main/notes/a.md"
    assert payload.project_path == "main"
    assert payload.relative_path == "notes/a.md"
    assert payload.etag == '"etag-a"'
    assert payload.size == 42
