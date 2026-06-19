"""Portable storage event normalization helpers."""

from dataclasses import dataclass

from basic_memory.runtime.contracts import (
    StorageBucketName,
    StorageEtag,
    StorageEventName,
    StorageEventPayload,
    StorageKey,
    StorageObjectIdentity,
    StorageObjectVersion,
)


@dataclass(frozen=True, slots=True)
class StorageEventInput:
    """Validated storage event fields from an external ingress adapter."""

    event_name: StorageEventName
    event_time: str
    bucket_name: StorageBucketName
    object_key: StorageKey
    etag: StorageEtag
    size: int | None = None


def storage_event_payload_from_input(event: StorageEventInput) -> StorageEventPayload:
    """Map validated storage event fields into the runtime storage event payload."""
    return StorageEventPayload(
        event_name=event.event_name,
        event_time=event.event_time,
        object_version=StorageObjectVersion(
            identity=StorageObjectIdentity(
                bucket_name=event.bucket_name,
                key=event.object_key,
            ),
            etag=event.etag,
            size=event.size,
        ),
    )
