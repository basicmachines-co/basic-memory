"""Tests for portable storage-event helpers."""

from basic_memory.runtime import (
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
    group_storage_events_by_bucket,
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
