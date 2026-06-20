"""Tests for core event-based indexing orchestration."""

from dataclasses import dataclass, field

import pytest

from basic_memory.index import (
    StorageEventBucketProcessor,
    StorageEventIndexRuntime,
    StorageEventOperationProcessorFactory,
    StorageEventProjectResolver,
    StorageEventSourceIndexRuntime,
    run_storage_event_source_indexing,
    run_storage_event_indexing,
)
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeStorageEventOperation,
    RuntimeStorageEventProcessingResult,
    StorageBucketName,
    StorageEventPayload,
    StorageObjectIdentity,
    StorageObjectVersion,
)


def storage_event(
    *,
    key: str,
    event_name: str = "OBJECT_CREATED_PUT",
    etag: str = "etag",
) -> StorageEventPayload:
    return StorageEventPayload(
        event_name=event_name,
        event_time="2026-06-20T10:15:00Z",
        object_version=StorageObjectVersion(
            identity=StorageObjectIdentity(bucket_name="tenant-bucket", key=key),
            etag=etag,
            size=42,
        ),
    )


def project_reference(project_id: int, project_path: str) -> ProjectRuntimeReference:
    return ProjectRuntimeReference(
        project_id=project_id,
        project_external_id=f"project-{project_id}",
        project_path=project_path,
        project_name=project_path.title(),
    )


@dataclass(slots=True)
class RecordingProjectResolver(StorageEventProjectResolver):
    projects_by_path: dict[str, ProjectRuntimeReference]
    requested_paths: list[str] = field(default_factory=list)

    async def resolve_project(self, project_path: str) -> ProjectRuntimeReference | None:
        self.requested_paths.append(project_path)
        return self.projects_by_path.get(project_path)


@dataclass(slots=True)
class RecordingStorageEventProcessor:
    project: ProjectRuntimeReference
    fail_relative_path: str | None = None
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
        skip_reason = operation.skip_reason
        if skip_reason is None:
            raise AssertionError("skip operation missing reason")
        self.calls.append((self.project.project_path, "skip", skip_reason.value))

    async def index_file(self, operation: RuntimeStorageEventOperation) -> None:
        relative_path = operation.require_relative_path()
        self.calls.append((self.project.project_path, "index", relative_path))
        if relative_path == self.fail_relative_path:
            raise RuntimeError("index failed")

    async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
        self.calls.append((self.project.project_path, "delete", operation.require_relative_path()))

    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        self.calls.append((self.project.project_path, "failed", str(exc)))


@dataclass(slots=True)
class RecordingProcessorFactory(StorageEventOperationProcessorFactory):
    fail_relative_path: str | None = None
    processors: list[RecordingStorageEventProcessor] = field(default_factory=list)

    def processor_for_project(
        self,
        project: ProjectRuntimeReference,
    ) -> RecordingStorageEventProcessor:
        processor = RecordingStorageEventProcessor(
            project=project,
            fail_relative_path=self.fail_relative_path,
        )
        self.processors.append(processor)
        return processor


@dataclass(slots=True)
class StaticStorageEventSource:
    events_by_bucket_result: dict[StorageBucketName, tuple[StorageEventPayload, ...]]

    def events_by_bucket(self) -> dict[StorageBucketName, tuple[StorageEventPayload, ...]]:
        return self.events_by_bucket_result


@dataclass(slots=True)
class RecordingBucketProcessor(StorageEventBucketProcessor):
    fail_bucket: StorageBucketName | None = None
    calls: list[tuple[StorageBucketName, tuple[str, ...]]] = field(default_factory=list)
    failures: list[tuple[StorageBucketName, int, str]] = field(default_factory=list)

    async def process_bucket_events(
        self,
        bucket_name: StorageBucketName,
        events: tuple[StorageEventPayload, ...],
    ) -> RuntimeStorageEventProcessingResult:
        self.calls.append((bucket_name, tuple(event.object_key for event in events)))
        if bucket_name == self.fail_bucket:
            raise RuntimeError("bucket failed")
        return RuntimeStorageEventProcessingResult.from_counts(processed=len(events))

    async def bucket_failed(
        self,
        bucket_name: StorageBucketName,
        events: tuple[StorageEventPayload, ...],
        exc: Exception,
    ) -> None:
        self.failures.append((bucket_name, len(events), str(exc)))


