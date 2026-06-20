"""Tests for inline storage-event operation processing."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from uuid import UUID

from basic_memory.index import (
    InlineStorageEventIndexRuntime,
    InlineStorageEventOperationProcessor,
)
from basic_memory.indexing import (
    ExternalFileDeleteResult,
    FileIndexOperation,
    FileIndexPlan,
    FileIndexResult,
    IndexFileJobResult,
    IndexFileObjectMetadata,
)
from basic_memory.indexing.file_index_planning import FileIndexTarget
from basic_memory.runtime import (
    ProjectRuntimeReference,
    RuntimeDeletedNoteEntityDeleteSource,
    RuntimeExternalFileDeleteAction,
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
    etag: str = "etag-a",
) -> StorageEventPayload:
    return StorageEventPayload(
        event_name=event_name,
        event_time="2026-06-20T14:00:00Z",
        object_version=StorageObjectVersion(
            identity=StorageObjectIdentity(bucket_name="local-filesystem", key=key),
            etag=etag,
            size=12,
        ),
    )


def project_reference() -> ProjectRuntimeReference:
    return ProjectRuntimeReference(
        project_id=101,
        project_external_id="project-main",
        project_path="main",
        project_name="Main",
    )


def index_operation() -> RuntimeStorageEventOperation:
    return RuntimeStorageEventOperation(
        kind=RuntimeStorageEventOperationKind.index_file,
        storage_event=storage_event(),
        relative_path="notes/a.md",
    )


def delete_operation() -> RuntimeStorageEventOperation:
    return RuntimeStorageEventOperation(
        kind=RuntimeStorageEventOperationKind.delete_file,
        storage_event=storage_event(event_name="OBJECT_DELETED"),
        relative_path="notes/a.md",
    )


class ReadEverythingChecker:
    def __init__(self) -> None:
        self.targets: tuple[FileIndexTarget, ...] = ()

    async def detect(self, targets: Sequence[FileIndexTarget]) -> FileIndexPlan:
        self.targets = tuple(targets)
        return FileIndexPlan(
            paths_to_read=tuple(target.path for target in targets),
            decisions=(),
        )


class CurrentFileMetadataSource:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def load_current_file_metadata(self, file_path: str) -> IndexFileObjectMetadata:
        self.paths.append(file_path)
        return IndexFileObjectMetadata(checksum="etag-a", metadata={})


class EmptyMaterializedNoteSource:
    async def load_current_materialized_note_entity(self, file_path: str) -> None:
        return None


class RecordingFileIndexer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def index_markdown_file(self, file_path: str, *, source: str) -> FileIndexResult:
        self.calls.append((file_path, source))
        return FileIndexResult(
            file_path=file_path,
            entity_id=42,
            external_id="note-42",
            title="Note 42",
            permalink="notes/note-42",
            checksum="checksum-42",
            operation=FileIndexOperation.created,
        )


@dataclass(frozen=True, slots=True)
class FakeDeletedEntity(RuntimeDeletedNoteEntityDeleteSource):
    id: int
    external_id: str
    title: str
    permalink: str


class RecordingDeleteEntities:
    def __init__(self, entity: FakeDeletedEntity | None) -> None:
        self.entity = entity
        self.find_paths: list[str] = []
        self.delete_requests: list[tuple[int, str]] = []

    async def find_entity_by_file_path(
        self,
        file_path: str,
    ) -> FakeDeletedEntity | None:
        self.find_paths.append(file_path)
        return self.entity

    async def delete_entity_if_file_path_matches(
        self,
        *,
        entity_id: int,
        file_path: str,
    ) -> bool:
        self.delete_requests.append((entity_id, file_path))
        return True


class RecordingDeleteObjects:
    def __init__(self, exists: bool) -> None:
        self.exists = exists
        self.paths: list[str] = []

    async def file_exists(self, file_path: str) -> bool:
        self.paths.append(file_path)
        return self.exists


@dataclass(slots=True)
class RecordingInlineResultRecorder:
    indexed: list[tuple[RuntimeStorageEventOperation, IndexFileJobResult]] = field(
        default_factory=list
    )
    deleted: list[tuple[RuntimeStorageEventOperation, ExternalFileDeleteResult]] = field(
        default_factory=list
    )
    skipped: list[RuntimeStorageEventOperation] = field(default_factory=list)
    failed: list[tuple[RuntimeStorageEventOperation, str]] = field(default_factory=list)

    async def index_file_completed(
        self,
        operation: RuntimeStorageEventOperation,
        result: IndexFileJobResult,
    ) -> None:
        self.indexed.append((operation, result))

    async def delete_file_completed(
        self,
        operation: RuntimeStorageEventOperation,
        result: ExternalFileDeleteResult,
    ) -> None:
        self.deleted.append((operation, result))

    async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
        self.skipped.append(operation)

    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        self.failed.append((operation, str(exc)))


def inline_runtime(
    *,
    checker: ReadEverythingChecker | None = None,
    metadata_source: CurrentFileMetadataSource | None = None,
    file_indexer: RecordingFileIndexer | None = None,
    delete_entities: RecordingDeleteEntities | None = None,
    delete_objects: RecordingDeleteObjects | None = None,
    recorder: RecordingInlineResultRecorder | None = None,
) -> InlineStorageEventIndexRuntime:
    return InlineStorageEventIndexRuntime(
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        project=project_reference(),
        checker=checker or ReadEverythingChecker(),
        metadata_source=metadata_source or CurrentFileMetadataSource(),
        materialized_note_source=EmptyMaterializedNoteSource(),
        file_indexer=file_indexer or RecordingFileIndexer(),
        delete_entities=delete_entities or RecordingDeleteEntities(None),
        delete_objects=delete_objects or RecordingDeleteObjects(exists=False),
        result_recorder=recorder or RecordingInlineResultRecorder(),
    )


async def test_inline_processor_indexes_storage_event_with_index_file_runner() -> None:
    checker = ReadEverythingChecker()
    metadata_source = CurrentFileMetadataSource()
    file_indexer = RecordingFileIndexer()
    recorder = RecordingInlineResultRecorder()
    processor = InlineStorageEventOperationProcessor(
        inline_runtime(
            checker=checker,
            metadata_source=metadata_source,
            file_indexer=file_indexer,
            recorder=recorder,
        )
    )

    await processor.index_file(index_operation())

    assert [
        (target.path, target.observed_checksum, target.observed_size) for target in checker.targets
    ] == [("notes/a.md", "etag-a", 12)]
    assert file_indexer.calls == [("notes/a.md", "s3_webhook")]
    assert metadata_source.paths == ["notes/a.md"]
    assert recorder.indexed[0][1].entity_id == 42
    assert recorder.indexed[0][1].reason == "file indexed: notes/a.md"


async def test_inline_processor_deletes_missing_file_with_external_delete_runner() -> None:
    delete_entities = RecordingDeleteEntities(
        FakeDeletedEntity(
            id=42,
            external_id="note-42",
            title="Note 42",
            permalink="notes/note-42",
        )
    )
    delete_objects = RecordingDeleteObjects(exists=False)
    recorder = RecordingInlineResultRecorder()
    processor = InlineStorageEventOperationProcessor(
        inline_runtime(
            delete_entities=delete_entities,
            delete_objects=delete_objects,
            recorder=recorder,
        )
    )

    await processor.delete_file(delete_operation())

    assert delete_entities.find_paths == ["notes/a.md"]
    assert delete_objects.paths == ["notes/a.md"]
    assert delete_entities.delete_requests == [(42, "notes/a.md")]
    assert recorder.deleted[0][1].entity_deleted is True
    assert recorder.deleted[0][1].plan.action == RuntimeExternalFileDeleteAction.delete_entity


async def test_inline_processor_delegates_skip_and_failed_notifications() -> None:
    recorder = RecordingInlineResultRecorder()
    processor = InlineStorageEventOperationProcessor(inline_runtime(recorder=recorder))
    operation = RuntimeStorageEventOperation(
        kind=RuntimeStorageEventOperationKind.skip,
        storage_event=storage_event(key="main/image.png"),
        relative_path="image.png",
        skip_reason=RuntimeStorageEventSkipReason.non_markdown,
    )

    await processor.skip_event(operation)
    await processor.event_failed(operation, RuntimeError("boom"))

    assert recorder.skipped == [operation]
    assert recorder.failed == [(operation, "boom")]
