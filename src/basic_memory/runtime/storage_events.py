"""Portable storage event normalization helpers."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from basic_memory.runtime.storage import (
    RuntimeJobCounts,
    RuntimeStorageEventOperation,
    RuntimeStorageEventOperationKind,
    StorageBucketName,
    StorageEtag,
    StorageEventName,
    StorageEventPayload,
    StorageKey,
    StorageObjectIdentity,
    StorageObjectVersion,
    group_storage_events_by_bucket,
    plan_runtime_storage_event_operations,
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


class RuntimeStorageEventOperationProcessor(Protocol):
    """Adapter for executing storage-event operations in one runtime."""

    async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
        """Handle a storage event that should not produce work."""

    async def index_file(self, operation: RuntimeStorageEventOperation) -> None:
        """Handle an object-created/update event for an indexable file."""

    async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
        """Handle an object-deleted event for an indexable file."""

    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        """Handle a per-event processing failure before the result is counted."""


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


@dataclass(frozen=True, slots=True)
class RuntimeStorageEventSource:
    """Normalized storage events grouped for runtime processing."""

    events: tuple[StorageEventPayload, ...]

    @classmethod
    def from_inputs(cls, events: Iterable[StorageEventInput]) -> "RuntimeStorageEventSource":
        """Build a source from validated ingress event fields."""
        return cls(tuple(storage_event_payload_from_input(event) for event in events))

    def events_by_bucket(self) -> dict[StorageBucketName, tuple[StorageEventPayload, ...]]:
        """Return events grouped by bucket in arrival order."""
        return group_storage_events_by_bucket(self.events)


async def run_runtime_storage_event_operations(
    events: Iterable[StorageEventPayload],
    processor: RuntimeStorageEventOperationProcessor,
) -> RuntimeJobCounts:
    """Execute normalized storage event operations and count per-event outcomes."""
    result = RuntimeJobCounts()

    for operation in plan_runtime_storage_event_operations(events):
        try:
            if operation.kind == RuntimeStorageEventOperationKind.skip:
                await processor.skip_event(operation)
                result = result.with_skipped()
                continue

            if operation.kind == RuntimeStorageEventOperationKind.index_file:
                await processor.index_file(operation)
                result = result.with_processed()
                continue

            if operation.kind == RuntimeStorageEventOperationKind.delete_file:
                await processor.delete_file(operation)
                result = result.with_processed()
                continue

            raise RuntimeError(f"Unhandled storage event operation kind: {operation.kind}")
        except Exception as exc:
            await processor.event_failed(operation, exc)
            result = result.with_failed()

    return result