@pytest.mark.asyncio
async def test_run_storage_event_source_indexing_routes_bucket_batches_in_order() -> None:
    processor = RecordingBucketProcessor()

    result = await run_storage_event_source_indexing(
        StaticStorageEventSource(
            {
                "alpha-bucket": (
                    storage_event(key="alpha/notes/a.md"),
                    storage_event(key="alpha/notes/b.md"),
                ),
                "beta-bucket": (storage_event(key="beta/notes/c.md"),),
            }
        ),
        StorageEventSourceIndexRuntime(bucket_processor=processor),
    )

    assert result.as_dict() == {"processed": 3, "failed": 0, "skipped": 0}
    assert processor.calls == [
        ("alpha-bucket", ("alpha/notes/a.md", "alpha/notes/b.md")),
        ("beta-bucket", ("beta/notes/c.md",)),
    ]
    assert processor.failures == []


@pytest.mark.asyncio
async def test_run_storage_event_source_indexing_counts_bucket_failures() -> None:
    processor = RecordingBucketProcessor(fail_bucket="alpha-bucket")

    result = await run_storage_event_source_indexing(
        StaticStorageEventSource(
            {
                "alpha-bucket": (
                    storage_event(key="alpha/notes/a.md"),
                    storage_event(key="alpha/notes/b.md"),
                ),
                "beta-bucket": (storage_event(key="beta/notes/c.md"),),
            }
        ),
        StorageEventSourceIndexRuntime(bucket_processor=processor),
    )

    assert result.as_dict() == {"processed": 1, "failed": 2, "skipped": 0}
    assert processor.calls == [
        ("alpha-bucket", ("alpha/notes/a.md", "alpha/notes/b.md")),
        ("beta-bucket", ("beta/notes/c.md",)),
    ]
    assert processor.failures == [("alpha-bucket", 2, "bucket failed")]


@pytest.mark.asyncio
async def test_run_storage_event_indexing_routes_project_batches_in_arrival_order() -> None:
    resolver = RecordingProjectResolver(
        {
            "alpha": project_reference(1, "alpha"),
            "beta": project_reference(2, "beta"),
        }
    )
    factory = RecordingProcessorFactory()

    result = await run_storage_event_indexing(
        (
            storage_event(key="alpha/notes/a.md"),
            storage_event(key="README.md"),
            storage_event(key="beta/notes/b.md"),
            storage_event(key="alpha/notes/c.md"),
        ),
        StorageEventIndexRuntime(
            project_resolver=resolver,
            operation_processor_factory=factory,
        ),
    )

    assert result.as_dict() == {"processed": 3, "failed": 0, "skipped": 1}
    assert resolver.requested_paths == ["alpha", "beta"]
    assert [processor.project.project_path for processor in factory.processors] == [
        "alpha",
        "beta",
    ]
    assert factory.processors[0].calls == [
        ("alpha", "index", "notes/a.md"),
        ("alpha", "index", "notes/c.md"),
    ]
    assert factory.processors[1].calls == [("beta", "index", "notes/b.md")]


@pytest.mark.asyncio
async def test_run_storage_event_indexing_skips_events_for_unknown_projects() -> None:
    resolver = RecordingProjectResolver({"alpha": project_reference(1, "alpha")})
    factory = RecordingProcessorFactory()

    result = await run_storage_event_indexing(
        (
            storage_event(key="alpha/notes/a.md"),
            storage_event(key="unknown/notes/b.md"),
        ),
        StorageEventIndexRuntime(
            project_resolver=resolver,
            operation_processor_factory=factory,
        ),
    )

    assert result.as_dict() == {"processed": 1, "failed": 0, "skipped": 1}
    assert resolver.requested_paths == ["alpha", "unknown"]
    assert [processor.project.project_path for processor in factory.processors] == ["alpha"]


@pytest.mark.asyncio
async def test_run_storage_event_indexing_accumulates_project_operation_results() -> None:
    resolver = RecordingProjectResolver({"alpha": project_reference(1, "alpha")})
    factory = RecordingProcessorFactory(fail_relative_path="notes/fail.md")

    result = await run_storage_event_indexing(
        (
            storage_event(key="alpha/notes/a.md"),
            storage_event(key="alpha/notes/b.md", event_name="OBJECT_DELETED"),
            storage_event(key="alpha/image.png"),
            storage_event(key="alpha/notes/fail.md"),
        ),
        StorageEventIndexRuntime(
            project_resolver=resolver,
            operation_processor_factory=factory,
        ),
    )

    assert result.as_dict() == {"processed": 2, "failed": 1, "skipped": 1}
    assert factory.processors[0].calls == [
        ("alpha", "index", "notes/a.md"),
        ("alpha", "delete", "notes/b.md"),
        ("alpha", "skip", "non_markdown"),
        ("alpha", "index", "notes/fail.md"),
        ("alpha", "failed", "index failed"),
    ]
