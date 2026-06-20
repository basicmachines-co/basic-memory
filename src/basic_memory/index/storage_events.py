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
    StorageEventPayload,
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


@dataclass(frozen=True, slots=True)
class StorageEventIndexRuntime:
    """Runtime dependencies needed to process normalized storage events."""

    project_resolver: StorageEventProjectResolver
    operation_processor_factory: StorageEventOperationProcessorFactory


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
