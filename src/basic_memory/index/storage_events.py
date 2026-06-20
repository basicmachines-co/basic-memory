"""Core orchestration for event-based file indexing."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from basic_memory.runtime import (
    ProjectPath,
    ProjectRuntimeReference,
    RuntimeStorageEventOperationProcessor,
    RuntimeStorageEventProcessingResult,
    StorageBucketName,
    StorageEventPayload,
    StorageEventSource,
    plan_runtime_storage_events_by_project,
    run_runtime_storage_event_operations,
)


class StorageEventProjectResolver(Protocol):
    """Resolve a project-prefixed storage namespace to a runtime project identity."""

    async def resolve_project(self, project_path: ProjectPath) -> ProjectRuntimeReference | None:
        """Return the runtime project for a storage project path, when it exists."""


class StorageEventOperationProcessorFactory(Protocol):
    """Build a project-scoped storage event processor."""

    def processor_for_project(
        self,
        project: ProjectRuntimeReference,
    ) -> RuntimeStorageEventOperationProcessor:
        """Return the operation processor for one resolved project."""


class StorageEventBucketProcessor(Protocol):
    """Process normalized storage events for one storage bucket."""

    async def process_bucket_events(
        self,
        bucket_name: StorageBucketName,
        events: tuple[StorageEventPayload, ...],
    ) -> RuntimeStorageEventProcessingResult:
        """Process one bucket's storage events and return aggregate counts."""

    async def bucket_failed(
        self,
        bucket_name: StorageBucketName,
        events: tuple[StorageEventPayload, ...],
        exc: Exception,
    ) -> None:
        """Record a bucket failure. Returning lets the source runner count it."""


@dataclass(frozen=True, slots=True)
class StorageEventIndexRuntime:
    """Runtime dependencies needed to process normalized storage events."""

    project_resolver: StorageEventProjectResolver
    operation_processor_factory: StorageEventOperationProcessorFactory


@dataclass(frozen=True, slots=True)
class StorageEventSourceIndexRuntime:
    """Runtime dependencies needed to process a normalized storage event source."""

    bucket_processor: StorageEventBucketProcessor


async def run_storage_event_source_indexing(
    source: StorageEventSource,
    runtime: StorageEventSourceIndexRuntime,
) -> RuntimeStorageEventProcessingResult:
    """Route normalized storage event batches by bucket and aggregate bucket results."""
    result = RuntimeStorageEventProcessingResult.empty()

    for bucket_name, events in source.events_by_bucket().items():
        try:
            bucket_result = await runtime.bucket_processor.process_bucket_events(
                bucket_name,
                events,
            )
        except Exception as exc:
            await runtime.bucket_processor.bucket_failed(bucket_name, events, exc)
            result = result.with_failed(len(events))
            continue

        result = result.add(bucket_result)

    return result


async def run_storage_event_indexing(
    events: Iterable[StorageEventPayload],
    runtime: StorageEventIndexRuntime,
) -> RuntimeStorageEventProcessingResult:
    """Route normalized storage events by project and execute project-scoped operations."""
    routing_plan = plan_runtime_storage_events_by_project(events)
    result = RuntimeStorageEventProcessingResult.empty().add_counts(routing_plan.skipped_counts)

    for project_batch in routing_plan.project_batches:
        project = await runtime.project_resolver.resolve_project(project_batch.project_path)
        if project is None:
            result = result.with_skipped(len(project_batch.events))
            continue

        processor = runtime.operation_processor_factory.processor_for_project(project)
        project_result = await run_runtime_storage_event_operations(
            project_batch.events,
            processor,
        )
        result = result.add(project_result)

    return result
