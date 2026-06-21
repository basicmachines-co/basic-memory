"""Tests for generic project-scoped storage event operation processing."""

from dataclasses import dataclass, field

from basic_memory.index import (
    StorageEventDeleteOperationRunner,
    StorageEventDeleteResourcesFactory,
    StorageEventIndexOperationRunner,
    StorageEventOperationObserver,
    StorageEventOperationProcessor,
    StorageEventOperationRuntime,
)
from basic_memory.runtime import (
    RuntimeStorageEventOperation,
    RuntimeStorageEventOperationKind,
    RuntimeStorageEventSkipReason,
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
)


def storage_event(
    *,
    event_name: str = "OBJECT_CREATED_PUT",
    key: str = "main/notes/a.md",
) -> StorageEventPayload:
    return StorageEventPayload(
        event_name=event_name,
        event_time="2026-06-20T14:00:00Z",
        object_version=StorageObjectVersion(
            identity=StorageObjectIdentity(bucket_name="runtime-bucket", key=key),
            etag="etag-a",
            size=12,
        ),
    )


def index_operation() -> RuntimeStorageEventOperation:
    return RuntimeStorageEventOperation(
        kind=RuntimeStorageEventOperationKind.index_file,
        storage_event=storage_event(),
        relative_path="notes/a.md",
    )


def delete_operation(file_path: str) -> RuntimeStorageEventOperation:
    return RuntimeStorageEventOperation(
        kind=RuntimeStorageEventOperationKind.delete_file,
        storage_event=storage_event(event_name="OBJECT_DELETED", key=f"main/{file_path}"),
        relative_path=file_path,
    )


@dataclass(frozen=True, slots=True)
class ProjectOperationContext:
    project_name: str


@dataclass(slots=True)
class RecordingIndexRunner(StorageEventIndexOperationRunner[ProjectOperationContext]):
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def index_file(
        self,
        context: ProjectOperationContext,
        operation: RuntimeStorageEventOperation,
    ) -> None:
        self.calls.append((context.project_name, operation.require_relative_path()))


@dataclass(slots=True)
class RecordingDeleteResourcesFactory(
    StorageEventDeleteResourcesFactory[ProjectOperationContext, str]
):
    calls: list[str] = field(default_factory=list)

    async def create_delete_resources(self, context: ProjectOperationContext) -> str:
        self.calls.append(context.project_name)
        return f"{context.project_name}-resources"


@dataclass(slots=True)
class RecordingDeleteRunner(StorageEventDeleteOperationRunner[ProjectOperationContext, str, str]):
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def delete_file(
        self,
        context: ProjectOperationContext,
        resources: str,
        operation: RuntimeStorageEventOperation,
    ) -> str:
        relative_path = operation.require_relative_path()
        self.calls.append((context.project_name, resources, relative_path))
        return f"deleted:{relative_path}"


@dataclass(slots=True)
class RecordingOperationObserver(StorageEventOperationObserver[ProjectOperationContext, str]):
    skipped: list[tuple[str, RuntimeStorageEventSkipReason]] = field(default_factory=list)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    async def skip_event(
        self,
        context: ProjectOperationContext,
        operation: RuntimeStorageEventOperation,
    ) -> None:
        if operation.skip_reason is None:
            raise AssertionError("skip operation missing reason")
        self.skipped.append((context.project_name, operation.skip_reason))

    async def delete_file_completed(
        self,
        context: ProjectOperationContext,
        operation: RuntimeStorageEventOperation,
        result: str,
    ) -> None:
        self.deleted.append((operation.require_relative_path(), result))

    async def event_failed(
        self,
        context: ProjectOperationContext,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        self.failed.append((operation.require_relative_path(), str(exc)))


def operation_processor(
    *,
    context: ProjectOperationContext | None = None,
    index_runner: RecordingIndexRunner | None = None,
    delete_resources_factory: RecordingDeleteResourcesFactory | None = None,
    delete_runner: RecordingDeleteRunner | None = None,
    observer: RecordingOperationObserver | None = None,
) -> StorageEventOperationProcessor[ProjectOperationContext, str, str]:
    return StorageEventOperationProcessor(
        StorageEventOperationRuntime(
            context=context or ProjectOperationContext("main"),
            index_runner=index_runner or RecordingIndexRunner(),
            delete_resources_factory=delete_resources_factory or RecordingDeleteResourcesFactory(),
            delete_runner=delete_runner or RecordingDeleteRunner(),
            observer=observer or RecordingOperationObserver(),
        )
    )


async def test_storage_event_operation_processor_delegates_index_operations() -> None:
    index_runner = RecordingIndexRunner()
    processor = operation_processor(index_runner=index_runner)

    await processor.index_file(index_operation())

    assert index_runner.calls == [("main", "notes/a.md")]


async def test_storage_event_operation_processor_reuses_delete_resources() -> None:
    delete_resources_factory = RecordingDeleteResourcesFactory()
    delete_runner = RecordingDeleteRunner()
    observer = RecordingOperationObserver()
    processor = operation_processor(
        delete_resources_factory=delete_resources_factory,
        delete_runner=delete_runner,
        observer=observer,
    )

    await processor.delete_file(delete_operation("notes/a.md"))
    await processor.delete_file(delete_operation("notes/b.md"))

    assert delete_resources_factory.calls == ["main"]
    assert delete_runner.calls == [
        ("main", "main-resources", "notes/a.md"),
        ("main", "main-resources", "notes/b.md"),
    ]
    assert observer.deleted == [
        ("notes/a.md", "deleted:notes/a.md"),
        ("notes/b.md", "deleted:notes/b.md"),
    ]


async def test_storage_event_operation_processor_notifies_skips_and_failures() -> None:
    observer = RecordingOperationObserver()
    processor = operation_processor(observer=observer)
    operation = RuntimeStorageEventOperation(
        kind=RuntimeStorageEventOperationKind.skip,
        storage_event=storage_event(key="main/image.png"),
        relative_path="image.png",
        skip_reason=RuntimeStorageEventSkipReason.non_markdown,
    )

    await processor.skip_event(operation)
    await processor.event_failed(operation, RuntimeError("boom"))

    assert observer.skipped == [("main", RuntimeStorageEventSkipReason.non_markdown)]
    assert observer.failed == [("image.png", "boom")]
