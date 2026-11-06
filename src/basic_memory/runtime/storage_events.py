"""Portable storage event normalization helpers."""

import asyncio
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


@dataclass(frozen=True, slots=True)
class RuntimeStorageEventStep:
    """One operation to run, standing for every event it covers.

    Consecutive deletes of one path collapse into a single step: a delete only
    asks whether the object is still there, so one check answers them all.
    """

    operation: RuntimeStorageEventOperation
    event_count: int = 1


def plan_runtime_storage_event_steps(
    operations: Iterable[RuntimeStorageEventOperation],
) -> tuple[tuple[RuntimeStorageEventStep, ...], ...]:
    """Group operations by path, in arrival order, collapsing repeated deletes.

    Deleting old object versions (a version prune) sends one delete event per
    version, so one notification batch can hold the same path many times.
    Operations without a path (skips) each form their own group.
    """
    groups: dict[str, list[RuntimeStorageEventStep]] = {}
    for position, operation in enumerate(operations):
        group_key = operation.relative_path or f"\0{position}"
        steps = groups.setdefault(group_key, [])
        previous = steps[-1] if steps else None
        if (
            previous is not None
            and previous.operation.kind == RuntimeStorageEventOperationKind.delete_file
            and operation.kind == RuntimeStorageEventOperationKind.delete_file
        ):
            # Keep the latest event; its check runs after every earlier delete.
            steps[-1] = RuntimeStorageEventStep(operation, previous.event_count + 1)
            continue
        steps.append(RuntimeStorageEventStep(operation))
    return tuple(tuple(steps) for steps in groups.values())


async def _run_storage_event_step(
    step: RuntimeStorageEventStep,
    processor: RuntimeStorageEventOperationProcessor,
) -> RuntimeJobCounts:
    operation = step.operation
    try:
        if operation.kind == RuntimeStorageEventOperationKind.skip:
            await processor.skip_event(operation)
            return RuntimeJobCounts().with_skipped(step.event_count)

        if operation.kind == RuntimeStorageEventOperationKind.index_file:
            await processor.index_file(operation)
            return RuntimeJobCounts().with_processed(step.event_count)

        if operation.kind == RuntimeStorageEventOperationKind.delete_file:
            await processor.delete_file(operation)
            return RuntimeJobCounts().with_processed(step.event_count)

        raise RuntimeError(f"Unhandled storage event operation kind: {operation.kind}")
    except Exception as exc:
        await processor.event_failed(operation, exc)
        return RuntimeJobCounts().with_failed(step.event_count)


async def _run_storage_event_path(
    steps: tuple[RuntimeStorageEventStep, ...],
    processor: RuntimeStorageEventOperationProcessor,
) -> RuntimeJobCounts:
    # One path's events stay in arrival order, so a create after a delete
    # still wins.
    result = RuntimeJobCounts()
    for step in steps:
        result = result.add(await _run_storage_event_step(step, processor))
    return result


async def run_runtime_storage_event_operations(
    events: Iterable[StorageEventPayload],
    processor: RuntimeStorageEventOperationProcessor,
    *,
    max_concurrent_paths: int = 1,
) -> RuntimeJobCounts:
    """Execute normalized storage event operations and count per-event outcomes.

    Different paths run up to ``max_concurrent_paths`` at a time; each path's
    own events stay in order. The default of one runs every path in turn. A
    caller raising it must bound it by what each operation holds (a database
    connection per check, for example).
    """
    if max_concurrent_paths < 1:
        raise ValueError("max_concurrent_paths must be at least 1")

    path_groups = plan_runtime_storage_event_steps(plan_runtime_storage_event_operations(events))

    if max_concurrent_paths == 1:
        result = RuntimeJobCounts()
        for steps in path_groups:
            result = result.add(await _run_storage_event_path(steps, processor))
        return result

    slots = asyncio.Semaphore(max_concurrent_paths)

    async def run_path(steps: tuple[RuntimeStorageEventStep, ...]) -> RuntimeJobCounts:
        async with slots:
            return await _run_storage_event_path(steps, processor)

    # Per-event failures are counted inside each path. Only an error the
    # processor chose to raise (a failure no later event would repair) gets
    # here; it cancels the other paths and fails the batch, as it would have
    # stopped the sequential loop.
    try:
        async with asyncio.TaskGroup() as task_group:
            tasks = [task_group.create_task(run_path(steps)) for steps in path_groups]
    except ExceptionGroup as failures:
        raise failures.exceptions[0] from None

    result = RuntimeJobCounts()
    for task in tasks:
        result = result.add(task.result())
    return result
