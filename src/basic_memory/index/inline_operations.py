"""Inline storage-event operation adapters for local runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from basic_memory.indexing.external_file_delete_runner import (
    ExternalFileDeleteEntities,
    ExternalFileDeleteObjects,
    ExternalFileDeleteResult,
    run_external_file_delete,
)
from basic_memory.indexing.index_file_runner import (
    IndexFileExecutor,
    IndexFileMaterializedNoteSource,
    IndexFileMetadataSource,
    IndexFileRunnerChecker,
    run_index_file,
)
from basic_memory.indexing.index_file_runtime import IndexFileRuntimeRequest
from basic_memory.indexing.models import IndexFileJobResult
from basic_memory.runtime.projects import ProjectRuntimeReference
from basic_memory.runtime.storage import RuntimeStorageEventOperation


class InlineStorageEventResultRecorder(Protocol):
    """Observe inline storage-event operation results."""

    async def index_file_completed(
        self,
        operation: RuntimeStorageEventOperation,
        result: IndexFileJobResult,
    ) -> None: ...

    async def delete_file_completed(
        self,
        operation: RuntimeStorageEventOperation,
        result: ExternalFileDeleteResult,
    ) -> None: ...

    async def skip_event(self, operation: RuntimeStorageEventOperation) -> None: ...

    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class InlineStorageEventIndexRuntime:
    """Dependencies needed to execute storage events inline for one project."""

    project: ProjectRuntimeReference
    checker: IndexFileRunnerChecker
    metadata_source: IndexFileMetadataSource
    materialized_note_source: IndexFileMaterializedNoteSource
    file_indexer: IndexFileExecutor
    delete_entities: ExternalFileDeleteEntities
    delete_objects: ExternalFileDeleteObjects
    result_recorder: InlineStorageEventResultRecorder
    index_embeddings: bool = True


@dataclass(frozen=True, slots=True)
class InlineStorageEventOperationProcessor:
    """Execute project-scoped storage-event operations in the current process."""

    runtime: InlineStorageEventIndexRuntime

    async def skip_event(self, operation: RuntimeStorageEventOperation) -> None:
        await self.runtime.result_recorder.skip_event(operation)

    async def index_file(self, operation: RuntimeStorageEventOperation) -> None:
        index_request = IndexFileRuntimeRequest.from_storage_event(
            project=self.runtime.project,
            storage_event=operation.storage_event,
            index_embeddings=self.runtime.index_embeddings,
        )
        result = await run_index_file(
            index_request,
            checker=self.runtime.checker,
            metadata_source=self.runtime.metadata_source,
            materialized_note_source=self.runtime.materialized_note_source,
            file_indexer=self.runtime.file_indexer,
        )
        await self.runtime.result_recorder.index_file_completed(operation, result)

    async def delete_file(self, operation: RuntimeStorageEventOperation) -> None:
        result = await run_external_file_delete(
            operation.require_relative_path(),
            entities=self.runtime.delete_entities,
            objects=self.runtime.delete_objects,
        )
        await self.runtime.result_recorder.delete_file_completed(operation, result)

    async def event_failed(
        self,
        operation: RuntimeStorageEventOperation,
        exc: Exception,
    ) -> None:
        await self.runtime.result_recorder.event_failed(operation, exc)
