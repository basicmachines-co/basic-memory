"""Local note-content materialization adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory.indexing import (
    ContentStoreNoteMaterializationFileWriter,
    IndexFileExecutor,
    RepositoryNoteMaterializationPreflight,
    RepositoryNoteMaterializationPublisher,
    RepositoryNoteMaterializationStatusPublisher,
    run_note_file_delete,
    run_note_materialization,
)
from basic_memory.runtime import (
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteResponse,
    RuntimeFileChecksum,
    RuntimeFileMetadataSource,
    RuntimeFilePath,
    RuntimeNoteContentResponsePayload,
    RuntimeNoteFileDeleteJobRequest,
    plan_note_file_delete_job_request,
    plan_note_materialization_job_request,
)
from basic_memory.services.file_service import FileService

LOCAL_NOTE_CONTENT_TENANT_ID = UUID(int=0)


def note_content_payload_file_path(
    payload: RuntimeNoteContentResponsePayload,
) -> RuntimeFilePath | None:
    """Return the materialized file path carried by an accepted-note payload."""
    if isinstance(payload, RuntimeAcceptedNoteResponse):
        return payload.file_path
    if isinstance(payload, Mapping):
        file_path = payload.get("file_path")
        if isinstance(file_path, str) and file_path:
            return file_path
    return None


@dataclass(frozen=True, slots=True)
class LocalNoteContentStorage:
    """Adapt the local FileService to note-content runtime storage protocols."""

    file_service: FileService

    async def write_file(
        self,
        path: RuntimeFilePath,
        content: str,
        *,
        metadata: dict[str, str] | None = None,
    ) -> RuntimeFileChecksum:
        _ = metadata
        return await self.file_service.write_file(path, content)

    async def get_file_metadata(self, path: RuntimeFilePath) -> RuntimeFileMetadataSource:
        return await self.file_service.get_file_metadata(path)

    async def exists(self, path: RuntimeFilePath) -> bool:
        return await self.file_service.exists(path)

    async def compute_checksum(self, path: RuntimeFilePath) -> RuntimeFileChecksum:
        return await self.file_service.compute_checksum(path)

    async def delete_file(self, path: RuntimeFilePath) -> None:
        await self.file_service.delete_file(path)


@dataclass(frozen=True, slots=True)
class InlineNoteFileDeleteEnqueuer:
    """Execute note-file cleanup immediately in the local runtime."""

    storage: LocalNoteContentStorage

    async def enqueue_note_file_delete(self, request: RuntimeNoteFileDeleteJobRequest) -> None:
        await run_note_file_delete(request, storage=self.storage)


@dataclass(frozen=True, slots=True)
class LocalNoteContentMaterializationProvider:
    """Run accepted-note materialization inline for the local runtime."""

    session_maker: async_sessionmaker[AsyncSession]
    file_service: FileService
    file_indexer: IndexFileExecutor | None = None

    async def materialize_write_change(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]:
        """Write accepted note content to the local filesystem when needed."""
        if accepted.materialization is None:
            return accepted

        storage = LocalNoteContentStorage(self.file_service)
        cleanup_enqueuer = InlineNoteFileDeleteEnqueuer(storage)
        await run_note_materialization(
            plan_note_materialization_job_request(
                tenant_id=LOCAL_NOTE_CONTENT_TENANT_ID,
                materialization=accepted.materialization,
            ),
            preflight=RepositoryNoteMaterializationPreflight(
                session_maker=self.session_maker,
            ),
            writer=ContentStoreNoteMaterializationFileWriter(storage),
            publisher=RepositoryNoteMaterializationPublisher(
                session_maker=self.session_maker,
            ),
            status_publisher=RepositoryNoteMaterializationStatusPublisher(
                session_maker=self.session_maker,
            ),
            cleanup_enqueuer=cleanup_enqueuer,
        )
        file_path = note_content_payload_file_path(accepted.payload)
        if file_path is not None and self.file_indexer is not None:
            await self.file_indexer.index_file(
                file_path,
                source="note-content-materialization",
            )
        return accepted

    async def materialize_delete_change(
        self,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
    ) -> RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload]:
        """Delete materialized files immediately after local accepted-note deletes."""
        if accepted.file_delete is None:
            return accepted

        storage = LocalNoteContentStorage(self.file_service)
        await InlineNoteFileDeleteEnqueuer(storage).enqueue_note_file_delete(
            plan_note_file_delete_job_request(
                tenant_id=LOCAL_NOTE_CONTENT_TENANT_ID,
                file_delete=accepted.file_delete,
            )
        )
        return accepted
