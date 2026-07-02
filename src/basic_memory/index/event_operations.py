"""Generic project-scoped storage event operation processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from basic_memory.runtime.storage import RuntimeStorageEventOperation

ProjectOperationContextT = TypeVar("ProjectOperationContextT")
DeleteResourcesT = TypeVar("DeleteResourcesT")
DeleteResultT = TypeVar("DeleteResultT")


class StorageEventIndexOperationRunner(Protocol[ProjectOperationContextT]):
    """Capability that handles one indexable storage event for a project runtime."""

    async def index_file(
        self,
        context: ProjectOperationContextT,
        operation: RuntimeStorageEventOperation,
    ) -> None: ...


class StorageEventDeleteResourcesFactory(Protocol[ProjectOperationContextT, DeleteResourcesT]):
    """Capability that creates delete resources for one project runtime."""

    async def create_delete_resources(
        self,
        context: ProjectOperationContextT,
    ) -> DeleteResourcesT: ...


class StorageEventDeleteOperationRunner(
    Protocol[ProjectOperationContextT, DeleteResourcesT, DeleteResultT]
):
    """Capability that handles one external object delete for a project runtime."""

    async def delete_file(
        self,
        context: ProjectOperationContextT,
        resources: DeleteResourcesT,
        operation: RuntimeStorageEventOperation,
    ) -> DeleteResultT: ...


class StorageEventOperationObserver(Protocol[ProjectOperationContextT, DeleteResultT]):
    """Observe project-scoped storage event operation outcomes."""

    async def skip_event(
        self,
        context: ProjectOperationContextT,
        operation: RuntimeStorageEventOperation,
    ) -> None: ...

    async def delete_file_completed(
        self,
        context: ProjectOperationContextT,
        operation: RuntimeStorageEventOperation,
        result: DeleteResultT,
    ) -> None: ...

    async def event_failed(
        self,
        context: ProjectOperationContextT,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class StorageEventOperationRuntime(
    Generic[ProjectOperationContextT, DeleteResourcesT, DeleteResultT]
):
    """Dependencies for executing project-scoped storage event operations."""

    context: ProjectOperationContextT
    index_runner: StorageEventIndexOperationRunner[ProjectOperationContextT]
    delete_resources_factory: StorageEventDeleteResourcesFactory[
        ProjectOperationContextT,
        DeleteResourcesT,
    ]
    delete_runner: StorageEventDeleteOperationRunner[
        ProjectOperationContextT,
        DeleteResourcesT,
        DeleteResultT,
    ]
    observer: StorageEventOperationObserver[ProjectOperationContextT, DeleteResultT]


@dataclass(slots=True)
class StorageEventOperationProcessor(
    Generic[ProjectOperationContextT, DeleteResourcesT, DeleteResultT]
):
    """Execute storage-event operations against typed runtime capabilities."""

    runtime: StorageEventOperationRuntime[
        ProjectOperationContextT,
        DeleteResourcesT,
        DeleteResultT,
    ]
    delete_resources: DeleteResourcesT | None = None

    async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
        await self.runtime.observer.skip_event(self.runtime.context, operation)

    async def index_file(self, operation: RuntimeStorageEventOperation) -> None:
        await self.runtime.index_runner.index_file(self.runtime.context, operation)

    async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
        if self.delete_resources is None:
            self.delete_resources = (
                await self.runtime.delete_resources_factory.create_delete_resources(
                    self.runtime.context,
                )
            )

        result = await self.runtime.delete_runner.delete_file(
            self.runtime.context,
            self.delete_resources,
            operation,
        )
        await self.runtime.observer.delete_file_completed(
            self.runtime.context,
            operation,
            result,
        )

    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        await self.runtime.observer.event_failed(self.runtime.context, operation, exc)
